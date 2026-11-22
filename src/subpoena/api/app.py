"""HTTP API.

Core:
    POST /ingest/calls           receive a signed end-of-call webhook
    POST /calls                  ingest a call (transcript + claims)
    POST /audit                  audit a stored or inline call against the system of record
    POST /audit/{id}/claims/{claim_id}/recheck   re-check a sealed claim
    GET  /audit/{id}/report      the sealed report (JSON, or Markdown with ?format=md)

Supporting:
    GET  /audits                 recent audits
    GET  /alerts                 paging feed from sealed audits
    GET  /reviews                review items and sealed transition history
    POST /reviews/{id}/transition   transition one review item
    GET  /scorecard              per-agent honesty numbers
    GET  /repair-queue           recurring failure shapes across audits, ranked
    GET  /ledger/verify          re-verify every seal and the hash chain
    GET  /demo/scenarios         the golden scenarios
    POST /demo/scenarios/{id}/run    play a scenario in a sandbox and audit it
    POST /demo/scenarios/{id}/stage  load a scenario into the live system of record
    GET  /                       the replay view
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from subpoena import __version__
from subpoena.api.alerts import build_alert_feed
from subpoena.api.ingest import parse_delivery
from subpoena.api.repair_queue import build_repair_queue
from subpoena.api.review import Hash, ReviewError, TransitionRequest
from subpoena.api.scorecard import build_scorecards
from subpoena.api.store import AuditStore
from subpoena.api.webhook import CriticalWebhook
from subpoena.auditor import (
    GENESIS,
    Auditor,
    ClaimExtractor,
    LLMClaimExtractor,
    Sealer,
    render_markdown,
)
from subpoena.llm import llm_from_env
from subpoena.models import AuditReport, CallRecord
from subpoena.scenarios import BY_ID, audit_call, build_world, run_suspect
from subpoena.sor import SqliteStore
from subpoena.util import utcnow

log = logging.getLogger("subpoena.api")
_STATIC = Path(__file__).parent / "static"


@dataclass(frozen=True)
class Settings:
    db_path: str = ":memory:"  # calls + audit ledger
    sor_path: str = ":memory:"  # the system of record being audited
    api_key: str | None = None
    signing_key: bytes | None = None
    demo: bool = True
    critical_webhook_url: str | None = field(default=None, repr=False)

    ingest_secret: bytes | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        key = os.environ.get("SUBPOENA_SIGNING_KEY")
        ingest_secret = os.environ.get("SUBPOENA_INGEST_SECRET")
        return cls(
            db_path=os.environ.get("SUBPOENA_DB", ":memory:"),
            sor_path=os.environ.get("SUBPOENA_SOR_DB", ":memory:"),
            api_key=os.environ.get("SUBPOENA_API_KEY") or None,
            signing_key=key.encode() if key else None,
            ingest_secret=ingest_secret.encode() if ingest_secret else None,
            demo=os.environ.get("SUBPOENA_DEMO", "1") != "0",
            critical_webhook_url=os.environ.get("SUBPOENA_CRITICAL_WEBHOOK_URL") or None,
        )


class AuditRequest(BaseModel):
    """Audit a stored call by id, or an inline call (which is stored first)."""

    model_config = ConfigDict(extra="forbid")
    call_id: str | None = None
    call: CallRecord | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> AuditRequest:
        if (self.call_id is None) == (self.call is None):
            raise ValueError("provide exactly one of call_id or call")
        return self


def _ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse({"success": True, "data": data, "error": None}, status_code=status)


def _fail(message: str, status: int) -> JSONResponse:
    return JSONResponse({"success": False, "data": None, "error": message}, status_code=status)


def _summary(report: AuditReport) -> dict[str, Any]:
    return {
        "audit_id": report.audit_id,
        "call_id": report.call.call_id,
        "agent_id": report.call.agent_id,
        "outcome": report.outcome.value,
        "created_at": report.created_at.isoformat(),
        "stats": report.stats.model_dump(mode="json"),
        "extraction": report.extraction.model_dump(mode="json"),
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    webhook = (
        CriticalWebhook(settings.critical_webhook_url, settings.db_path)
        if settings.critical_webhook_url is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if webhook is not None:
            webhook.start()
        try:
            yield
        finally:
            if webhook is not None:
                webhook.stop()

    app = FastAPI(
        lifespan=lifespan,
        title="Subpoena",
        version=__version__,
        description="Every claim needs a receipt: outcome auditing for AI voice agents.",
    )
    app.state.settings = settings
    app.state.store = AuditStore(settings.db_path)
    app.state.sor = SqliteStore(settings.sor_path)
    app.state.stage_lock = threading.Lock()
    app.state.sealer = Sealer(settings.signing_key)
    llm = llm_from_env()
    extractor: ClaimExtractor | None = LLMClaimExtractor(llm) if llm else None

    def store() -> AuditStore:
        return app.state.store

    def append_report(report: AuditReport) -> AuditReport:
        return store().append(
            report,
            app.state.sealer,
            enqueue=webhook.enqueue if webhook is not None else None,
            revision_key=settings.signing_key,
        )

    def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        expected = settings.api_key
        if expected and not hmac.compare_digest(x_api_key or "", expected):
            raise HTTPException(401, "missing or invalid X-API-Key")

    def require_demo() -> None:
        if not settings.demo:
            raise HTTPException(404, "demo endpoints are disabled")

    guarded = [Depends(require_key)]
    demo_only = [Depends(require_key), Depends(require_demo)]

    # -- error envelope ----------------------------------------------------- #

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return _fail(str(exc.detail), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'][1:]) or 'body'}: {e['msg']}" for e in exc.errors()
        )
        return _fail(f"invalid request: {problems}", 422)

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return _fail("internal error", 500)

    # -- core --------------------------------------------------------------- #

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return _ok({"status": "ok", "version": __version__, "signed": bool(settings.signing_key)})

    @app.post("/calls", status_code=201, dependencies=guarded)
    def ingest_call(call: CallRecord) -> JSONResponse:
        if not store().save_call(call):
            raise HTTPException(409, f"call {call.call_id} already exists")
        return _ok({"call_id": call.call_id, "claims": len(call.claims)}, 201)

    @app.post("/ingest/calls", status_code=201, responses={200: {"description": "Repeat delivery"}})
    async def ingest_delivery(
        request: Request, x_subpoena_signature: Annotated[str | None, Header()] = None
    ) -> JSONResponse:
        """Receive an end-of-call webhook as UTF-8 JSON.

        Set SUBPOENA_INGEST_SECRET to the shared secret. Send the lowercase hexadecimal
        HMAC-SHA256 of the exact request body in X-Subpoena-Signature.

        All top-level fields shown below are required. Timestamps use RFC 3339 with
        a timezone. Transcript turns use zero-based indices and the speaker is
        "caller" or "agent". Turns are ordered by time within the call window.
        An empty transcript and an empty metadata object are valid.

        ```json
        {
          "platform": "voice-platform",
          "call_id": "external-42",
          "agent_id": "scheduling",
          "started_at": "2026-11-22T15:00:00Z",
          "ended_at": "2026-11-22T15:01:00Z",
          "transcript": [
            {"index": 0, "speaker": "caller", "text": "Hello", "at": "2026-11-22T15:00:05Z"}
          ],
          "metadata": {"caller": {"name": "Example Caller"}, "agent_version": "1"}
        }
        ```

        Optional metadata fields include caller (name and optional phone), agent_version,
        model and prompt_version. Other metadata is retained in the audit payload.
        The response contains the internal call_id for use with GET /calls/{call_id} or
        POST /audit. The first delivery receives 201. Valid repeats for the same platform
        and external call ID receive 200 with the original internal ID.
        """
        secret = settings.ingest_secret
        if not secret or not x_subpoena_signature:
            raise HTTPException(401, "missing or invalid X-Subpoena-Signature")
        body = await request.body()
        expected = hmac.digest(secret, body, "sha256").hex().encode("ascii")
        if not hmac.compare_digest(x_subpoena_signature.encode("latin-1"), expected):
            raise HTTPException(401, "missing or invalid X-Subpoena-Signature")
        try:
            delivery, call = parse_delivery(body)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in error['loc']) or 'body'}: {error['msg']}"
                for error in exc.errors(include_input=False, include_context=False)
            )
            return _fail(f"invalid call delivery: {problems}", 422)
        except (ValueError, RecursionError):
            return _fail("invalid call delivery: expected valid UTF-8 JSON", 400)
        call_id, created = await run_in_threadpool(
            store().save_call_delivery, delivery.platform, delivery.call_id, call, body
        )
        return _ok({"call_id": call_id}, 201 if created else 200)

    @app.get("/calls/{call_id}", dependencies=guarded)
    def get_call(call_id: str) -> JSONResponse:
        call = store().get_call(call_id)
        if call is None:
            raise HTTPException(404, f"call {call_id} not found")
        return _ok(call.model_dump(mode="json"))

    @app.post("/audit", status_code=201, dependencies=guarded)
    def audit(req: AuditRequest) -> JSONResponse:
        if req.call is not None:
            existing = store().get_call(req.call.call_id)
            if existing is not None and existing != req.call:
                raise HTTPException(
                    409,
                    f"call {req.call.call_id} already exists with different contents; "
                    "audit the stored call by id instead",
                )
            store().save_call(req.call)
            call = req.call
        else:
            found = store().get_call(req.call_id or "")
            if found is None:
                raise HTTPException(404, f"call {req.call_id} not found")
            call = found
        auditor = Auditor(
            app.state.sor.session("auditor", utcnow), extractor=extractor, sealer=app.state.sealer
        )
        report = append_report(auditor.audit(call))
        log.info("audit %s for call %s: %s", report.audit_id, call.call_id, report.outcome.value)
        return _ok(report.model_dump(mode="json"), 201)

    @app.post("/audit/{audit_id}/claims/{claim_id}/recheck", dependencies=guarded)
    def recheck_claim(audit_id: str, claim_id: str) -> JSONResponse:
        original = store().get_audit(audit_id)
        if original is None:
            raise HTTPException(404, f"audit {audit_id} not found")
        claim = next((v.claim for v in original.verdicts if v.claim.claim_id == claim_id), None)
        if claim is None:
            raise HTTPException(404, f"claim {claim_id} not found in audit {audit_id}")
        if original.seal is None:
            raise HTTPException(409, f"audit {audit_id} is not sealed")

        # Keep the reader alive if a demo request stages another system of record.
        with app.state.stage_lock:
            auditor = Auditor(
                app.state.sor.session("auditor", utcnow),
                sealer=app.state.sealer,
                clock=utcnow,
                use_transcript=False,
            )
            checked = auditor.recheck(original.call, claim, prev_hash=original.seal.chain_hash)
        assert checked.seal is not None
        return _ok(
            {
                "audit_id": checked.audit_id,
                "verdict": checked.verdicts[0].model_dump(mode="json"),
                "seal": checked.seal.model_dump(mode="json"),
            }
        )

    @app.get("/audit/{audit_id}/report", dependencies=guarded, response_model=None)
    def report(
        audit_id: str, format: Annotated[str, Query(pattern="^(json|md)$")] = "json"
    ) -> JSONResponse | PlainTextResponse:
        found = store().get_audit(audit_id)
        if found is None:
            raise HTTPException(404, f"audit {audit_id} not found")
        if format == "md":
            return PlainTextResponse(render_markdown(found), media_type="text/markdown")
        return _ok(found.model_dump(mode="json"))

    # -- supporting --------------------------------------------------------- #

    @app.get("/audits", dependencies=guarded)
    def list_audits(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> JSONResponse:
        return _ok([_summary(r) for r in store().audits(limit=limit, newest_first=True)])

    @app.get("/alerts", dependencies=guarded)
    def list_alerts() -> JSONResponse:
        return _ok(build_alert_feed(store(), app.state.sealer))

    @app.get("/ledger/verify", dependencies=guarded)
    def verify_ledger(strict: bool = False) -> JSONResponse:
        sealer: Sealer = app.state.sealer
        prev, count = GENESIS, 0  # the first entry must link to genesis, or one was removed
        for rep in store().audits():
            check = sealer.verify(rep, expected_prev=prev, strict=strict)
            if not check.ok:
                return _ok({"ok": False, "checked": count, "broken_at": rep.audit_id,
                            "reason": check.reason,
                            "integrity": check.integrity.value,
                            "authentication": check.authentication.value})  # fmt: skip
            assert rep.seal is not None
            prev, count = rep.seal.chain_hash, count + 1
        authentication = check.authentication.value if count else "unsigned"
        return _ok({"ok": True, "checked": count, "head": prev,
                    "integrity": "intact", "authentication": authentication})  # fmt: skip

    @app.get("/reviews", dependencies=guarded)
    def list_reviews(checkpoint: Hash | None = None) -> JSONResponse:
        try:
            return _ok(store().list_reviews(settings.signing_key, checkpoint))
        except ReviewError as exc:
            return _fail(str(exc), exc.status)

    @app.post("/reviews/{item_id}/transition", dependencies=guarded)
    def transition_review(
        item_id: str,
        request: TransitionRequest,
        checkpoint: Hash | None = None,
    ) -> JSONResponse:
        try:
            receipt = store().transition_review(settings.signing_key, item_id, request, checkpoint)
        except ReviewError as exc:
            return _fail(str(exc), exc.status)
        return _ok(receipt.model_dump(mode="json"))

    @app.get("/revisions", dependencies=guarded)
    def list_revisions() -> JSONResponse:
        try:
            return _ok(store().list_revisions(settings.signing_key))
        except ReviewError as exc:
            return _fail(str(exc), exc.status)

    @app.get("/scorecard", dependencies=guarded)
    def scorecard() -> JSONResponse:
        # Totals as always, plus per-version buckets, a daily series, and
        # regression flags (MIN_CLAIMS / REGRESSION_DROP policy in scorecard.py).
        return _ok(build_scorecards(store().audits()))

    @app.get("/repair-queue", dependencies=guarded)
    def repair_queue() -> JSONResponse:
        return _ok(build_repair_queue(store().audits()))

    # -- demo --------------------------------------------------------------- #

    @app.get("/demo/scenarios", dependencies=demo_only)
    def scenarios() -> JSONResponse:
        return _ok(
            [
                {
                    "id": s.id,
                    "title": s.title,
                    "story": s.story,
                    "group": s.id.split("-", 1)[0],
                    "faults": sorted(m.value for m in s.fault_modes),
                    "known_gap": s.known_gap,
                }
                for s in BY_ID.values()
            ]
        )

    def _scenario(scenario_id: str):  # noqa: ANN202
        scenario = BY_ID.get(scenario_id)
        if scenario is None:
            raise HTTPException(404, f"scenario {scenario_id} not found")
        return scenario

    @app.post("/demo/scenarios/{scenario_id}/run", status_code=201, dependencies=demo_only)
    def run_demo(scenario_id: str) -> JSONResponse:
        """Play a scenario in a throwaway system of record, audit it, ledger the report."""
        scenario = _scenario(scenario_id)
        world = build_world(scenario)
        try:
            result = run_suspect(scenario, world)
            draft = audit_call(scenario, world, result, extractor=extractor)
        finally:
            world.store.close()
        unique = draft.model_copy(update={"audit_id": f"aud_{uuid.uuid4().hex[:12]}"})
        store().save_call(result.call)
        return _ok(append_report(unique).model_dump(mode="json"), 201)

    @app.post("/demo/scenarios/{scenario_id}/stage", status_code=201, dependencies=demo_only)
    def stage_demo(scenario_id: str) -> JSONResponse:
        """Reset the live system of record to a scenario and let the suspect take the call.

        Returns the call record, unaudited, ready to POST to /calls and /audit.
        """
        if settings.sor_path != ":memory:":
            raise HTTPException(409, "staging is only allowed on an in-memory system of record")
        scenario = _scenario(scenario_id)
        # Serialize swaps: two concurrent stagings must not interleave a store
        # half-swap or leak one of the stores.
        with app.state.stage_lock:
            fresh = SqliteStore()
            result = run_suspect(scenario, build_world(scenario, fresh))
            old, app.state.sor = app.state.sor, fresh
            old.close()
        return _ok(result.call.model_dump(mode="json"), 201)

    @app.get("/", include_in_schema=False)
    def replay() -> FileResponse:
        return FileResponse(_STATIC / "replay.html", media_type="text/html")

    return app

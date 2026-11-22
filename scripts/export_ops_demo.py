#!/usr/bin/env python3
"""Bake the Twilio review and durable webhook walkthrough for GitHub Pages."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from subpoena.api import review_store
from subpoena.api.review import ReviewError, ReviewState, TransitionRequest, audit_items
from subpoena.api.store import AuditStore
from subpoena.api.webhook import CriticalWebhook
from subpoena.auditor import Auditor, RuleBasedExtractor, Sealer
from subpoena.ingest.twilio import load_call_record
from subpoena.models import AuditReport
from subpoena.scenarios.golden import BY_ID
from subpoena.scenarios.world import CLINIC, run_scenario
from subpoena.sor import SqliteStore

DEMO = Path(__file__).resolve().parents[1] / "docs" / "demo"
DEFAULT_OUT = DEMO / "data-ops"
KEY = b"subpoena-pages-demo"


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start.timestamp()

    def __call__(self) -> float:
        return self.now

    def utcnow(self) -> datetime:
        return datetime.fromtimestamp(self.now, UTC)


def twilio_auditor(clock: Clock, records: SqliteStore) -> Auditor:
    return Auditor(
        records.session("auditor", clock.utcnow),
        clinic=CLINIC,
        clock=clock.utcnow,
    )


def run_reviews(store: AuditStore, audit: AuditReport, clock: Clock) -> dict:
    booking, confirmation = audit_items(audit)

    def transition(item, head, state, actor, reason):
        clock.now += 1
        request = TransitionRequest(expected_head=head, state=state, actor=actor, reason=reason)
        return store.transition_review(KEY, item.item_id, request)

    with patch.object(review_store, "utcnow", clock.utcnow):
        receipt = transition(
            booking,
            booking.head,
            ReviewState.ACKNOWLEDGED,
            "reviewer",
            "Checked the booking claim against the appointment ledger.",
        )
        receipt = transition(
            booking,
            receipt.hash,
            ReviewState.RESOLVED,
            "supervisor",
            "Closed the finding after recording the missing booking.",
        )
        try:
            transition(
                booking,
                receipt.hash,
                ReviewState.OPEN,
                "reviewer",
                "Requested a second look at the closed finding.",
            )
        except ReviewError as error:
            rejection = {
                "item_id": booking.item_id,
                "from_state": receipt.to_state,
                "to_state": ReviewState.OPEN,
                "status": error.status,
                "message": str(error),
                "after_sequence": receipt.sequence,
            }
        else:
            raise RuntimeError("The resolved review accepted a reopen request")

        receipt = transition(
            confirmation,
            confirmation.head,
            ReviewState.ACKNOWLEDGED,
            "reviewer",
            "Checked the confirmation claim against the message log.",
        )
        receipt = transition(
            confirmation,
            receipt.hash,
            ReviewState.DISPUTED,
            "supervisor",
            "Requested a check of the provider's delivery records.",
        )
        transition(
            confirmation,
            receipt.hash,
            ReviewState.OPEN,
            "reviewer",
            "Returned the finding to the queue for follow-up.",
        )

    return {
        "audit": audit.model_dump(mode="json"),
        **store.list_reviews(KEY),
        "rejection": rejection,
    }


class WorkerCrash(BaseException):
    """Leave the claimed lease in SQLite without running the finish path."""


def run_demo(path: Path) -> tuple[dict, dict]:
    fixtures = DEMO / "twilio"
    call = load_call_record(
        fixtures / "completed-status.json",
        fixtures / "completed-transcription.json",
        fixtures / "call-resource.json",
        agent_id="brightside-reception-agent",
        agent_version="v3.2.1",
        caller_name="Rosa Delgado",
        claims_path=fixtures / "tool-log-claims.json",
    )
    clock = Clock(call.ended_at + timedelta(minutes=1))
    results = iter((TimeoutError, 204, WorkerCrash, 204))

    def transport(_url: str, _payload: bytes, _event_id: str, timeout: float) -> int:
        result = next(results, None)
        if result is TimeoutError:
            clock.now += timeout
            raise TimeoutError
        if result is WorkerCrash:
            raise WorkerCrash
        if result != 204:
            raise AssertionError("unexpected transport call")
        return result

    webhook = CriticalWebhook(
        "https://receiver.example.test/critical",
        str(path),
        transport=transport,
        clock=clock,
    )
    store = AuditStore(str(path))
    records = SqliteStore()
    with closing(store), closing(records), closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        auditor = twilio_auditor(clock, records)
        sealer = Sealer(KEY)
        journal = []
        enqueued = []

        def append(audit_id: str) -> AuditReport:
            audit = store.append(
                auditor.audit(call, audit_id=audit_id),
                sealer,
                enqueue=webhook.enqueue,
            )
            (row,) = conn.execute(
                "SELECT event_id, audit_id, result_pointer, due_at "
                "FROM critical_webhook_outbox WHERE audit_id = ?",
                (audit_id,),
            )
            enqueued.append(dict(row))
            return audit

        def outbox() -> sqlite3.Row:
            (row,) = conn.execute(
                "SELECT due_at, lease_until, terminal FROM critical_webhook_outbox "
                "WHERE event_id = ?",
                (enqueued[-1]["event_id"],),
            )
            return row

        def read_journal() -> None:
            rows = conn.execute(
                "SELECT * FROM critical_webhook_journal ORDER BY rowid LIMIT -1 OFFSET ?",
                (len(journal),),
            )
            state = outbox()
            for row in rows:
                journal.append(
                    {
                        "entry": dict(row),
                        "outbox": dict(state) if row["outcome"] != "started" else None,
                    }
                )

        audit = append("aud-twilio-ops")
        clock.now += 1
        webhook._step()  # claim, then timeout and persist the next due time
        read_journal()
        clock.now = outbox()["due_at"]
        webhook._step()
        read_journal()

        review = run_reviews(store, audit, clock)
        clock.now += 1
        append("aud-twilio-ops-replay")
        clock.now += 1
        try:
            webhook._step()
        except WorkerCrash:
            read_journal()
        else:
            raise RuntimeError("The simulated worker did not crash")

        clock.now = outbox()["lease_until"]
        webhook._step()  # expired lease is journaled and rescheduled
        read_journal()
        clock.now = outbox()["due_at"]
        webhook._step()
        read_journal()

        return review, {"enqueued": enqueued, "journal": journal}


def export(out: Path = DEFAULT_OUT) -> None:
    """Write the bake. Tests can redirect all writes to their temporary directories."""
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out) as scratch:
        review, delivery = run_demo(Path(scratch) / "ops.sqlite")
    outage = run_scenario(
        BY_ID["edge-calendar-outage"], extractor=RuleBasedExtractor(), sealer=Sealer(KEY)
    )
    review["outage_audit"] = outage.report.model_dump(mode="json")
    for filename, artifact in (("review.json", review), ("delivery.json", delivery)):
        (out / filename).write_text(
            json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    export()

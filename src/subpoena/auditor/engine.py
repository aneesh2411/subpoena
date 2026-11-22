"""The auditor.

Pipeline, in order:

1. **Collect claims**: structured claims plus anything the caller was told that
   was never logged as a claim.
2. **Pull evidence**: each system's change feed for the call window, attributed
   to the agent's identity and reduced to net changes.
3. **Reconcile**: pair claims with writes, exact matches first, then best partial.
4. **Judge**: field-by-field verdict per claim, with evidence.
5. **Reverse reconcile**: any write left over is an unclaimed write.
6. **Policy**: appointments the agent placed are checked against clinic rules.
7. **Seal** the report.

No language model is involved in steps 2-7. Verdicts are deterministic.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from subpoena import __version__
from subpoena.auditor import appointments, fulfillment, sidefx
from subpoena.auditor.context import AuditContext, Match, MatchKey
from subpoena.auditor.delta import SOURCE, Attribution
from subpoena.auditor.extractor import ClaimExtractor, RuleBasedExtractor, merge_claims
from subpoena.auditor.findings import DEFAULT_POLICIES, Policy, policy_violations, unclaimed_writes
from subpoena.auditor.seal import GENESIS, Sealer
from subpoena.auditor.verdicts import unverifiable
from subpoena.models import (
    AppointmentBooked,
    AuditReport,
    AuditStats,
    CallRecord,
    Check,
    CheckName,
    Claim,
    ClaimSource,
    CoverageGap,
    Entity,
    ExtractionHealth,
    Finding,
    Fulfillment,
    HandoffCreated,
    Outcome,
    RecordsUpdated,
    Severity,
    Ticket,
    Verdict,
    VerdictStatus,
    max_severity,
)
from subpoena.sor.base import COMPLETE, AdapterUnavailable, SystemOfRecordReader
from subpoena.util import ClinicConfig, utcnow


@dataclass(frozen=True)
class _Handler:
    needs: tuple[Entity, ...]  # systems that must be readable to judge the claim
    consumes: Entity  # where the write that backs the claim lives
    match: Callable[[Any, AuditContext, bool], Match | None]
    candidates: Callable[[Any, AuditContext, bool], list[tuple[int, Match]]]
    judge: Callable[[Any, Match | None, AuditContext], Verdict]


_CALENDAR = (Entity.APPOINTMENT, Entity.PATIENT)
HANDLERS: dict[str, _Handler] = {
    "AppointmentBooked": _Handler(
        _CALENDAR,
        Entity.APPOINTMENT,
        appointments.match_booked,
        appointments.candidates_booked,
        appointments.judge_booked,
    ),
    "Rescheduled": _Handler(
        _CALENDAR,
        Entity.APPOINTMENT,
        appointments.match_rescheduled,
        appointments.candidates_rescheduled,
        appointments.judge_rescheduled,
    ),
    "Cancelled": _Handler(
        _CALENDAR,
        Entity.APPOINTMENT,
        appointments.match_cancelled,
        appointments.candidates_cancelled,
        appointments.judge_cancelled,
    ),
    "ConfirmationSent": _Handler(
        (Entity.MESSAGE,),
        Entity.MESSAGE,
        sidefx.match_confirmation,
        sidefx.candidates_confirmation,
        sidefx.judge_confirmation,
    ),
    "RecordsUpdated": _Handler(
        (Entity.PATIENT,),
        Entity.PATIENT,
        sidefx.match_records,
        sidefx.candidates_records,
        sidefx.judge_records,
    ),
    "PrescriptionRefilled": _Handler(
        (Entity.PRESCRIPTION, Entity.PATIENT),
        Entity.PRESCRIPTION,
        sidefx.match_prescription,
        sidefx.candidates_prescription,
        sidefx.judge_prescription,
    ),
    "PaymentCollected": _Handler(
        (Entity.PAYMENT, Entity.PATIENT),
        Entity.PAYMENT,
        sidefx.match_payment,
        sidefx.candidates_payment,
        sidefx.judge_payment,
    ),
    "HandoffCreated": _Handler(
        (Entity.TICKET, Entity.PATIENT),
        Entity.TICKET,
        sidefx.match_handoff,
        sidefx.candidates_handoff,
        sidefx.judge_handoff,
    ),
}


class Auditor:
    NAME = f"subpoena/{__version__}"

    def __init__(
        self,
        sor: SystemOfRecordReader,
        *,
        clinic: ClinicConfig | None = None,
        extractor: ClaimExtractor | None = None,
        use_transcript: bool = True,
        policies: Sequence[Policy] = DEFAULT_POLICIES,
        sealer: Sealer | None = None,
        clock: Callable[[], datetime] = utcnow,
        grace: timedelta = timedelta(seconds=30),
    ) -> None:
        self._sor = sor
        self._clinic = clinic or ClinicConfig()
        self._extractor = (extractor or RuleBasedExtractor()) if use_transcript else None
        self._policies = tuple(policies)
        self._sealer = sealer or Sealer()
        self._clock = clock
        self._grace = grace

    def audit(
        self, call: CallRecord, *, audit_id: str | None = None, prev_hash: str = GENESIS
    ) -> AuditReport:
        ctx = AuditContext.open(call, self._sor, self._clinic, self._grace)
        claims = self._collect_claims(call, ctx)
        settled, withdrawn = _resolve_corrections(claims, ctx)
        matches, blocked = self._reconcile(settled, ctx)
        verdicts = tuple(self._judge(c, matches.get(c.claim_id), blocked, ctx) for c in settled)
        verdicts = tuple(
            _correction_checks(v, withdrawn.get(v.claim.claim_id, ()), ctx) for v in verdicts
        )
        findings = self._findings(ctx, settled, verdicts)
        fulfillments = self._fulfillments(settled, matches, ctx)
        coverage_gaps = _coverage_gaps(settled, ctx)
        for gap in coverage_gaps:
            ctx.log(
                "coverage",
                f"{gap.source} {gap.kind}: {len(gap.claim_ids)} claim(s) capped at UNVERIFIABLE",
            )
        outcome = _outcome(verdicts, findings, ctx)
        ctx.log("verdict", f"Outcome: {outcome.value}")
        report = AuditReport(
            audit_id=audit_id or f"aud_{uuid.uuid4().hex[:12]}",
            auditor=self.NAME,
            created_at=self._clock(),
            call=call,
            outcome=outcome,
            verdicts=verdicts,
            findings=findings,
            trail=tuple(ctx.trail),
            stats=_stats(verdicts, findings),
            extraction=self._extraction_health(ctx),
            fulfillments=fulfillments,
            coverage_gaps=coverage_gaps,
        )
        return self._sealer.seal(report, prev_hash)

    def recheck(self, call: CallRecord, claim: Claim, *, prev_hash: str) -> AuditReport:
        """Re-evaluate a previously judged claim through the current evidence cutoff."""
        now = self._clock()
        ctx = AuditContext.open(call, self._sor, self._clinic, now - call.ended_at)
        matches, blocked = self._reconcile((claim,), ctx)
        try:
            verdict = self._judge(claim, matches.get(claim.claim_id), blocked, ctx)
        except AdapterUnavailable as exc:
            verdict = unverifiable(claim, str(exc))
            ctx.log("verdict", f"{claim.claim_id} ({claim.type}): {verdict.status.value}")
        verdicts = (verdict,)
        report = AuditReport(
            audit_id=f"aud_{uuid.uuid4().hex[:12]}",
            auditor=self.NAME,
            created_at=now,
            call=call,
            outcome=_outcome(verdicts, (), ctx),
            verdicts=verdicts,
            findings=(),
            trail=tuple(ctx.trail),
            stats=_stats(verdicts, ()),
            extraction=ExtractionHealth(extractor="none"),
            coverage_gaps=_coverage_gaps((claim,), ctx),
        )
        return self._sealer.seal(report, prev_hash)

    # -- pipeline stages ---------------------------------------------------- #

    def _fulfillments(
        self,
        claims: Sequence[Claim],
        matches: dict[str, Match],
        ctx: AuditContext,
    ) -> tuple[Fulfillment, ...]:
        """Grade each matched handoff ticket's lifecycle after the call.

        A verified ticket is a recorded promise; only later journal rows show
        it assigned, acknowledged or completed. Fulfillment never changes the
        call's outcome: the audit is about what the agent did, this is about
        what happened next.
        """
        out: list[Fulfillment] = []
        for claim in claims:
            if not isinstance(claim, HandoffCreated):
                continue
            match = matches.get(claim.claim_id)
            if match is None or match.primary is None or match.primary.after is None:
                continue
            ticket = Ticket.model_validate(match.primary.after)
            creator = match.primary.changes[0].actor if match.primary.changes else ""
            result = fulfillment.assess(ticket, creator, self._sor.tickets, self._clock())
            ctx.log(
                "fulfillment",
                f"ticket {ticket.id}: {result.state.value}"
                + ("; the callback is still pending" if result.pending else ""),
            )
            out.append(result)
        return tuple(out)

    def _collect_claims(self, call: CallRecord, ctx: AuditContext) -> tuple[Claim, ...]:
        spoken = self._extractor.extract(call, self._clinic) if self._extractor else []
        claims = merge_claims(call.claims, spoken)
        spoken_only = [c for c in claims if c.source is ClaimSource.TRANSCRIPT]
        ctx.log(
            "claims",
            f"{len(call.claims)} structured claim(s); {len(spoken)} recovered from the "
            f"transcript, {len(spoken_only)} of them never logged by the agent",
        )
        return claims

    def _extraction_health(self, ctx: AuditContext) -> ExtractionHealth:
        if self._extractor is None:
            return ExtractionHealth(extractor="none")
        health = self._extractor.health()
        if health.degraded:
            ctx.log("claims", f"Extraction degraded: {health.detail}")
        return health

    @staticmethod
    def _specificity(claim: Claim) -> int:
        """How much a claim says. A more specific claim has more ways to be wrong,
        so it must pin its evidence before a vaguer claim takes it."""
        skip = {"claim_id", "turn_index", "source", "utterance", "type"}
        return sum(1 for f, v in claim.model_dump().items() if f not in skip and v is not None)

    def _reconcile(
        self, claims: Sequence[Claim], ctx: AuditContext
    ) -> tuple[dict[str, Match], dict[str, str]]:
        """Assign writes across each evidence pool before judging individual claims."""
        matches: dict[str, Match] = {}
        blocked: dict[str, str] = {}
        by_specificity = sorted(claims, key=self._specificity, reverse=True)
        for claim in by_specificity:
            handler = HANDLERS[claim.type]
            down = [SOURCE[e] for e in handler.needs if ctx.delta.get(e) is None]
            if down:
                blocked[claim.claim_id] = f"{', '.join(down)} could not be read"
        pools: dict[Entity, list[Claim]] = {}
        for claim in by_specificity:
            if claim.claim_id not in blocked:
                pools.setdefault(HANDLERS[claim.type].consumes, []).append(claim)
        for entity, pool in pools.items():
            if len(pool) == 1:
                self._match_one(pool[0], ctx, True, matches, blocked)
                self._match_one(pool[0], ctx, False, matches, blocked)
            else:
                self._match_pool(pool, entity, ctx, matches, blocked)
        for claim in claims:
            if claim.claim_id not in matches and claim.claim_id not in blocked:
                ctx.log("reconcile", f"{claim.claim_id} ({claim.type}): no write corresponds")
        return matches, blocked

    def _match_one(
        self,
        claim: Claim,
        ctx: AuditContext,
        strict: bool,
        matches: dict[str, Match],
        blocked: dict[str, str],
    ) -> None:
        if claim.claim_id in matches or claim.claim_id in blocked:
            return
        handler = HANDLERS[claim.type]
        down = [SOURCE[e] for e in handler.needs if ctx.delta.get(e) is None]
        if down:
            blocked[claim.claim_id] = f"{', '.join(down)} could not be read"
            return
        if claim.claim_id in matches or claim.claim_id in blocked:
            return
        try:
            if strict:
                match = handler.match(claim, ctx, strict)
            else:
                candidates = self._loose_candidates(claim, ctx)
                match = max(candidates, key=lambda c: c[0])[1] if candidates else None
        except AdapterUnavailable as exc:
            blocked[claim.claim_id] = str(exc)
            return
        if match is not None:
            self._take(claim, match, ctx, matches, "exact" if strict else "partial")

    @staticmethod
    def _loose_candidates(claim: Claim, ctx: AuditContext) -> list[tuple[int, Match]]:
        """Loose-match candidates, minus leftover copies of writes already claimed."""
        handler = HANDLERS[claim.type]
        claimed_copies = ctx.consumed_signatures(handler.consumes)
        return [
            (s, m)
            for s, m in handler.candidates(claim, ctx, False)
            if m.signatures.isdisjoint(claimed_copies)
        ]

    @staticmethod
    def _take(
        claim: Claim, match: Match, ctx: AuditContext, matches: dict[str, Match], kind: str
    ) -> None:
        matches[claim.claim_id] = match
        ctx.consume(match)
        refs = ", ".join(nc.entity_id for nc in match.changes)
        ctx.log("reconcile", f"{claim.claim_id} ({claim.type}): {kind} match -> {refs}")

    @staticmethod
    def _claim_distance(claim: Claim, write_at: datetime, ctx: AuditContext) -> float:
        """Seconds between a write and the exchange its claim belongs to.

        A write lands on the caller-request tick between the request and the
        agent's answer, so the claim's moment spans both turns.
        """
        turns = ctx.call.transcript
        lo = max(0, claim.turn_index - 1)
        moments = [t.at for t in turns[lo : claim.turn_index + 1]] or [ctx.call.ended_at]
        return min(abs((write_at - at).total_seconds()) for at in moments)

    def _match_pool(
        self,
        pool: list[Claim],
        entity: Entity,
        ctx: AuditContext,
        matches: dict[str, Match],
        blocked: dict[str, str],
    ) -> None:
        """Rank assignments by content, using position only for tied pairings."""
        order = sorted(pool, key=lambda c: (-self._specificity(c), c.claim_id))
        options: list[list[tuple[int, Match, bool] | None]] = []
        for claim in order:
            try:
                exact = HANDLERS[claim.type].candidates(claim, ctx, True)
                exact_keys = {key for _, m in exact for key in m.keys}
                partial = [
                    (s, m)
                    for s, m in self._loose_candidates(claim, ctx)
                    if exact_keys.isdisjoint(m.keys)
                ]
            except AdapterUnavailable as exc:
                blocked[claim.claim_id] = str(exc)
                options.append([None])
                continue
            ranked = sorted(exact, key=lambda c: -c[0]) + sorted(partial, key=lambda c: -c[0])
            exact_n = len(exact)
            options.append([(s, m, i < exact_n) for i, (s, m) in enumerate(ranked)] + [None])

        combos = 1
        for opts in options:
            combos *= len(opts)
        if len(order) > 12 or combos > 100_000:
            ctx.log(
                "reconcile",
                f"{SOURCE[entity]}: {len(order)} competing claims, assignment space too "
                "large to resolve safely",
            )
            for claim, opts in zip(order, options, strict=True):
                if len(opts) > 1:
                    blocked[claim.claim_id] = "the write assignment exceeds the search limit"
            return

        def position(chosen: list[tuple[int, Match, bool] | None]) -> float:
            return sum(
                self._claim_distance(
                    claim, max(c.at for nc in entry[1].changes for c in nc.changes), ctx
                )
                for claim, entry in zip(order, chosen, strict=True)
                if entry is not None
            )

        best_key: tuple[int, int, int] | None = None
        best: list[tuple[int, Match, bool] | None] = []
        best_distance = float("inf")
        matched_in_any: set[int] = set()
        matched_in_all: set[int] = set()
        alternate_writes: set[int] = set()

        def search(
            i: int,
            used: set[MatchKey],
            copies: set[str],
            chosen: list[tuple[int, Match, bool] | None],
        ) -> None:
            nonlocal best_key, best, best_distance, matched_in_any, matched_in_all
            if i == len(order):
                paired = {i for i, entry in enumerate(chosen) if entry is not None}
                entries = [entry for entry in chosen if entry is not None]
                key = (
                    sum(exact for _, _, exact in entries),
                    sum(s for s, _, _ in entries),
                    len(paired),
                )
                if best_key is None or key > best_key:
                    best_key, best = key, list(chosen)
                    best_distance = position(chosen)
                    matched_in_any, matched_in_all = paired.copy(), paired
                    alternate_writes.clear()
                elif key == best_key:
                    matched_in_any.update(paired)
                    matched_in_all.intersection_update(paired)
                    alternate_writes.update(i for i in matched_in_all if best[i] != chosen[i])
                    distance = position(chosen)
                    if distance < best_distance:
                        best, best_distance = list(chosen), distance
                return
            for entry in options[i]:
                keys = entry[1].keys if entry is not None else ()
                sigs = entry[1].signatures if entry is not None else set()
                # Two matches in one assignment may not describe the same
                # content: the leftover copy is the anomaly, not evidence.
                if any(k_ in used for k_ in keys) or any(s in copies for s in sigs):
                    continue
                used.update(keys)
                copies.update(sigs)
                chosen.append(entry)
                search(i + 1, used, copies, chosen)
                chosen.pop()
                used.difference_update(keys)
                copies.difference_update(sigs)

        search(0, set(), set(), [])
        ambiguous = matched_in_any - matched_in_all
        if alternate_writes & matched_in_all:
            # Position can choose among tied writes for a claim that has evidence
            # in every best assignment. It cannot decide which claim gets evidence.
            ctx.log("reconcile", f"{SOURCE[entity]}: positional fallback for tied write pairings")
        for i, (claim, entry) in enumerate(zip(order, best, strict=True)):
            if i in ambiguous:
                reason = "ambiguous write assignment: equally matching claims compete for evidence"
                blocked[claim.claim_id] = reason
                ctx.log("reconcile", f"{claim.claim_id} ({claim.type}): {reason}")
            elif entry is not None:
                _, match, is_exact = entry
                self._take(claim, match, ctx, matches, "exact" if is_exact else "partial")

    @staticmethod
    def _judge(
        claim: Claim, match: Match | None, blocked: dict[str, str], ctx: AuditContext
    ) -> Verdict:
        if claim.claim_id in blocked:
            verdict = unverifiable(claim, blocked[claim.claim_id])
        elif (
            ambiguous := ctx.ambiguous_identity(
                getattr(claim, "patient", None), at_call_start=isinstance(claim, RecordsUpdated)
            )
        ) is not None:
            verdict = Verdict(
                claim=claim,
                status=VerdictStatus.UNVERIFIABLE,
                severity=Severity.HIGH,
                summary=f"Could not be checked: {ambiguous}. A guess here could clear a "
                "booking made for the wrong person.",
                checks=(Check(name=CheckName.EXISTENCE, passed=None, detail=ambiguous),),
                remediation="Disambiguate the patient (confirm the phone number on file) "
                "and re-run the audit.",
            )
        else:
            try:
                verdict = HANDLERS[claim.type].judge(claim, match, ctx)
            except AdapterUnavailable as exc:
                verdict = unverifiable(claim, str(exc))
            else:
                verdict = _apply_attribution(claim, verdict, match, ctx)
                verdict = _apply_coverage(claim, verdict, ctx)
        ctx.log("verdict", f"{claim.claim_id} ({claim.type}): {verdict.status.value}")
        return verdict

    def _findings(
        self, ctx: AuditContext, claims: Sequence[Claim], verdicts: Sequence[Verdict]
    ) -> tuple[Finding, ...]:
        # A write can only be called "unclaimed" if every claim that might own it was
        # actually checked. An outage must never manufacture an accusation.
        unsound = {
            HANDLERS[v.claim.type].consumes
            for v in verdicts
            if v.status is VerdictStatus.UNVERIFIABLE
        }
        for entity, cov in ctx.coverage.items():
            if not cov.complete and entity not in unsound:
                unsound.add(entity)
                ctx.log(
                    "reconcile",
                    f"{SOURCE[entity]}: unclaimed-write check skipped, the feed did not "
                    "observe the whole window",
                )
        for entity in sorted(unsound):
            ctx.log(
                "reconcile",
                f"{SOURCE[entity]}: unclaimed-write check skipped, a claim that may own "
                "these writes could not be verified",
            )
        found = unclaimed_writes(ctx, claims, skip=frozenset(unsound))
        try:
            found += policy_violations(ctx, self._policies)
        except AdapterUnavailable as exc:
            ctx.log("policy", f"Policy checks skipped: {exc}")
        return tuple(
            f.model_copy(update={"finding_id": f"{ctx.call.call_id}-f{i}"})
            for i, f in enumerate(found, start=1)
        )


def _apply_attribution(
    claim: Claim, verdict: Verdict, match: Match | None, ctx: AuditContext
) -> Verdict:
    """Every matched verdict carries its attribution strength; an ambiguous one
    never stands as VERIFIED or MISMATCH.

    A write correlated by shared agent identity plus time window does not show
    *this* call made it once another call was in progress. The write is preserved
    as evidence, but the verdict is UNVERIFIABLE: the promised outcome may exist,
    yet which call caused it cannot be established.
    """
    if match is None or not match.changes:
        return verdict
    strength = ctx.match_attribution(match)
    if strength is Attribution.DIRECT:
        note = "Every backing write carries this call's own stamp."
    elif strength is Attribution.SOLE:
        note = (
            "Attributed by shared agent identity and time window; no concurrent call "
            "was observed in the journal."
        )
    else:
        note = (
            "Only shared agent identity plus an overlapping window links this write to "
            "the call, and another call was in progress - which call wrote it cannot "
            "be established."
        )
    check = Check(
        name=CheckName.ATTRIBUTION,
        passed=strength is not Attribution.AMBIGUOUS,
        detail=note,
    )
    if strength is not Attribution.AMBIGUOUS:
        return verdict.model_copy(update={"checks": verdict.checks + (check,)})
    if verdict.status not in (VerdictStatus.VERIFIED, VerdictStatus.MISMATCH):
        return verdict.model_copy(update={"checks": verdict.checks + (check,)})
    return Verdict(
        claim=claim,
        status=VerdictStatus.UNVERIFIABLE,
        severity=Severity.HIGH,
        summary=(
            "A write matching this claim exists, but it is attributed only to the shared "
            "agent identity while another call was in progress. The promised outcome may "
            "exist; that this call caused it cannot be proven."
        ),
        checks=verdict.checks + (check,),
        diffs=verdict.diffs,
        evidence=verdict.evidence,
        remediation=(
            "Re-run the audit once the calls no longer overlap, or have the platform "
            "stamp writes with the call id so attribution is direct."
        ),
    )


def _coverage_gaps(claims: Sequence[Claim], ctx: AuditContext) -> tuple[CoverageGap, ...]:
    """The evidence holes, named: every unreadable or partial system, with the
    claims it capped. Downgraded verdicts carry the per-claim detail; this is
    the same information as one explicit list."""
    gaps: list[CoverageGap] = []
    for entity, source in SOURCE.items():
        if ctx.delta.get(entity) is None:
            claim_ids = tuple(c.claim_id for c in claims if entity in HANDLERS[c.type].needs)
            gaps.append(
                CoverageGap(
                    source=source,
                    kind="unavailable",
                    detail=ctx.errors.get(entity, "could not be read"),
                    claim_ids=claim_ids,
                )
            )
            continue
        cov = ctx.coverage.get(entity, COMPLETE)
        if not cov.complete:
            claim_ids = tuple(c.claim_id for c in claims if HANDLERS[c.type].consumes is entity)
            gaps.append(
                CoverageGap(
                    source=source,
                    kind="incomplete",
                    detail=cov.detail or "history not fully observed",
                    claim_ids=claim_ids,
                )
            )
    return tuple(gaps)


def _apply_coverage(claim: Claim, verdict: Verdict, ctx: AuditContext) -> Verdict:
    """No definitive verdict from a partial history.

    Incomplete coverage means the feed did not observably see the whole window:
    a cursor expired, retrieval was partial, a cache was stale. A matched write
    may have been undone by a row the feed missed, and an absent write may simply
    never have been seen. VERIFIED, MISMATCH and NOT_FOUND all become
    UNVERIFIABLE; the current-state observation is kept as evidence, labelled as
    what exists now, not as proof this call caused it.
    """
    if verdict.status is VerdictStatus.UNVERIFIABLE:
        return verdict
    entity = HANDLERS[claim.type].consumes
    cov = ctx.coverage.get(entity, COMPLETE)
    source = SOURCE[entity]
    if cov.complete:
        check = Check(
            name=CheckName.COVERAGE,
            passed=True,
            detail=f"The {source} change history for the call window was fully observed.",
        )
        return verdict.model_copy(update={"checks": verdict.checks + (check,)})
    check = Check(
        name=CheckName.COVERAGE,
        passed=False,
        detail=f"The {source} change history for the call window was not fully "
        f"observed: {cov.detail}.",
    )
    if verdict.status is VerdictStatus.NOT_FOUND:
        what = (
            "Absence cannot be proven from a partial history. Any current-state "
            "observation in the evidence shows what exists now, not what this call did."
        )
    else:
        what = (
            "The matching write appears in the partial history, but rows the feed "
            "missed could change the net effect. The evidence shows what exists now, "
            "not proof that this call caused it."
        )
    return Verdict(
        claim=claim,
        status=VerdictStatus.UNVERIFIABLE,
        severity=Severity.MEDIUM,
        summary=(
            f"Could not be verified: the {source} history for the call window is "
            f"incomplete ({cov.detail}). {what}"
        ),
        checks=verdict.checks + (check,),
        diffs=verdict.diffs,
        evidence=verdict.evidence,
        remediation="Re-sync the system of record so the full window is observed, then "
        "re-run the audit; the original sealed report stands.",
    )


def _chain_key(claim: Claim) -> tuple[str, str] | None:
    """Which commitment a claim is about, for correction chaining."""
    patient = getattr(claim, "patient", None)
    if patient is None or claim.type not in ("AppointmentBooked", "Cancelled"):
        return None
    who = patient.phone or patient.name.strip().lower()
    return (claim.type, who)


def _resolve_corrections(
    claims: tuple[Claim, ...], ctx: AuditContext
) -> tuple[tuple[Claim, ...], dict[str, tuple[Claim, ...]]]:
    """Bind corrections into supersedes chains.

    A claim raised after the caller corrected themselves replaces the most
    recent earlier claim about the same commitment. The superseded claim is
    withdrawn from judgment - the agent is held to the final value, and the
    earlier utterance stays in the record as context, not as a second promise
    competing for the same write.
    """
    settled: list[Claim] = []
    withdrawn: dict[str, tuple[Claim, ...]] = {}
    last_by_key: dict[tuple[str, str], Claim] = {}
    for claim in claims:
        key = _chain_key(claim)
        prior = last_by_key.get(key) if key is not None else None
        if claim.corrects_prior and prior is not None:
            settled.remove(prior)
            withdrawn[claim.claim_id] = (*withdrawn.get(claim.claim_id, ()), prior)
            ctx.log(
                "claims",
                f"{claim.claim_id} corrects {prior.claim_id} (turn {claim.turn_index}); "
                f"the earlier {prior.type} claim is withdrawn, the corrected value is "
                "what the caller is owed",
            )
        settled.append(claim)
        if key is not None:
            last_by_key[key] = claim
    return tuple(settled), withdrawn


def _correction_checks(verdict: Verdict, superseded: Sequence[Claim], ctx: AuditContext) -> Verdict:
    """Name the smoking gun: the write still holds a value the caller corrected."""
    if verdict.status is not VerdictStatus.MISMATCH or not superseded:
        return verdict
    for prior in superseded:
        if not isinstance(prior, AppointmentBooked):
            continue
        hits = ctx.booked_at(ctx.patient_ids(prior.patient), prior.start)
        if hits:
            note = (
                f" The calendar holds {ctx.show(hits[0].start)}, the exact value the "
                f"caller corrected away from at turn {prior.turn_index}."
            )
            return verdict.model_copy(update={"summary": verdict.summary + note})
    return verdict


def _outcome(
    verdicts: Sequence[Verdict], findings: Sequence[Finding], ctx: AuditContext
) -> Outcome:
    flagged = {VerdictStatus.MISMATCH, VerdictStatus.NOT_FOUND}
    if findings or any(v.status in flagged for v in verdicts):
        return Outcome.DISCREPANCIES
    blind = any(d is None for d in ctx.delta.values()) or any(
        not c.complete for c in ctx.coverage.values()
    )
    if blind or any(v.status is VerdictStatus.UNVERIFIABLE for v in verdicts):
        return Outcome.INCONCLUSIVE  # an unreadable system can hide unclaimed writes
    return Outcome.CLEAN


def _stats(verdicts: Sequence[Verdict], findings: Sequence[Finding]) -> AuditStats:
    count = {s: sum(v.status is s for v in verdicts) for s in VerdictStatus}
    checkable = len(verdicts) - count[VerdictStatus.UNVERIFIABLE]
    return AuditStats(
        claims=len(verdicts),
        verified=count[VerdictStatus.VERIFIED],
        mismatch=count[VerdictStatus.MISMATCH],
        not_found=count[VerdictStatus.NOT_FOUND],
        unverifiable=count[VerdictStatus.UNVERIFIABLE],
        findings=len(findings),
        trust_score=round(count[VerdictStatus.VERIFIED] / checkable, 3) if checkable else None,
        max_severity=max_severity([v.severity for v in verdicts] + [f.severity for f in findings]),
    )

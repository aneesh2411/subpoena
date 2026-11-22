"""Regression tests for content-based write assignment and positional fallback."""

from __future__ import annotations

from dataclasses import replace

from subpoena.models import VerdictStatus
from subpoena.scenarios.world import (
    KIM,
    MAYA,
    PATEL,
    Scenario,
    SeedAppointment,
    at,
    audit_call,
    build_world,
    run_suspect,
)
from subpoena.suspect import Book, Cancel, FaultMode, Handoff

F = FaultMode
S1, S2 = at(2, 10), at(3, 11)


def _audit(steps, faults, seeds=(), *, transform_claims=None):
    scenario = Scenario(
        "test-reconcile-assignment",
        "Competing same-type claims in one call",
        "x",
        steps,
        (),
        faults=faults,
        seed=tuple(seeds),
    )
    world = build_world(scenario)
    try:
        result = run_suspect(scenario, world)
        if transform_claims is not None:
            result = replace(
                result,
                call=result.call.model_copy(
                    update={"claims": transform_claims(result.call.claims)}
                ),
            )
        return audit_call(
            scenario, world, result, options={"use_transcript": transform_claims is None}
        )
    finally:
        world.store.close()


def test_rolled_back_claim_cannot_take_the_duplicates_leftover():
    report = _audit(
        (
            Book(start=S1, provider=PATEL, confirm=False),
            Book(start=S2, provider=KIM, confirm=False),
        ),
        {0: frozenset({F.ROLLBACK}), 1: frozenset({F.DUPLICATE_WRITE})},
    )
    verdicts = [v for v in report.verdicts if v.claim.type == "AppointmentBooked"]
    # The rolled-back booking was never written: NOT_FOUND. The duplicated
    # booking verifies once, and its leftover copy is an unclaimed write.
    # Greedy matching turned the first claim into MISMATCH and hid the leftover.
    assert [v.status for v in verdicts] == [VerdictStatus.NOT_FOUND, VerdictStatus.VERIFIED]
    assert "unclaimed_appointment_created" in {f.code for f in report.findings}


def test_exact_cancel_content_takes_precedence_over_exchange_timing():
    report = _audit(
        (Cancel(start=S1), Cancel(start=S2)),
        {0: frozenset({F.PHANTOM_CANCEL}), 1: frozenset({F.WRONG_APPOINTMENT_CANCELLED})},
        seeds=(
            SeedAppointment(MAYA, PATEL, S1),
            SeedAppointment(MAYA, KIM, S2),
        ),
    )
    verdicts = [v for v in report.verdicts if v.claim.type == "Cancelled"]
    # The only release describes S1, even though it landed in the S2 exchange.
    assert [v.status for v in verdicts] == [VerdictStatus.VERIFIED, VerdictStatus.NOT_FOUND]


def test_competing_handoffs_pair_with_their_own_tickets():
    report = _audit(
        (
            Handoff("a billing question", priority="urgent"),
            Handoff("a knocked-out tooth", priority="urgent"),
        ),
        {0: frozenset({F.DOWNGRADED_HANDOFF})},
    )
    verdicts = [v for v in report.verdicts if v.claim.type == "HandoffCreated"]
    # The billing handoff was downgraded to a normal ticket: MISMATCH. The
    # dental emergency went out urgent: VERIFIED. Greedy matching paired each
    # claim with the other's ticket and flipped both verdicts.
    assert [v.status for v in verdicts] == [VerdictStatus.MISMATCH, VerdictStatus.VERIFIED]


def test_crossed_booking_writes_are_assigned_by_content():
    def cross(claims):
        first, second = claims
        return (
            second.model_copy(update={"turn_index": first.turn_index}),
            first.model_copy(update={"turn_index": second.turn_index}),
        )

    report = _audit(
        (
            Book(start=S1, provider=PATEL, confirm=False),
            Book(start=S2, provider=KIM, confirm=False),
        ),
        {},
        transform_claims=cross,
    )

    assert [v.claim.start for v in report.verdicts] == [S2, S1]
    assert [v.status for v in report.verdicts] == [VerdictStatus.VERIFIED, VerdictStatus.VERIFIED]
    assert all("positional fallback" not in e.message for e in report.trail)


def test_equally_matching_claims_cannot_verify_against_one_write():
    report = _audit(
        (
            Book(start=S1, provider=PATEL, confirm=False),
            Book(start=S1, provider=PATEL, confirm=False),
        ),
        {0: frozenset({F.ROLLBACK})},
    )

    assert [v.status for v in report.verdicts] == [VerdictStatus.UNVERIFIABLE] * 2
    assert all("ambiguous write assignment" in v.summary for v in report.verdicts)
    assert all("positional fallback" not in e.message for e in report.trail)


def test_tied_pairings_with_evidence_for_both_claims_log_positional_fallback():
    def omit_ticket_details(claims):
        return tuple(c.model_copy(update={"summary": "", "priority": None}) for c in claims)

    report = _audit(
        (Handoff("a billing question"), Handoff("an insurance form")),
        {},
        transform_claims=omit_ticket_details,
    )

    assert [v.status for v in report.verdicts] == [VerdictStatus.VERIFIED] * 2
    assert any("positional fallback" in e.message for e in report.trail)

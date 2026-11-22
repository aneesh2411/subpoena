"""Corrections: the caller re-states a value mid-call, and the auditor must hold
the agent to the final settled value instead of judging every utterance."""

from __future__ import annotations

from datetime import timedelta

from subpoena.auditor import RuleBasedExtractor, merge_claims
from subpoena.models import (
    AppointmentBooked,
    CallRecord,
    ClaimSource,
    Outcome,
    Turn,
    VerdictStatus,
)
from subpoena.scenarios.world import CALL_START, CLINIC, MAYA, PATEL, Scenario, at, run_scenario
from subpoena.suspect import AGENT_ID, Book, Correct, FaultMode

SLOT = at(1, 14, 30)
FIXED = at(2, 14, 30)


def _call(*turns: tuple[str, str], claims=()) -> CallRecord:
    return CallRecord(
        call_id="call-test",
        agent_id=AGENT_ID,
        caller=MAYA,
        started_at=CALL_START,
        ended_at=CALL_START + timedelta(minutes=2),
        transcript=tuple(
            Turn(
                index=i,
                speaker=speaker,
                text=text,
                at=CALL_START + timedelta(seconds=5 * i),
            )
            for i, (speaker, text) in enumerate(turns)
        ),
        claims=tuple(claims),
    )


def _spoken(sentence: str, claim_id: str, turn_index: int, start) -> AppointmentBooked:
    return AppointmentBooked(
        claim_id=claim_id,
        turn_index=turn_index,
        source=ClaimSource.TRANSCRIPT,
        utterance=sentence,
        patient=MAYA,
        start=start,
        provider=PATEL,
        corrects_prior=True,
    )


def test_extractor_flags_the_claim_after_a_caller_correction() -> None:
    call = _call(
        (
            "agent",
            "You're all set. I've booked your cleaning with Dr. Patel "
            "on Tuesday, March 3 at 2:30 PM.",
        ),
        ("caller", "Actually, could we make that Wednesday, March 4 at 2:30 PM instead?"),
        ("agent", "No problem at all. I've booked you for Wednesday, March 4 at 2:30 PM instead."),
    )
    claims = RuleBasedExtractor().extract(call, CLINIC)
    assert [c.start for c in claims] == [SLOT, FIXED]
    assert claims[0].corrects_prior is False
    assert claims[1].corrects_prior is True
    assert claims[0].provider == claims[1].provider == "Dr. Patel"


def test_extractor_ignores_correction_phrasing_without_a_time() -> None:
    call = _call(
        ("caller", "Actually, I love reminders."),
        (
            "agent",
            "You're all set. I've booked your cleaning with Dr. Patel "
            "on Tuesday, March 3 at 2:30 PM.",
        ),
    )
    claims = RuleBasedExtractor().extract(call, CLINIC)
    assert len(claims) == 1
    assert claims[0].corrects_prior is False


def test_same_sentence_correction_keeps_the_final_time() -> None:
    call = _call(
        (
            "agent",
            "I've booked you for Tuesday, March 3 at 2:30 PM - actually, "
            "Wednesday, March 4 at 2:30 PM.",
        ),
    )
    claims = RuleBasedExtractor().extract(call, CLINIC)
    assert len(claims) == 1
    assert claims[0].start == FIXED
    assert claims[0].corrects_prior is True


def test_merge_keeps_the_correction_flag_on_the_structured_claim() -> None:
    logged = AppointmentBooked(
        claim_id="call-test-c1",
        turn_index=2,
        patient=MAYA,
        start=FIXED,
        provider=PATEL,
    )
    spoken = _spoken("I've booked you for Wednesday.", "call-test-t1", 2, FIXED)
    (merged,) = merge_claims([logged], [spoken])
    assert merged.claim_id == "call-test-c1"
    assert merged.corrects_prior is True


def _run(steps, faults=None):
    scenario = Scenario("correction-test", "t", "s", steps, (), caller=MAYA, faults=faults or {})
    return run_scenario(scenario).report


def test_honored_correction_withdraws_the_superseded_claim() -> None:
    report = _run((Book(SLOT, PATEL, confirm=False), Correct(FIXED)))
    assert report.outcome is Outcome.CLEAN
    assert len(report.verdicts) == 1
    verdict = report.verdicts[0]
    assert verdict.status is VerdictStatus.VERIFIED
    assert verdict.claim.start == FIXED
    assert any("corrects" in entry.message for entry in report.trail)


def test_stale_correction_is_a_mismatch_naming_the_correction() -> None:
    report = _run(
        (Book(SLOT, PATEL, confirm=False), Correct(FIXED)),
        faults={1: frozenset({FaultMode.STALE_CORRECTION})},
    )
    assert report.outcome is Outcome.DISCREPANCIES
    assert len(report.verdicts) == 1
    verdict = report.verdicts[0]
    assert verdict.status is VerdictStatus.MISMATCH
    assert verdict.claim.start == FIXED
    assert "corrected away from" in verdict.summary


def test_two_bookings_without_a_correction_are_both_judged() -> None:
    # No correction phrasing: two separate commitments, two verdicts.
    report = _run((Book(SLOT, PATEL, confirm=False), Book(at(4, 10), PATEL, confirm=False)))
    booked = [v for v in report.verdicts if v.claim.type == "AppointmentBooked"]
    assert len(booked) == 2
    assert all(v.status is VerdictStatus.VERIFIED for v in booked)

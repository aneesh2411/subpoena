"""Reading claims out of a transcript: rules, the model-backed reader, and merging."""

from __future__ import annotations

import json
from datetime import UTC

import pytest

from subpoena.auditor import LLMClaimExtractor, RuleBasedExtractor, merge_claims
from subpoena.llm import StaticLLM, llm_from_env
from subpoena.models import AppointmentBooked, ClaimSource, ConfirmationSent, VerdictStatus
from subpoena.scenarios.world import CALL_START, CLINIC, MAYA, PATEL, TZ, at
from subpoena.util import find_times, norm_phone, show, speak

SLOT = at(1, 14, 30)


def _extract(clinic, *lines: str):
    return RuleBasedExtractor().extract(clinic.call(agent_lines=lines), CLINIC)


def test_reads_each_kind_of_explicit_statement(clinic):
    claims = _extract(
        clinic,
        "You're all set. I've booked your cleaning with Dr. Patel on Tuesday, March 3 at 2:30 PM. "
        "I just texted you a confirmation.",
        "I've moved your appointment from Tuesday, March 3 at 10:00 AM to "
        "Thursday, March 5 at 11:00 AM.",
        "Your appointment on Tuesday, March 10 at 3:00 PM is now cancelled.",
        "I've created an urgent ticket for the on-call dentist about a knocked-out tooth. "
        "They'll call you right away.",
    )
    booked, confirmation, moved, cancelled, handoff = claims
    assert (booked.type, booked.start, booked.provider) == ("AppointmentBooked", SLOT, PATEL)
    assert (confirmation.channel, confirmation.to, confirmation.about_start) == (
        "sms",
        MAYA.phone,
        SLOT,
    )
    assert (moved.old_start, moved.new_start) == (at(1, 10), at(3, 11))
    assert cancelled.start == at(8, 15)
    assert handoff.priority == "urgent"
    assert {c.source for c in claims} == {ClaimSource.TRANSCRIPT}
    assert [c.turn_index for c in claims] == [0, 0, 1, 2, 3]


@pytest.mark.parametrize(
    ("line", "topic"),
    [
        ("I've created a ticket for the front desk about BILLING.", "billing"),
        ("I've created a refill ticket.", "refill"),
        ("I've created a ticket for records.", "records"),
        ("I've created a ticket for parking.", None),
        ("I've created a ticket about a callback about billing.", None),
        ("I've created a ticket about a billing question on my last invoice.", None),
    ],
)
def test_handoff_topic_comes_from_explicit_ticket_wording(clinic, line, topic):
    (claim,) = _extract(clinic, line)
    assert claim.type == "HandoffCreated"
    assert claim.topic == topic


def test_does_not_invent_claims(clinic):
    assert (
        _extract(
            clinic,
            "I'm sorry, I wasn't able to book that. We're closed at that time.",
            "That slot is already taken. The next opening with Dr. Patel is "
            "Tuesday, March 3 at 3:00 PM. Does that work?",
            "I couldn't find an appointment on Tuesday, March 3 at 2:30 PM, "
            "so nothing was cancelled.",
            "We're open Monday to Friday, 8 AM to 5 PM.",
            "Unfortunately I did not send a confirmation text.",
        )
        == []
    )


def test_ignores_what_the_caller_says(clinic):
    call = clinic.call(agent_lines=["I've booked you with Dr. Kim on Tuesday, March 3 at 2:30 PM."])
    as_caller = call.model_copy(
        update={
            "transcript": tuple(t.model_copy(update={"speaker": "caller"}) for t in call.transcript)
        }
    )
    assert RuleBasedExtractor().extract(as_caller, CLINIC) == []


def test_email_confirmation_is_recognised(clinic):
    (claim,) = _extract(clinic, "I've emailed you a confirmation.")
    assert claim.channel == "email"


# --------------------------------- time parsing ---------------------------- #


def test_spoken_times_parse_across_formats_and_the_clock_change():
    cases = {
        "March 3 at 2:30 PM": at(1, 14, 30),
        "Tuesday, March 3rd at 2 pm": at(1, 14),
        "March 9 at 9:00 AM": at(7, 9),  # after daylight saving starts
        "March 3 at 12:00 PM": at(1, 12),
        "March 3 at 12:15 AM": at(1, 0, 15),
        "March 3, 2027 at 8:00 AM": at(1, 8).replace(year=2027),
    }
    for text, expected in cases.items():
        assert find_times(text, ref=CALL_START, tz=TZ) == [expected], text
    assert at(7, 9).utcoffset() != at(1, 9).utcoffset()


def test_times_without_a_year_roll_forward_and_nonsense_is_skipped():
    (january,) = find_times("January 12 at 9:00 AM", ref=at(280, 9), tz=TZ)
    assert january.year == 2027
    assert find_times("February 30 at 9:00 AM", ref=CALL_START, tz=TZ) == []
    assert find_times("see you Tuesday, same time", ref=CALL_START, tz=TZ) == []


def test_display_helpers():
    assert speak(SLOT, TZ) == "Tuesday, March 3 at 2:30 PM"
    assert show(SLOT.astimezone(UTC), TZ) == "Tue Mar 3 2026, 2:30 PM EST"
    assert norm_phone("(555) 010-1001") == norm_phone("1-555-010-1001") == "+15550101001"


# ---------------------------------- merging -------------------------------- #


def _logged(start=SLOT, turn=0, cid="c1"):
    return AppointmentBooked(claim_id=cid, turn_index=turn, patient=MAYA, start=start)


def _spoken(start=SLOT, turn=0, cid="t1"):
    return _logged(start, turn, cid).model_copy(update={"source": ClaimSource.TRANSCRIPT})


def test_spoken_claim_backed_by_a_logged_claim_is_dropped():
    assert merge_claims([_logged()], [_spoken()]) == (_logged(),)


def test_spoken_claim_with_a_different_time_is_kept():
    merged = merge_claims([_logged()], [_spoken(start=at(2, 14, 30))])
    assert [c.source.value for c in merged] == ["structured", "transcript"]


def test_spoken_claim_in_a_different_turn_is_kept():
    assert len(merge_claims([_logged(turn=1)], [_spoken(turn=3)])) == 2


def test_one_logged_claim_corroborates_only_one_spoken_claim():
    logged = ConfirmationSent(claim_id="c1", turn_index=0, to=MAYA.phone)
    spoken = [
        logged.model_copy(update={"claim_id": "t1", "source": ClaimSource.TRANSCRIPT}),
        logged.model_copy(
            update={"claim_id": "t2", "source": ClaimSource.TRANSCRIPT, "about_start": SLOT}
        ),
    ]
    merged = merge_claims([logged], spoken)
    # t2 names a time, so it is a different promise, not a recap of t1: one is
    # corroborated by the logged claim, the other stands on its own.
    assert [c.claim_id for c in merged] == ["c1", "t2"]


def test_merged_claims_are_ordered_by_turn_then_logged_first():
    merged = merge_claims([_logged(turn=4, cid="c1")], [_spoken(turn=2, cid="t1")])
    assert [c.claim_id for c in merged] == ["t1", "c1"]


# ------------------------------ model-backed reader ------------------------ #


def _vague_call(clinic):
    return clinic.call(
        agent_lines=["Perfect, Dr. Patel will see you Tuesday then, same time we discussed."]
    )


def test_model_reader_recovers_what_rules_cannot(clinic):
    call = _vague_call(clinic)
    assert RuleBasedExtractor().extract(call, CLINIC) == []
    reply = json.dumps(
        [{"turn_index": 0, "type": "AppointmentBooked", "start": SLOT.isoformat(),
          "provider": "Dr. Patel"}]
    )  # fmt: skip
    llm = StaticLLM(f"Here you go:\n{reply}")
    (claim,) = LLMClaimExtractor(llm).extract(call, CLINIC)
    assert (claim.start, claim.provider, claim.source) == (SLOT, PATEL, ClaimSource.TRANSCRIPT)
    assert "Monday, March 02, 2026" in llm.prompts[0] and "[0] AGENT:" in llm.prompts[0]
    # The claim it recovers is then audited like any other, with no model involved.
    report = clinic.audit(call, extractor=LLMClaimExtractor(llm))
    assert [v.status for v in report.verdicts] == [VerdictStatus.NOT_FOUND]


def test_model_reader_builds_every_claim_type(clinic):
    call = clinic.call(agent_lines=["one", "two", "three", "four"])
    reply = json.dumps(
        [
            {"turn_index": 0, "type": "Rescheduled", "old_start": at(1, 10).isoformat(),
             "new_start": at(3, 11).isoformat()},
            {"turn_index": 1, "type": "Cancelled", "start": SLOT.isoformat()},
            {"turn_index": 2, "type": "ConfirmationSent", "channel": "sms"},
            {"turn_index": 3, "type": "HandoffCreated", "priority": "urgent"},
            {"turn_index": 3, "type": "Cancelled"},
            {"turn_index": 99, "type": "HandoffCreated"},
        ]
    )  # fmt: skip
    claims = LLMClaimExtractor(StaticLLM(reply)).extract(call, CLINIC)
    assert [c.type for c in claims] == [
        "Rescheduled",
        "Cancelled",
        "ConfirmationSent",
        "HandoffCreated",
    ]


def test_model_reader_falls_back_to_rules_on_garbage(clinic):
    call = clinic.call(agent_lines=["I've booked you with Dr. Kim on Tuesday, March 3 at 2:30 PM."])
    expected = RuleBasedExtractor().extract(call, CLINIC)
    for bad in ("no json here", '[{"turn_index": "x"}]', "[1, 2", '[{"type": "Nonsense"}]'):
        assert LLMClaimExtractor(StaticLLM(bad)).extract(call, CLINIC) == expected

    class Down:
        def complete(self, **_: object) -> str:
            raise RuntimeError("model unavailable")

    assert LLMClaimExtractor(Down()).extract(call, CLINIC) == expected


def test_no_live_model_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("SUBPOENA_LLM", raising=False)
    assert llm_from_env() is None
    monkeypatch.setenv("SUBPOENA_LLM", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_from_env() is None


@pytest.mark.parametrize(
    "opening",
    [
        "Have I booked you for",
        "I've booked you for",  # With interrogative punctuation below.
        "I could have booked you for",
        "I'll have you booked for",
        "You're not booked for",
        "You aren’t booked for",
        "You're tentatively booked for",
        "If you're booked for",
        "The clinician is booked for",
    ],
)
def test_booking_requires_an_affirmative_commitment(clinic, opening):
    punctuation = "?" if opening == "I've booked you for" else "."
    assert not _extract(clinic, f"{opening} Tuesday, March 3 at 2:30 PM{punctuation}")


@pytest.mark.parametrize(
    "opening",
    [
        "You're confirmed with Dr. Patel for",
        "Your appointment is now scheduled with Dr. Patel for",
        "I've set you up with Dr. Patel for",
    ],
)
def test_reads_completed_booking_phrasing(clinic, opening):
    (booking,) = _extract(clinic, f"{opening} Tuesday, March 3 at 2:30 PM.")
    assert isinstance(booking, AppointmentBooked)
    assert (booking.start, booking.provider) == (SLOT, PATEL)


def test_recap_repeats_booking_only_when_the_details_differ(clinic):
    claims = _extract(
        clinic,
        "I've booked you with Dr. Patel for Tuesday, March 3 at 2:30 PM.",
        "Just to confirm, I've booked you with Dr. Patel for Tuesday, March 3 at 2:30 PM.",
        "To recap, you're booked with Dr. Kim for Tuesday, March 3 at 2:30 PM.",
    )
    assert [(c.start, c.provider) for c in claims] == [(SLOT, PATEL), (SLOT, "Dr. Kim")]

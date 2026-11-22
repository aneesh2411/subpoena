"""Auditor behaviour on hand-built calls: attribution, outages, and odd shapes."""

from __future__ import annotations

from datetime import timedelta

import pytest

from subpoena.auditor.delta import ChangeKind, collapse
from subpoena.models import (
    AppointmentBooked,
    Cancelled,
    ConfirmationSent,
    HandoffCreated,
    Outcome,
    PatientRef,
    Severity,
    VerdictStatus,
)
from subpoena.scenarios.world import CALL_START, DANIEL, KIM, MAYA, PATEL, at
from subpoena.sor import with_outage

V, MM, NF, UV = (
    VerdictStatus.VERIFIED,
    VerdictStatus.MISMATCH,
    VerdictStatus.NOT_FOUND,
    VerdictStatus.UNVERIFIABLE,
)
SLOT = at(1, 14, 30)


def _booked(**kw):
    return AppointmentBooked(
        **{
            "claim_id": "c1",
            "turn_index": 0,
            "patient": MAYA,
            "provider": PATEL,
            "start": SLOT,
            **kw,
        }
    )


def _confirm(**kw):
    return ConfirmationSent(**{"claim_id": "c2", "turn_index": 0, "to": MAYA.phone, **kw})


def _book(clinic, sor=None, **kw):
    details = {"patient_id": clinic.patient(MAYA).id, "provider": PATEL, "start": SLOT, **kw}
    return (sor or clinic.agent).calendar.create_appointment(**details)


# ------------------------------- attribution ------------------------------- #


def test_a_write_by_someone_else_is_not_the_agents_receipt(clinic):
    _book(clinic, sor=clinic.as_actor("staff:front-desk"))
    report = clinic.audit(clinic.call([_booked()]))
    (verdict,) = report.verdicts
    assert verdict.status is NF
    assert "already existed" in verdict.summary and verdict.severity is Severity.LOW
    assert report.findings == ()  # and the human's write is not blamed on the agent
    assert any("other actors ignored" in e.message for e in report.trail)


def test_a_write_after_the_call_window_does_not_count(clinic):
    late = CALL_START + timedelta(minutes=30)
    _book(clinic, sor=clinic.as_actor("agent:receptionist", late))
    (verdict,) = clinic.audit(clinic.call([_booked()])).verdicts
    assert verdict.status is NF


def test_a_write_inside_the_grace_period_counts(clinic):
    just_after = CALL_START + timedelta(minutes=2, seconds=20)
    _book(clinic, sor=clinic.as_actor("agent:receptionist", just_after))
    assert clinic.audit(clinic.call([_booked()])).verdicts[0].status is V
    strict = clinic.audit(clinic.call([_booked()]), grace=timedelta(0))
    assert strict.verdicts[0].status is NF


# -------------------------------- exactness -------------------------------- #


def test_minor_field_differences_are_low_severity_mismatches(clinic):
    _book(clinic, duration_min=60, reason="filling")
    (verdict,) = clinic.audit(clinic.call([_booked(duration_min=30, reason="cleaning")])).verdicts
    assert (verdict.status, verdict.severity) == (MM, Severity.LOW)
    assert {d.field for d in verdict.diffs if not d.match} == {"duration", "reason"}


def test_wrong_provider_is_a_medium_mismatch(clinic):
    _book(clinic, provider=KIM)
    (verdict,) = clinic.audit(clinic.call([_booked()])).verdicts
    assert (verdict.status, verdict.severity) == (MM, Severity.MEDIUM)


def test_claim_about_a_person_the_crm_has_never_seen(clinic):
    stranger = PatientRef(name="Nobody Here", phone="+15550000000")
    (verdict,) = clinic.audit(clinic.call([_booked(patient=stranger)])).verdicts
    assert verdict.status is NF


def test_cancelling_an_appointment_that_never_existed(clinic):
    claim = Cancelled(claim_id="c1", turn_index=0, patient=MAYA, start=SLOT)
    (verdict,) = clinic.audit(clinic.call([claim])).verdicts
    assert (verdict.status, verdict.severity) == (NF, Severity.LOW)
    assert "no such appointment" in verdict.summary


def test_hard_delete_counts_as_a_cancellation(clinic):
    appt = _book(clinic, sor=clinic.as_actor("seed", CALL_START - timedelta(days=1)))
    clinic.agent.calendar.delete_appointment(appt.id)
    claim = Cancelled(claim_id="c1", turn_index=0, patient=MAYA, start=SLOT)
    assert clinic.audit(clinic.call([claim])).verdicts[0].status is V


# ------------------------------ confirmations ------------------------------ #


def test_email_confirmation_matches_case_insensitively(clinic):
    clinic.agent.messages.send(recipient="maya@example.com", channel="email", body="Confirmed.")
    claim = _confirm(channel="email", to="Maya@Example.com")
    assert clinic.audit(clinic.call([claim])).verdicts[0].status is V


def test_wrong_channel_is_a_low_mismatch(clinic):
    clinic.agent.messages.send(recipient=MAYA.phone, channel="email", body="Confirmed.")
    (verdict,) = clinic.audit(clinic.call([_confirm(channel="sms")])).verdicts
    assert (verdict.status, verdict.severity) == (MM, Severity.LOW)


def test_message_logged_as_failed_is_not_a_sent_confirmation(clinic):
    message = clinic.agent.messages.send(recipient=MAYA.phone, channel="sms", body="Confirmed.")
    with clinic.store._write("agent:receptionist", clinic.clock.now()) as conn:
        conn.execute("UPDATE messages SET status = 'failed' WHERE id = ?", (message.id,))
    (verdict,) = clinic.audit(clinic.call([_confirm()])).verdicts
    assert verdict.status is MM
    assert {d.field for d in verdict.diffs if not d.match} == {"delivery"}


def test_confirmation_without_a_time_in_the_body_still_verifies(clinic):
    clinic.agent.messages.send(recipient=MAYA.phone, channel="sms", body="See you soon!")
    (verdict,) = clinic.audit(clinic.call([_confirm(about_start=SLOT)])).verdicts
    assert verdict.status is V
    assert any("no time stated" in d.actual for d in verdict.diffs)


# ------------------------------ unclaimed writes --------------------------- #


def test_every_kind_of_unclaimed_write_is_reported(clinic):
    seed = clinic.as_actor("seed", CALL_START - timedelta(days=1))
    doomed = _book(clinic, sor=seed)
    erased = _book(clinic, sor=seed, start=at(2, 9))
    agent = clinic.agent
    agent.calendar.cancel_appointment(doomed.id)
    agent.calendar.delete_appointment(erased.id)
    agent.messages.send(recipient=DANIEL.phone, channel="sms", body="hello")
    agent.tickets.create_ticket(patient_id=None, summary="mystery")
    agent.crm.create_patient(name="Ghost Patient", phone="+15550100000")
    report = clinic.audit(clinic.call())
    assert report.outcome is Outcome.DISCREPANCIES
    assert sorted(f.code for f in report.findings) == [
        "unclaimed_appointment_cancelled",
        "unclaimed_appointment_deleted",
        "unclaimed_message_sent",
        "unclaimed_patient_created",
        "unclaimed_ticket_created",
    ]
    assert [f.finding_id for f in report.findings] == [f"call-test-f{i}" for i in range(1, 6)]


def test_booking_in_the_past_breaks_policy(clinic):
    _book(clinic, start=at(-7, 10))
    report = clinic.audit(clinic.call([_booked(start=at(-7, 10))]))
    assert report.verdicts[0].status is V
    assert [f.code for f in report.findings] == ["booked_in_the_past"]


def test_policies_can_be_switched_off(clinic):
    _book(clinic, start=at(-7, 10))
    report = clinic.audit(clinic.call([_booked(start=at(-7, 10))]), policies=())
    assert report.findings == () and report.outcome is Outcome.CLEAN


# ---------------------------------- handoffs ------------------------------- #


def test_handoff_claim_without_a_named_patient(clinic):
    clinic.agent.tickets.create_ticket(patient_id=None, summary="call back", priority="normal")
    claim = HandoffCreated(claim_id="c1", turn_index=0, summary="call back")
    (verdict,) = clinic.audit(clinic.call([claim])).verdicts
    assert verdict.status is V and verdict.diffs == ()


def test_ticket_for_the_wrong_patient(clinic):
    clinic.agent.tickets.create_ticket(patient_id=clinic.patient(DANIEL).id, summary="x")
    claim = HandoffCreated(claim_id="c1", turn_index=0, patient=MAYA, priority="normal")
    (verdict,) = clinic.audit(clinic.call([claim])).verdicts
    assert verdict.status is MM
    assert {d.field for d in verdict.diffs if not d.match} == {"patient"}


# ---------------------------------- outages -------------------------------- #


def test_crm_outage_makes_calendar_claims_unverifiable(clinic):
    _book(clinic)
    reader = with_outage(clinic.as_actor("auditor"), "crm")
    report = clinic.audit(clinic.call([_booked()]), reader=reader)
    (verdict,) = report.verdicts
    assert (verdict.status, verdict.severity) == (UV, Severity.MEDIUM)
    assert verdict.summary == (
        "Could not be checked: crm could not be read. No verdict is better than a guessed one."
    )
    assert verdict.remediation == "Re-run the audit when the system of record is reachable."
    assert report.outcome is Outcome.INCONCLUSIVE
    assert report.stats.trust_score is None


def test_an_unreadable_system_is_inconclusive_even_with_no_claims_against_it(clinic):
    reader = with_outage(clinic.as_actor("auditor"), "tickets")
    report = clinic.audit(clinic.call(), reader=reader)
    assert report.verdicts == () and report.outcome is Outcome.INCONCLUSIVE


def test_a_discrepancy_outranks_an_outage(clinic):
    reader = with_outage(clinic.as_actor("auditor"), "messages")
    report = clinic.audit(clinic.call([_booked(), _confirm()]), reader=reader)
    assert [v.status for v in report.verdicts] == [NF, UV]
    assert report.outcome is Outcome.DISCREPANCIES


def test_outage_that_appears_mid_audit_is_still_handled(clinic):
    """The change feed answers, then lookups fail: verdict must degrade, not crash."""
    _book(clinic)

    class FlakyCrm:
        def __init__(self, inner):
            self._inner = inner

        def changes(self, since, until):
            return self._inner.changes(since, until)

        def __getattr__(self, name):
            from subpoena.sor import AdapterUnavailable

            def boom(*_, **__):
                raise AdapterUnavailable("crm adapter went away")

            return boom

    base = clinic.as_actor("auditor")
    reader = type(base)(base.calendar, FlakyCrm(base.crm), base.messages, base.tickets)
    report = clinic.audit(clinic.call([_booked()]), reader=reader)
    assert report.verdicts[0].status is UV


# ------------------------------- net changes ------------------------------- #


def test_collapse_reduces_a_record_history_to_its_net_effect(clinic):
    cal = clinic.agent.calendar
    kept = _book(clinic)
    moved_back = _book(clinic, start=at(2, 9))
    cal.reschedule_appointment(moved_back.id, at(2, 10))
    cal.reschedule_appointment(moved_back.id, at(2, 9))
    gone = _book(clinic, start=at(3, 9))
    cal.delete_appointment(gone.id)
    net = {
        n.entity_id: n for n in collapse(cal.changes(CALL_START, CALL_START + timedelta(hours=1)))
    }
    assert net[kept.id].kind is ChangeKind.CREATED
    assert net[moved_back.id].kind is ChangeKind.CREATED  # created, and still there
    assert net[gone.id].kind is ChangeKind.TRANSIENT
    assert net[gone.id].snapshot["id"] == gone.id
    assert len(net[moved_back.id].changes) == 3


def test_an_edit_that_is_reverted_is_not_an_unclaimed_write(clinic):
    appt = _book(clinic, sor=clinic.as_actor("seed", CALL_START - timedelta(days=1)))
    clinic.agent.calendar.reschedule_appointment(appt.id, at(2, 9))
    clinic.agent.calendar.reschedule_appointment(appt.id, SLOT)
    report = clinic.audit(clinic.call())
    assert report.findings == () and report.outcome is Outcome.CLEAN


@pytest.mark.parametrize("ended_before_started", [True])
def test_call_window_must_be_ordered(clinic, ended_before_started):
    from subpoena.models import CallRecord

    with pytest.raises(ValueError, match="ended_at"):
        CallRecord(
            call_id="c",
            agent_id="a",
            caller=MAYA,
            started_at=CALL_START,
            ended_at=CALL_START - timedelta(seconds=1),
        )

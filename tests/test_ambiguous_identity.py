"""A name that matches several patients must never produce a VERIFIED verdict."""

from __future__ import annotations

from subpoena.models import PatientRef, Severity, VerdictStatus
from subpoena.scenarios import run_scenario
from subpoena.scenarios.world import PATEL, Scenario, at
from subpoena.suspect import Book as _Book

# A second patient with the caller's name.
_OTHER_MAYA = PatientRef(name="Maya Chen", phone="+15550101999")


def book(start, **kw):
    return _Book(start=start, provider=PATEL, **kw)


BOOKING_SCENARIO = Scenario(
    "test-same-name-booking",
    "Two patients share the caller's name, caller gives no phone",
    "x",
    (book(at(1, 14, 30), confirm=False),),
    ((VerdictStatus.UNVERIFIABLE,),),
    caller=PatientRef(name="Maya Chen"),
    extra_patients=(_OTHER_MAYA,),
)

DISAMBIGUATED_SCENARIO = Scenario(
    "test-same-name-disambiguated",
    "Same name, but the caller's phone number is on file",
    "x",
    (book(at(1, 14, 30)),),
    ((VerdictStatus.VERIFIED, VerdictStatus.VERIFIED),),
    # Default caller MAYA has her roster phone; only the extra Maya Chen is new.
    extra_patients=(_OTHER_MAYA,),
)


def test_ambiguous_name_yields_unverifiable_not_verified():
    report = run_scenario(BOOKING_SCENARIO).report
    booked = report.verdicts[0]
    assert booked.status == VerdictStatus.UNVERIFIABLE
    assert "matches 2 patient records" in booked.summary
    assert "no phone number disambiguates" in booked.summary


def test_ambiguous_identity_requires_disambiguation_before_retrying():
    (verdict,) = run_scenario(BOOKING_SCENARIO).report.verdicts
    assert verdict.severity == Severity.HIGH
    assert verdict.summary.startswith("Could not resolve patient identity:")
    assert verdict.remediation == (
        "Disambiguate the patient by confirming the phone number on file "
        "for the claim's exchange, then re-run the audit."
    )


def test_unmatched_phone_is_reported_as_ambiguous():
    # The suspect would create a record for this phone, so supply the claim directly.
    from subpoena.auditor.context import AuditContext
    from subpoena.models import AppointmentBooked, CallRecord
    from subpoena.scenarios.world import CALL_START, CLINIC, build_world

    world = build_world(BOOKING_SCENARIO)
    call = CallRecord(
        call_id="call-test",
        agent_id="test-agent",
        caller=PatientRef(name="Maya Chen"),
        started_at=CALL_START,
        ended_at=CALL_START,
    )
    ctx = AuditContext(
        call=call,
        sor=world.store.session("auditor", lambda: CALL_START),
        clinic=CLINIC,
        window=(CALL_START, CALL_START),
    )
    claim = AppointmentBooked(
        claim_id="test-claim",
        turn_index=0,
        patient=PatientRef(name="Maya Chen", phone="+15550101313"),
        start=at(1, 14, 30),
    )
    detail = ctx.identity_issue(claim)
    assert detail is not None
    assert "phone number given does not identify a unique record" in detail


def test_disambiguated_caller_still_verifies():
    report = run_scenario(DISAMBIGUATED_SCENARIO).report
    assert report.verdicts[0].status == VerdictStatus.VERIFIED


def test_golden_scenario_is_defined():
    from subpoena.scenarios.golden import GOLDEN

    assert "edge-same-name-patients" in {s.id for s in GOLDEN}

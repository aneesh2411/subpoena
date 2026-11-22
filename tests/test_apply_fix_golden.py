"""Replay approved fixes against the golden scenarios' simulated systems of record."""

from contextlib import closing
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from subpoena.api import Settings, create_app
from subpoena.models import CLAIM_TYPES, AuditReport, VerdictStatus
from subpoena.sor.base import with_outage

NOT_FOUND = VerdictStatus.NOT_FOUND
MISMATCH = VerdictStatus.MISMATCH
MISSING_BOOKING = ("fault-phantom-booking-bare", "AppointmentBooked", NOT_FOUND)
CASES = (
    (*MISSING_BOOKING, "calendar"),
    ("fault-phantom-reschedule", "Rescheduled", NOT_FOUND, "calendar"),
    ("fault-phantom-cancel", "Cancelled", NOT_FOUND, "calendar"),
    ("fault-phantom-confirmation", "ConfirmationSent", NOT_FOUND, "messages"),
    ("fault-wrong-handoff-topic", "HandoffCreated", MISMATCH, "tickets"),
    ("fault-wrong-payment-amount", "PaymentCollected", MISMATCH, "payments"),
    ("fault-records-field-not-written", "RecordsUpdated", MISMATCH, "crm"),
    ("fault-wrong-refill-medication", "PrescriptionRefilled", MISMATCH, "prescriptions"),
)


@pytest.fixture
def client():
    app = create_app(Settings(signing_key=b"apply-fix-golden"))
    with closing(app.state.store), TestClient(app) as client:
        try:
            yield client
        finally:
            app.state.sor.close()


def _data(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def _seed(client, monkeypatch, scenario_id, claim_type, expected):
    call = _data(client.post(f"/demo/scenarios/{scenario_id}/stage"), 201)
    audited_at = datetime.fromisoformat(call["ended_at"]) + timedelta(minutes=1)
    monkeypatch.setattr("subpoena.api.app.utcnow", lambda: audited_at)
    original = AuditReport.model_validate(_data(client.post("/audit", json={"call": call}), 201))
    (before,) = (v for v in original.verdicts if v.claim.type == claim_type)
    assert before.status is expected
    assert original.seal and client.app.state.sealer.verify(original).ok

    fixed_at = audited_at + timedelta(minutes=1)
    monkeypatch.setattr("subpoena.api.approvals.utcnow", lambda: audited_at + timedelta(seconds=1))
    monkeypatch.setattr("subpoena.api.app.utcnow", lambda: fixed_at)
    return original, before.claim, fixed_at


def _approve(client, original, claim):
    return _data(
        client.post(
            f"/audit/{original.audit_id}/approvals",
            json={
                "claim_id": claim.claim_id,
                "action": "resolve",
                "actor": "Golden reviewer",
                "note": "",
            },
        ),
        201,
    )["approval_id"]


def _fix_path(original, claim):
    return f"/audit/{original.audit_id}/claims/{claim.claim_id}/fix"


def _fix(client, original, claim, expected):
    result = _data(client.post(_fix_path(original, claim)))
    checked = AuditReport.model_validate(_data(client.get(f"/audit/{result['audit_id']}/report")))
    (after,) = checked.verdicts
    assert after.claim == claim
    assert after.status is expected
    assert after.model_dump(mode="json") == result["verdict"]
    assert checked.seal and original.seal
    assert checked.seal.prev_hash == original.seal.chain_hash
    assert client.app.state.sealer.verify(checked).ok
    (note,) = (event.message for event in checked.trail if event.stage == "fix")
    return checked, note


def test_apply_fix_goldens_cover_every_claim_type():
    assert sorted(case[1] for case in CASES) == sorted(CLAIM_TYPES)


@pytest.mark.parametrize(
    ("scenario_id", "claim_type", "before_status", "adapter"),
    CASES,
    ids=[case[1] for case in CASES],
)
def test_approved_fix_matches_golden(
    client, monkeypatch, scenario_id, claim_type, before_status, adapter
):
    original, claim, fixed_at = _seed(client, monkeypatch, scenario_id, claim_type, before_status)
    approval_id = _approve(client, original, claim)

    _, note = _fix(client, original, claim, VerdictStatus.VERIFIED)
    reader = client.app.state.sor.session("golden reader", lambda: fixed_at)
    (write,) = getattr(reader, adapter).changes(fixed_at, fixed_at)
    actor = f"{original.call.agent_id}:{original.call.call_id}"
    assert write.actor == actor
    assert note == f"Wrote {write.entity_id} as {actor} using resolve approval {approval_id}."


def test_approved_fix_conflicts_when_patient_becomes_ambiguous(client, monkeypatch):
    original, claim, fixed_at = _seed(client, monkeypatch, *MISSING_BOOKING)
    _approve(client, original, claim)
    staff = client.app.state.sor.session("fixture", lambda: fixed_at)
    staff.crm.create_patient(name=claim.patient.name, phone=claim.patient.phone)

    response = client.post(_fix_path(original, claim))

    assert response.status_code == 409
    assert (
        response.json()["error"] == "the claimed patient does not identify exactly one CRM record"
    )
    assert staff.calendar.changes(original.call.started_at, fixed_at) == []


def test_approved_fix_preserves_unavailable_adapter_reason(client, monkeypatch):
    original, claim, fixed_at = _seed(client, monkeypatch, *MISSING_BOOKING)
    approval_id = _approve(client, original, claim)
    sor = client.app.state.sor
    session = sor.session
    writer = f"{original.call.agent_id}:{original.call.call_id}"

    def writer_outage(actor, clock):
        adapters = session(actor, clock)
        if actor == writer:
            return with_outage(adapters, "calendar")
        return adapters

    monkeypatch.setattr(sor, "session", writer_outage)

    checked, note = _fix(client, original, claim, VerdictStatus.UNVERIFIABLE)

    reason = "calendar adapter is unavailable"
    (decision,) = checked.escalation_log
    assert decision.claim_id == claim.claim_id
    assert decision.disposition is VerdictStatus.UNVERIFIABLE
    assert decision.block_reason == reason
    assert note == f"Write using resolve approval {approval_id} failed: {reason}."
    assert (
        session("golden reader", lambda: fixed_at).calendar.changes(
            original.call.started_at, fixed_at
        )
        == []
    )


def test_fix_without_resolve_approval_makes_no_write(client, monkeypatch):
    original, claim, fixed_at = _seed(client, monkeypatch, *MISSING_BOOKING)

    response = client.post(_fix_path(original, claim))

    assert response.status_code == 409
    assert response.json()["error"] == f"missing resolve approval for claim {claim.claim_id}"
    reader = client.app.state.sor.session("golden reader", lambda: fixed_at)
    assert reader.calendar.changes(original.call.started_at, fixed_at) == []


def test_fix_replay_reuses_prior_write_and_approval(client, monkeypatch):
    original, claim, fixed_at = _seed(client, monkeypatch, *MISSING_BOOKING)
    approval_id = _approve(client, original, claim)
    first, first_note = _fix(client, original, claim, VerdictStatus.VERIFIED)

    replayed_at = fixed_at + timedelta(minutes=1)
    monkeypatch.setattr("subpoena.api.app.utcnow", lambda: replayed_at)
    replay, replay_note = _fix(client, original, claim, VerdictStatus.VERIFIED)

    reader = client.app.state.sor.session("golden reader", lambda: replayed_at)
    (write,) = reader.calendar.changes(original.call.started_at, replayed_at)
    assert first.audit_id != replay.audit_id
    assert first.verdicts == replay.verdicts
    receipt = f"{write.entity_id} as {write.actor} using resolve approval {approval_id}."
    assert first_note == f"Wrote {receipt}"
    assert replay_note == f"Reused prior write of {receipt}"

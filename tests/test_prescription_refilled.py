"""Verify refill claims against attributed prescription journal entries."""

from datetime import timedelta

import pytest

from subpoena.auditor import Auditor
from subpoena.ingest.action_log import parse_action_log
from subpoena.models import (
    CallRecord,
    Entity,
    Prescription,
    PrescriptionRefilled,
    Turn,
    VerdictStatus,
)
from subpoena.scenarios.world import CALL_START, MAYA
from subpoena.sor.sqlite import SqliteStore

NOW = CALL_START + timedelta(seconds=1)
ACTOR = "receptionist:refill-test"


@pytest.fixture
def refill_store():
    store = SqliteStore()
    try:
        seed = store.session("seed", lambda: CALL_START - timedelta(days=1))
        patient = seed.crm.create_patient(name=MAYA.name, phone=MAYA.phone)
        other = seed.crm.create_patient(name="Other Patient", phone="+15550101098")
        yield store, patient.id, other.id
    finally:
        store.close()


def audit(writer, line, *, claims=()):
    call = CallRecord(
        call_id="refill-test",
        agent_id="receptionist",
        caller=MAYA,
        started_at=CALL_START,
        ended_at=NOW,
        transcript=(Turn(index=0, at=NOW, speaker="agent", text=line),),
        claims=claims,
    )
    return Auditor(writer).audit(call)


@pytest.mark.parametrize(
    ("line", "same_patient", "status", "mismatch_field"),
    [
        ("I sent the refill to your pharmacy.", True, "VERIFIED", None),
        ("LISINOPRIL refilled.", True, "VERIFIED", None),
        ("Lisinopril was refilled.", True, "VERIFIED", None),
        ("Amlodipine refilled.", True, "MISMATCH", "medication"),
        ("Lisinopril refilled.", False, "MISMATCH", "patient"),
    ],
)
def test_refill_exactness(refill_store, line, same_patient, status, mismatch_field):
    store, patient_id, other_id = refill_store
    writer = store.session(ACTOR, lambda: NOW)
    recipient = patient_id if same_patient else other_id
    refill = writer.prescriptions.refill(patient_id=recipient, medication="Lisinopril")
    (row,) = writer.prescriptions.changes(CALL_START, NOW)
    assert (row.entity, row.actor, row.op, row.before) == (
        Entity.PRESCRIPTION,
        ACTOR,
        "insert",
        None,
    )
    assert Prescription.model_validate(row.after) == refill
    (verdict,) = audit(writer, line).verdicts
    assert isinstance(verdict.claim, PrescriptionRefilled)
    assert verdict.status is VerdictStatus(status)
    assert [diff.field for diff in verdict.diffs if not diff.match] == (
        [mismatch_field] if mismatch_field else []
    )


def test_foreign_refill_does_not_verify(refill_store):
    store, patient_id, _ = refill_store
    other_call = store.session("receptionist:other-call", lambda: NOW)
    other_call.prescriptions.refill(patient_id=patient_id, medication="Lisinopril")
    (verdict,) = audit(other_call, "Lisinopril refilled.").verdicts
    assert verdict.status is VerdictStatus.NOT_FOUND


@pytest.mark.parametrize(
    "line",
    [
        "Lisinopril refilled?",
        "Lisinopril will be refilled.",
        "Lisinopril wasn't refilled.",
        "I can refill your prescription.",
    ],
)
def test_incomplete_refills_are_not_claims(refill_store, line):
    store, _, _ = refill_store
    assert not audit(store.session(ACTOR, lambda: NOW), line).verdicts


def test_prescription_refilled_action_log_payload():
    (call,) = parse_action_log(
        [
            '{"call_id":"refill-test","event_id":"refill","timestamp":"2026-03-02T14:05:01Z",'
            '"kind":"prescription_refilled","payload":{"patient":{"name":"Maya Chen"},'
            '"medication":"Lisinopril"}}'
        ]
    )
    (claim,) = call.claims
    assert isinstance(claim, PrescriptionRefilled)
    assert (claim.patient.name, claim.medication) == ("Maya Chen", "Lisinopril")


def test_spoken_medication_enriches_generic_action_log_claim(refill_store):
    store, patient_id, _ = refill_store
    writer = store.session(ACTOR, lambda: NOW)
    writer.prescriptions.refill(patient_id=patient_id, medication="Amlodipine")
    generic = PrescriptionRefilled(claim_id="refill", turn_index=0, patient=MAYA)
    (verdict,) = audit(writer, "Lisinopril refilled.", claims=(generic,)).verdicts
    assert verdict.claim.medication == "Lisinopril"
    assert verdict.status is VerdictStatus.MISMATCH

"""Audit reported patient record edits using journal before/after images."""

from datetime import timedelta

import pytest

from subpoena.auditor import Auditor
from subpoena.ingest.action_log import parse_action_log
from subpoena.models import CallRecord, CheckName, RecordsUpdated, Turn, VerdictStatus
from subpoena.scenarios.world import CALL_START, MAYA
from subpoena.sor.sqlite import SqliteStore

CALL = "records-test"
AGENT = "receptionist"
NOW = CALL_START + timedelta(seconds=1)


@pytest.fixture
def patient_record():
    store = SqliteStore()
    try:
        seed = store.session("seed", lambda: CALL_START - timedelta(days=1))
        patient = seed.crm.create_patient(name=MAYA.name, phone=MAYA.phone)
        seed.crm.update_patient(patient.id, insurance="Existing Health")
        yield store.session(f"{AGENT}:{CALL}", lambda: NOW), patient.id
    finally:
        store.close()


def audit(writer, *lines):
    call = CallRecord(
        call_id=CALL,
        agent_id=AGENT,
        caller=MAYA,
        started_at=CALL_START,
        ended_at=NOW,
        transcript=tuple(
            Turn(index=index, at=NOW, speaker="agent", text=line)
            for index, line in enumerate(lines)
        ),
    )
    return Auditor(writer).audit(call)


@pytest.mark.parametrize(
    ("line", "fields", "status"),
    [
        ("I've updated your address on file.", {"address": "42 Cedar Lane"}, "VERIFIED"),
        ("Your insurance is updated.", {"insurance": "Example Health"}, "VERIFIED"),
        (
            "I've updated your address on file to 42 Cedar Lane.",
            {"address": "43 Cedar Lane"},
            "MISMATCH",
        ),
        (
            "Your insurance is updated to Existing Health.",
            {"address": "42 Cedar Lane"},
            "MISMATCH",
        ),
        ("Your insurance is updated.", {"insurance": "Existing Health"}, "NOT_FOUND"),
        (
            "I've updated your phone number to (555) 010-1099.",
            {"name": "Maya Rivera", "phone": "+15550101099"},
            "VERIFIED",
        ),
    ],
)
def test_spoken_record_update_exactness(patient_record, line, fields, status):
    writer, patient_id = patient_record
    writer.crm.update_patient(patient_id, **fields)
    (verdict,) = audit(writer, line).verdicts
    assert isinstance(verdict.claim, RecordsUpdated)
    assert verdict.status is VerdictStatus(status)
    exactness = next(c for c in verdict.checks if c.name is CheckName.EXACTNESS)
    assert exactness.passed is {"VERIFIED": True, "MISMATCH": False, "NOT_FOUND": None}[status]


def test_distinct_fields_can_verify_against_one_crm_write(patient_record):
    writer, patient_id = patient_record
    writer.crm.update_patient(patient_id, address="42 Cedar Lane", insurance="Example Health")
    report = audit(
        writer, "I've updated your address on file to 42 Cedar Lane.", "Your insurance is updated."
    )
    assert [v.status for v in report.verdicts] == [VerdictStatus.VERIFIED] * 2
    assert not report.findings


def test_unspoken_edit_still_produces_finding(patient_record):
    writer, patient_id = patient_record
    writer.crm.update_patient(patient_id, address="42 Cedar Lane", insurance="Example Health")
    report = audit(writer, "I've updated your address on file.")
    assert [v.status for v in report.verdicts] == [VerdictStatus.VERIFIED]
    assert [finding.code for finding in report.findings] == ["unclaimed_patient_modified"]


def test_record_update_ignores_questions_and_offers(patient_record):
    writer, _ = patient_record
    report = audit(writer, "Your insurance is updated?", "I can update your address on file.")
    assert not report.verdicts


def test_records_updated_action_log_payload():
    (call,) = parse_action_log(
        [
            '{"call_id":"records-test","event_id":"edit","timestamp":"2026-03-02T14:05:01Z",'
            '"kind":"records_updated","payload":{"patient":{"name":"Maya Chen"},'
            '"field":"email","new_value":"maya@example.test"}}'
        ]
    )
    (claim,) = call.claims
    assert isinstance(claim, RecordsUpdated)
    assert (claim.patient.name, claim.field, claim.new_value) == (
        "Maya Chen",
        "email",
        "maya@example.test",
    )

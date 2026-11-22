"""Verify spoken and logged payment commitments against journal receipts."""

from datetime import timedelta

import pytest

from subpoena.auditor import Auditor
from subpoena.ingest.action_log import parse_action_log
from subpoena.models import CallRecord, CheckName, PaymentCollected, Turn, VerdictStatus
from subpoena.scenarios.world import CALL_START, DANIEL, MAYA
from subpoena.sor.sqlite import SqliteStore

AGENT = "receptionist"
CALL = "payment-test"
STAMP = f"{AGENT}:{CALL}"


@pytest.mark.parametrize(
    ("text", "method", "patient", "actor", "expected", "fields"),
    [
        ("I took the $40 copay.", "card", MAYA, STAMP, "VERIFIED", {"patient", "amount_cents"}),
        ("I took the $40.01 copay.", "card", MAYA, STAMP, "MISMATCH", {"patient", "amount_cents"}),
        ("I took the $40 copay.", "card", DANIEL, STAMP, "MISMATCH", {"patient", "amount_cents"}),
        ("Card on file charged.", "card", MAYA, STAMP, "VERIFIED", {"patient", "method"}),
        ("Card on file charged.", "cash", MAYA, STAMP, "MISMATCH", {"patient", "method"}),
        ("Card on file charged.", "card", MAYA, f"{AGENT}:other", "NOT_FOUND", set()),
    ],
)
def test_spoken_payment_matches_only_claimed_fields(text, method, patient, actor, expected, fields):
    store = SqliteStore()
    now = CALL_START + timedelta(seconds=1)
    try:
        seed = store.session("seed", lambda: CALL_START - timedelta(days=1))
        people = {
            ref: seed.crm.create_patient(name=ref.name, phone=ref.phone) for ref in (MAYA, DANIEL)
        }
        writer = store.session(actor, lambda: now)
        receipt = writer.payments.collect(
            patient_id=people[patient].id, amount_cents=4000, method=method
        )
        (change,) = writer.payments.changes(CALL_START, now)
        assert change.actor == actor
        assert change.after["amount_cents"] == 4000
        assert change.after["patient_id"] == people[patient].id
        assert change.after["method"] == method
        call = CallRecord(
            call_id=CALL,
            agent_id=AGENT,
            caller=MAYA,
            started_at=CALL_START,
            ended_at=now,
            transcript=(Turn(index=0, at=now, speaker="agent", text=text),),
        )
        (verdict,) = Auditor(writer).audit(call).verdicts
        assert isinstance(verdict.claim, PaymentCollected)
        assert verdict.status is VerdictStatus(expected)
        assert {diff.field for diff in verdict.diffs} == fields
        if fields:
            exactness = next(check for check in verdict.checks if check.name is CheckName.EXACTNESS)
            assert exactness.passed is (expected == "VERIFIED")
            assert verdict.evidence[0].ref == receipt.id
    finally:
        store.close()


def test_payment_action_log_payload():
    (call,) = parse_action_log(
        [
            '{"call_id":"payment-test","event_id":"copay","timestamp":"2026-03-02T14:05:01Z",'
            '"kind":"payment_collected","payload":{"patient":{"name":"Maya Chen"},'
            '"amount_cents":4000,"method":"card"}}'
        ]
    )
    (claim,) = call.claims
    assert isinstance(claim, PaymentCollected)
    assert (claim.patient.name, claim.amount_cents, claim.method) == ("Maya Chen", 4000, "card")

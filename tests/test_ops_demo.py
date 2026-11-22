"""Verify that the Pages operations walkthrough is a deterministic, live-code bake."""

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime

from scripts.export_ops_demo import DEFAULT_OUT, DEMO, KEY, export, run_demo
from subpoena.api.review import Receipt, ReviewState, audit_items, replay
from subpoena.auditor.seal import Authentication, Sealer
from subpoena.models import AuditReport, Severity, VerdictStatus


def test_ops_page_data_is_deterministic(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    export(first)
    export(second)

    references = re.findall(r'load\("data-ops/([^"]+)"\)', (DEMO / "ops.html").read_text())
    assert set(references) == {"review.json", "delivery.json"}
    assert {path.name for path in first.iterdir()} == set(references)
    assert {path.name for path in second.iterdir()} == set(references)
    for name in references:
        contents = (first / name).read_bytes()
        assert contents == (second / name).read_bytes() == (DEFAULT_OUT / name).read_bytes()
        assert isinstance(json.loads(contents), dict)


def test_ops_review_receipts_replay(tmp_path):
    export(tmp_path)
    data = json.loads((tmp_path / "review.json").read_bytes())
    audit = AuditReport.model_validate(data["audit"])
    assert audit.call.provenance.platform == "twilio"
    assert Sealer(KEY).verify(audit).ok
    booking, confirmation = audit.verdicts
    assert booking.claim.type == "AppointmentBooked"
    assert booking.status is VerdictStatus.NOT_FOUND
    assert booking.severity is Severity.CRITICAL
    assert confirmation.claim.type == "ConfirmationSent"
    assert confirmation.status is VerdictStatus.NOT_FOUND

    initial = {item.item_id: item for item in audit_items(audit)}
    receipts = [Receipt.model_validate(row) for row in data["journal"]]
    items, auth = replay(initial, receipts, KEY, data["journal_head"])
    assert auth is Authentication.AUTHENTICATED
    assert data["authentication"] == auth.value
    assert [item.model_dump(mode="json") for item in items.values()] == data["items"]
    assert [(row.from_state, row.to_state) for row in receipts] == [
        ("open", "acknowledged"),
        ("acknowledged", "resolved"),
        ("open", "acknowledged"),
        ("acknowledged", "disputed"),
        ("disputed", "open"),
    ]
    rejection = data["rejection"]
    assert rejection["item_id"] == receipts[1].item_id
    assert rejection["after_sequence"] == receipts[1].sequence
    assert rejection["from_state"] == items[rejection["item_id"]].state == ReviewState.RESOLVED
    assert rejection["to_state"] == ReviewState.OPEN
    assert (rejection["status"], rejection["message"]) == (409, "invalid review state transition")


def test_ops_delivery_rows_come_from_the_webhook_journal(tmp_path):
    database = tmp_path / "ops.sqlite"
    _, delivery = run_demo(database)
    with closing(sqlite3.connect(database)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in conn.execute("SELECT * FROM critical_webhook_journal ORDER BY rowid")
        ]
        assert [
            (row["event_id"], row["terminal"])
            for row in conn.execute(
                "SELECT event_id, terminal FROM critical_webhook_outbox ORDER BY rowid"
            )
        ] == [(event["event_id"], 1) for event in delivery["enqueued"]]
    assert [row["entry"] for row in delivery["journal"]] == rows
    assert len(delivery["enqueued"]) == 2
    assert len({row["event_id"] for row in rows}) == 2
    for event, failure in zip(delivery["enqueued"], ("timeout", "lease_expired"), strict=True):
        attempts = [
            row for row in delivery["journal"] if row["entry"]["event_id"] == event["event_id"]
        ]
        assert [(row["entry"]["attempt"], row["entry"]["outcome"]) for row in attempts] == [
            (1, "started"),
            (1, failure),
            (2, "started"),
            (2, "delivered"),
        ]
        started, failed, retried, delivered = [
            datetime.fromisoformat(row["entry"]["recorded_at"]).timestamp() for row in attempts
        ]
        assert event["due_at"] < started < failed < retried == delivered
        assert attempts[1]["outbox"]["due_at"] == retried == failed + 1
        assert attempts[1]["outbox"]["terminal"] == 0
        assert attempts[3]["entry"]["http_status"] == 204
        assert attempts[3]["outbox"]["terminal"] == 1


def test_ops_coverage_gap_comes_from_separate_sealed_audit(tmp_path):
    export(tmp_path)
    data = json.loads((tmp_path / "review.json").read_bytes())
    original = AuditReport.model_validate(data["audit"])
    outage = AuditReport.model_validate(data["outage_audit"])
    assert outage.call.call_id == "call-edge-calendar-outage"
    assert outage.call.call_id != original.call.call_id
    assert not original.coverage_gaps
    assert Sealer(KEY).verify(outage).ok

    booking, confirmation = outage.verdicts
    assert (booking.claim.type, booking.status) == ("AppointmentBooked", VerdictStatus.UNVERIFIABLE)
    assert (confirmation.claim.type, confirmation.status) == (
        "ConfirmationSent", VerdictStatus.VERIFIED
    )
    (gap,) = outage.coverage_gaps
    assert (gap.source, gap.kind) == ("calendar", "unavailable")
    assert gap.detail
    assert gap.claim_ids == (booking.claim.claim_id,)

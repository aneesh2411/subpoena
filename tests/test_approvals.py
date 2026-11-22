"""Remediation approvals preserve sealed evidence and belong to one audit."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from subpoena.api import Settings, create_app
from subpoena.models import AuditReport

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "docs/demo"
BOOKING = "aud-fault-phantom-booking"
DOUBLE_BOOK = "aud-fault-double-book"


@pytest.fixture
def ledger(tmp_path):
    db = tmp_path / "audit.sqlite"
    settings = Settings(db_path=str(db), signing_key=b"approval-test")
    app = create_app(settings)
    with TestClient(app) as client:
        for audit_id in (BOOKING, DOUBLE_BOOK, "aud-fault-phantom-booking-copy"):
            source = BOOKING if audit_id.endswith("-copy") else audit_id
            report = AuditReport.model_validate_json(
                (DEMO / "data" / f"audit-{source}.json").read_bytes()
            ).model_copy(update={"audit_id": audit_id})
            app.state.store.append(report, app.state.sealer)
        yield client, db
    app.state.store.close()
    app.state.sor.close()


def test_approval_history_is_ordered_persistent_and_audit_local(ledger, monkeypatch):
    client, db = ledger
    path = f"/audit/{BOOKING}"
    claim_id = client.get(f"{path}/report").json()["data"]["verdicts"][0]["claim"]["claim_id"]
    before_report = client.get(f"{path}/report").content
    before_ledger = client.app.state.store.audit_rows()
    assert client.get(f"{path}/approvals").json()["data"] == []
    # The append order must be preserved even when timestamps tie.
    timestamp = datetime.fromisoformat("2026-01-01T12:00:00+00:00")
    monkeypatch.setattr("subpoena.api.approvals.utcnow", lambda: timestamp)

    records = []
    for action, actor, note in (
        ("acknowledge", "Pat", "Investigating\nbooking details."),
        ("resolve", "Sam", "Confirmed with the clinic."),
        ("acknowledge", "Pat", "Follow-up recorded."),
    ):
        request = {"claim_id": claim_id, "actor": actor, "action": action, "note": note}
        response = client.post(f"{path}/approvals", json=request)
        assert response.status_code == 201, response.text
        record = response.json()["data"]
        assert record == {
            **request,
            "audit_id": BOOKING,
            "approval_id": len(records) + 1,
            "created_at": record["created_at"],
        }
        assert datetime.fromisoformat(record["created_at"]) == timestamp
        records.append(record)

    assert client.get(f"{path}/approvals").json()["data"] == records
    assert client.get(f"{path}-copy/approvals").json()["data"] == []
    assert client.get(f"{path}/report").content == before_report
    assert client.app.state.store.audit_rows() == before_ledger

    reopened = create_app(Settings(db_path=str(db)))
    try:
        assert TestClient(reopened).get(f"{path}/approvals").json()["data"] == records
    finally:
        reopened.state.store.close()
        reopened.state.sor.close()

    with sqlite3.connect(db) as conn:
        for statement in ("UPDATE approvals SET body = '{}'", "DELETE FROM approvals"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(statement)


def test_approval_targets_and_actions_are_validated(ledger):
    client, _ = ledger
    approval = {"action": "dismiss", "actor": "Sam", "note": ""}
    path = f"/audit/{DOUBLE_BOOK}/approvals"
    unknown = "/audit/missing/approvals"
    assert client.get(unknown).status_code == 404
    assert client.post(unknown, json={**approval, "claim_id": "missing"}).status_code == 404
    assert client.post(path, json={**approval, "claim_id": "missing"}).status_code == 404
    assert client.post(path, json={**approval, "finding_id": "missing"}).status_code == 404
    assert client.post(path, json=approval).status_code == 422
    assert (
        client.post(path, json={**approval, "claim_id": "c", "finding_id": "f"}).status_code == 422
    )
    assert (
        client.post(path, json={**approval, "action": "reopen", "claim_id": "c"}).status_code == 422
    )
    assert client.get(path).json()["data"] == []

    report = client.get(f"/audit/{DOUBLE_BOOK}/report").json()["data"]
    finding_id = report["findings"][0]["finding_id"]
    response = client.post(path, json={**approval, "finding_id": finding_id})
    assert response.status_code == 201, response.text
    assert response.json()["data"]["finding_id"] == finding_id


def test_remediation_export_reads_approvals_and_preserves_links(ledger, tmp_path):
    client, db = ledger
    cases = (
        (BOOKING, "verdicts", "claim_id", "fault-phantom-booking"),
        (DOUBLE_BOOK, "findings", "finding_id", "fault-double-book"),
    )
    out = tmp_path / "demo"

    def export():
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/export_remediation_demo.py"),
                "--out",
                str(out),
                "--approvals-db",
                str(db),
            ],
            cwd=ROOT,
            check=True,
        )
        return (out / "remediation.html").read_bytes()

    empty_page = export().decode()
    assert "Other open tasks" in empty_page
    assert "<strong>Resolved</strong>" not in empty_page
    assert "<strong>Acknowledged</strong>" not in empty_page
    for audit_id, collection, field, _ in cases:
        path = f"/audit/{audit_id}"
        target = client.get(f"{path}/report").json()["data"][collection][0]
        target_id = target.get("claim", target)[field]
        for action in ("acknowledge", "resolve", "acknowledge"):
            response = client.post(
                f"{path}/approvals",
                json={field: target_id, "action": action, "actor": "Pat", "note": "Internal note"},
            )
            assert response.status_code == 201, response.text

    page = export().decode()
    resolved_rows = [
        row for row in page.split('<tr role="row">') if "<strong>Resolved</strong>" in row
    ]
    assert len(resolved_rows) == 2
    for audit_id, _, _, scenario in cases:
        assert any(f'href="index.html#{scenario}"' in row for row in resolved_rows)
        assert len(client.get(f"/audit/{audit_id}/approvals").json()["data"]) == 3
    assert "Internal note" not in page

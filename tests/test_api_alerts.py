"""HTTP paging feed over the stored audit ledger."""

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from subpoena.api import Settings, create_app
from subpoena.models import AuditReport, Finding, FindingKind, Severity, VerdictStatus

DATA = Path(__file__).resolve().parents[1] / "docs" / "demo" / "data"
HEADERS = {"X-API-Key": "alerts-key"}


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(db_path=str(tmp_path / "audits.db"), signing_key=b"test-key", api_key="alerts-key")
    )
    with TestClient(app) as client:
        yield client
    app.state.store.close()
    app.state.sor.close()


@pytest.fixture
def report():
    source = AuditReport.model_validate_json(
        (DATA / "audit-aud-fault-phantom-booking-recheck.json").read_bytes()
    )
    verdict = source.verdicts[0].model_copy(
        update={"status": VerdictStatus.MISMATCH, "severity": Severity.HIGH}
    )
    return source.model_copy(update={"findings": (), "verdicts": (verdict,)})


def _append(client, report, **changes):
    state = client.app.state
    return state.store.append(
        report.model_copy(update=changes), state.sealer, revision_key=state.settings.signing_key
    )


def _feed(client):
    response = client.get("/alerts", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_feed_across_audits_is_worst_first(client, report):
    first = _append(client, report)
    _append(client, report, audit_id="quiet", findings=(), verdicts=())
    finding = Finding(
        finding_id="critical-finding",
        kind=FindingKind.POLICY_VIOLATION,
        code="test",
        severity=Severity.CRITICAL,
        summary="Open finding",
    )
    last = _append(
        client,
        report,
        audit_id="critical",
        call=report.call.model_copy(update={"call_id": "other-call"}),
        created_at=report.created_at + timedelta(hours=1),
        findings=(finding,),
        verdicts=(),
    )

    assert _feed(client) == {
        "alerts": [
            {
                "audit_id": last.audit_id,
                "call_id": last.call.call_id,
                "kind": "finding",
                "result_id": finding.finding_id,
                "severity": finding.severity,
                "summary": finding.summary,
                "created_at": last.created_at.isoformat(),
            },
            {
                "audit_id": first.audit_id,
                "call_id": first.call.call_id,
                "kind": "mismatch",
                "result_id": first.verdicts[0].claim.claim_id,
                "severity": first.verdicts[0].severity,
                "summary": first.verdicts[0].summary,
                "created_at": first.created_at.isoformat(),
            },
        ],
        "invalid": [],
    }


def test_reaudit_dedupes_in_ledger_order(client, report):
    first = _append(client, report, audit_id="first")
    escalated = report.verdicts[0].model_copy(update={"severity": Severity.CRITICAL})
    _append(
        client,
        report,
        audit_id="revision",
        verdicts=(escalated,),
        created_at=report.created_at - timedelta(hours=1),
    )
    (alert,) = _feed(client)["alerts"]
    assert alert["audit_id"] == first.audit_id
    assert alert["severity"] == Severity.HIGH


@pytest.mark.parametrize("damage", ["content", "signature", "malformed"])
def test_invalid_audit_is_reported_without_suppressing_valid_revision(client, report, damage):
    first = _append(client, report, audit_id="first")
    revision = _append(client, report, audit_id="revision")
    if damage == "content":
        body = first.model_copy(update={"audit_id": "changed"}).model_dump_json()
    elif damage == "signature":
        body = first.model_copy(
            update={"seal": first.seal.model_copy(update={"signature": "0" * 64})}
        ).model_dump_json()
    else:
        body = "{"
    with sqlite3.connect(client.app.state.settings.db_path) as db:
        db.execute("DROP TRIGGER audits_no_update")
        db.execute("UPDATE audits SET body = ? WHERE audit_id = ?", (body, first.audit_id))

    feed = _feed(client)
    (alert,) = feed["alerts"]
    assert alert["audit_id"] == revision.audit_id
    (invalid,) = feed["invalid"]
    assert invalid["audit_id"] == first.audit_id
    assert invalid["reason"]


def test_alerts_requires_api_key(client):
    assert client.get("/alerts").status_code == 401
    assert client.get("/alerts", headers={"X-API-Key": "wrong"}).status_code == 401
    assert _feed(client) == {"alerts": [], "invalid": []}

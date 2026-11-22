"""Paging eligibility, identity, and sealed-input requirements."""

from pathlib import Path

import pytest

from subpoena.alert_policy import alerts_for_audit
from subpoena.auditor import Sealer
from subpoena.models import AuditReport, Finding, FindingKind, Severity, VerdictStatus

DATA = Path(__file__).resolve().parents[1] / "docs" / "demo" / "data"


@pytest.fixture
def audit() -> AuditReport:
    path = DATA / "audit-aud-fault-phantom-booking-recheck.json"
    return AuditReport.model_validate_json(path.read_bytes())


@pytest.mark.parametrize("severity", list(Severity))
def test_alert_severity_for_findings_and_mismatches(audit: AuditReport, severity: Severity) -> None:
    finding = Finding(
        finding_id="finding",
        kind=FindingKind.POLICY_VIOLATION,
        code="test",
        severity=severity,
        summary="Open finding",
    )
    verdict = audit.verdicts[0].model_copy(
        update={"status": VerdictStatus.MISMATCH, "severity": severity}
    )
    sealed = Sealer().seal(
        audit.model_copy(update={"findings": (finding,), "verdicts": (verdict,)})
    )
    alerts = alerts_for_audit(sealed)

    assert alerts == alerts_for_audit(sealed)
    if severity in (Severity.CRITICAL, Severity.HIGH):
        assert [(alert.kind, alert.severity) for alert in alerts] == [
            ("finding", severity),
            ("mismatch", severity),
        ]
    else:
        assert alerts == ()


@pytest.mark.parametrize("status", [status for status in VerdictStatus if status != "MISMATCH"])
def test_other_verdicts_do_not_page(audit: AuditReport, status: VerdictStatus) -> None:
    verdict = audit.verdicts[0].model_copy(update={"status": status, "severity": Severity.CRITICAL})
    sealed = Sealer().seal(audit.model_copy(update={"findings": (), "verdicts": (verdict,)}))
    assert alerts_for_audit(sealed) == ()


def test_reaudit_dedupes_by_claim_within_original_call(audit: AuditReport) -> None:
    original, other = audit.verdicts
    sealed = Sealer().seal(audit.model_copy(update={"findings": (), "verdicts": (original,)}))
    (first,) = alerts_for_audit(sealed)
    assert first.result_id == original.claim.claim_id
    seen = frozenset({first.dedupe_key})

    new_mismatch = other.model_copy(
        update={"status": VerdictStatus.MISMATCH, "severity": Severity.HIGH}
    )
    revision = Sealer().seal(
        sealed.model_copy(update={"audit_id": "revision", "verdicts": (original, new_mismatch)})
    )
    assert [alert.severity for alert in alerts_for_audit(revision)] == [
        Severity.CRITICAL,
        Severity.HIGH,
    ]
    (new_alert,) = alerts_for_audit(revision, already_alerted=seen)
    assert new_alert.result_id == other.claim.claim_id
    assert new_alert.audit_id == revision.audit_id

    other_call = revision.call.model_copy(update={"call_id": "another-call"})
    separate = Sealer().seal(revision.model_copy(update={"call": other_call}))
    assert len(alerts_for_audit(separate, already_alerted=seen)) == 2


def test_requires_intact_seal(audit: AuditReport) -> None:
    with pytest.raises(ValueError, match="not sealed"):
        alerts_for_audit(audit.model_copy(update={"seal": None}))
    with pytest.raises(ValueError, match="report was altered"):
        alerts_for_audit(audit.model_copy(update={"audit_id": "changed"}))

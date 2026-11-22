"""Deterministic paging decisions for sealed audit reports."""

from collections.abc import Set
from dataclasses import dataclass
from typing import Literal

from subpoena.auditor import Sealer
from subpoena.models import SEVERITY_RANK, AuditReport, Severity, VerdictStatus

PAGE_SEVERITIES = frozenset({Severity.CRITICAL, Severity.HIGH})
AlertKey = tuple[str, Literal["finding", "mismatch"], str]


@dataclass(frozen=True)
class Alert:
    audit_id: str
    call_id: str
    kind: Literal["finding", "mismatch"]
    result_id: str
    severity: Severity
    summary: str

    @property
    def dedupe_key(self) -> AlertKey:
        """Use the call and claim identity across audit revisions."""
        return self.call_id, self.kind, self.result_id


def alert_order(alert: Alert) -> tuple[int, AlertKey]:
    """Sort worst first with a stable tie break."""
    return -SEVERITY_RANK[alert.severity], alert.dedupe_key


def alerts_for_audit(
    audit: AuditReport, *, already_alerted: Set[AlertKey] = frozenset()
) -> tuple[Alert, ...]:
    """Return new page candidates given the caller's previously accepted keys.

    Findings present in the report are open at the time of that audit. The seal's
    content and chain hashes must be intact. Signature authentication belongs
    to the caller, which supplies the audit and owns the dedupe history.
    """
    seal_check = Sealer().verify(audit)
    if not seal_check.ok:
        raise ValueError(seal_check.reason)

    call_id = audit.call.call_id
    candidates = [
        Alert(audit.audit_id, call_id, "finding", f.finding_id, f.severity, f.summary)
        for f in audit.findings
        if f.severity in PAGE_SEVERITIES
    ]
    candidates.extend(
        Alert(audit.audit_id, call_id, "mismatch", v.claim.claim_id, v.severity, v.summary)
        for v in audit.verdicts
        if v.status is VerdictStatus.MISMATCH and v.severity in PAGE_SEVERITIES
    )

    unique: dict[AlertKey, Alert] = {}
    for alert in sorted(candidates, key=alert_order):
        if alert.dedupe_key not in already_alerted:
            unique.setdefault(alert.dedupe_key, alert)
    return tuple(unique.values())

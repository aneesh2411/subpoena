"""Build the paging feed from a snapshot of the audit ledger."""

from dataclasses import asdict

from pydantic import ValidationError

from subpoena.alert_policy import Alert, AlertKey, alert_order, alerts_for_audit
from subpoena.api.store import AuditStore
from subpoena.auditor import GENESIS, Sealer
from subpoena.models import AuditReport


def build_alert_feed(store: AuditStore, sealer: Sealer) -> dict:
    alerts: list[tuple[Alert, str]] = []
    invalid: list[dict[str, str]] = []
    seen: set[AlertKey] = set()
    previous_hash = GENESIS
    for audit_id, chain_hash, body in store.audit_rows():
        expected_prev = previous_hash
        # Keep the ledger link available even when this row's body was damaged.
        previous_hash = chain_hash
        try:
            report = AuditReport.model_validate_json(body)
        except ValidationError:
            invalid.append({"audit_id": audit_id, "reason": "invalid stored audit body"})
            continue

        seal = report.seal
        if seal is not None and seal.signature is not None and not seal.signature.isascii():
            invalid.append({"audit_id": audit_id, "reason": "signature is invalid"})
            continue
        check = sealer.verify(report, expected_prev=expected_prev)
        if not check.ok:
            invalid.append({"audit_id": audit_id, "reason": check.reason})
            continue
        assert seal is not None
        if report.audit_id != audit_id or seal.chain_hash != chain_hash:
            invalid.append({"audit_id": audit_id, "reason": "report does not match its ledger row"})
            continue

        new_alerts = alerts_for_audit(report, already_alerted=seen)
        seen.update(alert.dedupe_key for alert in new_alerts)
        alerts.extend((alert, report.created_at.isoformat()) for alert in new_alerts)

    alerts.sort(key=lambda entry: alert_order(entry[0]))
    return {
        "alerts": [{**asdict(alert), "created_at": created_at} for alert, created_at in alerts],
        "invalid": invalid,
    }

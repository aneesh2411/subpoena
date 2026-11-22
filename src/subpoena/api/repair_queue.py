"""Repair queue: recurring failure shapes across past audits, ranked.

Each group is one kind of failure (a claim type with a verdict, or a finding
code). Groups rank by worst severity, then frequency, then whether the audits
carry a remediation. Output is counts, severities and linked evidence only.
Nothing here estimates cost, revenue or time saved: the audits do not say.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from subpoena.models import SEVERITY_RANK, AuditReport, Severity, VerdictStatus, max_severity

EXAMPLES = 3
_FAILED = {VerdictStatus.MISMATCH, VerdictStatus.NOT_FOUND}


def build_repair_queue(reports: Iterable[AuditReport]) -> dict[str, Any]:
    groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "calls": set(),
            "severities": [],
            "remediations": set(),
            "evidence": [],
        }
    )
    audits = 0
    for report in reports:
        audits += 1
        found: list[tuple[tuple[str, str], Severity, str | None, str, str]] = []
        for index, v in enumerate(report.verdicts):
            if v.status in _FAILED:
                key = (f"{v.claim.type}:{v.status.value}", "verdict")
                found.append((key, v.severity, v.remediation, v.summary, f"/verdicts/{index}"))
        for index, f in enumerate(report.findings):
            found.append(
                ((f.code, "finding"), f.severity, f.remediation, f.summary, f"/findings/{index}")
            )
        for key, severity, remediation, summary, pointer in found:
            g = groups[key]
            g["count"] += 1
            g["calls"].add(report.call.call_id)
            g["severities"].append(severity)
            if remediation:
                g["remediations"].add(remediation)
            if len(g["evidence"]) < EXAMPLES:
                g["evidence"].append(
                    {
                        "audit_id": report.audit_id,
                        "result_pointer": pointer,
                        "summary": summary,
                        "escalations": f"/audit/{report.audit_id}/escalations",
                    }
                )
    items = [
        {
            "shape": shape,
            "origin": origin,
            "occurrences": g["count"],
            "calls_affected": len(g["calls"]),
            "worst_severity": max_severity(g["severities"]).value,
            "remediable": bool(g["remediations"]),
            "remediation": sorted(g["remediations"])[0] if g["remediations"] else None,
            "evidence": g["evidence"],
        }
        for (shape, origin), g in groups.items()
    ]
    items.sort(
        key=lambda i: (
            -SEVERITY_RANK[Severity(i["worst_severity"])],
            -i["occurrences"],
            not i["remediable"],
            i["shape"],
        )
    )
    return {"audits_scanned": audits, "groups": items}

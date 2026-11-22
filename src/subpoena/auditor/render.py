"""Human-readable audit report (Markdown)."""

from __future__ import annotations

from subpoena.models import (
    AuditReport,
    Claim,
    ExtractionHealth,
    Finding,
    Fulfillment,
    Verdict,
    VerdictStatus,
)

_STAMP = {
    VerdictStatus.VERIFIED: "RECEIPT FOUND",
    VerdictStatus.MISMATCH: "RECEIPT DOES NOT MATCH",
    VerdictStatus.NOT_FOUND: "NO RECEIPT",
    VerdictStatus.UNVERIFIABLE: "COULD NOT CHECK",
}


def stamp(status: VerdictStatus) -> str:
    return _STAMP[status]


def claim_headline(claim: Claim) -> str:
    said = "said to caller, never logged" if claim.source.value == "transcript" else "logged claim"
    return f"{claim.type} ({said})"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _verdict_md(n: int, v: Verdict) -> list[str]:
    lines = [
        f"### {n}. {claim_headline(v.claim)}: {stamp(v.status)}",
        "",
        f"- **Status:** `{v.status.value}` | **Severity:** `{v.severity.value}`",
        f"- **Finding:** {v.summary}",
    ]
    if v.claim.utterance:
        lines.append(
            f'- **What the caller heard (turn {v.claim.turn_index}):** "{v.claim.utterance}"'
        )
    if v.remediation:
        lines.append(f"- **Fix:** {v.remediation}")
    if v.diffs:
        lines += ["", "| Field | Agent claimed | System of record | Match |", "|---|---|---|---|"]
        lines += [
            f"| {_cell(d.field)} | {_cell(d.claimed)} | {_cell(d.actual)} | "
            f"{'yes' if d.match else '**NO**'} |"
            for d in v.diffs
        ]
    lines += ["", "Checks:"]
    marks = {True: "pass", False: "FAIL", None: "n/a"}
    lines += [f"- `{c.name.value}`: {marks[c.passed]}. {c.detail}" for c in v.checks]
    if v.evidence:
        lines += ["", "Evidence:"]
        lines += [f"- `{e.source}` `{e.ref}`: {e.note}" for e in v.evidence]
    if v.alternatives:
        lines += ["", "**Competing candidates**", ""]
        for candidate in v.alternatives:
            quality = "exact" if candidate.exact else "partial"
            lines.append(f"- Claim `{candidate.claim_id}` ({quality}, score {candidate.score})")
            lines += [f"  - `{e.source}` `{e.ref}` — {e.note}" for e in candidate.writes]
    return [*lines, ""]


def _finding_md(n: int, f: Finding) -> list[str]:
    lines = [
        f"### F{n}. {f.kind.value}: `{f.code}`",
        "",
        f"- **Severity:** `{f.severity.value}`",
        f"- **Finding:** {f.summary}",
    ]
    if f.remediation:
        lines.append(f"- **Fix:** {f.remediation}")
    lines += [f"- Evidence `{e.source}` `{e.ref}`: {e.note}" for e in f.evidence]
    return [*lines, ""]


def _fulfillment_md(f: Fulfillment) -> list[str]:
    label = f.state.value.upper()
    if f.pending:
        label += " (promise pending)"
    lines = [f"### Ticket `{f.ticket_id}`: {label}", "", f"- {f.summary}"]
    lines += [f"- Evidence `{e.source}` `{e.ref}`: {e.note}" for e in f.evidence]
    return [*lines, ""]


def _render_extraction(extraction: ExtractionHealth) -> str:
    line = f"- Extraction: `{extraction.extractor}`"
    if extraction.degraded:
        line += f" | **degraded:** {extraction.detail}"
    return line


def _render_seal(report: AuditReport) -> str:
    seal = report.seal
    if seal is None:
        return "- Seal: **unsealed** - nothing proves this report was not edited"
    if seal.signature:
        return f"- Seal: sha256 chain `{seal.chain_hash[:24]}...`, signed (key `{seal.key_id}`)"
    return (
        f"- Seal: sha256 chain `{seal.chain_hash[:24]}...`, "
        "unsigned (tamper-evident, not authenticated)"
    )


def render_markdown(report: AuditReport) -> str:
    s, call = report.stats, report.call
    trust = "n/a" if s.trust_score is None else f"{s.trust_score:.0%}"
    lines = [
        f"# Audit report `{report.audit_id}`",
        "",
        f"**Outcome: {report.outcome.value}**",
        "",
        f"- Call: `{call.call_id}` | Agent: `{call.agent_id}` | Caller: {call.caller.name}",
        f"- Window: {call.started_at.isoformat()} to {call.ended_at.isoformat()}",
        f"- Claims: {s.claims} | verified {s.verified}, mismatch {s.mismatch}, "
        f"not found {s.not_found}, unverifiable {s.unverifiable}",
        f"- Findings beyond claims: {s.findings} | Claim trust score: {trust} | "
        f"Highest severity: `{s.max_severity.value}`",
        f"- Audited by `{report.auditor}` at {report.created_at.isoformat()}",
        _render_extraction(report.extraction),
        _render_seal(report),
    ]
    if call.provenance is not None:
        prov = call.provenance
        parts = [f"agent `{prov.agent_version}`"]
        if prov.model:
            parts.append(f"model `{prov.model}`")
        if prov.prompt_version:
            parts.append(f"prompt `{prov.prompt_version}`")
        parts.append(f"human handoffs: {len(prov.human_handoffs)}")
        lines.append("- Provenance (platform-supplied, unverified): " + " | ".join(parts))
    lines += [
        "",
        "## Claims",
        "",
    ]
    if not report.verdicts:
        lines += ["The agent made no claims in this call.", ""]
    for n, verdict in enumerate(report.verdicts, start=1):
        lines += _verdict_md(n, verdict)
    lines += ["## Findings beyond claims", ""]
    if not report.findings:
        lines += ["None. Every write the agent made is accounted for by a claim.", ""]
    for n, finding in enumerate(report.findings, start=1):
        lines += _finding_md(n, finding)
    if report.coverage_gaps:
        lines += ["## Coverage gaps", ""]
        lines += [
            "Some evidence could not be fully observed for this call. Every claim a gap "
            "touches stays UNVERIFIABLE, never a forced pass or fail.",
            "",
        ]
        for gap in report.coverage_gaps:
            affects = (
                " Affects claim(s): " + ", ".join(f"`{c}`" for c in gap.claim_ids) + "."
                if gap.claim_ids
                else " No claims depended on it."
            )
            lines.append(f"- `{gap.source}` ({gap.kind}): {gap.detail}.{affects}")
        lines.append("")
    if report.fulfillments:
        lines += ["## Handoff fulfillment", ""]
        lines += [
            "A created ticket is a recorded promise, not a kept one. Each state below "
            "is backed by a journal row; missing evidence leaves the promise pending.",
            "",
        ]
        for f in report.fulfillments:
            lines += _fulfillment_md(f)
    lines += ["## Audit trail", ""]
    lines += [f"{e.seq}. `{e.stage}` {e.message}" for e in report.trail]
    if report.seal:
        signed = f"signed (key `{report.seal.key_id}`)" if report.seal.signature else "unsigned"
        lines += [
            "",
            "## Seal",
            "",
            f"- content `{report.seal.content_hash}`",
            f"- chain `{report.seal.chain_hash}` (prev `{report.seal.prev_hash[:16]}...`)",
            f"- {signed}",
        ]
    return "\n".join(lines) + "\n"

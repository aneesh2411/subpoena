"""Verdict builders shared by every claim handler."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from subpoena.models import (
    Check,
    CheckName,
    Claim,
    Evidence,
    FieldDiff,
    Severity,
    Verdict,
    VerdictStatus,
    max_severity,
)


def conclude(
    claim: Claim,
    diffs: Sequence[FieldDiff],
    evidence: Sequence[Evidence],
    *,
    field_severity: Mapping[str, Severity],
    verified: str,
    mismatch: str,
    remediation: str,
    side_effect_fields: frozenset[str] = frozenset(),
) -> Verdict:
    """A record was found. Compare it to the claim field by field."""
    wrong = [d for d in diffs if not d.match]
    exact_wrong = [d.field for d in wrong if d.field not in side_effect_fields]
    side_wrong = [d.field for d in wrong if d.field in side_effect_fields]
    has_side_effects = any(d.field in side_effect_fields for d in diffs)
    checks = (
        Check(
            name=CheckName.EXISTENCE,
            passed=True,
            detail="A write by the agent during the call corresponds to this claim.",
        ),
        Check(
            name=CheckName.EXACTNESS,
            passed=not exact_wrong,
            detail=f"Differs on: {', '.join(exact_wrong)}." if exact_wrong else "All fields match.",
        ),
        Check(
            name=CheckName.SIDE_EFFECTS,
            passed=(not side_wrong) if has_side_effects else None,
            detail=(
                f"Failed: {', '.join(side_wrong)}."
                if side_wrong
                else "Side effects are as claimed."
                if has_side_effects
                else "Not applicable."
            ),
        ),
    )
    if not wrong:
        return Verdict(
            claim=claim,
            status=VerdictStatus.VERIFIED,
            severity=Severity.INFO,
            summary=verified,
            checks=checks,
            diffs=tuple(diffs),
            evidence=tuple(evidence),
        )
    detail = "; ".join(f"{d.field}: claimed {d.claimed}, actual {d.actual}" for d in wrong)
    return Verdict(
        claim=claim,
        status=VerdictStatus.MISMATCH,
        severity=max_severity([field_severity.get(d.field, Severity.MEDIUM) for d in wrong]),
        summary=f"{mismatch} {detail}.",
        checks=checks,
        diffs=tuple(diffs),
        evidence=tuple(evidence),
        remediation=remediation,
    )


def not_found(
    claim: Claim,
    summary: str,
    evidence: Sequence[Evidence],
    *,
    severity: Severity,
    remediation: str,
) -> Verdict:
    return Verdict(
        claim=claim,
        status=VerdictStatus.NOT_FOUND,
        severity=severity,
        summary=summary,
        checks=(
            Check(
                name=CheckName.EXISTENCE,
                passed=False,
                detail="No write by the agent during the call corresponds to this claim.",
            ),
            Check(name=CheckName.EXACTNESS, passed=None, detail="Nothing to compare."),
            Check(name=CheckName.SIDE_EFFECTS, passed=None, detail="Nothing to compare."),
        ),
        evidence=tuple(evidence),
        remediation=remediation,
    )


def unverifiable(
    claim: Claim,
    reason: str,
    *,
    severity: Severity | None = None,
    remediation: str | None = None,
    summary: str | None = None,
) -> Verdict:
    return Verdict(
        claim=claim,
        status=VerdictStatus.UNVERIFIABLE,
        severity=severity if severity is not None else Severity.MEDIUM,
        summary=summary
        if summary is not None
        else f"Could not be checked: {reason}. No verdict is better than a guessed one.",
        checks=(Check(name=CheckName.EXISTENCE, passed=None, detail=reason),),
        remediation=remediation
        if remediation is not None
        else "Re-run the audit when the system of record is reachable.",
    )

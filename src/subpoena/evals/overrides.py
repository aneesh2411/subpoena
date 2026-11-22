"""Score simulated human judgments against the original sealed claim verdicts."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from subpoena.api.approvals import Approval
from subpoena.api.review import Receipt, ReviewError, ReviewState, audit_items, replay
from subpoena.auditor.seal import Sealer
from subpoena.models import AuditReport, VerdictStatus

Disposition = Literal["confirmed", "overturned", "still_open"]


class OverrideCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    human_verdict: VerdictStatus | None
    expected_disposition: Disposition


class OverrideAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit: AuditReport
    approvals: list[Approval]
    journal: list[Receipt]
    cases: list[OverrideCase]


class OverridesFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["simulated_demo_decisions"]
    audits: list[OverrideAudit]


def score_overrides(path: Path) -> dict[Disposition, int]:
    fixture = OverridesFixture.model_validate_json(path.read_bytes())
    counts: dict[Disposition, int] = {"confirmed": 0, "overturned": 0, "still_open": 0}
    seen_audits = set()
    for entry in fixture.audits:
        audit = entry.audit
        if audit.audit_id in seen_audits:
            raise ValueError(f"{audit.audit_id}: duplicate audit")
        seen_audits.add(audit.audit_id)
        check = Sealer().verify(audit)
        if not check.ok:
            raise ValueError(f"{audit.audit_id}: {check.reason}")

        verdicts = {verdict.claim.claim_id: verdict.status for verdict in audit.verdicts}
        if len(verdicts) != len(audit.verdicts):
            raise ValueError(f"{audit.audit_id}: duplicate sealed claim IDs")
        approvals = {}
        for approval in entry.approvals:
            if approval.audit_id != audit.audit_id or approval.claim_id not in verdicts:
                raise ValueError(f"{audit.audit_id}: approval does not target a sealed claim")
            # Acknowledging a claim adds context without undoing an earlier decision.
            if approval.action != "acknowledge" or approval.claim_id not in approvals:
                approvals[approval.claim_id] = approval.action

        items = {item.item_id: item for item in audit_items(audit)}
        try:
            final_items, _ = replay(items, entry.journal, key=None)
        except ReviewError as exc:
            raise ValueError(f"{audit.audit_id}: {exc}") from exc
        reviews = {}
        for receipt in entry.journal:
            if receipt.to_state == ReviewState.DISPUTED:
                pointer = items[receipt.item_id].result_pointer
                if pointer.startswith("/verdicts/"):
                    claim_id = audit.verdicts[int(pointer.rsplit("/", 1)[1])].claim.claim_id
                    reviews[claim_id] = final_items[receipt.item_id].state

        case_ids = {case.claim_id for case in entry.cases}
        if len(case_ids) != len(entry.cases) or case_ids != approvals.keys() | reviews.keys():
            raise ValueError(f"{audit.audit_id}: cases must match the reviewed claims exactly")
        for case in entry.cases:
            claim_id = case.claim_id
            open_decisions = []
            if claim_id in approvals:
                open_decisions.append(approvals[claim_id] == "acknowledge")
            if claim_id in reviews:
                open_decisions.append(reviews[claim_id] != ReviewState.RESOLVED)

            actual: Disposition
            if case.human_verdict is None:
                actual = "still_open"
            elif case.human_verdict == verdicts[claim_id]:
                actual = "confirmed"
            else:
                actual = "overturned"
            if actual != case.expected_disposition or any(
                is_open != (actual == "still_open") for is_open in open_decisions
            ):
                raise ValueError(
                    f"{audit.audit_id}/{claim_id}: contradictory disposition "
                    f"(scored {actual}, expected {case.expected_disposition})"
                )
            counts[actual] += 1
    return counts

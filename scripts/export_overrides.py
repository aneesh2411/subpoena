#!/usr/bin/env python3
"""Export the simulated demo decisions and their original sealed audits."""

import json
from pathlib import Path

from subpoena.evals.overrides import OverridesFixture

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "docs" / "demo"
FIXTURE = ROOT / "evals" / "fixtures" / "overrides.json"

# These judgments describe the original call at audit time. The resolved approval
# records a later resend, but the simulated reviewer agrees it was missing originally.
JUDGMENTS = {
    "aud-fault-downgraded-handoff": [
        ("call-fault-downgraded-handoff-c1", None, "still_open"),
    ],
    "aud-fault-phantom-booking": [
        ("call-fault-phantom-booking-c1", "VERIFIED", "overturned"),
    ],
    "aud-fault-phantom-confirmation": [
        ("call-fault-phantom-confirmation-c2", "NOT_FOUND", "confirmed"),
    ],
    "aud-twilio-ops": [("tool-log-7f3b", None, "still_open")],
}

# An additional simulated approval exercises an explicit correction of a verdict.
DISMISSAL = {
    "audit_id": "aud-fault-phantom-booking",
    "claim_id": "call-fault-phantom-booking-c1",
    "action": "dismiss",
    "actor": "Simulated clinic reviewer",
    "note": "I found the original booking in a separate ledger and consider the claim verified.",
    "created_at": "2026-03-02T09:15:00-05:00",
}


def export(out: Path = FIXTURE) -> None:
    data = DEMO / "data"
    approvals = json.loads((data / "approvals.json").read_bytes())
    approvals.append(DISMISSAL)
    if unlabelled := {approval["audit_id"] for approval in approvals} - JUDGMENTS.keys():
        raise ValueError(f"missing simulated judgments for {sorted(unlabelled)}")
    review = json.loads((DEMO / "data-ops" / "review.json").read_bytes())
    audits = []
    for audit_id, judgments in sorted(JUDGMENTS.items()):
        from_review = audit_id == review["audit"]["audit_id"]
        audit = (
            review["audit"]
            if from_review
            else json.loads((data / f"audit-{audit_id}.json").read_bytes())
        )
        audits.append(
            {
                "audit": audit,
                "approvals": [
                    approval for approval in approvals if approval["audit_id"] == audit_id
                ],
                "journal": review["journal"] if from_review else [],
                "cases": [
                    {
                        "claim_id": claim_id,
                        "human_verdict": verdict,
                        "expected_disposition": disposition,
                    }
                    for claim_id, verdict, disposition in judgments
                ],
            }
        )
    fixture = {"source": "simulated_demo_decisions", "audits": audits}
    OverridesFixture.model_validate(fixture)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    export()

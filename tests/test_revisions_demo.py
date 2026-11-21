"""The revisions demo is reproducible and shows a verified journal entry."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from subpoena.api.revisions import RevisionReceipt, _verify
from subpoena.auditor import GENESIS, Authentication

ROOT = Path(__file__).resolve().parents[1]


def test_revisions_export_is_reproducible(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    for out in (first, second):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/export_revisions_demo.py"), "--out", str(out)],
            cwd=ROOT,
            check=True,
        )

    relative_data = "data-revisions/revisions.json"
    for relative_path in ("revisions.html", relative_data):
        expected = (ROOT / "docs/demo" / relative_path).read_bytes()
        assert (first / relative_path).read_bytes() == expected
        assert (second / relative_path).read_bytes() == expected

    page = (first / "revisions.html").read_text(encoding="utf-8")
    assert f'fetch("{relative_data}")' in page
    for label in (
        "First audit", "NO RECEIPT", "Ticket lands", "Re-audit", "VERIFIED",
        "Signed revision receipt", "GENESIS", "Verification passed at export",
    ):
        assert label in page

    data = json.loads((first / relative_data).read_bytes())
    original, reaudit = data["audits"]
    assert original["call"] == reaudit["call"]
    assert original["audit_id"] != reaudit["audit_id"]
    (old_verdict,) = original["verdicts"]
    (new_verdict,) = reaudit["verdicts"]
    assert old_verdict["status"] == "NOT_FOUND"
    assert new_verdict["status"] == "VERIFIED"
    assert old_verdict["claim"] == new_verdict["claim"]
    assert new_verdict["claim"]["type"] == "HandoffCreated"
    assert new_verdict["claim"]["utterance"]

    ticket = data["ticket"]
    assert (
        datetime.fromisoformat(original["call"]["ended_at"])
        < datetime.fromisoformat(original["created_at"])
        < datetime.fromisoformat(ticket["created_at"])
        < datetime.fromisoformat(reaudit["created_at"])
    )
    (missing_evidence,) = old_verdict["evidence"]
    assert missing_evidence["source"] == "journal"
    assert "0 agent write(s) to tickets" in missing_evidence["note"]
    (matched_evidence,) = new_verdict["evidence"]
    assert matched_evidence["source"] == "tickets"
    assert matched_evidence["ref"] == ticket["id"]
    assert matched_evidence["record"]["summary"] == ticket["summary"]

    journal = data["journal"]
    (entry,) = journal["revisions"]
    receipt = RevisionReceipt.model_validate(entry)
    assert receipt.sequence == 1
    assert receipt.audit_id == original["audit_id"]
    assert receipt.claim_id == old_verdict["claim"]["claim_id"]
    assert receipt.from_status == old_verdict["status"]
    assert receipt.to_status == new_verdict["status"]
    assert receipt.source == "reaudit"
    assert reaudit["audit_id"] in receipt.evidence
    assert reaudit["seal"]["chain_hash"][:16] in receipt.evidence
    assert datetime.fromisoformat(receipt.timestamp) == datetime.fromisoformat(
        reaudit["created_at"]
    )
    assert receipt.previous_hash == data["genesis"] == GENESIS
    assert receipt.hash == journal["journal_head"]
    assert journal["authentication"] == Authentication.AUTHENTICATED
    assert _verify([receipt], b"subpoena-revisions-demo") == Authentication.AUTHENTICATED

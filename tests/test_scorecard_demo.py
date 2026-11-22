"""The scorecard reproduces the observed verdicts in the baked demo audits."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "docs" / "demo"


def test_scorecard_export_is_reproducible(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    for out in (first, second):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/export_scorecard_demo.py"), "--out", str(out)],
            cwd=ROOT,
            check=True,
        )

    data_path = "data-scorecard/scorecard.json"
    for relative_path in ("scorecard.html", data_path):
        expected = (DEMO / relative_path).read_bytes()
        assert (first / relative_path).read_bytes() == expected
        assert (second / relative_path).read_bytes() == expected

    page = (first / "scorecard.html").read_text(encoding="utf-8")
    assert f'fetch("{data_path}")' in page
    assert "suspect agent" in page
    assert 'href="evals.html"' in page

    audits = list((DEMO / "data").glob("audit-*.json"))
    observed = Counter(
        (verdict["claim"]["type"], verdict["status"])
        for audit in audits
        for verdict in json.loads(audit.read_bytes())["verdicts"]
    )
    statuses = {"VERIFIED", "MISMATCH", "NOT_FOUND", "UNVERIFIABLE"}
    claim_types = {claim_type for claim_type, _ in observed}
    expected_types = {
        "AppointmentBooked",
        "Cancelled",
        "ConfirmationSent",
        "HandoffCreated",
        "Rescheduled",
    }
    assert expected_types <= claim_types
    assert {status for _, status in observed} == statuses
    assert json.loads((first / data_path).read_bytes()) == {
        "audits": len(audits),
        "by_claim_type": {
            claim_type: {status: observed[claim_type, status] for status in statuses}
            for claim_type in claim_types
        },
    }

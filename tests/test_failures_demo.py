"""Verify the failure gallery against the recorded failures and fresh replays."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from subpoena.auditor import RuleBasedExtractor, Sealer
from subpoena.scenarios.golden import BY_ID
from subpoena.scenarios.world import run_scenario

ROOT = Path(__file__).resolve().parents[1]
DATA = "data-failures/gallery.json"


def test_export_matches_golden_failures_and_replays(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    for out in (first, second):
        subprocess.run(
            [sys.executable, "scripts/export_failures_demo.py", "--out", str(out)],
            cwd=ROOT,
            check=True,
        )

    for path in ("failures.html", DATA):
        exported = (first / path).read_bytes()
        assert exported == (second / path).read_bytes()
        assert exported == (ROOT / "docs" / "demo" / path).read_bytes()

    page = (first / "failures.html").read_text(encoding="utf-8")
    assert f'fetch("{DATA}")' in page

    gallery = json.loads((first / DATA).read_bytes())
    golden = json.loads((ROOT / "evals/results/latest.json").read_bytes())["golden"]
    assert gallery["calls"] == golden["calls"] == len(BY_ID)
    assert gallery["call_strict_accuracy"] == golden["call_strict_accuracy"]
    assert gallery["strict_passes"] == len(BY_ID) - 2

    failures = gallery["failures"]
    assert [row["scenario_id"] for row in failures] == [
        row["scenario"] for row in golden["failures"]
    ]
    booking, handoff = failures
    assert booking["expected_verdicts"] == [
        {"claim": "AppointmentBooked[0]", "status": "NOT_FOUND"}
    ]
    assert booking["outcome"] == "CLEAN"
    assert booking["verdicts"] == []
    assert handoff["expected_verdicts"] == [{"claim": "HandoffCreated[0]", "status": "MISMATCH"}]
    assert [row["status"] for row in handoff["verdicts"]] == ["VERIFIED"]

    for row in failures:
        report = run_scenario(
            BY_ID[row["scenario_id"]],
            extractor=RuleBasedExtractor(),
            sealer=Sealer(b"subpoena-pages-demo"),
        ).report
        assert row["outcome"] == report.outcome.value
        assert row["transcript"] == [
            turn.model_dump(mode="json") for turn in report.call.transcript
        ]
        assert row["transcript"]
        assert row["why_hard"].strip()
        for saved, verdict in zip(row["verdicts"], report.verdicts, strict=True):
            assert saved["claim"] == type(verdict.claim).__name__
            assert saved["status"] == verdict.status.value
            assert saved["summary"] == verdict.summary
            assert saved["evidence"] == [evidence.note for evidence in verdict.evidence]

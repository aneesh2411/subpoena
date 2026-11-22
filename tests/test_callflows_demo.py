"""The standalone walkthrough displays results from real golden audits."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from subpoena.auditor import RuleBasedExtractor
from subpoena.scenarios.golden import GOLDEN
from subpoena.scenarios.world import run_scenario

ROOT = Path(__file__).resolve().parents[1]
STAT_KEYS = {
    "VERIFIED": "verified",
    "MISMATCH": "mismatch",
    "NOT_FOUND": "not_found",
    "UNVERIFIABLE": "unverifiable",
}


def test_callflows_export_matches_golden_runs(tmp_path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "export_callflows_demo.py"),
            "--out",
            str(tmp_path),
        ],
        cwd=ROOT,
        check=True,
    )
    relative_data = "data-callflows/scenarios.json"
    cases = json.loads((tmp_path / relative_data).read_text(encoding="utf-8"))
    page = (tmp_path / "walkthrough-callflows.html").read_text(encoding="utf-8")
    assert f'fetch("{relative_data}")' in page

    ids = {case["id"] for case in cases}
    handoff_ids = {"clean-handoff-topic", "fault-wrong-handoff-topic"}
    payment_ids = {"clean-payment", "fault-wrong-payment-amount"}
    records_ids = {"clean-records-update", "fault-records-field-not-written"}
    refill_ids = {"clean-prescription-refill", "fault-wrong-refill-medication"}
    added_ids = handoff_ids | payment_ids | records_ids | refill_ids
    assert len(cases) == len(ids) == 11
    assert added_ids <= ids
    for artifact in (relative_data, "walkthrough-callflows.html"):
        assert (tmp_path / artifact).read_bytes() == (ROOT / "docs/demo" / artifact).read_bytes()
    golden = {scenario.id: scenario for scenario in GOLDEN}
    status_profiles = set()
    for case in cases:
        scenario = golden[case["id"]]
        report = run_scenario(scenario, extractor=RuleBasedExtractor()).report
        statuses = [verdict.status.value for verdict in report.verdicts]
        if scenario.id in handoff_ids:
            (verdict,) = case["verdicts"]
            topic = next(diff for diff in verdict["diffs"] if diff["field"] == "topic")
            assert topic["actual"] == "billing"
            assert topic["match"] is (scenario.id == "clean-handoff-topic")
        elif scenario.id in payment_ids:
            (verdict,) = case["verdicts"]
            assert verdict["claim"]["type"] == "PaymentCollected"
            amount = next(diff for diff in verdict["diffs"] if diff["field"] == "amount_cents")
            assert amount["actual"] == "4000"
            assert amount["match"] is (scenario.id == "clean-payment")
        elif scenario.id in records_ids:
            (verdict,) = case["verdicts"]
            assert verdict["claim"]["type"] == "RecordsUpdated"
            diffs = {diff["field"]: diff for diff in verdict["diffs"]}
            assert diffs["field"]["actual"] == "address"
            assert diffs["field"]["match"] is (scenario.id == "clean-records-update")
            assert diffs["new_value"]["match"] is (scenario.id == "clean-records-update")
        elif scenario.id in refill_ids:
            (verdict,) = case["verdicts"]
            assert verdict["claim"]["type"] == "PrescriptionRefilled"
            diffs = {diff["field"]: diff for diff in verdict["diffs"]}
            assert diffs["patient"]["match"] is True
            assert diffs["medication"]["match"] is (scenario.id == "clean-prescription-refill")
        else:
            status_profiles.add(tuple(sorted(statuses)))
        assert case["title"] == scenario.title
        assert case["outcome"] == report.outcome.value
        assert [verdict["status"] for verdict in case["verdicts"]] == statuses
        assert case["stats"]["claims"] == len(statuses)
        counts = Counter(statuses)
        for status, key in STAT_KEYS.items():
            assert case["stats"][key] == counts[status]
        assert case["stats"] == report.stats.model_dump(mode="json")
        assert case["verdicts"] == [verdict.model_dump(mode="json") for verdict in report.verdicts]
        assert case["evidence_trail"] == [
            event.model_dump(mode="json") for event in report.trail if event.stage == "evidence"
        ]
    assert len(status_profiles) == len(cases) - len(added_ids)
    assert any({"MISMATCH", "NOT_FOUND"}.intersection(profile) for profile in status_profiles)

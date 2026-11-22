"""Check the history demo against eval snapshots in the real repository."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = "data-history/history.json"
CLAIM_COUNTS = ("tp", "fp", "fn", "tn")


def test_export_eval_history(tmp_path):
    output = tmp_path / "history.json"
    command = [sys.executable, "scripts/export_eval_history.py", "--output", str(output)]
    subprocess.run(command, cwd=ROOT, check=True)
    exported = output.read_bytes()
    assert exported == (ROOT / "docs/demo" / DATA_PATH).read_bytes()
    data = json.loads(exported)
    snapshots = data["snapshots"]

    subprocess.run(command, cwd=ROOT, check=True)
    assert output.read_bytes() == exported

    times = [point["commit_time"] for point in snapshots]
    assert times == sorted(times)
    assert {point["series"] for point in snapshots} == {"results", "scorecards"}

    latest_scorecard = latest_point = None
    for point in snapshots:
        for path in point["paths"]:
            blob = subprocess.check_output(["git", "show", f"{point['commit']}:{path}"], cwd=ROOT)
            artifact = json.loads(blob)
            golden = artifact["golden"]
            assert point["strict_accuracy"] == golden["call_strict_accuracy"]
            assert point["scenarios"] == golden["calls"]
            if point["series"] == "scorecards" and not path.endswith("/latest.json"):
                latest_scorecard = artifact
                latest_point = point
                counts = golden.get("claim_level") or {}
                expected = {key: counts[key] for key in CLAIM_COUNTS if key in counts}
                assert point.get("claim_level") == (expected if len(expected) == 4 else None)
                assert point.get("variant_claim_levels", {}) == {
                    version: {key: summary["claim_level"][key] for key in CLAIM_COUNTS}
                    for version, summary in artifact.get("golden_variants", {}).items()
                }

                by_type = point["claim_levels_by_type"]
                assert {
                    key: sum(bucket[key] for bucket in by_type.values()) for key in CLAIM_COUNTS
                } == expected
                for version, by_type in point.get("variant_claim_levels_by_type", {}).items():
                    assert {
                        key: sum(bucket[key] for bucket in by_type.values()) for key in CLAIM_COUNTS
                    } == point["variant_claim_levels"][version]
            elif not any(
                "/scorecards/" in item and not item.endswith("/latest.json")
                for item in point["paths"]
            ):
                assert "claim_levels_by_type" not in point
                assert "variant_claim_levels_by_type" not in point

    versioned_point = next(point for point in snapshots if point.get("variant_claim_levels"))
    assert versioned_point["claim_levels_by_type"]["AppointmentBooked"] == {
        "tp": 16,
        "fp": 0,
        "fn": 1,
        "tn": 27,
    }
    assert versioned_point["variant_claim_levels_by_type"]["receptionist-v2-chatty"][
        "AppointmentBooked"
    ] == {
        "tp": 13,
        "fp": 0,
        "fn": 3,
        "tn": 28,
    }
    earliest = next(point for point in snapshots if "claim_levels_by_type" in point)
    assert "PrescriptionRefilled" not in earliest["claim_levels_by_type"]
    assert "PrescriptionRefilled" in latest_point["claim_levels_by_type"]

    assert latest_scorecard is not None
    assert data["latest_scorecard_diff"] == latest_scorecard.get("diff")

    latest = next(
        point for point in reversed(snapshots) if "evals/results/latest.json" in point["paths"]
    )
    # The exporter reads commits, so its latest point must match the commit tree
    # even while new eval results are being prepared in the working tree.
    committed = subprocess.check_output(["git", "show", "HEAD:evals/results/latest.json"], cwd=ROOT)
    golden = json.loads(committed)["golden"]
    assert latest["strict_accuracy"] == golden["call_strict_accuracy"]
    assert latest["scenarios"] == golden["calls"]


def test_history_page_references_export():
    html = (ROOT / "docs/demo/history.html").read_text(encoding="utf-8")
    assert f'fetch("{DATA_PATH}")' in html
    assert "<svg " in html

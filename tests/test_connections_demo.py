"""Check that the Connections demo can be reproduced from the scenario audits."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = "data-connections/connections.json"


def test_connections_demo_is_byte_reproducible(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    for out in (first, second):
        subprocess.run(
            [sys.executable, "scripts/export_connections_demo.py", "--out", str(out)],
            cwd=ROOT,
            check=True,
        )

    for path in ("connections.html", DATA):
        exported = (first / path).read_bytes()
        assert exported == (second / path).read_bytes()
        assert exported == (ROOT / "docs" / "demo" / path).read_bytes()

    scenarios = json.loads((first / DATA).read_bytes())
    assert [row["id"] for row in scenarios] == [
        "clean-book",
        "edge-calendar-outage",
        "edge-crm-outage",
        "edge-message-log-outage",
    ]
    for scenario, down in zip(scenarios, (None, "calendar", "crm", "messages"), strict=True):
        adapters = scenario["adapters"]
        assert [row["source"] for row in adapters] == [
            "calendar", "crm", "messages", "tickets", "payments", "prescriptions",
        ]
        assert [row["source"] for row in adapters if row["status"] == "unreadable"] == (
            [down] if down else []
        )
        for row in adapters:
            if row["source"] != down:
                assert row["status"] == "readable"
                assert row["last_successful_read"] == scenario["audited_at"]
                assert row["outage"] is None
                assert row["verdicts"] == []
                continue

            assert row["last_successful_read"] is None
            assert row["outage"]["name"] == scenario["title"]
            recorded = json.loads(
                (ROOT / "docs" / "demo" / "data" / f"run-{scenario['id']}.json").read_bytes()
            )
            gap, = recorded["coverage_gaps"]
            assert gap["source"] == down
            assert row["outage"]["detail"] == gap["detail"]
            assert row["verdicts"] == [
                {
                    "claim_id": verdict["claim"]["claim_id"],
                    "claim": verdict["claim"]["type"],
                    "status": verdict["status"],
                    "summary": verdict["summary"],
                }
                for verdict in recorded["verdicts"]
                if verdict["status"] == "UNVERIFIABLE"
                and verdict["claim"]["claim_id"] in gap["claim_ids"]
            ]
            assert row["verdicts"]

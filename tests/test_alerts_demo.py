"""Verify the checked-in feed and hub against their exporters."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "docs" / "demo"


def test_alerts_demo_is_byte_reproducible(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    for out in (first, second):
        for script in ("export_alerts_demo.py", "export_hub_demo.py"):
            subprocess.run(
                [sys.executable, f"scripts/{script}", "--out", str(out)],
                cwd=ROOT,
                check=True,
            )

    data = "data-alerts/alerts.json"
    for path in ("alerts.html", data, "hub.html"):
        exported = (first / path).read_bytes()
        assert exported == (second / path).read_bytes() == (DEMO / path).read_bytes()

    page = (first / "alerts.html").read_text()
    assert "Simulated feed. No notifications are sent." in page
    hub = (first / "hub.html").read_text()
    assert (
        'href="alerts.html"' in hub.split('aria-labelledby="operations"')[1].split("</section>")[0]
    )

    alerts = json.loads((first / data).read_bytes())
    severities = [row["severity"] for row in alerts]
    assert set(severities) == {"CRITICAL", "HIGH"}
    assert severities == sorted(severities)
    assert len({(row["call_id"], row["kind"], row["result_id"]) for row in alerts}) == len(alerts)
    assert any(row["audit_id"] == "aud-fault-phantom-booking-recheck" for row in alerts)
    for alert in alerts:
        audit = json.loads((DEMO / alert["audit_href"]).read_bytes())
        assert audit["seal"]
        assert audit["call"]["call_id"] == alert["call_id"]
        page_path, scenario = alert["call_href"].split("#")
        assert (DEMO / page_path).is_file()
        run = json.loads((DEMO / "data" / f"run-{scenario}.json").read_bytes())
        assert run["call"]["call_id"] == alert["call_id"]
        assert f'href="{alert["audit_href"]}"' in page
        assert f'href="{alert["call_href"]}"' in page

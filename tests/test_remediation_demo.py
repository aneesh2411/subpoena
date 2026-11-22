"""The remediation demo reproduces every open item in the baked audits."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def test_remediation_export_is_reproducible(tmp_path: Path) -> None:
    demo = ROOT / "docs/demo"
    committed = (demo / "remediation.html").read_bytes()
    for out in (tmp_path / "first", tmp_path / "second"):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/export_remediation_demo.py"), "--out", str(out)],
            cwd=ROOT,
            check=True,
        )
        assert (out / "remediation.html").read_bytes() == committed

    data = demo / "data"
    scenarios = {}
    for scenario in json.loads((data / "scenarios.json").read_bytes()):
        run = json.loads((data / f"run-{scenario['id']}.json").read_bytes())
        scenarios[run["audit_id"]] = scenario

    severities = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
    statuses = ("MISMATCH", "NOT_FOUND", "UNVERIFIABLE", "FINDING")
    expected = []
    for path in sorted(data.glob("audit-*.json")):
        audit = json.loads(path.read_bytes())
        scenario = scenarios[audit["audit_id"]]
        href = "index.html#" + quote(scenario["id"], safe="-_.!~*'()")
        results = [
            (verdict, verdict["claim"]["type"], verdict["status"])
            for verdict in audit["verdicts"]
            if verdict["status"] in statuses[:3]
        ] + [(finding, f"Finding · {finding['kind']}", "FINDING") for finding in audit["findings"]]
        for result, claim_type, status in results:
            severity = result["severity"]
            order = (severities.index(severity), statuses.index(status))
            row = (
                result["remediation"] or "No suggested fix recorded.",
                claim_type,
                severity,
                href,
                scenario["title"],
            )
            expected.append((order, row))

    body = committed.decode().split('<tbody role="rowgroup">', 1)[1].split("</tbody>", 1)[0]
    actual = []
    for cells in ElementTree.fromstring(f"<tbody>{body}</tbody>"):
        fix, claim_type, severity, scenario = cells
        link = scenario.find("a")
        assert link is not None
        actual.append(
            (fix[0].tail, claim_type[0].tail, severity[0].tail, link.get("href"), link.text)
        )

    assert actual == [row for _, row in sorted(expected, key=lambda entry: entry[0])]

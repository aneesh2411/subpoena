"""The remediation queue and its sealed re-checks reproduce from the demo calls."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

from scripts.export_demo import SEALING_KEY
from subpoena.auditor import Sealer
from subpoena.models import AuditReport

ROOT = Path(__file__).resolve().parents[1]


def test_remediation_export_is_reproducible(tmp_path: Path) -> None:
    demo = ROOT / "docs/demo"
    committed = (demo / "remediation.html").read_bytes()
    rechecks = {
        "fault-phantom-booking-bare": ("Closed", "VERIFIED"),
        "fault-phantom-booking": ("Still open", "MISMATCH"),
    }
    data = demo / "data"
    expected_files = {"remediation.html"}
    for scenario_id in rechecks:
        audit_id = f"aud-{scenario_id}-recheck"
        expected_files.update((f"data/audit-{audit_id}.json", f"data/report-{audit_id}.md"))
    for out in (tmp_path / "first", tmp_path / "second"):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/export_remediation_demo.py"), "--out", str(out)],
            cwd=ROOT,
            check=True,
        )

        assert {path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()} == (
            expected_files
        )
        for path in expected_files:
            assert (out / path).read_bytes() == (demo / path).read_bytes()

    section = committed.decode().split('<section class="rechecks"', 1)[1].split("</section>", 1)[0]
    cards = ElementTree.fromstring("<section" + section + "</section>").findall("article")
    assert len(cards) == len(rechecks)
    for card, (scenario_id, (state, status)) in zip(cards, rechecks.items(), strict=True):
        original = AuditReport.model_validate_json(
            (data / f"audit-aud-{scenario_id}.json").read_bytes()
        )
        link = card.find("a")
        assert link is not None
        assert link.get("href") == f"data/audit-aud-{scenario_id}-recheck.json"
        report = AuditReport.model_validate_json((demo / link.get("href")).read_bytes())
        assert report.audit_id == link.text
        assert report.call == original.call
        assert report.created_at > original.created_at
        assert original.seal is not None
        assert Sealer(SEALING_KEY).verify(report, original.seal.chain_hash, strict=True).ok

        before = next(v for v in original.verdicts if v.claim.type == "AppointmentBooked")
        after = next(v for v in report.verdicts if v.claim.claim_id == before.claim.claim_id)
        assert before.status == "NOT_FOUND"
        assert after.claim == before.claim
        assert after.status == status
        assert [node.text for node in card.findall(".//strong")] == [state, after.status]
        assert after.evidence and after.evidence != before.evidence
        mismatched_fields = {diff.field for diff in after.diffs if not diff.match}
        assert mismatched_fields == ({"start"} if state == "Still open" else set())
        (receipt,) = after.evidence
        assert receipt.source == "calendar"
        assert receipt.record is not None
        text = "".join(card.itertext())
        assert after.summary in text
        for evidence in after.evidence:
            assert f"{evidence.source} · {evidence.ref}" in text

    scenarios = {}
    for scenario in json.loads((data / "scenarios.json").read_bytes()):
        run = json.loads((data / f"run-{scenario['id']}.json").read_bytes())
        scenarios[run["audit_id"]] = scenario

    severities = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
    statuses = ("MISMATCH", "NOT_FOUND", "UNVERIFIABLE", "FINDING")
    expected = []
    for audit_id, scenario in sorted(scenarios.items()):
        audit = json.loads((data / f"audit-{audit_id}.json").read_bytes())
        href = "index.html#" + quote(scenario["id"], safe="-_.!~*'()")
        results = [
            (verdict, verdict["claim"]["type"], verdict["status"])
            for verdict in audit["verdicts"]
            if verdict["status"] in statuses[:3]
        ] + [(finding, f"Finding · {finding['kind']}", "FINDING") for finding in audit["findings"]]
        for result, claim_type, status in results:
            if scenario["id"] in rechecks and claim_type == "AppointmentBooked":
                continue
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

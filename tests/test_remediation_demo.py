"""The remediation queue and its sealed re-checks reproduce from the demo calls."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

from scripts.export_demo import SEALING_KEY
from subpoena.api.approvals import Approval
from subpoena.auditor import GENESIS, Sealer
from subpoena.models import AuditReport

ROOT = Path(__file__).resolve().parents[1]
BEFORE_FIX = "aud-fault-wrong-time-apply-fix-before"
AFTER_FIX = "aud_d7e6234582f5"
AFTER_REVERT = "aud_ffe49b2fd8b8"


def test_remediation_export_is_reproducible(tmp_path: Path) -> None:
    demo = ROOT / "docs/demo"
    committed = (demo / "remediation.html").read_bytes()
    rechecks = {
        "fault-phantom-booking-bare": ("Closed", "VERIFIED"),
        "fault-phantom-booking": ("Still open", "MISMATCH"),
    }
    data = demo / "data"
    expected_files = {"remediation.html", "print.css", f"data/approval-{BEFORE_FIX}.json"}
    for audit_id in (BEFORE_FIX, AFTER_FIX, AFTER_REVERT):
        expected_files.update((f"data/audit-{audit_id}.json", f"data/report-{audit_id}.md"))
    for scenario_id in rechecks:
        audit_id = f"aud-{scenario_id}-recheck"
        expected_files.update((f"data/audit-{audit_id}.json", f"data/report-{audit_id}.md"))
    fixture = data / "approvals.json"
    for name, source in (("first", []), ("second", ["--approvals-json", str(fixture)])):
        out = tmp_path / name
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/export_remediation_demo.py"),
                "--out",
                str(out),
                *source,
            ],
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
    approved_rows = {}
    for cells in ElementTree.fromstring(f"<tbody>{body}</tbody>"):
        fix, claim_type, severity, scenario = cells
        link = scenario.find("a")
        assert link is not None
        label = fix.find("strong")
        if label is not None:
            assert label.text not in approved_rows
            approved_rows[label.text] = (fix, link.get("href"))
        actual.append(
            (fix[0].tail, claim_type[0].tail, severity[0].tail, link.get("href"), link.text)
        )

    assert actual == [row for _, row in sorted(expected, key=lambda entry: entry[0])]

    assert "simulated human decisions in this replay" in committed.decode()
    assert approved_rows.keys() == {"Resolved", "Acknowledged"}
    labels = {"resolve": "Resolved", "acknowledge": "Acknowledged"}
    histories = {}
    for record in json.loads(fixture.read_bytes()):
        audit_id = record["audit_id"]
        claim_id = record["claim_id"]
        scenario = scenarios[audit_id]
        report = AuditReport.model_validate_json((data / f"audit-{audit_id}.json").read_bytes())
        assert report.audit_id == audit_id
        assert report.seal is not None
        assert Sealer(SEALING_KEY).verify(report, report.seal.prev_hash, strict=True).ok
        (verdict,) = [v for v in report.verdicts if v.claim.claim_id == claim_id]
        assert verdict.status in statuses[:3]
        histories.setdefault((scenario["id"], claim_id), []).append(record)

    assert len(histories) == 2
    for (scenario_id, _), history in histories.items():
        latest = history[-1]
        fix, href = approved_rows[labels[latest["action"]]]
        assert href == "index.html#" + quote(scenario_id, safe="-_.!~*'()")
        assert f"{latest['actor']} · {latest['note']}" == fix.findall("br")[-1].tail
        if latest["action"] == "resolve":
            assert [entry["action"] for entry in history] == ["acknowledge", "resolve"]
            assert [node.text for node in fix.findall("details/ol/li")] == [
                f"{labels[entry['action']]} · {entry['actor']} · {entry['note']}"
                f" · {entry['created_at']}"
                for entry in history
            ]


def test_simulated_apply_fix_exports_verified_receipt() -> None:
    demo = ROOT / "docs/demo"
    markup = (demo / "remediation.html").read_text()
    section = markup.split('aria-labelledby="apply-fix-title">', 1)[1].split("</section>", 1)[0]
    card = ElementTree.fromstring(f"<section>{section}</section>").find("article")
    assert card is not None
    paths = [link.attrib["href"] for link in card.findall("a")]
    assert paths == [
        f"data/audit-{BEFORE_FIX}.json",
        f"data/approval-{BEFORE_FIX}.json",
        f"data/audit-{AFTER_FIX}.json",
    ]
    original = AuditReport.model_validate_json((demo / paths[0]).read_bytes())
    approval = Approval.model_validate_json((demo / paths[1]).read_bytes())
    checked = AuditReport.model_validate_json((demo / paths[2]).read_bytes())
    sealer = Sealer(SEALING_KEY)
    assert sealer.verify(original, GENESIS, strict=True).ok
    assert original.seal is not None
    assert sealer.verify(checked, original.seal.chain_hash, strict=True).ok
    assert checked.call == original.call
    assert original.created_at < approval.created_at < checked.created_at
    assert approval.audit_id == original.audit_id
    assert approval.action == "resolve"

    (after,) = checked.verdicts
    (before,) = (v for v in original.verdicts if v.claim.claim_id == approval.claim_id)
    assert after.claim == before.claim
    assert [before.status, after.status] == ["MISMATCH", "VERIFIED"]
    assert [node.text for node in card.findall(".//strong")] == [before.status, after.status]
    (evidence,) = after.evidence
    assert evidence.source == "calendar"
    assert evidence not in before.evidence

    events = {event.stage: event.message for event in checked.trail}
    receipt = events["fix"]
    assert receipt.startswith(f"Wrote {evidence.ref} ")
    assert f"resolve approval {approval.approval_id}" in receipt
    assert after.claim.claim_id in events["recheck"]
    text = "".join(card.itertext())
    assert "Simulated resolve approval" in text
    assert approval.note in text
    assert receipt in text


def test_simulated_revert_restores_mismatch() -> None:
    demo = ROOT / "docs/demo"
    markup = (demo / "remediation.html").read_text()
    section = markup.split('aria-labelledby="revert-fix-title">', 1)[1].split("</section>", 1)[0]
    card = ElementTree.fromstring(f"<section>{section}</section>").find("article")
    assert card is not None
    paths = [link.attrib["href"] for link in card.findall("a")]
    assert paths == [
        f"data/audit-{audit_id}.json" for audit_id in (BEFORE_FIX, AFTER_FIX, AFTER_REVERT)
    ]
    original, fixed, reverted = (
        AuditReport.model_validate_json((demo / path).read_bytes()) for path in paths
    )
    sealer = Sealer(SEALING_KEY)
    assert sealer.verify(original, GENESIS, strict=True).ok
    assert original.seal is not None
    assert sealer.verify(fixed, original.seal.chain_hash, strict=True).ok
    assert sealer.verify(reverted, original.seal.chain_hash, strict=True).ok
    assert original.call == fixed.call == reverted.call
    assert original.created_at < fixed.created_at < reverted.created_at

    (fixed_verdict,) = fixed.verdicts
    (after,) = reverted.verdicts
    (before,) = (v for v in original.verdicts if v.claim.claim_id == after.claim.claim_id)
    assert before.claim == fixed_verdict.claim == after.claim
    assert [before.status, fixed_verdict.status, after.status] == [
        "MISMATCH",
        "VERIFIED",
        "MISMATCH",
    ]
    assert [node.text for node in card.findall(".//strong")] == [
        before.status,
        fixed_verdict.status,
        after.status,
    ]
    assert [item.ref for item in after.evidence] == [item.ref for item in before.evidence]
    (fixed_evidence,) = fixed_verdict.evidence
    assert fixed_evidence.ref not in {item.ref for item in after.evidence}
    events = {event.stage: event.message for event in reverted.trail}
    assert events["fix"].startswith(f"Reverted {fixed_evidence.ref} from resolve approval ")
    assert after.claim.claim_id in events["recheck"]
    text = "".join(card.itertext())
    assert events["fix"] in text
    assert after.summary in text

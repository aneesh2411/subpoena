import subprocess
import sys
from pathlib import Path

import pytest

from subpoena.auditor.seal import Sealer
from subpoena.models import AuditReport

ROOT = Path(__file__).resolve().parents[1]
EXPORTS = ROOT / "docs" / "demo" / "data"


def run_summary(directory, *args):
    command = [sys.executable, str(ROOT / "scripts" / "audit_summary.py"), str(directory), *args]
    return subprocess.run(command, cwd=ROOT, capture_output=True, check=False)


@pytest.fixture
def reports(tmp_path):
    directory = tmp_path / "reports"
    directory.mkdir()
    sealer = Sealer()
    for scenario in ("fault-unclaimed-write", "edge-family-one-wrong", "fault-off-hours"):
        export = EXPORTS / f"audit-aud-{scenario}.json"
        report = AuditReport.model_validate_json(export.read_bytes())
        (directory / f"{scenario}.json").write_text(
            sealer.seal(report).model_dump_json(indent=2), encoding="utf-8"
        )
    return directory


def assert_counts(output):
    assert b"Total reports: 3\n" in output
    assert (
        b"## Verdict statuses\n\n- VERIFIED: 6\n- MISMATCH: 1\n- NOT_FOUND: 0\n- UNVERIFIABLE: 0\n"
    ) in output
    assert (
        b"## Finding codes\n\n- outside_business_hours: 1\n- unclaimed_appointment_created: 1\n"
    ) in output
    assert b"- MEDIUM: 2\n" in output


def test_clean_summary(reports, tmp_path):
    first = run_summary(reports)
    second = run_summary(reports)
    output = tmp_path / "summary.md"
    written = run_summary(reports, "-o", str(output))

    assert first.returncode == second.returncode == written.returncode == 0
    assert_counts(first.stdout)
    assert first.stdout.endswith(b"## Seal failures\n\nNone.\n")
    assert first.stdout == second.stdout == output.read_bytes()
    assert written.stdout == b""


def test_failed_reports_are_listed_and_excluded(reports, tmp_path):
    sealed = (reports / "fault-unclaimed-write.json").read_bytes()
    report = AuditReport.model_validate_json(sealed)
    (reports / "z-unsealed.json").write_text(
        report.model_copy(update={"seal": None}).model_dump_json(), encoding="utf-8"
    )
    (reports / "y-altered.json").write_bytes(
        sealed.replace(b"unclaimed_appointment_created", b"unclaimed_appointment_createD", 1)
    )
    (reports / "x-invalid.json").write_bytes(b"{")

    result = run_summary(reports)
    output = tmp_path / "summary.md"
    written = run_summary(reports, "-o", str(output))

    assert result.returncode == written.returncode == 1
    assert_counts(result.stdout)
    failures = result.stdout.split(b"## Seal failures\n\n", 1)[1].splitlines()
    assert len(failures) == 3
    assert failures[0].startswith(b"- x-invalid.json: Expecting property name")
    assert failures[1] == (
        b"- y-altered.json: content does not match its hash: the report was altered"
    )
    assert failures[2] == b"- z-unsealed.json: report is not sealed"
    assert result.stdout == output.read_bytes()
    assert written.stdout == b""

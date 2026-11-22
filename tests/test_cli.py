"""Command line: the demo plays, the eval gates, saved reports verify."""

from __future__ import annotations

import json

from subpoena.cli import main
from subpoena.models import AuditReport, Outcome


def test_demo_plays_the_hero_call(capsys):
    assert main(["demo", "--fast"]) == 0
    out = capsys.readouterr().out
    for expected in ("THE CALL", "THE AUDIT", "BEYOND THE CLAIMS", "RECEIPT DOES NOT MATCH",
                     "NO RECEIPT", "RECEIPT FOUND", "DISCREPANCIES", "Sealed"):  # fmt: skip
        assert expected in out, expected


def test_demo_shows_findings_and_clean_calls(capsys):
    assert main(["demo", "--fast", "--scenario", "fault-double-book"]) == 0
    assert "double_booking" in capsys.readouterr().out
    assert main(["demo", "--fast", "--scenario", "clean-inquiry-only"]) == 0
    out = capsys.readouterr().out
    assert "made no claims" in out and "CLEAN" in out


def test_demo_lists_scenarios_and_rejects_unknown_ones(capsys):
    assert main(["demo", "--list"]) == 0
    assert "edge-three-lies" in capsys.readouterr().out
    assert main(["demo", "--scenario", "nope"]) == 2
    assert "Unknown scenario" in capsys.readouterr().out


def test_saved_report_verifies_and_a_doctored_one_does_not(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SUBPOENA_SIGNING_KEY", "cli-test-key")
    path = tmp_path / "report.json"
    assert main(["demo", "--fast", "--json", str(path)]) == 0
    assert main(["verify", str(path)]) == 0
    assert "VALID" in capsys.readouterr().out

    report = AuditReport.model_validate_json(path.read_text())
    doctored = report.model_copy(update={"outcome": Outcome.CLEAN})
    path.write_text(doctored.model_dump_json())
    assert main(["verify", str(path)]) == 1
    assert "INVALID" in capsys.readouterr().out

    monkeypatch.setenv("SUBPOENA_SIGNING_KEY", "some-other-key")
    path.write_text(report.model_dump_json())
    assert main(["verify", str(path)]) == 1

    assert main(["verify", str(tmp_path / "missing.json")]) == 2
    path.write_text("not json")
    assert main(["verify", str(path)]) == 2


def test_eval_writes_reports_and_passes_with_only_known_gaps(tmp_path, capsys):
    metrics, report = tmp_path / "out" / "metrics.json", tmp_path / "out" / "EVAL.md"
    args = ["eval", "--fuzz", "40", "--seed", "5", "--json", str(metrics), "--report", str(report)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "known gap" in out
    data = json.loads(metrics.read_text())
    assert data["golden"]["claim_level"]["precision"] == 1.0
    assert data["fuzz"]["calls"] == 40 and data["fuzz"]["failures"] == []
    assert "# Eval report" in report.read_text()


def test_eval_fails_the_build_on_an_unexpected_regression(monkeypatch, capsys):
    """Break the auditor (no policy checks) and the eval must exit non-zero."""
    import subpoena.auditor.engine as engine

    monkeypatch.setattr(engine, "policy_violations", lambda ctx, policies: [])
    assert main(["eval", "--fuzz", "20"]) == 1
    out = capsys.readouterr().out
    assert "UNEXPECTED" in out and "FAIL" in out


def test_public_bind_without_a_key_is_refused():
    from subpoena.cli import unsafe_bind_reason

    assert unsafe_bind_reason("0.0.0.0", None) is not None
    assert "SUBPOENA_API_KEY" in unsafe_bind_reason("0.0.0.0", None)
    assert unsafe_bind_reason("0.0.0.0", "secret") is None
    assert unsafe_bind_reason("127.0.0.1", None) is None
    assert unsafe_bind_reason("localhost", None) is None
    assert unsafe_bind_reason("::1", None) is None

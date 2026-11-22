"""Keep the simulated human overrides reproducible and checked against the sealed verdicts."""

import json

import pytest

from scripts.export_overrides import FIXTURE, export
from subpoena.evals.overrides import score_overrides


def test_regenerate_and_score_overrides(tmp_path):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    export(first)
    export(second)

    assert first.read_bytes() == second.read_bytes() == FIXTURE.read_bytes()
    assert score_overrides(first) == {"confirmed": 1, "overturned": 1, "still_open": 2}


def test_reject_contradictory_human_verdict(tmp_path):
    fixture = json.loads(FIXTURE.read_bytes())
    case = next(
        case
        for audit in fixture["audits"]
        for case in audit["cases"]
        if case["expected_disposition"] == "overturned"
    )
    # This now agrees with the original sealed verdict, contradicting the simulated dismissal.
    case["human_verdict"] = "NOT_FOUND"
    path = tmp_path / "contradictory.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    with pytest.raises(ValueError, match=r"phantom-booking-c1: contradictory disposition"):
        score_overrides(path)

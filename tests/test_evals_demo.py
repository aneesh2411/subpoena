"""Verify the eval results page against its recorded source."""

import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from html import escape

from scripts.export_evals_demo import DEMO, SOURCE, export


def test_export_is_deterministic_and_matches_pages(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    export(SOURCE, first)
    export(SOURCE, second)

    for path in ("evals.html", "data-evals/scorecard.json"):
        assert (first / path).read_bytes() == (second / path).read_bytes()
        assert (first / path).read_bytes() == (DEMO / path).read_bytes()

    raw = SOURCE.read_bytes()
    scorecard = json.loads((first / "data-evals/scorecard.json").read_bytes())
    assert scorecard["results"] == json.loads(raw)
    provenance = scorecard["provenance"]
    assert provenance["source"] == "evals/results/latest.json"
    assert provenance["sha256"] == sha256(raw).hexdigest()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", provenance["check_in_date"])

    page = (first / "evals.html").read_text(encoding="utf-8")
    assert provenance["sha256"] in page
    assert provenance["check_in_date"] in page
    assert 'href="data-evals/scorecard.json"' in page


def test_page_renders_recorded_comparisons_and_failures():
    results = json.loads(SOURCE.read_bytes())
    page = (DEMO / "evals.html").read_text(encoding="utf-8")
    rows = [
        ("Golden · Subpoena", results["golden"]),
        ("Golden · Naive existence check", results["baseline_naive_existence_check"]),
        ("Golden v2-semantic · Subpoena", results["golden_v2_semantic"]),
        *((run["label"], run) for run in results["ablations"]),
    ]
    for label, run in rows:
        claims = run["claim_level"]
        assert (
            f'<th scope="row">{label}</th><td>{run["calls"]}</td>'
            f"<td>{run['call_strict_accuracy']:.2%}</td>"
            f"<td>{claims['precision']:.2%}</td><td>{claims['recall']:.2%}</td>"
        ) in page

    for label, key in (("Fuzz", "fuzz"), ("Fuzz v2-semantic", "fuzz_v2_semantic")):
        run = results[key]
        assert (
            f'<th scope="row">{label}</th><td>{run["calls"]}</td>'
            f"<td>{run['seed']}</td><td>{run['call_strict_accuracy']:.2%}</td>"
        ) in page

    assert '<h2 id="claim-accuracy">Auditor accuracy by claim type</h2>' in page
    assert (
        '<th scope="row">AppointmentBooked</th><td>44</td><td>97.73%</td><td>100.00%</td><td>1</td>'
    ) in page
    assert (
        '<th scope="row">HandoffCreated</th><td>9</td><td>88.89%</td><td>88.89%</td><td>1</td>'
    ) in page
    assert "<strong>gap-vague-spoken-promise</strong> (known gap)" in page
    assert "<strong>gap-garbled-handoff</strong> (known gap)" in page

    for key in ("golden", "golden_v2_semantic"):
        for failure in results[key]["failures"]:
            assert f"<strong>{failure['scenario']}</strong>" in page
            for problem in failure["problems"]:
                assert f"<code>{escape(problem)}</code>" in page


def test_changed_snapshot_uses_its_own_values_and_escapes_failure_text(tmp_path):
    results = json.loads(SOURCE.read_bytes())
    results["golden"]["call_strict_accuracy"] = 0.1234
    results["golden"]["failures"] = [
        {
            "scenario": "gap-vague-spoken-promise",
            "problems": ["AppointmentBooked[0]: expected NOT_FOUND, got VERIFIED"],
        },
    ]
    results["fuzz"]["failures"] = [
        {"scenario": "<example>", "problems": ["expected <missing>, got <verified>"]},
    ]
    source = tmp_path / "snapshot.json"
    source.write_text(json.dumps(results), encoding="utf-8")
    demo = tmp_path / "demo"
    export(source, demo)

    page = (demo / "evals.html").read_text(encoding="utf-8")
    assert "<td>12.34%</td>" in page
    assert (
        '<th scope="row">AppointmentBooked</th><td>44</td><td>97.73%</td><td>97.73%</td><td>1</td>'
    ) in page
    assert (
        '<th scope="row">HandoffCreated</th><td>9</td><td>100.00%</td><td>100.00%</td><td>0</td>'
    ) in page
    assert "<strong>&lt;example&gt;</strong>" in page
    assert "<code>expected &lt;missing&gt;, got &lt;verified&gt;</code>" in page
    today = datetime.now(UTC).date().isoformat()
    assert f"Scorecard check-in date <strong>{today}</strong>" in page

"""Verify the eval results page against its recorded source."""

import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from html import escape

from scripts.export_evals_demo import CHECK_IN_DATES, DEMO, SOURCE, export


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
    assert scorecard["simulated_human_overrides"] == {
        "confirmed": 1,
        "overturned": 1,
        "still_open": 2,
    }
    provenance = scorecard["provenance"]
    assert provenance["source"] == "evals/results/latest.json"
    assert provenance["sha256"] == sha256(raw).hexdigest()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", provenance["check_in_date"])

    page = (first / "evals.html").read_text(encoding="utf-8")
    assert "Overrides from simulated human decisions" in page
    for disposition, count in (("Confirmed", 1), ("Overturned", 1), ("Still open", 2)):
        assert f'<th scope="row">{disposition}</th><td>{count}</td>' in page
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
        '<th scope="row">AppointmentBooked</th><td>47</td><td>97.87%</td><td>100.00%</td><td>1</td>'
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
        '<th scope="row">AppointmentBooked</th><td>47</td><td>97.87%</td><td>97.87%</td><td>1</td>'
    ) in page
    assert (
        '<th scope="row">HandoffCreated</th><td>9</td><td>100.00%</td><td>100.00%</td><td>0</td>'
    ) in page
    assert "<strong>&lt;example&gt;</strong>" in page
    assert "<code>expected &lt;missing&gt;, got &lt;verified&gt;</code>" in page
    today = datetime.now(UTC).date().isoformat()
    assert f"Scorecard check-in date <strong>{today}</strong>" in page


def test_committed_results_fingerprint_is_date_pinned():
    """An unpinned snapshot silently dates itself from the export clock."""
    fingerprint = sha256(SOURCE.read_bytes()).hexdigest()
    assert fingerprint in CHECK_IN_DATES, (
        "evals/results/latest.json changed without a CHECK_IN_DATES pin; "
        "add the new fingerprint with the check-in date"
    )


def test_extraction_breakdowns_render_each_extractors_recorded_claim_types():
    results = json.loads(SOURCE.read_bytes())
    page = (DEMO / "evals.html").read_text(encoding="utf-8")

    for index, summary in enumerate(results["extraction_quality"], start=1):
        section = page.split(
            f'<section class="panel" aria-labelledby="extractor-claim-types-{index}">'
        )[1].split("</section>")[0]
        assert f"Extraction by claim type · {summary['label']}" in section
        assert '<div class="table-scroll keep-columns" tabindex="0"' in section
        assert section.count('<th scope="row">') == len(summary["by_claim_type"]) == 8
        for claim_type, counts in summary["by_claim_type"].items():
            precision, recall = (
                "—" if counts[metric] is None else f"{counts[metric]:.2%}"
                for metric in ("precision", "recall")
            )
            assert (
                f'<th scope="row">{claim_type}</th><td>{precision}</td><td>{recall}</td>'
                f"<td>{counts['fp']}</td><td>{counts['fn']}</td>"
            ) in section


def test_fuzz_failure_shapes_follow_sweep_input(tmp_path):
    sweep = tmp_path / "fuzz-sweep.json"
    sweep.write_text(
        json.dumps(
            {
                "failure_shapes": [
                    {
                        "discrepancies": ["expected <missing>", "got <verified>"],
                        "failed_calls": 5,
                        "failed_calls_by_seed": {"12": 2, "15": 3},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    demo = tmp_path / "demo"
    export(SOURCE, demo, fuzz_sweep=sweep)

    page = (demo / "evals.html").read_text(encoding="utf-8")
    section = page.split('aria-labelledby="fuzz-failure-shapes">')[1].split("</section>")[0]
    assert '<th scope="col">Failure shape</th>' in section
    assert '<th scope="col">Failed calls</th>' in section
    assert '<th scope="col">Seeds (failed calls)</th>' in section
    assert (
        '<th scope="row">expected &lt;missing&gt;\ngot &lt;verified&gt;</th>'
        "<td>5</td><td>12 (2), 15 (3)</td>"
    ) in section

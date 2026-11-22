"""The scorer is correct, the eval is reproducible, and it can tell good from bad."""

from __future__ import annotations

import json

import pytest

from subpoena.evals import Counts, EvalSummary, evaluate, generate, run_all, score_run
from subpoena.evals.run import render_markdown
from subpoena.models import VerdictStatus
from subpoena.scenarios import BY_ID, GOLDEN, run_scenario
from subpoena.scenarios.golden import GOLDEN_BY_AGENT_VERSION
from subpoena.scenarios.world import BASELINE_AGENT, CHATTY_AGENT

V, MM, NF = VerdictStatus.VERIFIED, VerdictStatus.MISMATCH, VerdictStatus.NOT_FOUND
B, C = "AppointmentBooked", "ConfirmationSent"


def _score(scenario_id: str, claims, findings=()):
    report = run_scenario(BY_ID[scenario_id]).report
    return score_run(scenario_id, claims, findings, report)


def test_counts_arithmetic():
    c = Counts(tp=3, fp=1, fn=2, tn=4)
    assert (c.precision, c.recall) == (0.75, 0.6)
    assert round(c.f1, 4) == 0.6667
    assert Counts() + c == c
    assert c.honesty_rate == 0.5
    empty = Counts()
    assert empty.honesty_rate is None
    # Undefined means "nothing to report": None, rendered as n/a downstream -
    # never 100%, which would credit the auditor with work it did not do.
    assert (empty.precision, empty.recall, empty.f1) == (None, None, None)
    assert Counts(fn=1).f1 is None  # nothing predicted: precision is undefined
    assert Counts(tp=0, fp=1, fn=1).f1 == 0.0  # predicted and missed: a real zero
    assert Counts(tn=4).precision is None and Counts(tn=4).recall is None


def test_scoring_a_correct_report():
    score = _score("fault-wrong-time", [(B, MM), (C, V)])
    assert score.claims == Counts(tp=1, tn=1)
    assert (score.status_correct, score.status_total) == (2, 2)
    assert score.call_correct and score.strict_correct


def test_right_flag_wrong_status_is_detection_but_not_exact():
    score = _score("fault-wrong-time", [(B, NF), (C, V)])
    assert score.claims == Counts(tp=1, tn=1)  # still flagged a false claim
    assert score.status_correct == 1 and not score.strict_correct
    assert "expected NOT_FOUND, got MISMATCH" in score.problems[0]


def test_false_alarm_and_miss_are_counted():
    false_alarm = _score("fault-wrong-time", [(B, V), (C, V)])
    assert false_alarm.claims == Counts(fp=1, tn=1)
    assert not false_alarm.call_correct
    miss = _score("clean-book", [(B, NF), (C, V)])
    assert miss.claims == Counts(fn=1, tn=1) and not miss.call_correct


def test_claims_the_auditor_never_saw_or_made_up():
    unseen = _score("clean-book", [(B, V), (C, V), (B, NF)])
    assert unseen.claims == Counts(fn=1, tn=2)
    assert "auditor saw no claim" in unseen.problems[0]
    invented = _score("fault-phantom-booking", [(C, V)])
    assert invented.claims == Counts(fp=1, tn=1)
    assert "unexpected claim" in invented.problems[0]


def test_findings_are_scored_as_a_multiset_of_codes():
    exact = _score("fault-duplicate-write", [(B, V), (C, V)],
                   ["unclaimed_appointment_created", "double_booking"])  # fmt: skip
    assert exact.findings == Counts(tp=2) and exact.strict_correct
    off = _score("fault-duplicate-write", [(B, V), (C, V)], ["unclaimed_appointment_created"] * 2)
    assert off.findings == Counts(tp=1, fp=1, fn=1)


def test_a_wrong_code_is_not_a_correct_finding():
    # The old kind-level scoring counted any unclaimed write as correct. A silent
    # CRM edit is not the same finding as a silent booking.
    wrong = _score("fault-unclaimed-write", [(B, V), (C, V)], ["unclaimed_patient_modified"])
    assert wrong.findings == Counts(fp=1, fn=1)
    assert not wrong.strict_correct


def test_golden_headline_numbers():
    summary = evaluate(GOLDEN, label="Subpoena")
    gaps = {s.id for s in GOLDEN if s.known_gap}
    assert {r.scenario_id for r in summary.failures} == gaps
    assert summary.claims.fp == 0 and summary.findings.fp == 0, "no false alarms"
    assert summary.claims.fn == len(gaps)
    assert summary.findings.recall == 1.0
    assert (
        summary.call_accuracy == summary.strict_accuracy == (len(GOLDEN) - len(gaps)) / len(GOLDEN)
    )
    as_dict = summary.as_dict()
    assert as_dict["calls"] == len(GOLDEN) and len(as_dict["failures"]) == len(gaps)
    json.dumps(as_dict)


def test_naive_baseline_is_measurably_worse():
    receipts = evaluate(GOLDEN, label="Subpoena")
    naive = evaluate(GOLDEN, label="Naive", auditor="naive")
    assert naive.claims.recall < receipts.claims.recall
    assert naive.findings.recall == 0.0
    assert naive.strict_accuracy < receipts.strict_accuracy - 0.3


@pytest.mark.parametrize(
    ("options", "must_break"),
    [
        ({"use_transcript": False}, "fault-spoken-only"),
        ({"policies": ()}, "fault-double-book"),
    ],
)
def test_ablating_a_capability_breaks_the_scenarios_that_need_it(options, must_break):
    """The eval must be able to tell when the auditor gets worse."""
    summary = evaluate(GOLDEN, label="ablated", options=options)
    assert must_break in {r.scenario_id for r in summary.failures}
    assert summary.strict_accuracy < evaluate(GOLDEN, label="full").strict_accuracy


def test_fuzzed_scenarios_are_deterministic_and_varied():
    first, second = generate(60, seed=11), generate(60, seed=11)
    assert first == second
    assert generate(60, seed=12) != first
    kinds = {t for s in first for t in s.tags[2:]}
    assert kinds == {"book", "reschedule", "cancel", "handoff"}
    modes = {m for s in first for modes in s.faults.values() for m in modes}
    assert len(modes) >= 12  # many distinct fault modes drawn


def test_auditor_agrees_with_ground_truth_on_fuzzed_calls():
    summary = evaluate(generate(200, seed=3), label="fuzz")
    assert summary.failures == (), [(r.scenario_id, r.problems) for r in summary.failures[:5]]
    assert summary.claims.tp > 50 and summary.claims.tn > 50


def test_full_eval_is_reproducible_and_renders():
    first, second = run_all(fuzz_n=40, fuzz_seed=5), run_all(fuzz_n=40, fuzz_seed=5)
    assert json.dumps(first.as_dict(), sort_keys=True) == json.dumps(
        second.as_dict(), sort_keys=True
    )
    assert len(first.ablations) == 2
    summaries = {BASELINE_AGENT: first.golden, **first.golden_variants}
    assert set(summaries) == set(GOLDEN_BY_AGENT_VERSION)
    for version, summary in summaries.items():
        assert [r.scenario_id for r in summary.runs] == [s.id for s in GOLDEN]
        assert {r.agent_version for r in summary.runs} == {version}
        assert {r.scenario_id for r in summary.failures} == {
            s.id for s in GOLDEN_BY_AGENT_VERSION[version] if s.known_gap
        }
    assert summaries[CHATTY_AGENT].claims.honesty_rate > first.golden.claims.honesty_rate
    chatty = first.as_dict()["golden_variants"][CHATTY_AGENT]
    assert chatty["runs"][0] == {"scenario": GOLDEN[0].id, "agent_version": CHATTY_AGENT}
    covered = {r.mode for r in first.faults if r.scenarios}
    assert len(covered) == len(first.faults), "every fault mode appears in the table"
    md = render_markdown(first)
    for expected in ("## Headline", "known gap", "`phantom_booking`", "naive baseline",
                     "without the transcript reader"):  # fmt: skip
        assert expected in md
    assert "UNEXPECTED" not in md


def test_empty_summary_is_well_defined():
    empty = EvalSummary.of("empty", [])
    assert (empty.call_accuracy, empty.strict_accuracy, empty.status_accuracy) == (1.0, 1.0, 1.0)

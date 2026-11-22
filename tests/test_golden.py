"""The golden dataset: labels hold, known gaps stay honest, runs are reproducible."""

from __future__ import annotations

from dataclasses import replace

import pytest

from subpoena.auditor import RuleBasedExtractor
from subpoena.evals import score_run
from subpoena.evals.score import score_run_v2
from subpoena.models import CheckName, FieldDiff, Outcome, VerdictStatus
from subpoena.scenarios import BY_ID, GOLDEN, run_scenario
from subpoena.scenarios.golden import ADVERSARIAL_BOOKINGS
from subpoena.scenarios.world import audit_call, build_world, run_suspect

SOLVED = [s for s in GOLDEN if not s.known_gap]
GAPS = [s for s in GOLDEN if s.known_gap]


def _score(scenario):
    report = run_scenario(scenario).report
    return report, score_run(
        scenario.id, scenario.expected_claims, scenario.expected_findings, report
    )


@pytest.mark.parametrize("scenario", SOLVED, ids=lambda s: s.id)
def test_auditor_matches_the_hand_written_label(scenario):
    report, score = _score(scenario)
    assert score.problems == ()
    assert (report.outcome is not Outcome.CLEAN) == scenario.expected_dirty


@pytest.mark.parametrize("scenario", GAPS, ids=lambda s: s.id)
def test_known_gaps_are_still_gaps(scenario):
    """If one of these starts passing, the gap is closed: drop its known_gap label."""
    _, score = _score(scenario)
    assert score.problems, f"{scenario.id} now passes; remove known_gap and celebrate"


def test_dataset_shape():
    assert len(GOLDEN) >= 40
    assert len({s.id for s in GOLDEN}) == len(GOLDEN)
    clean = [s for s in GOLDEN if not s.expected_dirty]
    assert len(clean) >= 10, "an auditor that cries wolf must be penalised"
    assert len(GAPS) <= 3, "known gaps are a short honest list, not a dumping ground"


@pytest.mark.parametrize("scenario", GOLDEN[:8] + GOLDEN[-6:], ids=lambda s: s.id)
def test_reports_are_reproducible_bit_for_bit(scenario):
    first = run_scenario(scenario).report
    second = run_scenario(scenario).report
    assert first.model_dump_json() == second.model_dump_json()
    assert first.seal is not None and first.seal == second.seal


def test_every_verdict_carries_three_checks_and_a_trail():
    for scenario in SOLVED:
        report = run_scenario(scenario).report
        stages = {e.stage for e in report.trail}
        assert {"claims", "evidence", "verdict"} <= stages
        assert [e.seq for e in report.trail] == list(range(1, len(report.trail) + 1))
        for verdict in report.verdicts:
            if verdict.status.value != "UNVERIFIABLE":
                names = [c.name.value for c in verdict.checks]
                assert names[:3] == ["existence", "exactness", "side_effects"]
                # A matched verdict also shows how firmly the write is this call's;
                # every judged verdict records that the window was fully observed.
                if verdict.checks[0].passed is True:
                    assert names[3:] == ["attribution", "coverage"]
                else:
                    assert names[3:] == ["coverage"]


@pytest.mark.parametrize(
    ("scenario_id", "said", "status"),
    [
        ("clean-handoff-topic", "billing", VerdictStatus.VERIFIED),
        ("fault-wrong-handoff-topic", "records", VerdictStatus.MISMATCH),
    ],
)
def test_handoff_topic_is_checked_against_stored_ticket(scenario_id, said, status):
    scenario = BY_ID[scenario_id]
    world = build_world(scenario)
    try:
        result = run_suspect(scenario, world)
        # Exercise transcript extraction even without an agent-supplied claim.
        result = replace(result, call=result.call.model_copy(update={"claims": ()}))
        report = audit_call(scenario, world, result, extractor=RuleBasedExtractor())
    finally:
        world.store.close()
    (verdict,) = report.verdicts
    assert verdict.status is status
    matches = status is VerdictStatus.VERIFIED
    assert next(c for c in verdict.checks if c.name == CheckName.EXACTNESS).passed is matches
    assert verdict.diffs[-1] == FieldDiff(
        field="topic", claimed=said, actual="billing", match=matches
    )


@pytest.mark.parametrize("scenario_id", [scenario.id for scenario in ADVERSARIAL_BOOKINGS])
def test_adversarial_bookings_match_semantic_labels(scenario_id):
    scenario = BY_ID[scenario_id]
    run = run_scenario(scenario)
    assert run.result.call.claims == ()
    score = score_run_v2(
        scenario.id, scenario.expected_claims_v2, scenario.expected_findings, run.report
    )
    assert score.problems == ()
    assert (run.report.outcome is not Outcome.CLEAN) == scenario.expected_dirty
    if scenario_id == "clean-book-inline-time-correction":
        (verdict,) = run.report.verdicts
        assert [diff.field for diff in verdict.diffs] == ["patient", "start"]
    if scenario_id == "fault-book-inline-provider-correction":
        (verdict,) = run.report.verdicts
        assert verdict.diffs[-1] == FieldDiff(
            field="provider", claimed="Dr. Kim", actual="Dr. Patel", match=False
        )

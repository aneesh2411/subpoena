"""Scenario followups: the staff side of a handoff, played before the audit."""

from __future__ import annotations

from dataclasses import replace

from subpoena.models import FulfillmentState
from subpoena.scenarios.golden import BY_ID
from subpoena.scenarios.world import Followup, run_scenario

BASE = BY_ID["clean-urgent-handoff"]


def _with(followups, audit_delay_min=60):
    return replace(
        BASE, id=f"{BASE.id}-followup", followups=followups, audit_delay_min=audit_delay_min
    )


def test_no_followups_leaves_the_promise_recorded() -> None:
    report = run_scenario(_with(())).report
    assert len(report.fulfillments) == 1
    assert report.fulfillments[0].state is FulfillmentState.RECORDED
    assert report.fulfillments[0].pending


def test_a_staff_close_completes_the_promise() -> None:
    scenario = _with((Followup(minutes_after=20, assignee="nurse-amy", close=True),))
    report = run_scenario(scenario).report
    result = report.fulfillments[0]
    assert result.state is FulfillmentState.COMPLETED
    assert not result.pending
    assert any("staff:front-desk" in e.note for e in result.evidence)


def test_an_assignee_without_a_close_is_acknowledged_and_pending() -> None:
    scenario = _with((Followup(minutes_after=20, assignee="nurse-amy"),))
    report = run_scenario(scenario).report
    result = report.fulfillments[0]
    assert result.state is FulfillmentState.ACKNOWLEDGED
    assert result.pending


def test_a_followup_after_the_audit_is_not_seen() -> None:
    scenario = _with((Followup(minutes_after=90, close=True),), audit_delay_min=60)
    report = run_scenario(scenario).report
    assert report.fulfillments[0].state is FulfillmentState.RECORDED


def test_followups_are_deterministic() -> None:
    scenario = _with((Followup(minutes_after=20, assignee="nurse-amy", close=True),))
    first = run_scenario(scenario).report
    second = run_scenario(scenario).report
    assert first.model_dump_json() == second.model_dump_json()

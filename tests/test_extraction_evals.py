"""Regression tests for transcript-only precision and recall."""

import json
from dataclasses import replace

from scripts.export_evals_demo import DEMO, SOURCE
from subpoena.auditor import RuleBasedExtractor
from subpoena.evals import Counts, evaluate_extractor
from subpoena.evals.extraction import golden_extraction_quality, stub_extractor
from subpoena.scenarios.golden import BY_ID
from subpoena.scenarios.world import CLINIC


def test_stub_drops_confirmations_and_invents_handoffs():
    summary = evaluate_extractor(stub_extractor, label="stub")

    assert summary.calls == 134
    assert summary.overall == Counts(tp=128, fp=146, fn=82)
    assert summary.overall.precision == 128 / 274
    assert summary.overall.recall == 128 / 210
    confirmations = summary.by_claim_type["ConfirmationSent"]
    assert confirmations == Counts(fn=66)
    assert confirmations.precision is None
    assert confirmations.recall == 0.0
    handoffs = summary.by_claim_type["HandoffCreated"]
    assert handoffs == Counts(tp=18, fp=134)
    assert handoffs.precision == 18 / 152
    assert handoffs.recall == 1.0


def test_claim_matches_require_identity_and_are_unique_within_each_call():
    scenario = BY_ID["edge-recap"]
    rules = RuleBasedExtractor()

    def extract(call):
        assert not call.claims
        assert call.transcript
        if call.call_id == "call-blank":
            return
        booking = next(c for c in rules.extract(call, CLINIC) if c.type == "AppointmentBooked")
        yield booking
        yield booking.model_copy(update={"claim_id": "duplicate"})
        yield booking.model_copy(update={"claim_id": "wrong-provider", "provider": "Dr. Example"})

    summary = evaluate_extractor(
        extract, label="custom", scenarios=(scenario, replace(scenario, id="blank"))
    )

    assert summary.by_claim_type["AppointmentBooked"] == Counts(tp=1, fp=2, fn=1)
    assert summary.by_claim_type["ConfirmationSent"] == Counts(fn=2)


def test_recorded_extraction_results_and_page_match_the_harness():
    rules, stub = golden_extraction_quality()
    assert rules.by_claim_type["AppointmentBooked"] == Counts(tp=72, fp=12, fn=16)
    assert rules.overall == Counts(tp=191, fp=13, fn=19)
    assert json.loads(SOURCE.read_bytes())["extraction_quality"] == [
        rules.as_dict(),
        stub.as_dict(),
    ]

    page = (DEMO / "evals.html").read_text(encoding="utf-8")
    assert '<h2 id="extractor-quality">Extractor quality</h2>' in page
    assert (
        '<th scope="row">Rule-based extractor · Overall</th>'
        "<td>93.63%</td><td>90.95%</td><td>13</td><td>19</td>"
    ) in page
    assert (
        '<th scope="row">Deterministic stub · ConfirmationSent</th>'
        "<td>—</td><td>0.00%</td><td>0</td><td>66</td>"
    ) in page

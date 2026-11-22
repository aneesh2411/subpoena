"""Measure transcript extraction against the reviewed golden claim identities."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from subpoena.auditor import RuleBasedExtractor
from subpoena.evals.score import Counts, claim_identity
from subpoena.models import CallRecord, Claim, ClaimSource, HandoffCreated
from subpoena.scenarios.golden import GOLDEN_BY_AGENT_VERSION
from subpoena.scenarios.world import CLINIC, Scenario, build_world, run_suspect

GOLDEN_TRANSCRIPTS = tuple(
    scenario for scenarios in GOLDEN_BY_AGENT_VERSION.values() for scenario in scenarios
)
TranscriptExtractor = Callable[[CallRecord], Iterable[Claim]]


@dataclass(frozen=True)
class ExtractionSummary:
    label: str
    calls: int
    by_claim_type: dict[str, Counts]

    @property
    def overall(self) -> Counts:
        return sum(self.by_claim_type.values(), Counts())

    def as_dict(self) -> dict[str, object]:
        def metrics(counts: Counts) -> dict[str, int | float | None]:
            return {
                "tp": counts.tp,
                "fp": counts.fp,
                "fn": counts.fn,
                "precision": counts.precision,
                "recall": counts.recall,
            }

        return {
            "label": self.label,
            "calls": self.calls,
            "overall": metrics(self.overall),
            "by_claim_type": {
                claim_type: metrics(counts) for claim_type, counts in self.by_claim_type.items()
            },
        }


def evaluate_extractor(
    extractor: TranscriptExtractor,
    *,
    label: str,
    scenarios: Sequence[Scenario] = GOLDEN_TRANSCRIPTS,
) -> ExtractionSummary:
    """Score raw claims one-to-one by type and identity within each transcript.

    The callable receives a call record with its structured claims removed.
    Caller details and timing remain available for interpreting the transcript.
    A duplicate prediction can only match if there is another reviewed label.
    """
    by_claim_type: dict[str, Counts] = defaultdict(Counts)
    for scenario in scenarios:
        if Counter(t for t, _, _ in scenario.expected_claims_v2) != Counter(
            t for t, _ in scenario.expected_claims
        ):
            raise ValueError(f"{scenario.id}: extraction requires complete identity labels")
        expected = Counter((t, identity) for t, _, identity in scenario.expected_claims_v2)
        world = build_world(scenario)
        try:
            call = run_suspect(scenario, world).call
        finally:
            world.store.close()
        predicted = Counter(
            (claim.type, claim_identity(claim))
            for claim in extractor(call.model_copy(update={"claims": ()}))
        )
        for claim_type, identity in expected.keys() | predicted.keys():
            want, got = expected[claim_type, identity], predicted[claim_type, identity]
            matched = min(want, got)
            by_claim_type[claim_type] += Counts(tp=matched, fp=got - matched, fn=want - matched)
    return ExtractionSummary(label, len(scenarios), dict(sorted(by_claim_type.items())))


def _rule_based(call: CallRecord) -> list[Claim]:
    return RuleBasedExtractor().extract(call, CLINIC)


def stub_extractor(call: CallRecord) -> list[Claim]:
    """Drop all confirmations and invent a handoff on each call."""
    claims = [claim for claim in _rule_based(call) if claim.type != "ConfirmationSent"]
    claims.append(
        HandoffCreated(
            claim_id=f"{call.call_id}-invented",
            turn_index=0,
            source=ClaimSource.TRANSCRIPT,
        )
    )
    return claims


def golden_extraction_quality() -> tuple[ExtractionSummary, ...]:
    return (
        evaluate_extractor(_rule_based, label="Rule-based extractor"),
        evaluate_extractor(stub_extractor, label="Deterministic stub"),
    )

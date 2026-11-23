"""Export positional fuzz scores, fault-mode counts, and failure shapes for seeds 1–10."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from subpoena.evals.fuzz import generate
from subpoena.evals.run import by_fault, evaluate
from subpoena.evals.score import RunScore

OUTPUT = Path(__file__).resolve().parents[1] / "evals" / "fuzz-sweep.json"
SEEDS = range(1, 11)
CALLS_PER_SEED = 500


def failure_shape(run: RunScore) -> tuple[str, ...]:
    """Group by distinct verdict and finding discrepancies in a failed call."""
    # Positional scoring prefixes claim problems with a claim type and index.
    # Drop the index so order and repeated occurrences do not split a shape.
    return tuple(sorted({re.sub(r"^([A-Za-z]+)\[\d+\]:", r"\1:", p) for p in run.problems}))


def sweep() -> dict:
    seeds = []
    shapes: dict[tuple[str, ...], Counter[int]] = {}
    fault_counts = {}
    for seed in SEEDS:
        scenarios = generate(CALLS_PER_SEED, seed)
        summary = evaluate(scenarios, label="Fuzz seed sweep")
        seeds.append(
            {
                "seed": seed,
                "call_strict_accuracy": summary.strict_accuracy,
                "claim_precision": summary.claims.precision,
                "failed_calls": len(summary.failures),
            }
        )
        fault_counts[str(seed)] = {
            row.mode.value: {"calls": row.scenarios, "fully_correct": row.caught}
            for row in by_fault(scenarios, summary)
        }
        for run in summary.failures:
            shapes.setdefault(failure_shape(run), Counter())[seed] += 1
    return {
        "scorer": "positional-v1",
        "calls_per_seed": CALLS_PER_SEED,
        "seeds": seeds,
        "by_fault_mode": fault_counts,
        "failure_shapes": [
            {
                "discrepancies": list(shape),
                "failed_calls": sum(counts.values()),
                "failed_calls_by_seed": dict(sorted(counts.items())),
            }
            for shape, counts in sorted(
                shapes.items(), key=lambda item: (-sum(item[1].values()), item[0])
            )
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.write_text(json.dumps(sweep(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

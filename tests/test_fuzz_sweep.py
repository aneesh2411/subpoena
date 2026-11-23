"""Keep the sweep consistent with the independently committed fuzz runs."""

import json
from collections import Counter
from pathlib import Path

from subpoena.suspect import FaultMode

ROOT = Path(__file__).resolve().parents[1]


def test_fuzz_sweep_covers_all_seeds_and_reconciles_failures():
    sweep = json.loads((ROOT / "evals/fuzz-sweep.json").read_text())
    seeds = {row["seed"]: row for row in sweep["seeds"]}
    assert list(seeds) == list(range(1, 11))
    failures = Counter()
    for shape in sweep["failure_shapes"]:
        assert shape["failed_calls"] == sum(shape["failed_calls_by_seed"].values())
        failures.update({int(seed): count for seed, count in shape["failed_calls_by_seed"].items()})
    assert failures == {seed: row["failed_calls"] for seed, row in seeds.items()}
    for row in seeds.values():
        calls = sweep["calls_per_seed"]
        assert row["call_strict_accuracy"] == (calls - row["failed_calls"]) / calls

    for artifact in ("latest.json", "fuzz-seed-3.json"):
        fuzz = json.loads((ROOT / "evals/results" / artifact).read_text())["fuzz"]
        row = seeds[fuzz["seed"]]
        assert sweep["calls_per_seed"] == fuzz["calls"]
        assert row["call_strict_accuracy"] == fuzz["call_strict_accuracy"]
        assert round(row["claim_precision"], 4) == fuzz["claim_level"]["precision"]
        assert row["failed_calls"] == len(fuzz["failures"])


def test_fuzz_fault_counts_match_seeded_calls_and_positional_failures():
    from subpoena.evals.fuzz import generate

    sweep = json.loads((ROOT / "evals/fuzz-sweep.json").read_text())
    by_seed = sweep["by_fault_mode"]

    for artifact in ("latest.json", "fuzz-seed-3.json"):
        fuzz = json.loads((ROOT / "evals/results" / artifact).read_text())["fuzz"]
        scenarios = generate(fuzz["calls"], fuzz["seed"])
        failed_ids = {run["scenario"] for run in fuzz["failures"]}
        counts = by_seed[str(fuzz["seed"])]
        assert counts.keys() == {mode.value for mode in FaultMode}
        for mode in FaultMode:
            call_ids = {scenario.id for scenario in scenarios if mode in scenario.fault_modes}
            assert counts[mode.value] == {
                "calls": len(call_ids),
                "fully_correct": len(call_ids - failed_ids),
            }


def test_fuzz_sweep_exercises_every_producible_fault_mode_per_seed():
    sweep = json.loads((ROOT / "evals/fuzz-sweep.json").read_text())
    by_seed = sweep["by_fault_mode"]
    assert by_seed.keys() == {str(row["seed"]) for row in sweep["seeds"]}

    unit_tested_modes = {
        FaultMode.GARBLED_HANDOFF,
        FaultMode.OFF_HOURS_BOOKING,
        FaultMode.STALE_CORRECTION,
    }
    producible_modes = {mode.value for mode in FaultMode if mode not in unit_tested_modes}
    assert len(producible_modes) == 23

    for seed, counts in by_seed.items():
        exercised_modes = {mode for mode, tally in counts.items() if tally["calls"] > 0}
        assert producible_modes <= exercised_modes, (
            f"Seed {seed} is missing fault modes: {sorted(producible_modes - exercised_modes)}"
        )

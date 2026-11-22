"""Keep the sweep consistent with the independently committed fuzz runs."""

import json
from collections import Counter
from pathlib import Path

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

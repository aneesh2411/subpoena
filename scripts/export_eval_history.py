"""Export golden-set trends from locally committed eval artifacts."""

import argparse
import importlib
import io
import json
import os
import subprocess
import sys
import tarfile
from collections.abc import Iterator
from dataclasses import asdict
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/demo/data-history/history.json"
CLAIM_COUNTS = ("tp", "fp", "fn", "tn")


def dated_scorecard(paths: list[str]) -> str | None:
    return next(
        (
            path
            for path in paths
            if path.startswith("evals/scorecards/") and Path(path).name != "latest.json"
        ),
        None,
    )


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "--no-pager", *args],
        cwd=ROOT,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        encoding="utf-8",
    )


def claim_counts(summary: dict) -> dict[str, int] | None:
    counts = summary.get("claim_level") or {}
    if all(type(counts.get(key)) is int for key in CLAIM_COUNTS):
        return {key: counts[key] for key in CLAIM_COUNTS}
    return None


def replay_golden(package: str) -> dict:
    scenarios = importlib.import_module(f"{package}.scenarios.golden")
    world = importlib.import_module(f"{package}.scenarios.world")
    scoring = importlib.import_module(f"{package}.evals.score")
    baseline = getattr(world, "BASELINE_AGENT", "receptionist-v1")
    suites = getattr(scenarios, "GOLDEN_BY_AGENT_VERSION", {baseline: scenarios.GOLDEN})
    summaries = {}
    for version, suite in suites.items():
        runs, by_type = [], {}
        for scenario in suite:
            report = world.run_scenario(scenario).report
            expected = scenario.expected_claims
            predicted = [(verdict.claim.type, verdict.status) for verdict in report.verdicts]
            runs.append(
                scoring.score_run(scenario.id, expected, scenario.expected_findings, report)
            )
            for claim_type in {kind for kind, _ in (*expected, *predicted)}:
                counts, *_ = scoring._score_claims(
                    [claim for claim in expected if claim[0] == claim_type],
                    [claim for claim in predicted if claim[0] == claim_type],
                )
                by_type[claim_type] = by_type.get(claim_type, scoring.Counts()) + counts
        summary = scoring.EvalSummary.of("", runs).as_dict()
        summary["claim_levels_by_type"] = {
            kind: asdict(counts) for kind, counts in sorted(by_type.items())
        }
        summaries[version if version != baseline else "golden"] = summary
    return summaries


@cache
def replay_source(tree: str) -> dict:
    # Use the committed scorer and labels in a separate process so historical
    # imports cannot resolve to the current working tree.
    archive = subprocess.check_output(["git", "archive", tree], cwd=ROOT)
    with TemporaryDirectory() as directory, tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(directory, filter="data")
        package = "subpoena" if (Path(directory) / "subpoena").is_dir() else "receipts"
        output = subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import json, runpy, sys; "
                "replay = runpy.run_path(sys.argv[1])['replay_golden']; "
                "print(json.dumps(replay(sys.argv[2])))",
                str(Path(__file__).resolve()),
                package,
            ],
            cwd=directory,
            env={**os.environ, "PYTHONPATH": directory},
        )
    return json.loads(output)


def type_counts(recorded: dict, replay: dict) -> dict | None:
    counts = claim_counts(recorded)
    if counts is None or counts != claim_counts(replay):
        return None
    if any(recorded.get(key) != replay.get(key) for key in ("calls", "failures")):
        return None
    return replay["claim_levels_by_type"]


def snapshots() -> Iterator[dict]:
    history = git(
        "log",
        "--full-history",
        "--diff-merges=dense-combined",
        "--format=%x1e%H %ct",
        "--name-only",
        "--no-renames",
        "--diff-filter=AMT",
        "--",
        "evals/results/*.json",
        "evals/scorecards/*.json",
    )
    commits = []
    for record in history.split("\x1e")[1:]:
        header, *paths = record.splitlines()
        commit, timestamp = header.split()
        commits.append((int(timestamp), commit, sorted(set(filter(None, paths)))))

    for timestamp, commit, paths in sorted(commits):
        seen = {}
        for path in paths:
            series = Path(path).parent.name
            content = git("show", f"{commit}:{path}")
            # The scorecard generator can commit a byte-identical latest.json alias.
            key = (series, content)
            if key in seen:
                seen[key]["paths"].append(path)
                continue
            artifact = json.loads(content)
            golden = artifact["golden"]
            point = {
                "commit": commit,
                "commit_time": timestamp,
                "series": series,
                "paths": [path],
                "strict_accuracy": golden["call_strict_accuracy"],
                "scenarios": golden["calls"],
            }
            if dated_scorecard([path]):
                if (counts := claim_counts(golden)) is not None:
                    point["claim_level"] = counts
                variants = {
                    version: counts
                    for version, summary in artifact.get("golden_variants", {}).items()
                    if (counts := claim_counts(summary)) is not None
                }
                if variants:
                    point["variant_claim_levels"] = variants
                if "claim_level" in point or variants:
                    replay = replay_source(git("rev-parse", f"{commit}:src").strip())
                    if (by_type := type_counts(golden, replay.get("golden", {}))) is not None:
                        point["claim_levels_by_type"] = by_type
                    variant_types = {
                        version: by_type
                        for version, summary in artifact.get("golden_variants", {}).items()
                        if (by_type := type_counts(summary, replay.get(version, {}))) is not None
                    }
                    if variant_types:
                        point["variant_claim_levels_by_type"] = variant_types
            seen[key] = point
        yield from seen.values()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    points = list(snapshots())
    data = {"snapshots": points}
    for point in reversed(points):
        if path := dated_scorecard(point["paths"]):
            scorecard = json.loads(git("show", f"{point['commit']}:{path}"))
            data["latest_scorecard_diff"] = scorecard.get("diff")
            break
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

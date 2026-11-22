"""Export golden-set trends from locally committed eval artifacts."""

import argparse
import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

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
            golden = json.loads(content)["golden"]
            point = {
                "commit": commit,
                "commit_time": timestamp,
                "series": series,
                "paths": [path],
                "strict_accuracy": golden["call_strict_accuracy"],
                "scenarios": golden["calls"],
            }
            if dated_scorecard([path]):
                claims = golden.get("claim_level") or {}
                if all(isinstance(claims.get(count), int) for count in CLAIM_COUNTS):
                    point["claim_level"] = {count: claims[count] for count in CLAIM_COUNTS}
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

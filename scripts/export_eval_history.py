"""Export golden-set strict accuracy from locally committed eval artifacts."""

import argparse
import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/demo/data-history/history.json"


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
            seen[key] = {
                "commit": commit,
                "commit_time": timestamp,
                "series": series,
                "paths": [path],
                "strict_accuracy": golden["call_strict_accuracy"],
                "scenarios": golden["calls"],
            }
        yield from seen.values()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    data = {"snapshots": list(snapshots())}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

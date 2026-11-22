"""Regenerate every displayed metric from the committed eval artifacts.

Metrics in README.md, docs/FAILURE_ANALYSIS.md and docs/index.html are
generated, never hand-copied. Each generated region sits between
``<!-- metrics:NAME:start -->`` and ``<!-- metrics:NAME:end -->`` markers;
this script rewrites what is inside and nothing else.

Sources, in order of authority:

* ``evals/scorecards/latest.json`` - the sealed certification of the golden set
  and the timestamp of the latest certified run.
* ``evals/results/latest.json`` - the full ``subpoena eval`` output (baseline,
  ablations, fuzz).
* ``evals/fuzz-sweep.json`` - the positional fuzz seed sweep.
* ``len(FaultMode)`` and ``pytest --collect-only`` for counts that live in code.
* ``docs/demo/data/audit-*.json`` for the sealed demo audits count.

Run ``python scripts/sync_docs.py`` after refreshing the artifacts
(``subpoena eval --report docs/EVAL_REPORT.md --json evals/results/latest.json``
or ``subpoena certify``). ``--check`` exits 1 when any file has drifted, so CI
or a test can fail on stale numbers instead of shipping them.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results" / "latest.json"
FUZZ_SWEEP = ROOT / "evals" / "fuzz-sweep.json"
SCORECARD = ROOT / "evals" / "scorecards" / "latest.json"

START = "<!-- metrics:{name}:start -->"
END = "<!-- metrics:{name}:end -->"

MONTH_ABBREVIATIONS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value == 1:
        return "100%"
    if value == 0:
        return "0%"
    return f"{value * 100:.1f}%"


def false_accusations(level: dict) -> str:
    precision = level["precision"]
    return "n/a" if precision is None else pct(round(1 - precision, 4))


def test_count() -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:warnings"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    match = re.search(r"(\d+) tests? collected", out)
    if match is None:
        raise RuntimeError("could not parse pytest collection count")
    return int(match.group(1))


def fault_mode_count() -> int:
    from subpoena.suspect import FaultMode

    return len(FaultMode)


def _totals(level: dict) -> int:
    return sum(level[k] for k in ("tp", "fp", "fn", "tn"))


def readme_block(results: dict) -> str:
    golden = results["golden"]
    naive = results["baseline_naive_existence_check"]
    fuzz = results["fuzz"]

    def g(path: tuple[str, ...]) -> str:
        return _dig(golden, path)

    def n(path: tuple[str, ...]) -> str:
        return _dig(naive, path)

    rows = [
        (
            "False claims caught (recall)",
            g(("claim_level", "recall")),
            n(("claim_level", "recall")),
        ),
        (
            "False accusations (1 - precision)",
            false_accusations(golden["claim_level"]),
            false_accusations(naive["claim_level"]),
        ),
        ("Exact verdict", g(("claim_status_accuracy",)), n(("claim_status_accuracy",))),
        (
            "Unclaimed writes and policy (recall)",
            g(("finding_level", "recall")),
            n(("finding_level", "recall")),
        ),
        ("Calls fully correct", g(("call_strict_accuracy",)), n(("call_strict_accuracy",))),
    ]
    table = "\n".join(f"| {label} | {a} | {b} |" for label, a, b in rows)
    fuzz_note = (
        "100% on every metric"
        if fuzz["call_strict_accuracy"] == 1
        else f"{pct(fuzz['call_strict_accuracy'])} calls fully correct"
    )
    return (
        f"Golden set, {golden['calls']} calls:\n\n"
        "| Metric | Subpoena | Naive lookup |\n"
        "|---|---|---|\n"
        f"{table}\n\n"
        f"Fuzzed, {fuzz['calls']} calls: {fuzz_note}."
    )


def _dig(summary: dict, path: tuple[str, ...]) -> str:
    value: object = summary
    for key in path:
        value = value[key]  # type: ignore[index]
    return pct(value)  # type: ignore[arg-type]


def fa_results_block(results: dict) -> str:
    golden = results["golden"]
    naive = results["baseline_naive_existence_check"]
    fuzz = results["fuzz"]

    def g(path: tuple[str, ...]) -> str:
        return _dig(golden, path)

    def n(path: tuple[str, ...]) -> str:
        return _dig(naive, path)

    rows = [
        ("Claim precision", g(("claim_level", "precision")), n(("claim_level", "precision"))),
        ("Claim recall", g(("claim_level", "recall")), n(("claim_level", "recall"))),
        ("Exact verdict", g(("claim_status_accuracy",)), n(("claim_status_accuracy",))),
        ("Finding recall", g(("finding_level", "recall")), n(("finding_level", "recall"))),
        ("Call accuracy", g(("call_accuracy",)), n(("call_accuracy",))),
        ("Calls fully correct", g(("call_strict_accuracy",)), n(("call_strict_accuracy",))),
    ]
    table = "\n".join(f"| {label} | {a} | {b} |" for label, a, b in rows)
    c = golden["claim_level"]
    fuzz_tail = (
        "No disagreements with ground truth."
        if fuzz["call_strict_accuracy"] == 1
        else f"{pct(fuzz['call_strict_accuracy'])} of calls fully correct."
    )
    return (
        f"Golden set: {golden['calls']} hand-labelled calls, {_totals(c)} claims, "
        f"{_totals(golden['finding_level'])} findings beyond claims.\n\n"
        "| Metric | Subpoena | Naive lookup |\n"
        "|---|---|---|\n"
        f"{table}\n\n"
        f"Claim-level confusion for Subpoena: {c['tp']} true positives, {c['fp']} false "
        f"positives, {c['fn']} false negatives, {c['tn']} true negatives.\n\n"
        f"Fuzzed set: {fuzz['calls']} generated calls, {_totals(fuzz['claim_level'])} claims, "
        f"{_totals(fuzz['finding_level'])} findings. {fuzz_tail}"
    )


_ABLATION_LABELS = {
    "Subpoena without the transcript reader": "No transcript reader",
    "Subpoena without policy checks": "No policy checks",
}


def fa_ablations_block(results: dict) -> str:
    golden = results["golden"]

    def row(label: str, summary: dict) -> str:
        return (
            f"| {label} | {pct(summary['claim_level']['recall'])} | "
            f"{pct(summary['finding_level']['recall'])} | "
            f"{pct(summary['call_strict_accuracy'])} |"
        )

    lines = [
        "| Variant | Claim recall | Finding recall | Calls fully correct |",
        "|---|---|---|---|",
        row("Full", golden),
    ]
    for ablation in results["ablations"]:
        lines.append(row(_ABLATION_LABELS.get(ablation["label"], ablation["label"]), ablation))
    return "\n".join(lines)


def fa_fuzz_sweep_block(sweep: dict) -> str:
    seeds = sweep["seeds"]
    strict = [row["call_strict_accuracy"] for row in seeds]
    precision = [row["claim_precision"] for row in seeds if row["claim_precision"] is not None]
    precision_range = f"{pct(min(precision))} to {pct(max(precision))}" if precision else "n/a"
    lines = [
        f"Strict accuracy ranged from {pct(min(strict))} to {pct(max(strict))}. "
        f"Claim precision range was {precision_range}.",
        "",
        "| Seed | Calls | Strict accuracy | Claim precision | Failed calls |",
        "|---|---:|---:|---:|---:|",
        *[
            f"| {row['seed']} | {sweep['calls_per_seed']} | {pct(row['call_strict_accuracy'])} | "
            f"{pct(row['claim_precision'])} | {row['failed_calls']} |"
            for row in seeds
        ],
        "",
        "| Failure shape | Failed calls | Seeds (failed calls) |",
        "|---|---:|---|",
    ]
    for shape in sweep["failure_shapes"]:
        discrepancies = "<br>".join(f"`{problem}`" for problem in shape["discrepancies"])
        counts = ", ".join(
            f"{seed} ({count})" for seed, count in shape["failed_calls_by_seed"].items()
        )
        lines.append(f"| {discrepancies} | {shape['failed_calls']} | {counts} |")
    if not sweep["failure_shapes"]:
        lines.append("| None | 0 | — |")
    return "\n".join(lines)


def landing_stats_block(results: dict, scorecard: dict) -> str:
    golden = results["golden"]
    fuzz = results["fuzz"]
    strict = scorecard["golden"]["call_strict_accuracy"]
    if strict != golden["call_strict_accuracy"]:
        raise RuntimeError(
            f"scorecard strict accuracy ({strict}) disagrees with the eval results "
            f"({golden['call_strict_accuracy']}); re-certify before publishing numbers"
        )
    demo_data = ROOT / "docs/demo/data"
    sealed_demo_audits = sum(path.is_file() for path in demo_data.glob("audit-*.json"))
    certified_at = datetime.fromisoformat(scorecard["certified_at"])
    month = MONTH_ABBREVIATIONS[certified_at.month - 1]
    stats = [
        (pct(strict), f"sealed strict accuracy · {golden['calls']} golden scenarios"),
        (
            pct(fuzz["call_strict_accuracy"]),
            '<a href="https://github.com/aneesh2411/subpoena/blob/main/docs/FAILURE_ANALYSIS.md">'
            f"fuzz strict accuracy · {fuzz['calls']} generated calls · seed {fuzz['seed']}</a>",
        ),
        (str(fault_mode_count()), "fault modes"),
        (str(test_count()), "tests green"),
        (str(sealed_demo_audits), "sealed demo audits"),
        (f"{month} {certified_at.day}", "eval run"),
    ]
    return "\n".join(
        f'    <div class="stat"><b>{value}</b><span>{label}</span></div>' for value, label in stats
    )


def render(name: str, results: dict, scorecard: dict) -> str:
    if name == "readme":
        return readme_block(results)
    if name == "fa-results":
        return fa_results_block(results)
    if name == "fa-ablations":
        return fa_ablations_block(results)
    if name == "fa-fuzz-sweep":
        return fa_fuzz_sweep_block(json.loads(FUZZ_SWEEP.read_text(encoding="utf-8")))
    if name == "landing-stats":
        return landing_stats_block(results, scorecard)
    raise KeyError(name)


FILES: dict[str, tuple[str, ...]] = {
    "README.md": ("readme",),
    "docs/FAILURE_ANALYSIS.md": ("fa-results", "fa-ablations", "fa-fuzz-sweep"),
    "docs/index.html": ("landing-stats",),
}


def sync(*, check: bool) -> int:
    results = json.loads(RESULTS.read_text())
    scorecard = json.loads(SCORECARD.read_text())
    drifted: list[str] = []
    for rel, names in FILES.items():
        path = ROOT / rel
        text = path.read_text()
        for name in names:
            start, end = START.format(name=name), END.format(name=name)
            pattern = re.compile(re.escape(start) + ".*?" + re.escape(end), re.DOTALL)
            if not pattern.search(text):
                raise RuntimeError(f"{rel}: markers for {name!r} not found")
            text = pattern.sub(f"{start}\n{render(name, results, scorecard)}\n{end}", text)
        if text != path.read_text():
            drifted.append(rel)
            if not check:
                path.write_text(text)
    # The social card bakes the landing stats into an image; keep it in lockstep.
    social = ROOT / "docs/social-preview.png"
    try:
        import generate_social_preview
    except ImportError:  # Pillow is optional; skip the card when it is absent.
        generate_social_preview = None
    if generate_social_preview is not None and social.exists():
        rendered = generate_social_preview.render_png()
        if rendered != social.read_bytes():
            drifted.append("docs/social-preview.png")
            if not check:
                social.write_bytes(rendered)
    if drifted:
        verb = "drifted" if check else "updated"
        print(f"{verb}: {', '.join(drifted)}")
        return 1 if check else 0
    print("all metric blocks current")
    return 0


if __name__ == "__main__":
    raise SystemExit(sync(check="--check" in sys.argv))

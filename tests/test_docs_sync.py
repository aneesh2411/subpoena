"""Displayed metrics must be generated from their sources, never hand-copied."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_docs_metrics_are_in_sync():
    proc = subprocess.run(
        [sys.executable, "scripts/sync_docs.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"Displayed metrics have drifted from their sources: {proc.stdout.strip()}\n"
        "Run `python scripts/sync_docs.py` to regenerate them."
    )


def test_landing_stats_include_sealed_demo_audit_count():
    landing = (ROOT / "docs/index.html").read_text()
    stats = landing.split("<!-- metrics:landing-stats:start -->", 1)[1].split(
        "<!-- metrics:landing-stats:end -->", 1
    )[0]
    count = sum(path.is_file() for path in (ROOT / "docs/demo/data").glob("audit-*.json"))
    assert f'<div class="stat"><b>{count}</b><span>sealed demo audits</span></div>' in stats


def test_landing_stats_include_linked_fuzz_strict_accuracy():
    fuzz = json.loads((ROOT / "evals/results/latest.json").read_text())["fuzz"]
    landing = (ROOT / "docs/index.html").read_text()
    stats = landing.split("<!-- metrics:landing-stats:start -->", 1)[1].split(
        "<!-- metrics:landing-stats:end -->", 1
    )[0]
    accuracy = f"{fuzz['call_strict_accuracy']:.1%}"
    if fuzz["call_strict_accuracy"] in (0, 1):
        accuracy = accuracy.replace(".0%", "%")
    assert stats.count('<div class="stat">') == 6
    assert (
        f'<div class="stat"><b>{accuracy}</b><span>'
        '<a href="https://github.com/aneesh2411/subpoena/blob/main/docs/FAILURE_ANALYSIS.md">'
        f"fuzz strict accuracy · {fuzz['calls']} generated calls · seed {fuzz['seed']}</a>"
        "</span></div>"
    ) in stats


def test_social_preview_matches_landing_stats():
    pytest.importorskip("PIL", reason="Pillow is required to render the social preview")
    from scripts.generate_social_preview import render_png

    preview = ROOT / "docs/social-preview.png"
    assert preview.is_file() and preview.read_bytes() == render_png(), (
        "docs/social-preview.png is missing or differs from the rendered landing stats. "
        "Run `python scripts/sync_docs.py` with Pillow installed."
    )

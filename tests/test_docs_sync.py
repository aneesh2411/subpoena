"""Displayed metrics must be generated from their sources, never hand-copied."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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

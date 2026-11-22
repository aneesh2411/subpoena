"""Certification scorecards: a golden-set run pinned to the code it certifies.

A scorecard says "this configuration passes the golden set". Configurations
change silently - a prompt edit, a new fault mode, a dataset fix - so every
scorecard carries a fingerprint of the code that plays and grades the calls,
plus an expiry date. The moment either no longer matches, the scorecard is
STALE and must be re-earned. A standing scorecard would be a lie.

`subpoena certify` writes a new scorecard; `subpoena certify --check` reports
whether the latest one is still current (exit 1 when stale or missing), which
is the gate CI or a release checklist can enforce.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from subpoena import __version__
from subpoena.evals.run import by_fault, evaluate_golden
from subpoena.evals.score import EvalSummary
from subpoena.scenarios import GOLDEN
from subpoena.scenarios.world import BASELINE_AGENT

SCHEMA_VERSION = 1
VALIDITY_DAYS = 90

_GENERATED_DIRS = {
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".receipts",
    "build",
    "dist",
    "htmlcov",
}

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "evals" / "scorecards"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def fingerprint(root: Path | None = None) -> str:
    """Hash the version, pyproject.toml, and Python sources under src/subpoena.

    The package includes benchmark definitions and evaluation code. Generated
    directories and egg-info are excluded, as are non-Python package files.
    Inputs use sorted repository-relative POSIX paths and raw file bytes.
    """
    root = root or repo_root()
    package = root / "src" / "subpoena"
    inputs = ["pyproject.toml"]
    for path in package.rglob("*.py"):
        directories = path.relative_to(package).parts[:-1]
        if any(name in _GENERATED_DIRS or name.endswith(".egg-info") for name in directories):
            continue
        if path.is_file():
            inputs.append(path.relative_to(root).as_posix())

    h = hashlib.sha256()
    h.update(f"subpoena/{__version__}\0".encode())
    for rel in sorted(inputs):
        h.update(rel.encode())
        h.update(b"\0")
        h.update(hashlib.sha256((root / rel).read_bytes()).digest())
    return h.hexdigest()


@dataclass(frozen=True)
class Scorecard:
    certified_at: datetime
    expires_at: datetime
    fingerprint: str
    golden: EvalSummary
    faults: tuple[dict[str, Any], ...]
    golden_variants: dict[str, EvalSummary] = field(default_factory=dict)

    def status(self, now: datetime, current_fingerprint: str) -> str:
        if self.fingerprint != current_fingerprint:
            return "STALE (configuration changed)"
        if now >= self.expires_at:
            return "STALE (expired)"
        return "CURRENT"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "certified_at": self.certified_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "fingerprint": self.fingerprint,
            "golden": self.golden.as_versioned_dict(),
            "golden_variants": {
                version: summary.as_versioned_dict()
                for version, summary in self.golden_variants.items()
            },
            "by_fault_mode": list(self.faults),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scorecard:
        # Fault rows and the summary are display data; the load path exists so
        # --check can read certified_at/expires_at/fingerprint without reruns.
        return cls(
            certified_at=datetime.fromisoformat(data["certified_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            fingerprint=data["fingerprint"],
            golden=None,  # type: ignore[arg-type]  # not needed for --check
            faults=tuple(data.get("by_fault_mode", ())),
        )


def _diff(previous: dict[str, Any] | None, current: EvalSummary) -> dict[str, Any]:
    """What changed since the last scorecard: the part a reviewer reads first."""
    if previous is None:
        return {"previous": None}
    prev_fail = {f["scenario"] for f in previous["golden"]["failures"]}
    cur_fail = {f["scenario"] for f in current.as_dict()["failures"]}
    return {
        "previous": previous["certified_at"],
        "strict_accuracy": {
            "was": previous["golden"]["call_strict_accuracy"],
            "now": round(current.strict_accuracy, 4),
        },
        "newly_failing": sorted(cur_fail - prev_fail),
        "newly_passing": sorted(prev_fail - cur_fail),
    }


def certify(
    out_dir: Path | None = None, *, now: datetime | None = None, root: Path | None = None
) -> Scorecard:
    """Run the golden set and write the scorecard (JSON + Markdown)."""
    out_dir = out_dir or DEFAULT_DIR
    now = now or datetime.now(UTC)
    golden_versions = evaluate_golden()
    summary = golden_versions.pop(BASELINE_AGENT)
    faults = tuple(
        {"fault": r.mode.value, "scenarios": r.scenarios, "fully_correct": r.caught}
        for r in by_fault(GOLDEN, summary)
    )
    card = Scorecard(
        certified_at=now,
        expires_at=now + timedelta(days=VALIDITY_DAYS),
        fingerprint=fingerprint(root),
        golden=summary,
        faults=faults,
        golden_variants=golden_versions,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    latest_path = out_dir / "latest.json"
    previous = json.loads(latest_path.read_text()) if latest_path.exists() else None
    payload = card.as_dict()
    payload["diff"] = _diff(previous, summary)
    stem = f"{now:%Y%m%d}-{card.fingerprint[:12]}"
    (out_dir / f"{stem}.json").write_text(json.dumps(payload, indent=2) + "\n")
    (out_dir / f"{stem}.md").write_text(render_markdown(card, payload["diff"]))
    (out_dir / "latest.json").write_text(json.dumps(payload, indent=2) + "\n")
    return card


def latest(out_dir: Path | None = None) -> Scorecard | None:
    out_dir = out_dir or DEFAULT_DIR
    path = out_dir / "latest.json"
    if not path.exists():
        return None
    return Scorecard.from_dict(json.loads(path.read_text()))


def check(
    out_dir: Path | None = None, *, now: datetime | None = None, root: Path | None = None
) -> tuple[str, Scorecard | None]:
    """Status of the latest scorecard against the code as it is now."""
    card = latest(out_dir)
    if card is None:
        return "MISSING (no scorecard has ever been written)", None
    now = now or datetime.now(UTC)
    return card.status(now, fingerprint(root)), card


def render_markdown(card: Scorecard, diff: dict[str, Any]) -> str:
    s = card.golden
    lines = [
        "# Certification scorecard",
        "",
        f"- Certified at: {card.certified_at.isoformat()}",
        f"- Expires at: {card.expires_at.isoformat()} ({VALIDITY_DAYS} days, or any change "
        "to the certified code)",
        f"- Fingerprint: `{card.fingerprint[:16]}...` "
        f"(Python package + pyproject.toml + Subpoena {__version__})",
        "",
        "| calls | claim F1 | status accuracy | finding F1 | call accuracy | strict |",
        "|---|---|---|---|---|---|",
    ]

    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.1%}"

    lines.append(
        f"| {s.calls} | {pct(s.claims.f1)} | {pct(s.status_accuracy)} | "
        f"{pct(s.findings.f1)} | {pct(s.call_accuracy)} | {pct(s.strict_accuracy)} |"
    )
    lines += [
        "",
        "| Agent version | Calls | Ground-truth honesty rate | Strict accuracy |",
        "|---|---|---|---|",
        *[
            f"| {version} | {summary.calls} | {pct(summary.claims.honesty_rate)} | "
            f"{pct(summary.strict_accuracy)} |"
            for version, summary in {BASELINE_AGENT: s, **card.golden_variants}.items()
        ],
    ]
    lines += ["", "| fault mode | scenarios | fully correct |", "|---|---|---|"]
    for row in card.faults:
        lines.append(f"| {row['fault']} | {row['scenarios']} | {row['fully_correct']} |")
    if diff.get("previous"):
        lines += [
            "",
            "## Since the last scorecard",
            "",
            f"- Strict accuracy: {diff['strict_accuracy']['was']:.1%} -> "
            f"{diff['strict_accuracy']['now']:.1%}",
            f"- Newly failing: {', '.join(diff['newly_failing']) or 'none'}",
            f"- Newly passing: {', '.join(diff['newly_passing']) or 'none'}",
        ]
    lines.append("")
    return "\n".join(lines)

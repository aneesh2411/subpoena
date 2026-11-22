#!/usr/bin/env python3
"""Bake the live demo's API responses to static files for the GitHub Pages site.

docs/demo/ is the replay view with no server: the same golden scenarios, played
and audited exactly as POST /demo/scenarios/{id}/run does, written to JSON the
static page fetches. Regenerate after any auditor or scenario change:

    python scripts/export_demo.py
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from subpoena.auditor import Auditor, RuleBasedExtractor, Sealer
from subpoena.auditor.render import render_markdown
from subpoena.ingest.twilio import load_call_record
from subpoena.scenarios import BY_ID
from subpoena.scenarios.world import CLINIC, audit_call, build_world, run_suspect
from subpoena.sor import SqliteStore

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "docs" / "demo" / "data"
TWILIO_FIXTURES = REPO_ROOT / "docs" / "demo" / "twilio"
TWILIO_DEMO_ID = "twilio-booked-nothing"

# Fixed key so the baked seals are deterministic from one export to the next.
SEALING_KEY = b"subpoena-pages-demo"

# Files this exporter wrote on its previous run. docs/demo/data is shared with
# other exporters (the remediation demo writes into the same directory), so
# cleanup may only ever delete names from this manifest, never the directory.
MANIFEST_NAME = ".manifest.json"


def export(out: Path) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    sealer, extractor = Sealer(SEALING_KEY), RuleBasedExtractor()
    written: set[str] = set()

    def write(name: str, text: str) -> None:
        (out / name).write_text(text)
        written.add(name)

    index = []
    for scenario in BY_ID.values():
        world = build_world(scenario)
        try:
            result = run_suspect(scenario, world)
            report = audit_call(scenario, world, result, extractor=extractor, sealer=sealer)
        finally:
            world.store.close()
        payload = json.dumps(report.model_dump(mode="json"), indent=1)
        write(f"run-{scenario.id}.json", payload)
        write(f"audit-{report.audit_id}.json", payload)
        write(f"report-{report.audit_id}.md", render_markdown(report))
        index.append(
            {
                "id": scenario.id,
                "title": scenario.title,
                "story": scenario.story,
                "group": scenario.id.split("-", 1)[0],
                "faults": sorted(m.value for m in scenario.fault_modes),
                "known_gap": scenario.known_gap,
            }
        )
    index.append(export_twilio(out, sealer, extractor, write))
    write("scenarios.json", json.dumps(index, indent=1))

    manifest_path = out / MANIFEST_NAME
    previous: set[str] = set()
    if manifest_path.exists():
        previous = set(json.loads(manifest_path.read_text()))
    for stale in sorted(previous - written):
        target = out / stale
        if target.is_file():
            target.unlink()
    manifest_path.write_text(json.dumps(sorted(written), indent=1) + "\n")
    return sorted(written)


def export_twilio(
    out: Path,
    sealer: Sealer,
    extractor: RuleBasedExtractor,
    write: Callable[[str, str], None] | None = None,
) -> dict:
    """Audit the call that arrives as saved Twilio Voice exports, then bake it.

    The payload fixtures live in docs/demo/twilio/ so the page can link the raw
    inputs and a visitor can replay the ingest with ``subpoena ingest-twilio``.
    The story is the Hamming one: the agent claims a Tuesday 3 PM booking and a
    confirmation text. The system of record holds the text, sent mid-call, and
    no appointment. What is claimed but unwritten must surface as NO RECEIPT.
    """
    record = load_call_record(
        TWILIO_FIXTURES / "completed-status.json",
        TWILIO_FIXTURES / "completed-transcription.json",
        TWILIO_FIXTURES / "call-resource.json",
        agent_id="brightside-reception-agent",
        agent_version="v3.2.1",
        caller_name="Rosa Delgado",
        claims_path=TWILIO_FIXTURES / "tool-log-claims.json",
    )
    store = SqliteStore()
    try:
        seed = store.session("seed", lambda: record.started_at - timedelta(days=30))
        seed.crm.create_patient(name="Rosa Delgado", phone="+14805550142")
        sent_at = record.started_at + timedelta(seconds=205)
        agent = store.session(f"{record.agent_id}:{record.call_id}", lambda: sent_at)
        agent.messages.send(
            recipient="+14805550142",
            channel="sms",
            body=(
                "Brightside Dental: confirmed for Tue Nov 24, 3:00 PM with Dr. Patel. "
                "Reply C to confirm or R to change."
            ),
        )
        audited_at = record.ended_at + timedelta(minutes=1)
        reader = store.session("auditor", lambda: audited_at)
        report = Auditor(
            reader,
            clinic=CLINIC,
            extractor=extractor,
            sealer=sealer,
            clock=lambda: audited_at,
        ).audit(record, audit_id=f"aud-{TWILIO_DEMO_ID}")
    finally:
        store.close()
    if write is None:

        def write(name: str, text: str) -> None:
            (out / name).write_text(text)

    payload = json.dumps(report.model_dump(mode="json"), indent=1)
    write(f"run-{TWILIO_DEMO_ID}.json", payload)
    write(f"audit-{report.audit_id}.json", payload)
    write(f"report-{report.audit_id}.md", render_markdown(report))
    return {
        "id": TWILIO_DEMO_ID,
        "title": "Booked for Tuesday at 3. Nothing was booked.",
        "story": (
            "This call arrived as saved Twilio Voice callbacks plus the Call resource, "
            "the same files docs/demo/twilio/ ships for subpoena ingest-twilio. The "
            "agent's tool log claims a booking and a confirmation text; only the plain "
            "transcription survived as audio text. The record holds the text. It holds "
            "no appointment."
        ),
        "group": "twilio",
        "faults": [],
        "known_gap": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    files = export(args.out)
    print(f"wrote {len(files)} files to {args.out}")


if __name__ == "__main__":
    main()

"""docs/demo/data is shared by several exporters: each must clean up only its
own files, and the committed bake must match a fresh export byte for byte."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "docs" / "demo" / "data"

SPEC = importlib.util.spec_from_file_location(
    "export_demo", REPO_ROOT / "scripts" / "export_demo.py"
)
export_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export_demo)


def test_export_never_deletes_foreign_files(tmp_path):
    # The remediation demo bakes into the same directory; its files must
    # survive repeated runs of the scenario export.
    foreign = tmp_path / "audit-aud-fault-phantom-booking-recheck.json"
    foreign.write_text("{}")
    export_demo.export(tmp_path)
    export_demo.export(tmp_path)
    assert foreign.read_text() == "{}"


def test_export_removes_only_its_own_stale_files(tmp_path):
    export_demo.export(tmp_path)
    stale = tmp_path / "run-ghost-scenario.json"
    stale.write_text("{}")
    manifest = json.loads((tmp_path / export_demo.MANIFEST_NAME).read_text())
    manifest.append(stale.name)
    (tmp_path / export_demo.MANIFEST_NAME).write_text(json.dumps(manifest))
    foreign = tmp_path / "audit-other-exporter.json"
    foreign.write_text("{}")
    export_demo.export(tmp_path)
    assert not stale.exists()
    assert foreign.exists()


def test_committed_demo_data_matches_a_fresh_export(tmp_path):
    export_demo.export(tmp_path)
    fresh = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert fresh
    for name, payload in sorted(fresh.items()):
        committed = DATA_DIR / name
        assert committed.is_file(), f"{name} missing from docs/demo/data; run scripts/sync_docs.py"
        assert committed.read_bytes() == payload, f"{name} is stale; run scripts/sync_docs.py"

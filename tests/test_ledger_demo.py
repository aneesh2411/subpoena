"""Pair the journal ledger's offline export with its committed Pages artifacts."""

import json

from scripts.export_ledger_demo import DATA_FILE, DEFAULT_OUT, export


def test_ledger_export_matches_pages(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    export(first)
    export(second)

    for artifact in (DATA_FILE, "ledger.html"):
        assert (
            (first / artifact).read_bytes()
            == (second / artifact).read_bytes()
            == (DEFAULT_OUT / artifact).read_bytes()
        )

    inserted, removed, confirmation = json.loads((first / DATA_FILE).read_bytes())
    assert [(row["seq"], row["op"]) for row in (inserted, removed, confirmation)] == [
        (6, "insert"),
        (7, "delete"),
        (8, "insert"),
    ]
    assert inserted["before"] is None and removed["after"] is None
    audit = json.loads((DEFAULT_OUT / "data/audit-aud-fault-rollback.json").read_bytes())
    evidence = {
        item["ref"]: item["record"]
        for verdict in audit["verdicts"]
        for item in verdict["evidence"]
        if item["record"] is not None
    }
    assert inserted["after"] == removed["before"] == evidence[inserted["entity_id"]]
    assert confirmation["after"] == evidence[confirmation["entity_id"]]

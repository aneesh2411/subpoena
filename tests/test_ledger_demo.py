"""Pair the journal ledger's offline export with its committed Pages artifacts."""

import json
from dataclasses import replace

from scripts.export_ledger_demo import DATA_FILE, DEFAULT_OUT, SCENARIO_ID, export
from subpoena.scenarios.golden import BY_ID
from subpoena.suspect.agent import CollectPayment


def test_ledger_export_matches_pages(tmp_path, monkeypatch):
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

    scenario = BY_ID[SCENARIO_ID]
    monkeypatch.setitem(
        BY_ID, SCENARIO_ID, replace(scenario, steps=(*scenario.steps, CollectPayment(4000)))
    )
    with_payment = tmp_path / "with-payment"
    export(with_payment)

    entries = json.loads((with_payment / DATA_FILE).read_bytes())
    assert entries[:-1] == [inserted, removed, confirmation]
    payment = entries[-1]
    assert (payment["seq"], payment["entity"], payment["op"]) == (9, "payment", "insert")
    assert payment["after"]["amount_cents"] == 4000

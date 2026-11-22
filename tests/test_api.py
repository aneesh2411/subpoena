"""HTTP API: the three core endpoints, the ledger, the demo sandbox and auth."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from subpoena.api import Settings, create_app
from subpoena.models import AppointmentBooked
from subpoena.scenarios import BY_ID, HERO, build_world, run_suspect
from subpoena.sor.base import with_outage


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(signing_key=b"k")))


def _call_json(scenario_id: str = "fault-phantom-booking") -> dict:
    scenario = BY_ID[scenario_id]
    world = build_world(scenario)
    try:
        return run_suspect(scenario, world).call.model_dump(mode="json")
    finally:
        world.store.close()


def _data(response, status: int = 200):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["success"] is True and body["error"] is None
    return body["data"]


def _error(response, status: int) -> str:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["success"] is False and body["data"] is None
    return body["error"]


def test_health(client):
    assert _data(client.get("/healthz")) == {"status": "ok", "version": "0.1.0", "signed": True}


def test_ingest_audit_report_flow_against_the_live_system_of_record(client, monkeypatch):
    """Stage a scenario, then drive the three core endpoints the way an integrator would."""
    call = _data(client.post("/demo/scenarios/fault-wrong-time/stage"), 201)
    assert _data(client.post("/calls", json=call), 201) == {"call_id": call["call_id"], "claims": 2}
    assert _data(client.get(f"/calls/{call['call_id']}"))["call_id"] == call["call_id"]

    report = _data(client.post("/audit", json={"call_id": call["call_id"]}), 201)
    assert report["outcome"] == "DISCREPANCIES"
    assert [v["status"] for v in report["verdicts"]] == ["MISMATCH", "VERIFIED"]
    assert report["seal"]["signature"]

    fetched = _data(client.get(f"/audit/{report['audit_id']}/report"))
    assert fetched == report
    md = client.get(f"/audit/{report['audit_id']}/report", params={"format": "md"})
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "RECEIPT DOES NOT MATCH" in md.text

    claim = AppointmentBooked.model_validate(report["verdicts"][0]["claim"])
    path = f"/audit/{report['audit_id']}/claims/{claim.claim_id}/recheck"
    ended = datetime.fromisoformat(call["ended_at"])
    monkeypatch.setattr("subpoena.api.app.utcnow", lambda: ended + timedelta(minutes=2))
    sor = client.app.state.sor
    store = client.app.state.store
    sor_changes = sor._conn.total_changes
    audit_changes = store._conn.total_changes

    checked = _data(client.post(path))
    assert checked["verdict"] == report["verdicts"][0]
    assert checked["audit_id"] != report["audit_id"]
    assert checked["seal"]["prev_hash"] == report["seal"]["chain_hash"]
    assert checked["seal"]["signature"]
    assert sor._conn.total_changes == sor_changes

    # A call-linked retry lands beyond the original audit's grace period.
    writer = sor.session(
        f"{call['agent_id']}:{call['call_id']}", lambda: ended + timedelta(minutes=1)
    )
    (patient,) = writer.crm.find_patients(phone=claim.patient.phone)
    retry = writer.calendar.create_appointment(
        patient_id=patient.id,
        start=claim.start,
        provider=claim.provider,
        duration_min=claim.duration_min,
        reason=claim.reason,
    )
    sor_changes = sor._conn.total_changes
    checked = _data(client.post(path))
    verdict = checked["verdict"]
    assert verdict["status"] == "VERIFIED"
    assert verdict["claim"] == report["verdicts"][0]["claim"]
    assert retry.id in str(verdict["evidence"])
    assert checked["seal"]["prev_hash"] == report["seal"]["chain_hash"]

    session = sor.session
    monkeypatch.setattr(
        sor, "session", lambda actor, clock: with_outage(session(actor, clock), "calendar")
    )
    assert _data(client.post(path))["verdict"]["status"] == "UNVERIFIABLE"
    assert sor._conn.total_changes == sor_changes
    assert store._conn.total_changes == audit_changes
    assert _data(client.get(f"/audit/{report['audit_id']}/report")) == report


def test_claims_with_no_backing_writes_are_not_found(client):
    """An empty system of record: every claim in the call is a claim without a receipt."""
    report = _data(client.post("/audit", json={"call": _call_json("clean-book")}), 201)
    assert [v["status"] for v in report["verdicts"]] == ["NOT_FOUND", "NOT_FOUND"]


def test_duplicate_and_missing_resources(client):
    call = _call_json()
    _data(client.post("/calls", json=call), 201)
    assert "already exists" in _error(client.post("/calls", json=call), 409)
    assert "not found" in _error(client.get("/calls/nope"), 404)
    assert "not found" in _error(client.post("/audit", json={"call_id": "nope"}), 404)
    assert "not found" in _error(client.get("/audit/nope/report"), 404)
    assert "not found" in _error(client.post("/demo/scenarios/nope/run"), 404)
    assert "audit" in _error(client.post("/audit/nope/claims/nope/recheck"), 404)
    report = _data(client.post("/audit", json={"call_id": call["call_id"]}), 201)
    assert "claim" in _error(client.post(f"/audit/{report['audit_id']}/claims/nope/recheck"), 404)


def test_conflicting_inline_call_is_rejected_identical_is_idempotent(client):
    call = _call_json()
    _data(client.post("/calls", json=call), 201)
    # Same call again, inline: idempotent, audits fine.
    report = _data(client.post("/audit", json={"call": call}), 201)
    assert report["audit_id"]
    # Same id but different contents: a conflict, not a silent overwrite.
    tampered = {**call, "caller": {**call["caller"], "name": "Not Maya Chen"}}
    assert "different contents" in _error(client.post("/audit", json={"call": tampered}), 409)


def test_stage_swap_is_serialized(client):
    # The lock exists and two staged scenarios land cleanly one after another.
    assert hasattr(client.app.state, "stage_lock")
    first = _data(client.post("/demo/scenarios/clean-book/stage"), 201)
    second = _data(client.post("/demo/scenarios/fault-wrong-time/stage"), 201)
    assert first["call_id"] != second["call_id"]


def test_invalid_requests_are_rejected_with_a_clear_message(client):
    call = _call_json()
    assert "exactly one" in _error(client.post("/audit", json={}), 422)
    assert "exactly one" in _error(client.post("/audit", json={"call_id": "x", "call": call}), 422)
    backwards = {**call, "ended_at": "2020-01-01T00:00:00Z"}
    assert "ended_at" in _error(client.post("/calls", json=backwards), 422)
    naive_time = {**call, "started_at": "2026-03-02T09:05:00"}
    assert "started_at" in _error(client.post("/calls", json=naive_time), 422)
    bad_id = {**call, "call_id": "has spaces/and?stuff"}
    assert "call_id" in _error(client.post("/calls", json=bad_id), 422)
    unknown_claim = {**call, "claims": [{"type": "MoneyWired", "claim_id": "c", "turn_index": 0}]}
    _error(client.post("/calls", json=unknown_claim), 422)
    assert "invalid" in _error(client.get("/audit/x/report", params={"format": "pdf"}), 422)


def test_demo_run_ledgers_every_report_and_the_chain_verifies(client):
    ids = [
        _data(client.post(f"/demo/scenarios/{sid}/run"), 201)["audit_id"]
        for sid in (HERO, "clean-book", HERO)
    ]
    assert len(set(ids)) == 3  # re-running a scenario creates a new ledger entry
    listed = _data(client.get("/audits"))
    assert [a["audit_id"] for a in listed] == ids[::-1]
    assert listed[0]["outcome"] == "DISCREPANCIES" and listed[1]["outcome"] == "CLEAN"
    verify = _data(client.get("/ledger/verify"))
    assert verify["ok"] is True and verify["checked"] == 3

    hero = _data(client.get(f"/audit/{ids[0]}/report"))
    assert [v["status"] for v in hero["verdicts"]] == [
        "MISMATCH", "NOT_FOUND", "NOT_FOUND", "VERIFIED",
    ]  # fmt: skip


def test_ledger_is_append_only_and_detects_tampering(client):
    _data(client.post(f"/demo/scenarios/{HERO}/run"), 201)
    store = client.app.state.store
    with pytest.raises(Exception, match="append-only"):
        store._conn.execute("UPDATE audits SET body = '{}'")
    with pytest.raises(Exception, match="append-only"):
        store._conn.execute("DELETE FROM audits")


def test_ledger_verify_reports_a_broken_chain(client):
    _data(client.post("/demo/scenarios/clean-book/run"), 201)
    _data(client.post(f"/demo/scenarios/{HERO}/run"), 201)
    conn = client.app.state.store._conn
    conn.execute("DROP TRIGGER audits_no_delete")
    conn.execute("DELETE FROM audits WHERE seq = 1")  # someone removes an inconvenient audit
    verify = _data(client.get("/ledger/verify"))
    assert verify["ok"] is False and "not linked" in verify["reason"]


def test_scorecard_aggregates_by_agent(client):
    assert _data(client.get("/scorecard")) == {}
    for sid in (HERO, "clean-book"):
        _data(client.post(f"/demo/scenarios/{sid}/run"), 201)
    card = _data(client.get("/scorecard"))["agent:receptionist"]
    assert (card["calls"], card["calls_with_discrepancies"]) == (2, 1)
    assert (card["claims"], card["verified"]) == (6, 3)
    assert card["claim_honesty"] == 0.5
    assert card["by_claim_type"]["HandoffCreated"] == {"claims": 1, "verified": 0}


def test_scenario_catalogue(client):
    scenarios = _data(client.get("/demo/scenarios"))
    assert len(scenarios) == len(BY_ID)
    hero = next(s for s in scenarios if s["id"] == HERO)
    assert hero["group"] == "edge" and "wrong_time" in hero["faults"]
    assert sum(1 for s in scenarios if s["known_gap"]) == 2


def test_replay_page_is_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "SUBPOENA" in page.text
    assert "innerHTML" not in page.text  # transcript text is untrusted: textContent only


def test_api_key_is_enforced_when_configured():
    client = TestClient(create_app(Settings(api_key="s3cret")))
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200
    for method, path in (("get", "/audits"), ("post", "/calls"), ("get", "/demo/scenarios"),
                         ("get", "/scorecard"), ("get", "/audit/x/report"),
                         ("post", "/audit/x/claims/y/recheck")):  # fmt: skip
        assert "X-API-Key" in _error(getattr(client, method)(path), 401)
    assert "X-API-Key" in _error(client.get("/audits", headers={"X-API-Key": "wrong"}), 401)
    assert _data(client.get("/audits", headers={"X-API-Key": "s3cret"})) == []


def test_demo_endpoints_can_be_disabled():
    client = TestClient(create_app(Settings(demo=False)))
    assert "disabled" in _error(client.get("/demo/scenarios"), 404)
    assert "disabled" in _error(client.post(f"/demo/scenarios/{HERO}/run"), 404)


def test_staging_refuses_to_wipe_a_persistent_system_of_record(tmp_path):
    settings = Settings(sor_path=str(tmp_path / "sor.db"), db_path=str(tmp_path / "audit.db"))
    client = TestClient(create_app(settings))
    assert "in-memory" in _error(client.post(f"/demo/scenarios/{HERO}/stage"), 409)


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("SUBPOENA_API_KEY", "abc")
    monkeypatch.setenv("SUBPOENA_SIGNING_KEY", "sign")
    monkeypatch.setenv("SUBPOENA_DEMO", "0")
    settings = Settings.from_env()
    assert (settings.api_key, settings.signing_key, settings.demo) == ("abc", b"sign", False)
    assert settings.db_path == settings.sor_path == ":memory:"


def test_unhandled_errors_do_not_leak_details(client, monkeypatch):
    def boom(*_: object, **__: object):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("subpoena.api.app.render_markdown", boom)
    audit_id = _data(client.post("/demo/scenarios/clean-book/run"), 201)["audit_id"]
    quiet = TestClient(client.app, raise_server_exceptions=False)
    response = quiet.get(f"/audit/{audit_id}/report", params={"format": "md"})
    assert _error(response, 500) == "internal error"

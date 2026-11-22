import hmac
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from subpoena.api.app import Settings, create_app

SECRET = b"test-ingest-secret"
PAYLOAD = {
    "platform": "voice-platform",
    "call_id": "ext/42",
    "agent_id": "agent-1",
    "started_at": "2026-11-22T15:00:00Z",
    "ended_at": "2026-11-22T15:01:00Z",
    "transcript": [{"index": 0, "speaker": "agent", "text": "Hello", "at": "2026-11-22T15:00:05Z"}],
    "metadata": {"caller": {"name": "Example Caller"}, "agent_version": "v2", "custom": [1]},
}


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBPOENA_INGEST_SECRET", SECRET.decode())
    monkeypatch.setenv("SUBPOENA_DB", str(tmp_path / "calls.db"))
    app = create_app(Settings.from_env())
    with TestClient(app) as client, sqlite3.connect(app.state.settings.db_path) as db:
        yield client, db
    app.state.store.close()


def _body(**changes):
    return (json.dumps(PAYLOAD | changes, indent=2) + "\n").encode()


def _post(client, body):
    signature = hmac.digest(SECRET, body, "sha256").hex()
    return client.post("/ingest/calls", content=body, headers={"X-Subpoena-Signature": signature})


def _assert_empty(db):
    assert db.execute("SELECT count(*) FROM calls").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM call_deliveries").fetchone() == (0,)


def test_ingest_persists_call_and_exact_body(receiver):
    client, db = receiver
    body = _body()
    response = _post(client, body)
    assert response.status_code == 201
    call_id = response.json()["data"]["call_id"]
    result = client.get(f"/calls/{call_id}").json()["data"]
    for field in ("agent_id", "started_at", "ended_at", "transcript"):
        assert result[field] == PAYLOAD[field]
    assert result["claims"] == []
    assert result["provenance"]["platform"] == PAYLOAD["platform"]
    assert result["provenance"]["agent_version"] == "v2"
    assert result["caller"]["name"] == "Example Caller"
    assert db.execute(
        "SELECT platform, external_call_id, call_id, raw_body FROM call_deliveries"
    ).fetchall() == [(PAYLOAD["platform"], PAYLOAD["call_id"], call_id, body)]


@pytest.mark.parametrize("headers", [{}, {"X-Subpoena-Signature": "0" * 64}])
def test_ingest_rejects_unverified_body_before_parsing(receiver, headers):
    client, db = receiver
    response = client.post("/ingest/calls", content=b"{", headers=headers)
    assert response.status_code == 401
    assert "X-Subpoena-Signature" in response.json()["error"]
    _assert_empty(db)


def test_ingest_requires_a_configured_secret():
    with TestClient(create_app(Settings())) as client:
        assert _post(client, _body()).status_code == 401


@pytest.mark.parametrize(
    ("body", "status", "reason"),
    [
        (b"{", 400, "UTF-8 JSON"),
        (json.dumps({k: v for k, v in PAYLOAD.items() if k != "agent_id"}), 422, "agent_id"),
        (_body(ended_at="2026-11-22T14:00:00Z", transcript=[]), 422, "ended_at"),
        (_body(transcript=[{"index": 0}]), 422, "transcript.0.speaker"),
    ],
)
def test_ingest_rejects_invalid_deliveries(receiver, body, status, reason):
    client, db = receiver
    response = _post(client, body.encode() if isinstance(body, str) else body)
    assert response.status_code == status
    assert reason in response.json()["error"]
    _assert_empty(db)


def test_ingest_deduplicates_on_platform_and_external_call_id(receiver):
    client, db = receiver
    body = _body()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: _post(client, body), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 201]
    first, repeated = (response.json()["data"]["call_id"] for response in responses)
    assert repeated == first

    changed = _post(client, _body(agent_id="changed"))
    assert changed.status_code == 200
    assert changed.json()["data"]["call_id"] == first
    assert db.execute("SELECT raw_body FROM call_deliveries").fetchall() == [(body,)]
    assert db.execute("SELECT count(*) FROM calls").fetchone() == (1,)

    other_platform = _post(client, _body(platform="another-platform"))
    assert other_platform.status_code == 201
    assert other_platform.json()["data"]["call_id"] != first
    assert db.execute("SELECT count(*) FROM calls").fetchone() == (2,)

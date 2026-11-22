"""Opening a pre-change_seq database must migrate it and keep revert working.

The first applied_fixes schema had no journal pointer, so revert falls back
to the single unattributed update for the fix's actor and record. Pin both
the ALTER TABLE migration and that legacy path against a real old file.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from subpoena.sor.sqlite import FixReceiptConflict, SqliteStore

OLD_DDL = """
CREATE TABLE change_log(
    seq INTEGER PRIMARY KEY,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    op TEXT NOT NULL,
    before TEXT,
    after TEXT,
    actor TEXT NOT NULL,
    at TEXT NOT NULL);
CREATE TABLE applied_fixes(
    audit_id TEXT NOT NULL, claim_id TEXT NOT NULL, approval_id INTEGER NOT NULL,
    record_id TEXT NOT NULL, PRIMARY KEY(audit_id, claim_id));
INSERT INTO change_log (entity, entity_id, op, before, after, actor, at)
VALUES ('patient', 'pat-1', 'update',
        '{"address": null}', '{"address": "999 Fix Ave"}', 'agent-x:call-1',
        '2026-03-15T10:00:00+00:00');
INSERT INTO applied_fixes (audit_id, claim_id, approval_id, record_id)
VALUES ('aud-old', 'claim-1', 7, 'pat-1');
"""

CLOCK = lambda: datetime(2026, 3, 15, 11, 0, tzinfo=UTC)  # noqa: E731


@pytest.fixture
def old_db(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_DDL)
    conn.close()
    return path


def test_old_db_migrates_and_preserves_receipts(old_db):
    store = SqliteStore(str(old_db))
    try:
        columns = {c["name"] for c in store._conn.execute("PRAGMA table_info(applied_fixes)")}
        assert "change_seq" in columns
        row = store._conn.execute(
            "SELECT * FROM applied_fixes WHERE audit_id = 'aud-old'"
        ).fetchone()
        assert row["record_id"] == "pat-1"
        assert row["change_seq"] is None
    finally:
        store.close()


def test_reverted_fixes_is_append_only(old_db):
    store = SqliteStore(str(old_db))
    try:
        store._conn.execute("BEGIN")
        store._conn.execute(
            "INSERT INTO reverted_fixes (audit_id, claim_id) VALUES ('aud-old', 'claim-1')"
        )
        for bad in (
            "UPDATE reverted_fixes SET claim_id = 'other'",
            "DELETE FROM reverted_fixes",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                store._conn.execute(bad)
        store._conn.execute("ROLLBACK")
    finally:
        store.close()


def test_revert_on_old_receipt_uses_the_journal_before_image(old_db):
    store = SqliteStore(str(old_db))
    captured = {}

    def inverse(session, record_id, change):
        captured["record_id"] = record_id
        captured["change"] = change

    try:
        approval_id, record_id = store.revert_fix(
            "aud-old", "claim-1", "agent-x:call-1", CLOCK, inverse
        )
        assert (approval_id, record_id) == (7, "pat-1")
        change = captured["change"]
        assert change is not None
        assert change.before == {"address": None}
        assert change.after == {"address": "999 Fix Ave"}
        with pytest.raises(FixReceiptConflict, match="already been reverted"):
            store.revert_fix("aud-old", "claim-1", "agent-x:call-1", CLOCK, inverse)
    finally:
        store.close()

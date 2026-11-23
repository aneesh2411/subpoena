"""Persistence for ingested calls and the append-only audit ledger."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator

from subpoena.api import approvals, review_store, revisions
from subpoena.api.fixes import FIX_SCHEMA
from subpoena.api.review import Receipt, TransitionRequest
from subpoena.api.webhook import SCHEMA as WEBHOOK_SCHEMA
from subpoena.api.webhook import write_transaction
from subpoena.auditor.seal import GENESIS, Sealer
from subpoena.models import AuditReport, CallRecord, Outcome

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls(call_id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS call_deliveries(
    platform TEXT NOT NULL, external_call_id TEXT NOT NULL,
    call_id TEXT NOT NULL UNIQUE REFERENCES calls(call_id), raw_body BLOB NOT NULL,
    PRIMARY KEY(platform, external_call_id));
CREATE TABLE IF NOT EXISTS audits(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, audit_id TEXT NOT NULL UNIQUE,
    call_id TEXT NOT NULL, chain_hash TEXT NOT NULL, body TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audits_no_update BEFORE UPDATE ON audits
    BEGIN SELECT RAISE(ABORT, 'the audit ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audits_no_delete BEFORE DELETE ON audits
    BEGIN SELECT RAISE(ABORT, 'the audit ledger is append-only'); END;
"""


class AuditStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(
                _SCHEMA
                + review_store.REVIEW_SCHEMA
                + revisions.REVISION_SCHEMA
                + WEBHOOK_SCHEMA
                + approvals.APPROVAL_SCHEMA
                + FIX_SCHEMA
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- calls -------------------------------------------------------------- #

    def save_call(self, call: CallRecord) -> bool:
        """Store a call. Returns False if that call id is already stored."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO calls(call_id, body) VALUES (?, ?)",
                (call.call_id, call.model_dump_json()),
            )
            return cur.rowcount == 1

    def save_call_delivery(
        self, platform: str, external_call_id: str, call: CallRecord, body: bytes
    ) -> tuple[str, bool]:
        """Store the first delivery and its call atomically, returning (call_id, created)."""
        with self._lock, write_transaction(self._conn):
            existing = self._conn.execute(
                "SELECT call_id FROM call_deliveries WHERE platform = ? AND external_call_id = ?",
                (platform, external_call_id),
            ).fetchone()
            if existing is not None:
                return existing[0], False
            self._conn.execute(
                "INSERT INTO calls(call_id, body) VALUES (?, ?)",
                (call.call_id, call.model_dump_json()),
            )
            self._conn.execute(
                "INSERT INTO call_deliveries(platform, external_call_id, call_id, raw_body) "
                "VALUES (?, ?, ?, ?)",
                (platform, external_call_id, call.call_id, body),
            )
        return call.call_id, True

    def get_call(self, call_id: str) -> CallRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT body FROM calls WHERE call_id = ?", (call_id,)
            ).fetchone()
        return CallRecord.model_validate_json(row[0]) if row else None

    # -- ledger ------------------------------------------------------------- #

    def head(self) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT chain_hash FROM audits ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else GENESIS

    def append(
        self,
        report: AuditReport,
        sealer: Sealer,
        *,
        enqueue: Callable[[sqlite3.Connection, AuditReport], None] | None = None,
        revision_key: bytes | None = None,
    ) -> AuditReport:
        """Seal ``report`` onto the end of the ledger and store it, atomically.

        A re-audit of an already-audited call also records one verdict
        revision per claim whose verdict changed, in the same transaction.
        """
        with self._lock, write_transaction(self._conn):
            sealed = sealer.seal(report, self.head())
            assert sealed.seal is not None
            prior = self._conn.execute(
                "SELECT body FROM audits WHERE call_id = ? ORDER BY seq DESC LIMIT 1",
                (sealed.call.call_id,),
            ).fetchone()
            self._conn.execute(
                "INSERT INTO audits(audit_id, call_id, chain_hash, body) VALUES (?, ?, ?, ?)",
                (
                    sealed.audit_id,
                    sealed.call.call_id,
                    sealed.seal.chain_hash,
                    sealed.model_dump_json(),
                ),
            )
            if prior is not None:
                revisions.record_reaudit_revisions(
                    self._conn,
                    revision_key,
                    old=AuditReport.model_validate_json(prior[0]),
                    new=sealed,
                )
            if enqueue is not None:
                enqueue(self._conn, sealed)
        return sealed

    def get_audit(self, audit_id: str) -> AuditReport | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT body FROM audits WHERE audit_id = ?", (audit_id,)
            ).fetchone()
        return AuditReport.model_validate_json(row[0]) if row else None

    def save_fix_recheck(self, original_audit_id: str, report: AuditReport) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO fix_rechecks(audit_id, original_audit_id, body) VALUES (?, ?, ?)",
                (report.audit_id, original_audit_id, report.model_dump_json()),
            )

    def get_fix_recheck(self, audit_id: str) -> AuditReport | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT body FROM fix_rechecks WHERE audit_id = ?", (audit_id,)
            ).fetchone()
        return AuditReport.model_validate_json(row[0]) if row else None

    def audits(self, limit: int | None = None, newest_first: bool = False) -> Iterator[AuditReport]:
        for _, _, body in self.audit_rows(limit=limit, newest_first=newest_first):
            yield AuditReport.model_validate_json(body)

    def recent_audits(
        self, limit: int, outcome: Outcome | None, has_escalations: bool | None
    ) -> Iterator[AuditReport]:
        """Return matching ledger reports, newest appended first."""
        if outcome is None and has_escalations is None:
            yield from self.audits(limit=limit, newest_first=True)
            return
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT body FROM audits
                WHERE (? IS NULL OR json_extract(body, '$.outcome') = ?)
                  AND (? IS NULL OR (COALESCE(
                      json_array_length(body, '$.escalation_log'), 0
                  ) > 0) = ?)
                ORDER BY seq DESC
                LIMIT ?
                """,
                (outcome, outcome, has_escalations, has_escalations, limit),
            ).fetchall()
        for (body,) in rows:
            yield AuditReport.model_validate_json(body)

    def audit_rows(
        self, limit: int | None = None, newest_first: bool = False
    ) -> list[tuple[str, str, str]]:
        """Read a snapshot with ledger metadata, even if a report body cannot be decoded."""
        order = "DESC" if newest_first else "ASC"
        sql = f"SELECT audit_id, chain_hash, body FROM audits ORDER BY seq {order}"
        params: tuple[int, ...] = ()
        if limit is not None:
            sql, params = sql + " LIMIT ?", (limit,)
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def append_approval(
        self, audit_id: str, request: approvals.ApprovalRequest
    ) -> approvals.Approval:
        with self._lock:
            return approvals.append_approval(self._conn, audit_id, request)

    def list_approvals(self, audit_id: str) -> list[approvals.Approval]:
        with self._lock:
            return approvals.list_approvals(self._conn, audit_id)

    def list_revisions(self, key: bytes | None) -> dict:
        with self._lock:
            return revisions.list_revisions(self._conn, key)

    def list_reviews(self, key: bytes | None, checkpoint: str | None = None) -> dict:
        with self._lock:
            return review_store.list_reviews(self._conn, key, checkpoint)

    def transition_review(
        self,
        key: bytes | None,
        item_id: str,
        request: TransitionRequest,
        checkpoint: str | None = None,
    ) -> Receipt:
        with self._lock:
            return review_store.transition_review(self._conn, key, item_id, request, checkpoint)

"""SQLite system of record with a store-level change journal.

The journal is written by **database triggers**, not by application code. An
agent that "forgets" to log a write still leaves a journal row, and an agent
that claims a write it never made leaves none. The journal is append-only:
triggers reject UPDATE and DELETE on it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, get_args

from subpoena.models import (
    Appointment,
    AppointmentStatus,
    Change,
    Channel,
    Entity,
    HandoffTopic,
    Medication,
    Message,
    Patient,
    Payment,
    PaymentAmount,
    PaymentMethod,
    Prescription,
    Priority,
    RecordField,
    Ticket,
)
from subpoena.sor.base import AdapterUnavailable, RecordNotFound, SystemOfRecord
from subpoena.util import norm_phone, norm_text, to_utc_iso

Clock = Callable[[], datetime]

_TABLES: dict[Entity, tuple[str, tuple[str, ...]]] = {
    Entity.PATIENT: ("patients", ("id", "name", "phone", "email")),
    Entity.APPOINTMENT: (
        "appointments",
        ("id", "patient_id", "provider", "start", "duration_min", "reason", "status"),
    ),
    Entity.MESSAGE: ("messages", ("id", "recipient", "channel", "body", "status", "sent_at")),
    Entity.TICKET: (
        "tickets",
        ("id", "patient_id", "summary", "priority", "status", "assignee", "created_at"),
    ),
    Entity.PAYMENT: ("payments", ("id", "patient_id", "amount_cents", "method", "collected_at")),
    Entity.PRESCRIPTION: ("prescriptions", ("id", "patient_id", "medication", "refilled_at")),
}
_ID_PREFIX = {
    Entity.PATIENT: "pat",
    Entity.APPOINTMENT: "apt",
    Entity.MESSAGE: "msg",
    Entity.TICKET: "tkt",
    Entity.PAYMENT: "pay",
    Entity.PRESCRIPTION: "rx",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS patients(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL, email TEXT,
    address TEXT, insurance TEXT);
CREATE TABLE IF NOT EXISTS appointments(
    id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id),
    provider TEXT NOT NULL, start TEXT NOT NULL, duration_min INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'booked');
CREATE TABLE IF NOT EXISTS messages(
    id TEXT PRIMARY KEY, recipient TEXT NOT NULL, channel TEXT NOT NULL,
    body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'sent', sent_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tickets(
    id TEXT PRIMARY KEY, patient_id TEXT, summary TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'open',
    assignee TEXT, created_at TEXT NOT NULL, topic TEXT);
CREATE TABLE IF NOT EXISTS payments(
    id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id),
    amount_cents INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer' AND amount_cents > 0),
    method TEXT NOT NULL CHECK (method IN ('card', 'cash', 'check')), collected_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prescriptions(
    id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id),
    medication TEXT NOT NULL CHECK (length(trim(medication)) BETWEEN 1 AND 4000),
    refilled_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS applied_fixes(
    audit_id TEXT NOT NULL, claim_id TEXT NOT NULL, approval_id INTEGER NOT NULL,
    record_id TEXT NOT NULL, change_seq INTEGER REFERENCES change_log(seq),
    PRIMARY KEY(audit_id, claim_id));
CREATE TABLE IF NOT EXISTS reverted_fixes(
    audit_id TEXT NOT NULL, claim_id TEXT NOT NULL,
    PRIMARY KEY(audit_id, claim_id),
    FOREIGN KEY(audit_id, claim_id) REFERENCES applied_fixes(audit_id, claim_id));
CREATE TRIGGER IF NOT EXISTS reverted_fixes_no_update BEFORE UPDATE ON reverted_fixes
    BEGIN SELECT RAISE(ABORT, 'reverted_fixes is append-only'); END;
CREATE TRIGGER IF NOT EXISTS reverted_fixes_no_delete BEFORE DELETE ON reverted_fixes
    BEGIN SELECT RAISE(ABORT, 'reverted_fixes is append-only'); END;
CREATE TABLE IF NOT EXISTS id_seq(entity TEXT PRIMARY KEY, n INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS session_ctx(
    id INTEGER PRIMARY KEY CHECK (id = 1), actor TEXT NOT NULL, now TEXT NOT NULL);
INSERT OR IGNORE INTO session_ctx(id, actor, now)
    VALUES (1, 'bootstrap', '1970-01-01T00:00:00+00:00');
CREATE TABLE IF NOT EXISTS change_log(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
    op TEXT NOT NULL, before TEXT, after TEXT, actor TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_change_log_at ON change_log(entity, at);
CREATE TRIGGER IF NOT EXISTS change_log_no_update BEFORE UPDATE ON change_log
    BEGIN SELECT RAISE(ABORT, 'change_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_log_no_delete BEFORE DELETE ON change_log
    BEGIN SELECT RAISE(ABORT, 'change_log is append-only'); END;
"""


def _journal_triggers() -> list[str]:
    """Generate INSERT/UPDATE/DELETE journal triggers for every audited table."""
    ctx = "(SELECT actor FROM session_ctx WHERE id = 1), (SELECT now FROM session_ctx WHERE id = 1)"
    sql: list[str] = []
    for entity, (table, cols) in _TABLES.items():

        def snap(alias: str, cols: tuple[str, ...] = cols, entity: Entity = entity) -> str:
            snapshot = "json_object(" + ", ".join(f"'{c}', {alias}.{c}" for c in cols) + ")"
            if entity is Entity.PATIENT:
                # Keep existing receipts stable when the new fields are absent.
                return (
                    f"json_patch({snapshot}, json_object('address', {alias}.address, "
                    f"'insurance', {alias}.insurance))"
                )
            if entity is Entity.TICKET:
                # Preserve the shape of historical evidence when no topic is recorded.
                return f"json_patch({snapshot}, json_object('topic', {alias}.topic))"
            return snapshot

        for op, when, ident, before, after in (
            ("insert", "INSERT", "NEW.id", "NULL", snap("NEW")),
            ("update", "UPDATE", "NEW.id", snap("OLD"), snap("NEW")),
            ("delete", "DELETE", "OLD.id", snap("OLD"), "NULL"),
        ):
            sql.append(
                f"CREATE TRIGGER IF NOT EXISTS journal_{table}_{op} AFTER {when} ON {table} "
                f"BEGIN INSERT INTO change_log(entity, entity_id, op, before, after, actor, at) "
                f"VALUES ('{entity.value}', {ident}, '{op}', {before}, {after}, {ctx}); END;"
            )
    return sql


class FixReceiptConflict(ValueError):
    """The requested operation conflicts with the fix receipt history."""


class SqliteStore:
    """SQLite storage for the audited systems of record."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(_SCHEMA)
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                columns = self._conn.execute("PRAGMA table_info(tickets)").fetchall()
                if "topic" not in {col["name"] for col in columns}:
                    self._conn.execute("ALTER TABLE tickets ADD COLUMN topic TEXT")
                    for op in ("insert", "update", "delete"):
                        self._conn.execute(f"DROP TRIGGER IF EXISTS journal_tickets_{op}")
                columns = {col["name"] for col in self._conn.execute("PRAGMA table_info(patients)")}
                missing = {"address", "insurance"} - columns
                for column in sorted(missing):
                    self._conn.execute(f"ALTER TABLE patients ADD COLUMN {column} TEXT")
                if missing:
                    for op in ("insert", "update", "delete"):
                        self._conn.execute(f"DROP TRIGGER IF EXISTS journal_patients_{op}")
                columns = {
                    c["name"] for c in self._conn.execute("PRAGMA table_info(applied_fixes)")
                }
                if "change_seq" not in columns:
                    self._conn.execute(
                        "ALTER TABLE applied_fixes ADD COLUMN change_seq "
                        "INTEGER REFERENCES change_log(seq)"
                    )
                for trigger in _journal_triggers():
                    self._conn.execute(trigger)
            except sqlite3.Error:
                self._conn.rollback()
                self._conn.close()
                raise
            else:
                self._conn.commit()

    def session(self, actor: str, clock: Clock) -> SystemOfRecord:
        """A handle whose writes are journaled under ``actor`` at ``clock()``."""
        return SystemOfRecord(
            calendar=_Calendar(self, actor, clock),
            crm=_Crm(self, actor, clock),
            messages=_Messages(self, actor, clock),
            tickets=_Tickets(self, actor, clock),
            payments=_Payments(self, actor, clock),
            prescriptions=_Prescriptions(self, actor, clock),
        )

    def apply_fix(
        self,
        audit_id: str,
        claim_id: str,
        approval_id: int,
        actor: str,
        clock: Clock,
        write: Callable[[SystemOfRecord], str],
    ) -> tuple[int, str, bool]:
        """Commit the adapter write and its retry receipt in one transaction."""
        with self._fix_transaction() as conn:
            existing = self._fix_receipt(audit_id, claim_id)
            if existing is not None:
                return existing["approval_id"], existing["record_id"], False
            previous_seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM change_log").fetchone()[
                0
            ]
            record_id = write(self.session(actor, clock))
            changes = conn.execute(
                "SELECT seq FROM change_log WHERE seq > ? AND entity_id = ? AND actor = ?",
                (previous_seq, record_id, actor),
            ).fetchall()
            if len(changes) != 1:
                raise AdapterUnavailable("the fix did not produce a single journal receipt")
            conn.execute(
                "INSERT INTO applied_fixes (audit_id, claim_id, approval_id, record_id, change_seq)"
                " VALUES (?, ?, ?, ?, ?)",
                (audit_id, claim_id, approval_id, record_id, changes[0]["seq"]),
            )
            return approval_id, record_id, True

    def revert_fix(
        self,
        audit_id: str,
        claim_id: str,
        actor: str,
        clock: Clock,
        inverse: Callable[[SystemOfRecord, str, Change | None], None],
    ) -> tuple[int, str]:
        """Commit the inverse adapter write and a new receipt together."""
        with self._fix_transaction() as conn:
            receipt = self._fix_receipt(audit_id, claim_id)
            if receipt is None:
                raise FixReceiptConflict(f"no applied fix for claim {claim_id}")
            record_id = receipt["record_id"]
            if receipt["change_seq"] is None:
                # Old receipts lack a journal pointer. Use a before-image only
                # when exactly one update can belong to this call and record.
                rows = conn.execute(
                    "SELECT * FROM change_log WHERE actor = ? AND entity_id = ? AND op = 'update' "
                    "AND NOT EXISTS (SELECT 1 FROM applied_fixes "
                    "WHERE change_seq = change_log.seq) "
                    "LIMIT 2",
                    (actor, record_id),
                ).fetchall()
                row = rows[0] if len(rows) == 1 else None
            else:
                row = conn.execute(
                    "SELECT * FROM change_log WHERE seq = ?", (receipt["change_seq"],)
                ).fetchone()
            try:
                inverse(self.session(actor, clock), record_id, _change(row) if row else None)
            except RecordNotFound as exc:
                raise AdapterUnavailable(str(exc)) from exc
            conn.execute(
                "INSERT INTO reverted_fixes (audit_id, claim_id) VALUES (?, ?)",
                (audit_id, claim_id),
            )
            return receipt["approval_id"], record_id

    def _fix_receipt(self, audit_id: str, claim_id: str) -> sqlite3.Row | None:
        receipt = self._conn.execute(
            "SELECT applied_fixes.*, reverted_fixes.claim_id IS NOT NULL AS reverted "
            "FROM applied_fixes LEFT JOIN reverted_fixes USING (audit_id, claim_id) "
            "WHERE audit_id = ? AND claim_id = ?",
            (audit_id, claim_id),
        ).fetchone()
        if receipt is not None and receipt["reverted"]:
            raise FixReceiptConflict(f"fix for claim {claim_id} has already been reverted")
        return receipt

    @contextmanager
    def _fix_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- internals shared by the adapters ---------------------------------- #

    @contextmanager
    def _write(self, actor: str, now: datetime) -> Iterator[sqlite3.Connection]:
        with self._lock:
            nested = self._conn.in_transaction
            self._conn.execute("SAVEPOINT adapter_write" if nested else "BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE session_ctx SET actor = ?, now = ? WHERE id = 1",
                    (actor, to_utc_iso(now)),
                )
                yield self._conn
            except BaseException:
                if nested:
                    self._conn.execute("ROLLBACK TO adapter_write")
                    self._conn.execute("RELEASE adapter_write")
                else:
                    self._conn.rollback()
                raise
            else:
                self._conn.execute("RELEASE adapter_write" if nested else "COMMIT")

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    @staticmethod
    def _next_id(conn: sqlite3.Connection, entity: Entity) -> str:
        row = conn.execute(
            "INSERT INTO id_seq(entity, n) VALUES (?, 1) "
            "ON CONFLICT(entity) DO UPDATE SET n = n + 1 RETURNING n",
            (entity.value,),
        ).fetchone()
        return f"{_ID_PREFIX[entity]}_{row[0]:04d}"

    def _changes(self, entity: Entity, since: datetime, until: datetime) -> list[Change]:
        rows = self._rows(
            "SELECT * FROM change_log WHERE entity = ? AND at >= ? AND at <= ? ORDER BY seq",
            (entity.value, to_utc_iso(since), to_utc_iso(until)),
        )
        return [_change(row) for row in rows]


def _change(row: sqlite3.Row | dict[str, Any]) -> Change:
    return Change(
        seq=row["seq"],
        entity=row["entity"],
        entity_id=row["entity_id"],
        op=row["op"],
        before=json.loads(row["before"]) if row["before"] else None,
        after=json.loads(row["after"]) if row["after"] else None,
        actor=row["actor"],
        at=row["at"],
    )


class _Adapter:
    entity: Entity

    def __init__(self, store: SqliteStore, actor: str, clock: Clock) -> None:
        self._store = store
        self._actor = actor
        self._clock = clock

    def _tx(self) -> Any:
        return self._store._write(self._actor, self._clock())

    def _delete(self, record_id: str) -> None:
        table, _ = _TABLES[self.entity]
        with self._tx() as conn:
            if not conn.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,)).rowcount:
                raise RecordNotFound(f"{self.entity.value} {record_id} does not exist")

    def changes(self, since: datetime, until: datetime) -> list[Change]:
        return self._store._changes(self.entity, since, until)


class _Calendar(_Adapter):
    entity = Entity.APPOINTMENT

    def create_appointment(
        self,
        *,
        patient_id: str,
        provider: str,
        start: datetime,
        duration_min: int = 30,
        reason: str = "",
    ) -> Appointment:
        with self._tx() as conn:
            new_id = self._store._next_id(conn, Entity.APPOINTMENT)
            conn.execute(
                "INSERT INTO appointments(id, patient_id, provider, start, duration_min, reason)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (new_id, patient_id, provider, to_utc_iso(start), duration_min, reason),
            )
        return self._require(new_id)

    def get_appointment(self, appointment_id: str) -> Appointment | None:
        rows = self._store._rows("SELECT * FROM appointments WHERE id = ?", (appointment_id,))
        return Appointment.model_validate(rows[0]) if rows else None

    def list_appointments(
        self,
        *,
        patient_id: str | None = None,
        provider: str | None = None,
        window: tuple[datetime, datetime] | None = None,
        include_cancelled: bool = False,
    ) -> list[Appointment]:
        sql, params = "SELECT * FROM appointments WHERE 1 = 1", []
        if patient_id is not None:
            sql, params = sql + " AND patient_id = ?", [*params, patient_id]
        if provider is not None:
            sql, params = sql + " AND provider = ?", [*params, provider]
        if not include_cancelled:
            sql += " AND status = 'booked'"
        appts = [
            Appointment.model_validate(r)
            for r in self._store._rows(sql + " ORDER BY start, id", tuple(params))
        ]
        if window is None:
            return appts
        lo, hi = window
        return [a for a in appts if a.start < hi and a.end > lo]

    def reschedule_appointment(self, appointment_id: str, new_start: datetime) -> Appointment:
        self._require(appointment_id)
        with self._tx() as conn:
            conn.execute(
                "UPDATE appointments SET start = ? WHERE id = ?",
                (to_utc_iso(new_start), appointment_id),
            )
        return self._require(appointment_id)

    _EDITABLE = frozenset({"patient_id", "provider", "duration_min", "reason"})

    def update_appointment(self, appointment_id: str, **fields: Any) -> Appointment:
        unknown = set(fields) - self._EDITABLE
        if unknown:
            raise ValueError(f"cannot edit appointment fields: {sorted(unknown)}")
        self._require(appointment_id)
        if fields:
            columns = ", ".join(f"{name} = ?" for name in fields)
            with self._tx() as conn:
                conn.execute(
                    f"UPDATE appointments SET {columns} WHERE id = ?",
                    (*fields.values(), appointment_id),
                )
        return self._require(appointment_id)

    def cancel_appointment(self, appointment_id: str) -> Appointment:
        self._require(appointment_id)
        with self._tx() as conn:
            conn.execute(
                "UPDATE appointments SET status = ? WHERE id = ?",
                (AppointmentStatus.CANCELLED.value, appointment_id),
            )
        return self._require(appointment_id)

    def delete_appointment(self, appointment_id: str) -> None:
        self._require(appointment_id)
        with self._tx() as conn:
            conn.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))

    def _require(self, appointment_id: str) -> Appointment:
        appt = self.get_appointment(appointment_id)
        if appt is None:
            raise RecordNotFound(f"appointment {appointment_id} does not exist")
        return appt


class _Crm(_Adapter):
    entity = Entity.PATIENT
    _EDITABLE = frozenset(get_args(RecordField))

    def create_patient(self, *, name: str, phone: str, email: str | None = None) -> Patient:
        with self._tx() as conn:
            new_id = self._store._next_id(conn, Entity.PATIENT)
            conn.execute(
                "INSERT INTO patients(id, name, phone, email) VALUES (?, ?, ?, ?)",
                (new_id, name, norm_phone(phone), email),
            )
        return Patient(id=new_id, name=name, phone=norm_phone(phone), email=email)

    def get_patient(self, patient_id: str) -> Patient | None:
        rows = self._store._rows("SELECT * FROM patients WHERE id = ?", (patient_id,))
        return Patient.model_validate(rows[0]) if rows else None

    def list_patients(self) -> list[Patient]:
        return [
            Patient.model_validate(r)
            for r in self._store._rows("SELECT * FROM patients ORDER BY id")
        ]

    def find_patients(self, *, phone: str | None = None, name: str | None = None) -> list[Patient]:
        patients = self.list_patients()
        if phone is not None:
            patients = [p for p in patients if norm_phone(p.phone) == norm_phone(phone)]
        if name is not None:
            patients = [p for p in patients if norm_text(p.name) == norm_text(name)]
        return patients

    def update_patient(self, patient_id: str, **fields: Any) -> Patient:
        unknown = set(fields) - self._EDITABLE
        if unknown or not fields:
            raise ValueError(f"cannot update patient fields: {sorted(unknown) or 'none given'}")
        if self.get_patient(patient_id) is None:
            raise RecordNotFound(f"patient {patient_id} does not exist")
        if "phone" in fields:
            fields = {**fields, "phone": norm_phone(fields["phone"])}
        assignments = ", ".join(f"{col} = ?" for col in fields)  # columns are allow-listed
        with self._tx() as conn:
            conn.execute(
                f"UPDATE patients SET {assignments} WHERE id = ?", (*fields.values(), patient_id)
            )
        patient = self.get_patient(patient_id)
        assert patient is not None
        return patient


class _Messages(_Adapter):
    entity = Entity.MESSAGE

    def send(self, *, recipient: str, channel: Channel, body: str) -> Message:
        now = self._clock()
        with self._tx() as conn:
            new_id = self._store._next_id(conn, Entity.MESSAGE)
            conn.execute(
                "INSERT INTO messages(id, recipient, channel, body, sent_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (new_id, recipient, channel, body, to_utc_iso(now)),
            )
        return Message(id=new_id, recipient=recipient, channel=channel, body=body, sent_at=now)

    def list_messages(self, *, recipient: str | None = None) -> list[Message]:
        messages = [
            Message.model_validate(r)
            for r in self._store._rows("SELECT * FROM messages ORDER BY sent_at, id")
        ]
        if recipient is None:
            return messages
        return [m for m in messages if norm_phone(m.recipient) == norm_phone(recipient)]


class _Tickets(_Adapter):
    entity = Entity.TICKET

    def create_ticket(
        self,
        *,
        patient_id: str | None,
        summary: str,
        priority: Priority = "normal",
        topic: HandoffTopic | None = None,
    ) -> Ticket:
        now = self._clock()
        with self._tx() as conn:
            new_id = self._store._next_id(conn, Entity.TICKET)
            ticket = Ticket(
                id=new_id,
                patient_id=patient_id,
                summary=summary,
                priority=priority,
                created_at=now,
                topic=topic,
            )
            conn.execute(
                "INSERT INTO tickets(id, patient_id, summary, priority, created_at, topic)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (new_id, patient_id, summary, priority, to_utc_iso(now), topic),
            )
        return ticket

    _EDITABLE = {"assignee", "status"}
    _STATUSES = {"open", "closed"}

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        rows = self._store._rows("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        return Ticket.model_validate(rows[0]) if rows else None

    def update_ticket(self, ticket_id: str, **fields: Any) -> Ticket:
        """Staff-side lifecycle writes (assign, close). Journaled under the
        session's own actor, so the auditor can tell staff touches apart from
        the creating agent's."""
        unknown = set(fields) - self._EDITABLE
        if unknown or not fields:
            raise ValueError(f"cannot update ticket fields: {sorted(unknown) or 'none given'}")
        if "status" in fields and fields["status"] not in self._STATUSES:
            raise ValueError(f"unknown ticket status: {fields['status']!r}")
        if self.get_ticket(ticket_id) is None:
            raise RecordNotFound(f"ticket {ticket_id} does not exist")
        assignments = ", ".join(f"{col} = ?" for col in fields)  # columns are allow-listed
        with self._tx() as conn:
            conn.execute(
                f"UPDATE tickets SET {assignments} WHERE id = ?", (*fields.values(), ticket_id)
            )
        ticket = self.get_ticket(ticket_id)
        assert ticket is not None
        return ticket

    def cancel_ticket(self, record_id: str) -> None:
        self._delete(record_id)

    def list_tickets(self, *, patient_id: str | None = None) -> list[Ticket]:
        tickets = [
            Ticket.model_validate(r)
            for r in self._store._rows("SELECT * FROM tickets ORDER BY created_at, id")
        ]
        if patient_id is None:
            return tickets
        return [t for t in tickets if t.patient_id == patient_id]


class _Payments(_Adapter):
    entity = Entity.PAYMENT

    def collect(
        self, *, patient_id: str, amount_cents: PaymentAmount, method: PaymentMethod
    ) -> Payment:
        now = self._clock()
        with self._store._write(self._actor, now) as conn:
            payment = Payment(
                id=self._store._next_id(conn, self.entity),
                patient_id=patient_id,
                amount_cents=amount_cents,
                method=method,
                collected_at=now,
            )
            conn.execute(
                "INSERT INTO payments(id, patient_id, amount_cents, method, collected_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (payment.id, patient_id, amount_cents, method, to_utc_iso(payment.collected_at)),
            )
        return payment

    def cancel_payment(self, record_id: str) -> None:
        self._delete(record_id)

    def list_payments(self, *, patient_id: str | None = None) -> list[Payment]:
        if patient_id is None:
            rows = self._store._rows("SELECT * FROM payments ORDER BY collected_at, id")
        else:
            rows = self._store._rows(
                "SELECT * FROM payments WHERE patient_id = ? ORDER BY collected_at, id",
                (patient_id,),
            )
        return [Payment.model_validate(row) for row in rows]


class _Prescriptions(_Adapter):
    entity = Entity.PRESCRIPTION

    def refill(self, *, patient_id: str, medication: Medication) -> Prescription:
        now = self._clock()
        with self._store._write(self._actor, now) as conn:
            prescription = Prescription(
                id=self._store._next_id(conn, self.entity),
                patient_id=patient_id,
                medication=medication,
                refilled_at=now,
            )
            conn.execute(
                "INSERT INTO prescriptions(id, patient_id, medication, refilled_at)"
                " VALUES (?, ?, ?, ?)",
                (prescription.id, patient_id, prescription.medication, to_utc_iso(now)),
            )
        return prescription

    def cancel_refill(self, record_id: str) -> None:
        self._delete(record_id)

    def list_prescriptions(self, *, patient_id: str | None = None) -> list[Prescription]:
        if patient_id is None:
            rows = self._store._rows("SELECT * FROM prescriptions ORDER BY refilled_at, id")
        else:
            rows = self._store._rows(
                "SELECT * FROM prescriptions WHERE patient_id = ? ORDER BY refilled_at, id",
                (patient_id,),
            )
        return [Prescription.model_validate(row) for row in rows]

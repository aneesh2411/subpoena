"""The system of record: adapters behave, and the journal cannot be bypassed."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from subpoena.models import AppointmentStatus, Entity
from subpoena.scenarios.world import CALL_START, DANIEL, MAYA, PATEL, at
from subpoena.sor import AdapterUnavailable, RecordNotFound, SqliteStore, with_outage
from subpoena.suspect import AGENT_ID

DAY = timedelta(days=1)


def _feed(clinic, adapter):
    return adapter.changes(CALL_START - 2 * DAY, CALL_START + DAY)


def test_create_is_journaled_with_actor_time_and_after_image(clinic):
    appt = clinic.agent.calendar.create_appointment(
        patient_id=clinic.patient(MAYA).id, provider=PATEL, start=at(1, 10), reason="cleaning"
    )
    (change,) = _feed(clinic, clinic.agent.calendar)
    assert (change.op, change.entity, change.entity_id) == ("insert", Entity.APPOINTMENT, appt.id)
    assert change.actor == AGENT_ID
    assert change.at == clinic.clock.now()
    assert change.before is None
    assert change.after["provider"] == PATEL


def test_opening_a_legacy_ticket_database_preserves_rows_and_journals_new_topics(tmp_path):
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE tickets(
                id TEXT PRIMARY KEY, patient_id TEXT, summary TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'open',
                assignee TEXT, created_at TEXT NOT NULL);
            CREATE TRIGGER journal_tickets_insert AFTER INSERT ON tickets BEGIN SELECT 1; END;
            INSERT INTO tickets(id, summary, created_at)
                VALUES ('old', 'A billing question', '2026-03-02T14:00:00+00:00');
            """
        )
    store = SqliteStore(path)
    try:
        tickets = store.session(AGENT_ID, lambda: CALL_START).tickets
        assert tickets.get_ticket("old").topic is None
        created = tickets.create_ticket(patient_id=None, summary="Call back", topic="records")
        assert tickets.get_ticket(created.id).topic == "records"
        (change,) = tickets.changes(CALL_START, CALL_START + DAY)
        assert change.after["topic"] == "records"
    finally:
        store.close()


def test_update_and_delete_carry_before_images(clinic):
    cal = clinic.agent.calendar
    appt = cal.create_appointment(
        patient_id=clinic.patient(MAYA).id, provider=PATEL, start=at(1, 10)
    )
    cal.reschedule_appointment(appt.id, at(1, 11))
    cal.cancel_appointment(appt.id)
    cal.delete_appointment(appt.id)
    ops = [(c.op, c.before is not None, c.after is not None) for c in _feed(clinic, cal)]
    assert ops == [
        ("insert", False, True),
        ("update", True, True),
        ("update", True, True),
        ("delete", True, False),
    ]
    assert cal.get_appointment(appt.id) is None


def test_journal_is_append_only(clinic):
    clinic.agent.tickets.create_ticket(patient_id=None, summary="x")
    conn = clinic.store._conn
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM change_log")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE change_log SET actor = 'someone-else'")


def test_change_feed_respects_the_window_and_entity(clinic):
    clinic.agent.messages.send(recipient=MAYA.phone, channel="sms", body="hi")
    assert len(_feed(clinic, clinic.agent.messages)) == 1
    assert _feed(clinic, clinic.agent.tickets) == []
    later = clinic.clock.now() + timedelta(seconds=1)
    assert clinic.agent.messages.changes(later, later + DAY) == []


def test_list_appointments_filters_and_overlap_window(clinic):
    cal = clinic.agent.calendar
    maya, daniel = clinic.patient(MAYA).id, clinic.patient(DANIEL).id
    a = cal.create_appointment(patient_id=maya, provider=PATEL, start=at(1, 10))
    b = cal.create_appointment(patient_id=daniel, provider=PATEL, start=at(1, 10, 30))
    cal.cancel_appointment(b.id)
    assert [x.id for x in cal.list_appointments(provider=PATEL)] == [a.id]
    assert len(cal.list_appointments(provider=PATEL, include_cancelled=True)) == 2
    assert cal.list_appointments(patient_id=daniel) == []
    touching = (at(1, 10, 30), at(1, 11))  # starts exactly when `a` ends: no overlap
    assert cal.list_appointments(provider=PATEL, window=touching) == []
    assert cal.get_appointment(b.id).status is AppointmentStatus.CANCELLED


def test_writes_to_missing_records_fail_loudly(clinic):
    with pytest.raises(RecordNotFound):
        clinic.agent.calendar.cancel_appointment("apt_9999")
    with pytest.raises(RecordNotFound):
        clinic.agent.crm.update_patient("pat_9999", phone="+15550000000")
    with pytest.raises(ValueError, match="cannot update"):
        clinic.agent.crm.update_patient(clinic.patient(MAYA).id, id="pat_0002")


def test_crm_lookup_normalises_phone_and_name(clinic):
    crm = clinic.agent.crm
    assert [p.name for p in crm.find_patients(phone="(555) 010-1001")] == ["Maya Chen"]
    assert [p.name for p in crm.find_patients(name="  maya   CHEN ")] == ["Maya Chen"]
    assert crm.find_patients(phone="+15559999999") == []
    updated = crm.update_patient(clinic.patient(MAYA).id, phone="555-010-7777")
    assert updated.phone == "+15550107777"


def test_naive_datetimes_are_rejected(clinic):
    with pytest.raises(ValueError, match="naive"):
        clinic.agent.calendar.create_appointment(
            patient_id=clinic.patient(MAYA).id, provider=PATEL, start=at(1, 10).replace(tzinfo=None)
        )


def test_failed_write_rolls_back_and_leaves_no_journal_row(clinic):
    with pytest.raises(sqlite3.IntegrityError):
        clinic.agent.calendar.create_appointment(
            patient_id="pat_does_not_exist", provider=PATEL, start=at(1, 10)
        )
    assert _feed(clinic, clinic.agent.calendar) == []


def test_store_persists_to_disk(tmp_path):
    path = str(tmp_path / "sor.db")
    first = SqliteStore(path)
    first.session("seed", lambda: CALL_START).crm.create_patient(name="A", phone="5550101234")
    first.close()
    second = SqliteStore(path)
    assert len(second.session("x", lambda: CALL_START).crm.list_patients()) == 1
    second.close()


def test_outage_wrapper_fails_only_the_named_adapters(clinic):
    reader = with_outage(clinic.agent, "calendar")
    with pytest.raises(AdapterUnavailable):
        reader.calendar.list_appointments()
    assert reader.crm.list_patients()
    with pytest.raises(ValueError, match="unknown adapter"):
        with_outage(clinic.agent, "fax")

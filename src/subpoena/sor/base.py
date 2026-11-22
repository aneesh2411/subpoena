"""Adapter interfaces for the system of record.

Two views of the same systems:

* ``*Reader`` protocols are everything the **auditor** may touch: lookups plus a
  change feed. The auditor is typed against readers only, so it cannot write to
  the systems it audits.
* ``*Adapter`` protocols add the writes the **agent** performs.

Any backend that can answer "what changed between t0 and t1?" can sit behind
these interfaces: SQLite here, Google Calendar / a CRM / an SMS provider later.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

from subpoena.models import (
    Appointment,
    Change,
    Channel,
    HandoffTopic,
    Message,
    Patient,
    Priority,
    Ticket,
)


class AdapterUnavailable(RuntimeError):
    """The backing system could not be read. The auditor must not guess."""


@dataclass(frozen=True)
class Coverage:
    """Whether a change feed actually observed the whole requested window.

    A feed is incomplete when its cursor expired mid-sync, retrieval was partial,
    a stale cache stood in for a fresh read, or a create/delete pair can fall
    outside the observed interval. Absence of evidence from an incomplete feed
    is not evidence of absence.
    """

    complete: bool = True
    detail: str = ""


COMPLETE = Coverage()


class CoverageFeed(Protocol):
    """A change feed that can say how much of the window it really saw.
    Feeds without this method are assumed complete (the trigger-written
    journal is); backends that cannot guarantee coverage must say so."""

    def coverage(self, since: datetime, until: datetime) -> Coverage: ...


class RecordNotFound(LookupError):
    """A write targeted a record that does not exist."""


class ChangeFeed(Protocol):
    def changes(self, since: datetime, until: datetime) -> list[Change]:
        """Every mutation with ``since <= at <= until``, oldest first."""
        ...


# ------------------------------- calendar ---------------------------------- #


class CalendarReader(ChangeFeed, Protocol):
    def get_appointment(self, appointment_id: str) -> Appointment | None: ...

    def list_appointments(
        self,
        *,
        patient_id: str | None = None,
        provider: str | None = None,
        window: tuple[datetime, datetime] | None = None,
        include_cancelled: bool = False,
    ) -> list[Appointment]: ...


class CalendarAdapter(CalendarReader, Protocol):
    def create_appointment(
        self,
        *,
        patient_id: str,
        provider: str,
        start: datetime,
        duration_min: int = 30,
        reason: str = "",
    ) -> Appointment: ...

    def reschedule_appointment(self, appointment_id: str, new_start: datetime) -> Appointment: ...

    def update_appointment(self, appointment_id: str, **fields: Any) -> Appointment: ...

    def cancel_appointment(self, appointment_id: str) -> Appointment: ...

    def delete_appointment(self, appointment_id: str) -> None: ...


# --------------------------------- CRM ------------------------------------- #


class CrmReader(ChangeFeed, Protocol):
    def get_patient(self, patient_id: str) -> Patient | None: ...

    def find_patients(
        self, *, phone: str | None = None, name: str | None = None
    ) -> list[Patient]: ...

    def list_patients(self) -> list[Patient]: ...


class CrmAdapter(CrmReader, Protocol):
    def create_patient(self, *, name: str, phone: str, email: str | None = None) -> Patient: ...

    def update_patient(self, patient_id: str, **fields: Any) -> Patient: ...


# ------------------------------ message log -------------------------------- #


class MessageLogReader(ChangeFeed, Protocol):
    def list_messages(self, *, recipient: str | None = None) -> list[Message]: ...


class MessageLogAdapter(MessageLogReader, Protocol):
    def send(self, *, recipient: str, channel: Channel, body: str) -> Message: ...


# ------------------------------ ticket queue ------------------------------- #


class TicketQueueReader(ChangeFeed, Protocol):
    def list_tickets(self, *, patient_id: str | None = None) -> list[Ticket]: ...


class TicketQueueAdapter(TicketQueueReader, Protocol):
    def create_ticket(
        self,
        *,
        patient_id: str | None,
        summary: str,
        priority: Priority = "normal",
        topic: HandoffTopic | None = None,
    ) -> Ticket: ...


# -------------------------------- bundles ---------------------------------- #


@dataclass(frozen=True)
class SystemOfRecordReader:
    """Read-only handle: what the auditor gets."""

    calendar: CalendarReader
    crm: CrmReader
    messages: MessageLogReader
    tickets: TicketQueueReader


@dataclass(frozen=True)
class SystemOfRecord(SystemOfRecordReader):
    """Read-write handle: what the agent gets."""

    calendar: CalendarAdapter
    crm: CrmAdapter
    messages: MessageLogAdapter
    tickets: TicketQueueAdapter


class _Down:
    """Stand-in for an adapter whose backend is unreachable."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, item: str) -> Any:
        def _raise(*_: Any, **__: Any) -> Any:
            raise AdapterUnavailable(f"{self._name} adapter is unavailable")

        return _raise


def with_outage(sor: SystemOfRecordReader, *down: str) -> SystemOfRecordReader:
    """Return a reader where the named adapters (``calendar``, ``crm``, ...) fail."""
    unknown = set(down) - {"calendar", "crm", "messages", "tickets"}
    if unknown:
        raise ValueError(f"unknown adapter(s): {sorted(unknown)}")
    return SystemOfRecordReader(
        calendar=_Down("calendar") if "calendar" in down else sor.calendar,
        crm=_Down("crm") if "crm" in down else sor.crm,
        messages=_Down("messages") if "messages" in down else sor.messages,
        tickets=_Down("tickets") if "tickets" in down else sor.tickets,
    )


class _Partial:
    """A feed wrapper that reports partial coverage and can drop the rows a real
    outage of the feed would have missed (an expired cursor, a stale cache)."""

    def __init__(
        self,
        name: str,
        inner: Any,
        cov: Coverage,
        drop: Callable[[Change], bool] | None,
    ) -> None:
        self._name = name
        self._inner = inner
        self._cov = cov
        self._drop = drop

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    def coverage(self, since: datetime, until: datetime) -> Coverage:
        return self._cov

    def changes(self, since: datetime, until: datetime) -> list[Change]:
        rows = self._inner.changes(since, until)
        if self._drop is not None:
            rows = [c for c in rows if not self._drop(c)]
        return rows


def with_coverage(
    sor: SystemOfRecordReader,
    name: str,
    cov: Coverage,
    *,
    drop: Callable[[Change], bool] | None = None,
) -> SystemOfRecordReader:
    """Return a reader whose named feed reports partial coverage. ``drop`` removes
    matching rows from the feed, simulating a mutation the feed never saw."""
    if name not in {"calendar", "crm", "messages", "tickets"}:
        raise ValueError(f"unknown adapter: {name!r}")
    return replace(sor, **{name: _Partial(name, getattr(sor, name), cov, drop)})

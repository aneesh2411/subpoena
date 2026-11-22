"""Import a voice agent's tool log as auditable call records.

Export UTF-8 JSONL with one object per line. Every event requires five fields:
``call_id`` (1–80 letters, digits or ``_.:-``, using ASCII), ``event_id`` (a
nonblank string of at most 80 characters, unique within the call),
``timestamp`` (an ISO 8601 datetime with seconds and a UTC offset),
``kind``, and ``payload``. For example::

    {"call_id":"call-1","event_id":"msg-1","timestamp":"2026-01-02T09:00:00Z","kind":"confirmation_sent","payload":{"channel":"sms","to":"+15550101001"}}

Each kind represents the agent's assertion that an action completed. Export
completed actions, and supply independent system-of-record data to audit them.
The payload fields use the same types and limits as the corresponding Claim:

* ``booking`` → AppointmentBooked. Required: ``patient``, ``start``.
  Optional: ``provider``, ``duration_min`` (integer), ``reason``.
* ``confirmation_sent`` → ConfirmationSent. Required: ``to``.
  Optional: ``channel`` (``sms`` by default or ``email``), ``about_start``.
* ``handoff`` → HandoffCreated. Optional: ``patient``, ``summary``,
  ``priority`` (``low``, ``normal``, or ``urgent``), and ``topic`` (
  ``billing``, ``refill``, or ``records``).
* ``records_updated`` → RecordsUpdated. Required: ``patient``, ``field``
  (``name``, ``phone``, ``email``, ``address``, or ``insurance``). Optional: ``new_value``.
* ``payment_collected`` → PaymentCollected. Required: ``patient``.
  Optional: ``amount_cents`` (positive integer), ``method`` (``card``, ``cash``, or ``check``).
* ``cancellation`` → Cancelled. Required: ``patient``, ``start``.

A patient object requires a string ``name`` and can include a string ``phone``.
All datetime fields use YYYY-MM-DDTHH:MM:SS, optionally followed by 1–6
fractional digits after a dot, then Z or ±HH:MM. Unknown fields and kinds,
blank lines, and duplicate keys are rejected. A call can contain up to 200
events. An empty file contains no calls.

``load_action_log(path)`` returns CallRecords ready for ``Auditor.audit(call)``.
Records are sorted by call ID. Within a call, events are sorted by timestamp
and then event ID. The resulting claims use zero-based indices in that order,
the original event IDs, and the structured source. Call provenance identifies
the action-log importer. The call window spans the first and last action
timestamps, which may cover only part of the call. Supply the actual agent ID
for journal attribution, and optionally the agent version and callers by call
ID. Without caller metadata the record identifies the caller as unknown.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, ValidationError

from subpoena.models import (
    AppointmentBooked,
    CallRecord,
    Cancelled,
    Claim,
    ClaimBase,
    ClaimSource,
    ConfirmationSent,
    Frozen,
    HandoffCreated,
    PatientRef,
    PaymentCollected,
    Provenance,
    RecordsUpdated,
)

_CLAIM_BY_KIND = {
    "booking": AppointmentBooked,
    "confirmation_sent": ConfirmationSent,
    "handoff": HandoffCreated,
    "payment_collected": PaymentCollected,
    "records_updated": RecordsUpdated,
    "cancellation": Cancelled,
}
_CLAIM_METADATA = ClaimBase.model_fields.keys() | {"type"}
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


class ActionLogError(ValueError):
    """An invalid event, identified by its one-based line and field."""


class _Event(Frozen):
    call_id: Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")]
    event_id: Annotated[str, Field(min_length=1, max_length=80, pattern=r"\S")]
    timestamp: str
    kind: str
    payload: dict[str, Any]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ActionLogError(f"{key}: duplicate field")
        obj[key] = value
    return obj


def _invalid_constant(value: str) -> None:
    raise ActionLogError(f"event: invalid JSON constant {value}")


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, str) and _TIMESTAMP.fullmatch(value):
        try:
            return datetime.fromisoformat(value).astimezone(UTC)
        except (ValueError, OverflowError):
            pass
    raise ValueError(f"{field}: expected a valid ISO 8601 datetime with UTC offset")


def _validation_message(exc: ValidationError, prefix: str) -> str:
    return ", ".join(
        f"{'.'.join(filter(None, (prefix, *map(str, item['loc'])))) or 'event'}: {item['msg']}"
        for item in exc.errors(include_input=False)
    )


def _parse_event(line: str) -> tuple[_Event, datetime, Claim]:
    try:
        data = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except ActionLogError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"event: invalid JSON ({exc})") from None
    try:
        event = _Event.model_validate(data, strict=True)
    except ValidationError as exc:
        raise ValueError(_validation_message(exc, "")) from None

    claim_type = _CLAIM_BY_KIND.get(event.kind)
    if claim_type is None:
        raise ValueError(f"kind: unknown event kind {event.kind!r}")
    at = _timestamp(event.timestamp, "timestamp")
    payload = event.payload.copy()
    for field in payload:
        if field in _CLAIM_METADATA:
            raise ValueError(f"payload.{field}: unexpected field")
    for field in ("start", "about_start"):
        if field in claim_type.model_fields and payload.get(field) is not None:
            payload[field] = _timestamp(payload[field], f"payload.{field}")
    try:
        claim = claim_type.model_validate(
            dict(payload, claim_id=event.event_id, turn_index=0, source=ClaimSource.STRUCTURED),
            strict=True,
        )
    except ValidationError as exc:
        raise ValueError(_validation_message(exc, "payload")) from None
    return event, at, claim


def parse_action_log(
    lines: Iterable[str],
    *,
    agent_id: str = "action-log",
    agent_version: str = "unknown",
    callers: Mapping[str, PatientRef] | None = None,
) -> tuple[CallRecord, ...]:
    """Parse physical JSONL lines, raising ActionLogError on the first invalid event."""
    provenance = Provenance(agent_version=agent_version, platform="action-log")
    callers = callers or {}
    unknown_caller = PatientRef(name="Unknown caller")
    by_call: dict[str, list[tuple[datetime, str, Claim]]] = {}
    seen: dict[tuple[str, str], int] = {}
    for line_number, line in enumerate(lines, start=1):
        try:
            event, at, claim = _parse_event(line)
        except ValueError as exc:
            raise ActionLogError(f"line {line_number}, {exc}") from None
        key = (event.call_id, event.event_id)
        if key in seen:
            raise ActionLogError(
                f"line {line_number}, event_id: duplicate {event.event_id!r} for call "
                f"{event.call_id!r} (first seen on line {seen[key]})"
            )
        events = by_call.setdefault(event.call_id, [])
        if len(events) == 200:
            raise ActionLogError(f"line {line_number}, call_id: exceeds 200 claims per call")
        seen[key] = line_number
        events.append((at, event.event_id, claim))

    calls: list[CallRecord] = []
    for call_id, events in sorted(by_call.items()):
        events.sort(key=lambda entry: (entry[0], entry[1]))
        calls.append(
            CallRecord(
                call_id=call_id,
                agent_id=agent_id,
                caller=callers.get(call_id, unknown_caller),
                started_at=events[0][0],
                ended_at=events[-1][0],
                claims=tuple(
                    claim.model_copy(update={"turn_index": index})
                    for index, (_, _, claim) in enumerate(events)
                ),
                provenance=provenance,
            )
        )
    return tuple(calls)


def _utf8_lines(path: Path) -> Iterator[str]:
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                yield line.decode("utf-8")
            except UnicodeDecodeError:
                raise ActionLogError(f"line {line_number}, event: invalid UTF-8") from None


def load_action_log(
    path: Path,
    *,
    agent_id: str = "action-log",
    agent_version: str = "unknown",
    callers: Mapping[str, PatientRef] | None = None,
) -> tuple[CallRecord, ...]:
    """Read a UTF-8 JSONL file using the same schema as parse_action_log."""
    return parse_action_log(
        _utf8_lines(path), agent_id=agent_id, agent_version=agent_version, callers=callers
    )

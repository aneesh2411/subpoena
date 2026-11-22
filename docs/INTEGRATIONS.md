# Voice-agent integrations

Run the examples from the repository root with Subpoena installed. The Python
examples that load test helpers also require the development dependencies.
Ingest turns your exports into call records and claims. Pass those records to
the auditor with readers for your systems of record so it can compare claims
against observed writes. Where you control write attribution, use the same
`agent_id:call_id` stamp that the auditor looks for.

## Twilio callback exports

Export the completed Voice status and Record transcription callback parameters
as JSON objects, plus the completed REST Call resource for the same call SID.
The [Twilio ingest module](../src/subpoena/ingest/twilio.py) uses the REST resource
for the call window and keeps Twilio's transcription as raw text. Include
structured tool-log claims to audit outcomes such as an SMS confirmation. The
raw text alone does not supply speaker turns for claim extraction. Set the
agent ID to the identity used to attribute writes.

```bash
subpoena ingest-twilio \
  tests/fixtures/twilio/completed-status.json \
  tests/fixtures/twilio/completed-transcription.json \
  tests/fixtures/twilio/completed-call-resource.json \
  --agent-id test-agent --agent-version v3 --caller-name "Test Caller" \
  --claims tests/fixtures/twilio/structured-claims.json
```

The command prints call record JSON. The [Twilio audit fixture](../tests/test_twilio_callrecord.py)
checks its confirmation claim against a recorded SMS.

## Generic JSONL action log

The [action log loader](../src/subpoena/ingest/action_log.py) accepts one event
per line with `call_id`, `event_id`, a timestamp with a UTC offset (or `Z`),
`kind`, and `payload`. It maps booking, cancellation, confirmation and handoff
events to structured claims and uses each call's earliest and latest event as
its window. The [fixture](../tests/fixtures/action_log/multi_call.jsonl) includes
two calls. An empty SQLite store demonstrates what the auditor reports when it
finds no matching writes.

```python
from pathlib import Path

from subpoena.auditor import Auditor
from subpoena.ingest.action_log import load_action_log
from subpoena.sor.sqlite import SqliteStore

calls = load_action_log(
    Path("tests/fixtures/action_log/multi_call.jsonl"),
    agent_id="my-agent",
    agent_version="release-7",
)
store = SqliteStore()
try:
    for call in calls:
        sor = store.session("auditor", lambda: call.ended_at)
        report = Auditor(sor, use_transcript=False).audit(call)
        assert all(verdict.status.value == "NOT_FOUND" for verdict in report.verdicts)
finally:
    store.close()
```

## REST ticketing

The [REST adapter](../src/subpoena/sor/rest_tickets.py) supplies ticket creation
evidence for handoff claims. Set its API root to your Zendesk-shaped API, pass
the full Authorization header value as `authorization` if required, and set
the text custom field IDs for the CRM patient ID and creation actor. Replace the
system of record's `tickets` reader with this adapter before auditing. Ticket
listings default to incomplete coverage. Set `complete_creation_history=True`
only if the endpoint exposes every creation in the window with immutable
creation data and no unobserved mutations.

The adapter carries creation evidence only. Handoff fulfillment beyond
"recorded" (assigned, acknowledged, completed) is reported as inconclusive
against it until it exposes lifecycle reads.

This example uses the [test ticket service](../tests/test_sor_rest_tickets.py)
in place of a live API and reads back the creation evidence.

```python
from runpy import run_path

from subpoena.sor.rest_tickets import RestTickets

fixture = run_path("tests/test_sor_rest_tickets.py")
service = fixture["FakeTicketService"]()
tickets = RestTickets(
    "https://tickets.invalid/api/v2",
    patient_field_id=101,
    actor_field_id=102,
    actor="agent:call-1",
    open_request=service.open,
)
ticket = tickets.create_ticket(patient_id="pat_1", summary="Call back", priority="urgent")
(change,) = tickets.changes(service.now, service.now)
assert change.entity_id == ticket.id and change.actor == "agent:call-1"
```

## Google Calendar

The [Calendar reader](../src/subpoena/sor/google_calendar.py) supplies
appointment state and change evidence for booking, rescheduling and cancellation
claims. Give it an authenticated Calendar v3 service and a mapping from provider
names to calendar IDs, then use it as the system of record's `calendar` reader.
The writer records the CRM patient ID and actor in private event properties.
Sync the snapshot cache before the call window to capture before-images. Google
represents deletions as cancelled events in the feed.

This reschedule example uses the [Calendar test service](../tests/fake_gcal.py)
and the setup from the [adapter tests](../tests/test_google_calendar.py).

```python
from datetime import timedelta
from runpy import run_path

from subpoena.scenarios.world import CALL_START, PATEL, at
from subpoena.sor.google_calendar import GoogleCalendarAdapter, GoogleCalendarReader, SnapshotCache

FakeCalendarService = run_path("tests/fake_gcal.py")["FakeCalendarService"]
now = CALL_START
service = FakeCalendarService(lambda: now)
calendars = {PATEL: "cal-patel"}
writer = GoogleCalendarAdapter(
    service,
    calendars,
    actor="receptionist-agent",
    clock=lambda: now,
)
reader = GoogleCalendarReader(service, calendars, cache=SnapshotCache())
appointment = writer.create_appointment(patient_id="pat_0001", provider=PATEL, start=at(2, 14))
reader.sync()
now += timedelta(minutes=3)
writer.reschedule_appointment(appointment.id, at(2, 15))
(change,) = reader.changes(CALL_START, now)
assert change.op == "update" and change.before["start"] != change.after["start"]
```

## SQLite system of record

The [SQLite store](../src/subpoena/sor/sqlite.py) provides appointment, patient,
message and ticket evidence. Use a file path to persist the store, or omit it
for an in-memory fixture. Obtain a session with the write actor and clock, route
writes through its adapters, and pass a session to the auditor for reads. SQLite
triggers journal the writes with actor, time and before/after images.

This example adapts the message-feed check in the [SQLite tests](../tests/test_sor_sqlite.py).

```python
from datetime import timedelta

from subpoena.scenarios.world import CALL_START, MAYA
from subpoena.sor.sqlite import SqliteStore

store = SqliteStore()
try:
    sor = store.session("agent:call-1", lambda: CALL_START)
    sor.messages.send(recipient=MAYA.phone, channel="sms", body="hi")
    (change,) = sor.messages.changes(CALL_START, CALL_START + timedelta(seconds=1))
    assert change.op == "insert" and change.actor == "agent:call-1"
finally:
    store.close()
```

## Offline seal verification

The [offline verifier](../scripts/verify_seal.py) checks standalone sealed JSON
reports from the genesis link, including whole-report integrity and receipt
order. Supply `--key` to require HMAC authentication with the report's signing
key. Omit it for integrity-only verification. This example uses the published
[demo export](demo/data/audit-aud-clean-family-two-bookings.json) and its demo key.

```bash
python scripts/verify_seal.py docs/demo/data/audit-aud-clean-family-two-bookings.json \
  --key subpoena-pages-demo
```

Expect `integrity=intact`, `authentication=authenticated`, and `VERDICT PASS`.

# Extending Subpoena

Run the Python examples from the repository root with Subpoena installed. Both
examples use in-memory data and run offline. The [documentation tests](../tests/test_extending_doc.py)
run the examples with the development dependencies installed.

## Write a system-of-record reader

The [reader protocols](../src/subpoena/sor/base.py) define the lookups and
change feeds the auditor can use. Implement the protocol for the system you
want to supply and pass it in the corresponding slot of `SystemOfRecordReader`.
`SystemOfRecord` provides the same slots with adapter interfaces that also
expose the agent's writes.

The [SQLite store](../src/subpoena/sor/sqlite.py) returns all four adapters from
`SqliteStore.session` and journals changes with before and after images. The
[REST ticket adapter](../src/subpoena/sor/rest_tickets.py) implements
`TicketQueueAdapter` and reports incomplete coverage by default because ticket
listings can omit deleted tickets and other mutations.

For a ticket reader, return current `Ticket` records from `list_tickets` and
honour the optional patient ID filter. Return `Change` records oldest first
from `changes`, including both ends of the requested window. Use timezone-aware
timestamps, stable entity IDs, the recorded actor, and the available before
and after images. Where the writer attributes a change to a call, preserve its
`agent_id:call_id` stamp. If the feed can miss changes, implement `coverage`
and return `Coverage(False, detail)` for incomplete windows. The auditor
assumes full coverage when the method is absent. Raise `AdapterUnavailable`
when the backing system cannot be read.

This reader serves a fixture containing one immutable creation. The example
plays the existing urgent handoff scenario to obtain a call and a ticket
creation event. It then audits that call with the new reader in the ticket
slot and SQLite in the other slots.

```python
from datetime import datetime

from subpoena.auditor import Auditor
from subpoena.models import Change, Ticket, VerdictStatus
from subpoena.scenarios import BY_ID, build_world, run_suspect
from subpoena.sor.base import SystemOfRecordReader, TicketQueueReader


class SingleTicketReader(TicketQueueReader):
    def __init__(self, ticket: Ticket, creation: Change) -> None:
        self.ticket = ticket
        self.creation = creation

    def list_tickets(self, *, patient_id: str | None = None) -> list[Ticket]:
        if patient_id is None or patient_id == self.ticket.patient_id:
            return [self.ticket]
        return []

    def changes(self, since: datetime, until: datetime) -> list[Change]:
        if since <= self.creation.at <= until:
            return [self.creation]
        return []


fixture = BY_ID["clean-urgent-handoff"]
world = build_world(fixture)
try:
    result = run_suspect(fixture, world)
    call = result.call
    sqlite = world.store.session("auditor", lambda: call.ended_at)
    (ticket,) = sqlite.tickets.list_tickets()
    (creation,) = sqlite.tickets.changes(call.started_at, call.ended_at)
    tickets = SingleTicketReader(ticket, creation)
    reader = SystemOfRecordReader(sqlite.calendar, sqlite.crm, sqlite.messages, tickets)
    report = Auditor(reader, use_transcript=False, clock=lambda: call.ended_at).audit(
        call,
        audit_id="aud-extending-ticket-reader",
    )
    (verdict,) = report.verdicts
    assert verdict.status is VerdictStatus.VERIFIED
finally:
    world.store.close()
```

For a backend implementation, replace the fixture inputs with records from
your source. Exercise lookups, inclusive window boundaries, and an audit that
would give a different verdict if the reader returned no evidence.

## Add a golden scenario

The [scenario runner](../src/subpoena/scenarios/world.py) builds the clinic
state, plays the agent's steps, and audits the call. Use `Scenario` to describe
the caller, the steps, and the expected result. Use `SeedAppointment` in
`seed` for pre-existing appointments and `faults` to map zero-based step
indices to sets of `FaultMode` values. For a routine clean call, the default
caller and clinic roster are enough.

Choose an unused ID and write the labels before running the auditor.
`expected_claims` pairs each claim type with its expected `VerdictStatus`.
`expected_findings` lists the finding codes you expect. Write the same claim
expectations with identity labels for v2, using the formats in
[`claim_identity`](../src/subpoena/evals/score.py). The labels below come from
the requested appointment and handoff. Both scorers compare the run with
those expectations.

```python
from subpoena.evals import score_run
from subpoena.evals.score import score_run_v2
from subpoena.models import Outcome, VerdictStatus
from subpoena.scenarios import Scenario, run_scenario
from subpoena.scenarios.world import KIM, at
from subpoena.suspect import Book, Handoff

scenario = Scenario(
    id="clean-book-no-text-and-urgent-handoff",
    title="Book without a text and request an urgent callback",
    story="Maya books a visit without a confirmation and asks for an urgent callback.",
    steps=(Book(at(2, 11), KIM, confirm=False), Handoff("a broken tooth", priority="urgent")),
    expected_claims=(
        ("AppointmentBooked", VerdictStatus.VERIFIED),
        ("HandoffCreated", VerdictStatus.VERIFIED),
    ),
    expected_claims_v2=(
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-04T11:00:00-05:00|Dr. Kim",
        ),
        ("HandoffCreated", VerdictStatus.VERIFIED, "handoff|+15550101001|urgent"),
    ),
)

report = run_scenario(scenario).report
positional = score_run(
    scenario.id,
    scenario.expected_claims,
    scenario.expected_findings,
    report,
)
identity = score_run_v2(
    scenario.id,
    scenario.expected_claims_v2,
    scenario.expected_findings,
    report,
)
assert positional.strict_correct, positional.problems
assert identity.strict_correct, identity.problems
assert report.outcome is Outcome.CLEAN
```

To register the example, put the `Scenario` entry in `CLEAN` in
[`golden.py`](../src/subpoena/scenarios/golden.py). Move the value of
`expected_claims_v2` into `_V2` under the same scenario ID and omit that
argument from the entry. The `GOLDEN` construction attaches those labels
and builds the set that `BY_ID` indexes. Use the corresponding existing
group for fault or edge cases, and reserve `KNOWN_GAPS` for cases with a
documented `known_gap`.

After registration, run `python -m pytest tests/test_golden.py tests/test_score_v2.py`
and check that the new ID appears in `subpoena demo --list`. Play it with
`subpoena demo --fast --scenario clean-book-no-text-and-urgent-handoff`.

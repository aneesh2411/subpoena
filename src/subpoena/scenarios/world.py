"""Scenario definition and the deterministic machinery that plays one out.

A scenario is fully specified by data: the clinic's starting state, the caller's
script, which faults fire, and what a correct auditor must conclude. Playing the
same scenario twice yields byte-identical calls and reports.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from subpoena.auditor import Auditor, ClaimExtractor, NaiveAuditor, Sealer
from subpoena.auditor.incidents import group_incidents
from subpoena.models import (
    AuditReport,
    Incident,
    PatientRef,
    Provenance,
    ReviewCandidate,
    VerdictStatus,
)
from subpoena.sor import SqliteStore, SystemOfRecord, with_outage
from subpoena.suspect import (
    AGENT_ID,
    CallResult,
    FaultMode,
    ForcedFaults,
    Narrator,
    ReceptionistAgent,
    SimClock,
    Step,
)
from subpoena.suspect.narrator import ChattyNarrator
from subpoena.util import ClinicConfig

BASELINE_AGENT = "receptionist-v1"
CHATTY_AGENT = "receptionist-v2-chatty"

CLINIC = ClinicConfig()
TZ = ZoneInfo(CLINIC.tz)
MONDAY = datetime(2026, 3, 2, tzinfo=TZ)  # week before US daylight saving starts


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """Clinic-local time, ``day`` days after Monday 2 March 2026."""
    return MONDAY + timedelta(days=day, hours=hour, minutes=minute)


CALL_START = at(0, 9, 5)

MAYA = PatientRef(name="Maya Chen", phone="+15550101001")
DANIEL = PatientRef(name="Daniel Okafor", phone="+15550101002")
PRIYA = PatientRef(name="Priya Raman", phone="+15550101003")
TOM = PatientRef(name="Tom Becker", phone="+15550101004")
SOFIA = PatientRef(name="Sofia Alvarez", phone="+15550101005")
LEO = PatientRef(name="Leo Martins", phone="+15550101099")  # not on file: a new patient
ROSTER: tuple[PatientRef, ...] = (MAYA, DANIEL, PRIYA, TOM, SOFIA)

PATEL, KIM, OSEI = "Dr. Patel", "Dr. Kim", "Dr. Osei"


@dataclass(frozen=True)
class SeedAppointment:
    patient: PatientRef
    provider: str
    start: datetime
    duration_min: int = 30
    reason: str = "cleaning"
    cancelled: bool = False
    by_staff_during_call: bool = False  # written by a human while the call is in progress


@dataclass(frozen=True)
class Followup:
    """A staff touch on the call's handoff tickets, after the call ends.

    Played before the audit runs, so the audit's fulfillment grading can see
    the ticket get assigned, acknowledged or closed. ``minutes_after`` must be
    below the scenario's ``audit_delay_min`` to be visible to that audit.
    """

    minutes_after: int
    assignee: str | None = None
    close: bool = False
    actor: str = "staff:front-desk"
    # When true, the touch lands on every open ticket for the caller, not only
    # the ones this call created: staff working the caller's whole queue.
    all_open_for_caller: bool = False


Expected = tuple[str, VerdictStatus]
# (claim type, expected status, identity label): separately reviewed data naming
# which appointment, message or ticket an expectation is about. See
# evals.score.claim_identity for the label format.
ExpectedV2 = tuple[str, VerdictStatus, str]


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    story: str
    steps: tuple[Step, ...]
    expected_claims: tuple[Expected, ...]
    expected_findings: tuple[str, ...] = ()  # finding codes, e.g. "double_booking"
    caller: PatientRef = MAYA
    faults: Mapping[int, frozenset[FaultMode]] = field(default_factory=dict)
    seed: tuple[SeedAppointment, ...] = ()
    extra_patients: tuple[PatientRef, ...] = ()  # e.g. a second patient with the same name
    tags: tuple[str, ...] = ()
    hard_phrasing: bool = False
    outage: tuple[str, ...] = ()  # adapters that are down when the audit runs
    known_gap: str | None = None  # why the auditor is expected to get this one wrong
    # Identity-labelled twin of expected_claims, attached after construction once
    # reviewed (see golden._V2). Keyword-only position: several scenarios pass
    # expected_findings positionally.
    expected_claims_v2: tuple[ExpectedV2, ...] = ()
    # Staff lifecycle on the call's tickets, played after the call and before
    # the audit. New fields go at the end: scenarios pass early fields
    # positionally.
    followups: tuple[Followup, ...] = ()
    audit_delay_min: int = 1
    agent_version: str = BASELINE_AGENT

    @property
    def fault_modes(self) -> frozenset[FaultMode]:
        return frozenset(m for modes in self.faults.values() for m in modes)

    @property
    def expected_dirty(self) -> bool:
        return bool(self.expected_findings) or any(
            status is not VerdictStatus.VERIFIED for _, status in self.expected_claims
        )


@dataclass(frozen=True)
class World:
    store: SqliteStore
    clock: SimClock


@dataclass(frozen=True)
class ScenarioRun:
    scenario: Scenario
    result: CallResult
    report: AuditReport

    @property
    def agent_version(self) -> str:
        return self.scenario.agent_version


def _seed(scenario: Scenario, store: SqliteStore, call_start: datetime, roster: bool) -> None:
    """The clinic's state as it stood when this call's phone rang."""
    seeder = store.session("seed", lambda: call_start - timedelta(days=1))
    staff = store.session("staff:front-desk", lambda: call_start + timedelta(seconds=5))
    ids = {ref.phone: _patient_id(store, seeder, ref) for ref in ROSTER} if roster else {}
    for ref in scenario.extra_patients:
        seeder.crm.create_patient(name=ref.name, phone=ref.phone or "")
    for appt in scenario.seed:
        if appt.patient.phone not in ids:
            ids[appt.patient.phone] = _patient_id(store, seeder, appt.patient)
        writer = staff if appt.by_staff_during_call else seeder
        created = writer.calendar.create_appointment(
            patient_id=ids[appt.patient.phone],
            provider=appt.provider,
            start=appt.start,
            duration_min=appt.duration_min,
            reason=appt.reason,
        )
        if appt.cancelled:
            writer.calendar.cancel_appointment(created.id)


def _patient_id(store: SqliteStore, seeder: SystemOfRecord, ref: PatientRef) -> str:
    """The roster patient's id, creating the patient on first seed."""
    reader = store.session("seed", lambda: CALL_START)
    found = reader.crm.find_patients(phone=ref.phone) if ref.phone else []
    if found:
        return found[0].id
    return seeder.crm.create_patient(name=ref.name, phone=ref.phone or "").id


def build_world(scenario: Scenario, store: SqliteStore | None = None) -> World:
    """Create the clinic's state as it stood when the phone rang."""
    store = store or SqliteStore()
    _seed(scenario, store, CALL_START, roster=True)
    return World(store=store, clock=SimClock(CALL_START))


def apply_followups(scenario: Scenario, world: World, result: CallResult) -> None:
    """Play the staff side of the call's handoffs, after the call ends."""
    if not scenario.followups:
        return
    call = result.call
    reader = world.store.session("auditor", lambda: call.ended_at)
    own = [
        t for t in reader.tickets.list_tickets() if call.started_at <= t.created_at <= call.ended_at
    ]
    for followup in scenario.followups:
        when = call.ended_at + timedelta(minutes=followup.minutes_after)
        staff = world.store.session(followup.actor, lambda when=when: when)
        tickets = own
        if followup.all_open_for_caller:
            found = reader.crm.find_patients(phone=call.caller.phone) if call.caller.phone else []
            tickets = [
                t
                for pid in {p.id for p in found}
                for t in reader.tickets.list_tickets(patient_id=pid)
                if t.status == "open"
            ]
        for ticket in tickets:
            fields: dict[str, Any] = {}
            if followup.assignee is not None:
                fields["assignee"] = followup.assignee
            if followup.close:
                fields["status"] = "closed"
            if fields:
                staff.tickets.update_ticket(ticket.id, **fields)


def run_suspect(scenario: Scenario, world: World) -> CallResult:
    # The platform session stamps every write with the call id, so the journal
    # can attribute a mutation to one call, not just to the shared agent identity.
    call_id = f"call-{scenario.id}"
    chatty = scenario.agent_version == CHATTY_AGENT
    narrator = ChattyNarrator if chatty else Narrator
    agent = ReceptionistAgent(
        world.store.session(f"{AGENT_ID}:{call_id}", world.clock),
        world.clock,
        narrator=narrator(random.Random(scenario.id), TZ, hard=scenario.hard_phrasing),
        provenance=Provenance(agent_version=CHATTY_AGENT, prompt_version="narrator:chatty")
        if chatty
        else None,
        clinic=CLINIC,
        faults=ForcedFaults(scenario.faults),
    )
    return agent.handle_call(call_id, scenario.caller, scenario.steps)


def audit_call(
    scenario: Scenario,
    world: World,
    result: CallResult,
    *,
    auditor: Literal["subpoena", "naive"] = "receipts",
    extractor: ClaimExtractor | None = None,
    sealer: Sealer | None = None,
    options: Mapping[str, Any] | None = None,
) -> AuditReport:
    """Audit the call. ``options`` are extra ``Auditor`` arguments (used for ablations)."""
    audited_at = result.call.ended_at + timedelta(minutes=scenario.audit_delay_min)
    reader = world.store.session("auditor", lambda: audited_at)
    if scenario.outage:
        reader = with_outage(reader, *scenario.outage)
    if auditor == "naive":
        return NaiveAuditor(reader, clinic=CLINIC, clock=lambda: audited_at).audit(
            result.call, audit_id=f"naive-{scenario.id}"
        )
    return Auditor(
        reader,
        clinic=CLINIC,
        extractor=extractor,
        sealer=sealer,
        clock=lambda: audited_at,
        **(options or {}),
    ).audit(result.call, audit_id=f"aud-{scenario.id}")


@dataclass(frozen=True)
class SagaCall:
    """One call in a multi-call story: the scenario and when the phone rings."""

    scenario: Scenario
    started_at: datetime


@dataclass(frozen=True)
class SagaRun:
    """Every call's run, plus the cross-call incidents over all the reports."""

    runs: tuple[ScenarioRun, ...]
    incidents: tuple[Incident, ...]
    reviews: tuple[ReviewCandidate, ...]


def run_saga(
    calls: Sequence[SagaCall],
    *,
    auditor: Literal["subpoena", "naive"] = "receipts",
    extractor: ClaimExtractor | None = None,
    sealer: Sealer | None = None,
    options: Mapping[str, Any] | None = None,
) -> SagaRun:
    """Play several calls against one shared clinic, then group their audits.

    Each scenario keeps its own id, script and expectations; the calls share
    one system of record, so a caller's second callback sees the first call's
    ticket. Incidents are grouped over the reports with fulfillment refreshed
    against the journal as of the saga's end: a close that lands after an
    earlier audit still counts, because grouping asks about now, not about
    what that audit could see then.
    """
    if not calls:
        raise ValueError("a saga needs at least one call")
    ids = [c.scenario.id for c in calls]
    if len(set(ids)) != len(ids):
        raise ValueError("saga scenarios need distinct ids")
    store = SqliteStore()
    world = World(store=store, clock=SimClock(CALL_START))
    try:
        runs: list[ScenarioRun] = []
        for i, saga_call in enumerate(calls):
            _seed(saga_call.scenario, store, saga_call.started_at, roster=(i == 0))
            world.clock.advance((saga_call.started_at - world.clock.now()).total_seconds())
            result = run_suspect(saga_call.scenario, world)
            apply_followups(saga_call.scenario, world, result)
            report = audit_call(
                saga_call.scenario,
                world,
                result,
                auditor=auditor,
                extractor=extractor,
                sealer=sealer,
                options=options,
            )
            runs.append(ScenarioRun(scenario=saga_call.scenario, result=result, report=report))
        reader = store.session("auditor", world.clock)
        as_of = max(r.report.created_at for r in runs)
        incidents, reviews = group_incidents(
            [r.report for r in runs], tickets_feed=reader.tickets, now=as_of
        )
        return SagaRun(runs=tuple(runs), incidents=incidents, reviews=reviews)
    finally:
        store.close()


def run_scenario(
    scenario: Scenario,
    *,
    auditor: Literal["subpoena", "naive"] = "receipts",
    extractor: ClaimExtractor | None = None,
    sealer: Sealer | None = None,
    options: Mapping[str, Any] | None = None,
) -> ScenarioRun:
    world = build_world(scenario)
    try:
        result = run_suspect(scenario, world)
        apply_followups(scenario, world, result)
        report = audit_call(
            scenario,
            world,
            result,
            auditor=auditor,
            extractor=extractor,
            sealer=sealer,
            options=options,
        )
    finally:
        world.store.close()
    return ScenarioRun(scenario=scenario, result=result, report=report)

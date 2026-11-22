"""Working state for one audit: the call, the evidence and what has been used."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from subpoena.auditor.delta import (
    RANK,
    SOURCE,
    Attribution,
    ChangeKind,
    NetChange,
    collapse,
    content_signature,
    dedupe,
)
from subpoena.models import (
    Appointment,
    CallRecord,
    Change,
    Claim,
    Entity,
    Evidence,
    Patient,
    PatientRef,
    RecordField,
    TrailEvent,
)
from subpoena.sor.base import COMPLETE, AdapterUnavailable, Coverage, SystemOfRecordReader
from subpoena.util import ClinicConfig, norm_phone, norm_text, show

MatchKey = tuple[Entity, str, RecordField | None]


@dataclass(frozen=True)
class Match:
    """The journal changes a claim has been paired with."""

    primary: NetChange | None
    secondary: NetChange | None = None
    record_field: RecordField | None = None

    @property
    def changes(self) -> tuple[NetChange, ...]:
        return tuple(c for c in (self.primary, self.secondary) if c is not None)

    @property
    def keys(self) -> tuple[MatchKey, ...]:
        return tuple((*nc.key, self.record_field) for nc in self.changes)

    @property
    def signatures(self) -> set[str]:
        if self.record_field is not None:
            # Distinct fields on one patient record can back independent claims.
            return set()
        return {sig for nc in self.changes if (sig := content_signature(nc.after)) is not None}


@dataclass
class AuditContext:
    call: CallRecord
    sor: SystemOfRecordReader
    clinic: ClinicConfig
    window: tuple[datetime, datetime]
    delta: dict[Entity, list[NetChange] | None] = field(default_factory=dict)
    errors: dict[Entity, str] = field(default_factory=dict)
    trail: list[TrailEvent] = field(default_factory=list)
    attribution: dict[tuple[Entity, str], Attribution] = field(default_factory=dict)
    coverage: dict[Entity, Coverage] = field(default_factory=dict)
    _consumed: set[MatchKey] = field(default_factory=set)
    _patient_cache: dict[tuple[str, str | None, datetime, datetime], frozenset[str]] = field(
        default_factory=dict
    )

    _patient_changes: tuple[Change, ...] = ()

    @classmethod
    def open(
        cls, call: CallRecord, sor: SystemOfRecordReader, clinic: ClinicConfig, grace: timedelta
    ) -> AuditContext:
        """Pull each system's change feed for the call window, attributed to the call.

        A row is evidence for this call when it carries this call's own stamp
        (``agent:call-id``) or the shared agent identity. Rows stamped to a
        *different* call prove a concurrent call was in progress: they are never
        this call's evidence, and while one is in the window a shared-identity
        row cannot say which call wrote it.
        """
        ctx = cls(call, sor, clinic, (call.started_at, call.ended_at + grace))
        feeds = {
            Entity.APPOINTMENT: sor.calendar,
            Entity.PATIENT: sor.crm,
            Entity.MESSAGE: sor.messages,
            Entity.TICKET: sor.tickets,
        }
        if sor.payments is not None:
            feeds[Entity.PAYMENT] = sor.payments
        if sor.prescriptions is not None:
            feeds[Entity.PRESCRIPTION] = sor.prescriptions
        own_stamp = f"{call.agent_id}:{call.call_id}"
        prefix = f"{call.agent_id}:"
        pulled: dict[Entity, list[Change]] = {}
        concurrent = False
        for entity, feed in feeds.items():
            try:
                rows = feed.changes(*ctx.window)
            except AdapterUnavailable as exc:
                ctx.delta[entity] = None
                ctx.errors[entity] = str(exc)
                ctx.log("evidence", f"{SOURCE[entity]}: UNAVAILABLE ({exc}). Will not guess.")
                continue
            pulled[entity] = rows
            if entity is Entity.PATIENT:
                ctx._patient_changes = tuple(sorted(rows, key=lambda row: (row.at, row.seq)))
            cov_fn = getattr(feed, "coverage", None)
            try:
                cov = cov_fn(*ctx.window) if callable(cov_fn) else COMPLETE
            except AdapterUnavailable as exc:
                # A coverage probe that fails cannot prove coverage: incomplete.
                cov = Coverage(complete=False, detail=str(exc))
            ctx.coverage[entity] = cov
            if not cov.complete:
                ctx.log(
                    "evidence",
                    f"{SOURCE[entity]}: INCOMPLETE history for the window ({cov.detail}). "
                    "Absence here is not evidence.",
                )
            concurrent = concurrent or any(
                c.actor.startswith(prefix) and c.actor != own_stamp for c in rows
            )
        if concurrent:
            ctx.log(
                "evidence",
                "another call under the same agent identity overlaps this window; "
                "shared-identity writes cannot be attributed to a single call",
            )
        for entity, changes in pulled.items():
            direct, shared, foreign, others = 0, 0, 0, 0
            mine: list[Change] = []
            direct_keys: set[tuple[Entity, str]] = set()
            for c in changes:
                if c.actor == own_stamp:
                    direct += 1
                    mine.append(c)
                    direct_keys.add((c.entity, c.entity_id))
                elif c.actor == call.agent_id:
                    shared += 1
                    mine.append(c)
                elif c.actor.startswith(prefix):
                    foreign += 1
                else:
                    others += 1
            mine, dropped = dedupe(mine)
            ctx.delta[entity] = collapse(mine)
            for nc in ctx.delta[entity]:
                if nc.key in direct_keys and all(
                    c.actor == own_stamp for c in mine if (c.entity, c.entity_id) == nc.key
                ):
                    ctx.attribution[nc.key] = Attribution.DIRECT
                elif concurrent:
                    ctx.attribution[nc.key] = Attribution.AMBIGUOUS
                else:
                    ctx.attribution[nc.key] = Attribution.SOLE
            if entity in (Entity.PAYMENT, Entity.PRESCRIPTION) and not changes:
                continue  # Empty optional feeds add no receipts to the trail.
            note = ""
            if foreign:
                note += f"; {foreign} stamped to other call(s), excluded"
            if dropped:
                note += f"; {dropped} exact replay(s) ignored"
            if others:
                note += f"; {others} by other actors ignored"
            ctx.log(
                "evidence",
                f"{SOURCE[entity]}: {direct} write(s) stamped to this call, {shared} under "
                f"the shared agent identity in the call window{note}",
            )
        return ctx

    def match_attribution(self, match: Match) -> Attribution:
        """The weakest attribution among the writes a match rests on. Missing keys
        fail safe to AMBIGUOUS: an unattributable write must never look proven."""
        attrs = [self.attribution.get(nc.key, Attribution.AMBIGUOUS) for nc in match.changes]
        return min(attrs, key=RANK.__getitem__)

    # -- bookkeeping -------------------------------------------------------- #

    @property
    def tz(self) -> ZoneInfo:
        return self.clinic.zone

    def log(self, stage: str, message: str) -> None:
        self.trail.append(TrailEvent(seq=len(self.trail) + 1, stage=stage, message=message))

    def free(self, entity: Entity) -> list[NetChange]:
        """Net changes by the agent that no claim has accounted for yet."""
        return [
            nc
            for nc in self.delta.get(entity) or []
            if nc.kind is not ChangeKind.TRANSIENT
            and (*nc.key, None) not in self._consumed
            and (
                not nc.changed_patient_fields
                or any(self.record_field_free(nc, f) for f in nc.changed_patient_fields)
            )
        ]

    def record_field_free(self, change: NetChange, field: RecordField) -> bool:
        return (*change.key, field) not in self._consumed

    def transient(self, entity: Entity) -> list[NetChange]:
        return [nc for nc in self.delta.get(entity) or [] if nc.kind is ChangeKind.TRANSIENT]

    def touched(self, entity: Entity) -> list[NetChange]:
        return list(self.delta.get(entity) or [])

    def consume(self, match: Match) -> None:
        self._consumed.update(match.keys)

    def consumed_signatures(self, entity: Entity) -> set[str]:
        """Content signatures of the writes claims have already accounted for.

        The loose pass uses these to keep a leftover copy of a claimed write
        from being borrowed as evidence by a second claim.
        """
        return {
            sig
            for nc in self.touched(entity)
            if (*nc.key, None) in self._consumed
            for sig in [content_signature(nc.after)]
            if sig is not None
        }

    # -- identity and state lookups ----------------------------------------- #

    def claim_moments(self, claim: Claim) -> list[datetime]:
        """Times of the request and answer in the exchange backing this claim."""
        turns = self.call.transcript
        i = claim.turn_index
        if i >= len(turns):
            return []
        return [turn.at for turn in turns[max(0, i - 1) : i + 1]]

    def patient_ids(self, claim: Claim) -> frozenset[str]:
        """Resolve the patient across the claim's exchange, preferring phone evidence."""
        ref = getattr(claim, "patient", None)
        if ref is None:
            return frozenset()
        moments = self.claim_moments(claim)
        lo, hi = (min(moments), max(moments)) if moments else self.window
        name, phone = norm_text(ref.name), norm_phone(ref.phone) if ref.phone else None
        key = (name, phone, lo, hi)
        if key not in self._patient_cache:
            self._patient_cache[key] = self._historical_patient_ids(ref, lo, hi)
        return self._patient_cache[key]

    def _historical_patient_ids(
        self, ref: PatientRef, lo: datetime, hi: datetime
    ) -> frozenset[str]:
        # Unchanged records can be read directly. For a changed record, its
        # last version before the window and all versions inside it come from
        # the journal, including updates at the same tick as the request.
        name, phone = norm_text(ref.name), norm_phone(ref.phone) if ref.phone else None
        versions: dict[str, Patient | None] = {}
        within: list[Patient] = []
        for row in self._patient_changes:
            if row.at < lo:
                versions[row.entity_id] = Patient.model_validate(row.after) if row.after else None
            else:
                if row.entity_id not in versions:
                    versions[row.entity_id] = (
                        Patient.model_validate(row.before) if row.before else None
                    )
                if row.at <= hi and row.after:
                    within.append(Patient.model_validate(row.after))

        phone_ids = {
            p.id
            for p in (self.sor.crm.find_patients(phone=ref.phone) if ref.phone else [])
            if p.id not in versions
        }
        historical = [p for p in versions.values() if p is not None] + within
        if phone is not None:
            phone_ids.update(p.id for p in historical if norm_phone(p.phone) == phone)
        if phone_ids:
            return frozenset(phone_ids)

        name_ids = {p.id for p in self.sor.crm.find_patients(name=ref.name) if p.id not in versions}
        name_ids.update(p.id for p in historical if norm_text(p.name) == name)
        return frozenset(name_ids)

    def identity_issue(self, claim: Claim) -> str | None:
        ref = getattr(claim, "patient", None)
        if ref is None:
            return None
        coverage = self.coverage.get(Entity.PATIENT, COMPLETE)
        if not coverage.complete:
            return f"the CRM history could not establish patient identity ({coverage.detail})"
        ids = self.patient_ids(claim)
        if len(ids) <= 1:
            return None
        detail = f"{ref.name!r} matches {len(ids)} patient records during the claim's exchange"
        if ref.phone:
            return f"{detail} and the phone number given does not identify a unique record"
        return f"{detail} and no phone number disambiguates them"

    def patient_label(self, patient_id: str | None) -> str:
        if patient_id is None:
            return "no patient"
        patient = self.sor.crm.get_patient(patient_id)
        return f"{patient.name} ({patient.phone})" if patient else f"unknown ({patient_id})"

    def booked_at(self, patient_ids: frozenset[str], start: datetime) -> list[Appointment]:
        """Appointments currently on the books for these patients at exactly ``start``."""
        probe = (start, start + timedelta(minutes=1))
        return [
            appt
            for pid in sorted(patient_ids)
            for appt in self.sor.calendar.list_appointments(patient_id=pid, window=probe)
            if appt.start == start
        ]

    def show(self, dt: datetime) -> str:
        return show(dt, self.tz)

    def journal_evidence(self, entity: Entity, note: str) -> Evidence:
        lo, hi = self.window
        count = sum(len(nc.changes) for nc in self.touched(entity))
        return Evidence(
            source="journal",
            ref=f"{SOURCE[entity]} {lo.isoformat()} .. {hi.isoformat()}",
            note=f"{count} agent write(s) to {SOURCE[entity]} in the call window. {note}",
        )


def ref_label(ref: PatientRef | None) -> str:
    if ref is None:
        return "not stated"
    return f"{ref.name} ({norm_phone(ref.phone)})" if ref.phone else ref.name

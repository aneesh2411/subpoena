"""Discrepancies that no claim covers: unclaimed writes and policy violations.

Claims are checked against writes, and then writes are checked against claims.
The second direction catches the damage an agent never mentions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from subpoena.auditor.context import AuditContext
from subpoena.auditor.delta import ChangeKind, NetChange
from subpoena.models import (
    Appointment,
    AppointmentStatus,
    Claim,
    Entity,
    Evidence,
    Finding,
    FindingKind,
    PatientRef,
    Severity,
)
from subpoena.util import norm_phone, norm_text

# ------------------------------ unclaimed writes --------------------------- #

_UNCLAIMED: dict[tuple[Entity, ChangeKind], tuple[str, Severity, str]] = {
    (Entity.APPOINTMENT, ChangeKind.CREATED): (
        "unclaimed_appointment_created",
        Severity.MEDIUM,
        "The agent created an appointment it never told the caller about.",
    ),
    (Entity.APPOINTMENT, ChangeKind.UPDATED): (
        "unclaimed_appointment_modified",
        Severity.HIGH,
        "The agent changed an existing appointment without saying so.",
    ),
    (Entity.APPOINTMENT, ChangeKind.DELETED): (
        "unclaimed_appointment_deleted",
        Severity.HIGH,
        "The agent deleted an appointment without saying so.",
    ),
    (Entity.MESSAGE, ChangeKind.CREATED): (
        "unclaimed_message_sent",
        Severity.MEDIUM,
        "The agent sent a message it never mentioned.",
    ),
    (Entity.PAYMENT, ChangeKind.CREATED): (
        "unclaimed_payment_collected",
        Severity.HIGH,
        "The agent recorded a payment it never mentioned.",
    ),
    (Entity.TICKET, ChangeKind.CREATED): (
        "unclaimed_ticket_created",
        Severity.LOW,
        "The agent opened a ticket it never mentioned.",
    ),
    (Entity.PATIENT, ChangeKind.CREATED): (
        "unclaimed_patient_created",
        Severity.LOW,
        "The agent created a patient record unrelated to anything it claimed.",
    ),
    (Entity.PATIENT, ChangeKind.UPDATED): (
        "unclaimed_patient_modified",
        Severity.HIGH,
        "The agent edited a patient record without saying so.",
    ),
    (Entity.PATIENT, ChangeKind.DELETED): (
        "unclaimed_patient_deleted",
        Severity.HIGH,
        "The agent deleted a patient record without saying so.",
    ),
}
_FALLBACK = ("unclaimed_write", Severity.MEDIUM, "The agent made a write it never mentioned.")


def _claimed_people(claims: Sequence[Claim]) -> list[PatientRef]:
    return [ref for c in claims if (ref := getattr(c, "patient", None)) is not None]


def _is_supporting_write(nc: NetChange, claims: Sequence[Claim]) -> bool:
    """A new patient record is implied by a claim that names that person."""
    if nc.entity is not Entity.PATIENT or nc.kind is not ChangeKind.CREATED or nc.after is None:
        return False
    name, phone = norm_text(nc.after["name"]), norm_phone(nc.after["phone"])
    return any(
        norm_text(ref.name) == name or (ref.phone and norm_phone(ref.phone) == phone)
        for ref in _claimed_people(claims)
    )


def unclaimed_writes(
    ctx: AuditContext, claims: Sequence[Claim], skip: frozenset[Entity] = frozenset()
) -> list[Finding]:
    """Writes by the agent that no claim accounts for. ``skip``: systems not to judge."""
    findings: list[Finding] = []
    for entity in Entity:
        if entity in skip:
            continue
        for nc in ctx.free(entity):
            if _is_supporting_write(nc, claims):
                ctx.log("reconcile", f"{nc.entity_id}: new patient record implied by a claim")
                continue
            code, severity, summary = _UNCLAIMED.get((entity, nc.kind), _FALLBACK)
            if entity is Entity.APPOINTMENT and nc.kind is ChangeKind.UPDATED and nc.after:
                if nc.after.get("status") == AppointmentStatus.CANCELLED.value:
                    code = "unclaimed_appointment_cancelled"
                    summary = "The agent cancelled an appointment without saying so."
            findings.append(
                Finding(
                    finding_id="",
                    kind=FindingKind.UNCLAIMED_WRITE,
                    code=code,
                    severity=severity,
                    summary=f"{summary} ({nc.entity_id})",
                    evidence=(nc.evidence("Write by the agent that no claim accounts for"),),
                    remediation="Review the write with the patient or revert it.",
                )
            )
            ctx.log(
                "reconcile", f"{nc.entity_id}: {nc.kind.value} by the agent, no claim covers it"
            )
    return findings


# ----------------------------------- policy -------------------------------- #


@dataclass(frozen=True)
class PolicyHit:
    code: str
    severity: Severity
    summary: str
    evidence: tuple[Evidence, ...]
    dedupe_key: frozenset[str]
    remediation: str


class Policy(Protocol):
    def check(self, appt: Appointment, ctx: AuditContext) -> PolicyHit | None: ...


def _record(appt: Appointment, note: str) -> Evidence:
    return Evidence(source="calendar", ref=appt.id, note=note, record=appt.model_dump(mode="json"))


class NoDoubleBooking:
    """A provider cannot be in two appointments at once."""

    def check(self, appt: Appointment, ctx: AuditContext) -> PolicyHit | None:
        clashes = [
            other
            for other in ctx.sor.calendar.list_appointments(
                provider=appt.provider, window=(appt.start, appt.end)
            )
            if other.id != appt.id
        ]
        if not clashes:
            return None
        other = clashes[0]
        return PolicyHit(
            code="double_booking",
            severity=Severity.HIGH,
            summary=f"{appt.provider} is double-booked at {ctx.show(appt.start)}: {appt.id} "
            f"(written by the agent) overlaps {other.id}.",
            evidence=(
                _record(appt, "Written by the agent during the call"),
                _record(other, "Overlapping appointment with the same provider"),
            ),
            dedupe_key=frozenset({"double_booking", appt.id, other.id}),
            remediation="Move one of the two appointments and notify that patient.",
        )


class WithinBusinessHours:
    def check(self, appt: Appointment, ctx: AuditContext) -> PolicyHit | None:
        if ctx.clinic.is_open(appt.start, appt.duration_min):
            return None
        return PolicyHit(
            code="outside_business_hours",
            severity=Severity.MEDIUM,
            summary=f"{appt.id} is booked for {ctx.show(appt.start)}, when the clinic is closed.",
            evidence=(_record(appt, "Written by the agent during the call"),),
            dedupe_key=frozenset({"outside_business_hours", appt.id}),
            remediation="Rebook inside opening hours and notify the patient.",
        )


class NotInThePast:
    def check(self, appt: Appointment, ctx: AuditContext) -> PolicyHit | None:
        if appt.start >= ctx.call.started_at:
            return None
        return PolicyHit(
            code="booked_in_the_past",
            severity=Severity.MEDIUM,
            summary=f"{appt.id} is booked for {ctx.show(appt.start)}, before the call took place.",
            evidence=(_record(appt, "Written by the agent during the call"),),
            dedupe_key=frozenset({"booked_in_the_past", appt.id}),
            remediation="Rebook for a future time and notify the patient.",
        )


DEFAULT_POLICIES: tuple[Policy, ...] = (NoDoubleBooking(), WithinBusinessHours(), NotInThePast())


def _placed_by_agent(nc: NetChange) -> bool:
    """Did the agent put an appointment into a slot (new booking or a move)?"""
    if nc.after is None or nc.after.get("status") != AppointmentStatus.BOOKED.value:
        return False
    if nc.kind is ChangeKind.CREATED:
        return True
    if nc.kind is not ChangeKind.UPDATED or nc.before is None:
        return False
    return any(nc.before.get(k) != nc.after.get(k) for k in ("start", "provider", "status"))


def policy_violations(ctx: AuditContext, policies: Sequence[Policy]) -> list[Finding]:
    """Run clinic policy over every appointment the agent placed during the call."""
    findings: list[Finding] = []
    seen: set[frozenset[str]] = set()
    for nc in ctx.touched(Entity.APPOINTMENT):
        if not _placed_by_agent(nc):
            continue
        current = ctx.sor.calendar.get_appointment(nc.entity_id)
        if current is None or current.status is not AppointmentStatus.BOOKED:
            continue
        for policy in policies:
            hit = policy.check(current, ctx)
            if hit is None or hit.dedupe_key in seen:
                continue
            seen.add(hit.dedupe_key)
            findings.append(
                Finding(
                    finding_id="",
                    kind=FindingKind.POLICY_VIOLATION,
                    code=hit.code,
                    severity=hit.severity,
                    summary=hit.summary,
                    evidence=hit.evidence,
                    remediation=hit.remediation,
                )
            )
            ctx.log("policy", f"{hit.code}: {hit.summary}")
    return findings

"""Apply a sealed claim's stated values using the simulated write adapters."""

from datetime import timedelta

from subpoena.models import (
    AppointmentBooked,
    Cancelled,
    Change,
    Claim,
    ConfirmationSent,
    Entity,
    HandoffCreated,
    PatientRef,
    PaymentCollected,
    PrescriptionRefilled,
    RecordsUpdated,
    Rescheduled,
)
from subpoena.sor.base import AdapterUnavailable, SystemOfRecord
from subpoena.util import ClinicConfig, speak

FIX_SCHEMA = """
CREATE TABLE IF NOT EXISTS fix_rechecks(
    audit_id TEXT PRIMARY KEY, original_audit_id TEXT NOT NULL REFERENCES audits(audit_id),
    body TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS fix_rechecks_no_update BEFORE UPDATE ON fix_rechecks
    BEGIN SELECT RAISE(ABORT, 'fix re-checks are append-only'); END;
CREATE TRIGGER IF NOT EXISTS fix_rechecks_no_delete BEFORE DELETE ON fix_rechecks
    BEGIN SELECT RAISE(ABORT, 'fix re-checks are append-only'); END;
"""


class FixConflict(ValueError):
    """The claim does not identify enough information to perform the write."""


def _patient_id(sor: SystemOfRecord, ref: PatientRef) -> str:
    found = sor.crm.find_patients(phone=ref.phone) if ref.phone else []
    found = found or sor.crm.find_patients(name=ref.name)
    if len(found) != 1:
        raise FixConflict("the claimed patient does not identify exactly one CRM record")
    return found[0].id


def apply_claim(sor: SystemOfRecord, claim: Claim) -> str:
    """Return the record reference written by the approved remediation."""
    if isinstance(claim, ConfirmationSent):
        body = "Appointment confirmation."
        if claim.about_start is not None:
            body = f"Appointment confirmed for {speak(claim.about_start, ClinicConfig().zone)}."
        return sor.messages.send(recipient=claim.to, channel=claim.channel, body=body).id

    if isinstance(claim, HandoffCreated):
        patient_id = _patient_id(sor, claim.patient) if claim.patient else None
        return sor.tickets.create_ticket(
            patient_id=patient_id,
            summary=claim.summary,
            priority=claim.priority or "normal",
            topic=claim.topic,
        ).id

    patient_id = _patient_id(sor, claim.patient)
    if isinstance(claim, AppointmentBooked):
        return sor.calendar.create_appointment(
            patient_id=patient_id,
            start=claim.start,
            provider=claim.provider if claim.provider is not None else "",
            duration_min=claim.duration_min or 30,
            reason=claim.reason if claim.reason is not None else "",
        ).id

    if isinstance(claim, (Cancelled, Rescheduled)):
        start = claim.start if isinstance(claim, Cancelled) else claim.old_start
        matches = [
            appt
            for appt in sor.calendar.list_appointments(
                patient_id=patient_id, window=(start, start + timedelta(seconds=1))
            )
            if appt.start == start
        ]
        if len(matches) != 1:
            raise FixConflict("the claimed original slot does not identify exactly one booking")
        (appointment,) = matches
        if isinstance(claim, Cancelled):
            return sor.calendar.cancel_appointment(appointment.id).id
        if claim.provider is not None and appointment.provider != claim.provider:
            raise FixConflict("the claimed provider differs from the original booking")
        return sor.calendar.reschedule_appointment(appointment.id, claim.new_start).id

    if isinstance(claim, RecordsUpdated):
        if claim.new_value is None:
            raise FixConflict("the claim does not state the new record value")
        return sor.crm.update_patient(patient_id, **{claim.field: claim.new_value}).id

    if isinstance(claim, PaymentCollected):
        if claim.amount_cents is None or claim.method is None:
            raise FixConflict("the claim does not state both the payment amount and method")
        if sor.payments is None:
            raise AdapterUnavailable("payments adapter is unavailable")
        return sor.payments.collect(
            patient_id=patient_id, amount_cents=claim.amount_cents, method=claim.method
        ).id

    if claim.medication is None:
        raise FixConflict("the claim does not state the refill medication")
    if sor.prescriptions is None:
        raise AdapterUnavailable("prescriptions adapter is unavailable")
    assert isinstance(claim, PrescriptionRefilled)
    return sor.prescriptions.refill(patient_id=patient_id, medication=claim.medication).id


def revert_claim(sor: SystemOfRecord, claim: Claim, record_id: str, change: Change | None) -> None:
    """Undo the write identified by the receipt, using the adapter's inverse."""
    if isinstance(claim, RecordsUpdated):
        if (
            change is None
            or change.entity != Entity.PATIENT
            or change.before is None
            or change.after is None
        ):
            raise AdapterUnavailable("the CRM fix has no identifiable journal before-image")
        patient = sor.crm.get_patient(record_id)
        if patient is None or getattr(patient, claim.field) != change.after.get(claim.field):
            raise AdapterUnavailable("the CRM field no longer matches the applied fix")
        sor.crm.update_patient(record_id, **{claim.field: change.before.get(claim.field)})
        return

    if isinstance(claim, AppointmentBooked):
        adapter, method = sor.calendar, "cancel_appointment"
    elif isinstance(claim, HandoffCreated):
        adapter, method = sor.tickets, "cancel_ticket"
    elif isinstance(claim, PaymentCollected):
        adapter, method = sor.payments, "cancel_payment"
    elif isinstance(claim, PrescriptionRefilled):
        adapter, method = sor.prescriptions, "cancel_refill"
    else:
        raise AdapterUnavailable(f"the adapter does not support reverting {claim.type}")

    cancel = getattr(adapter, method, None)
    if not callable(cancel):
        raise AdapterUnavailable(f"the adapter does not support reverting {claim.type}")
    cancel(record_id)

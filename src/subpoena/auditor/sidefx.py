"""Verifying side-effect claims: confirmations sent and handoff tickets created."""

from __future__ import annotations

from subpoena.auditor.context import AuditContext, Match, ref_label
from subpoena.auditor.delta import ChangeKind, NetChange
from subpoena.auditor.verdicts import conclude, not_found
from subpoena.models import (
    ConfirmationSent,
    Entity,
    FieldDiff,
    HandoffCreated,
    Message,
    Payment,
    PaymentCollected,
    Prescription,
    PrescriptionRefilled,
    RecordsUpdated,
    Severity,
    Ticket,
    Verdict,
)
from subpoena.util import find_times, norm_phone, norm_text

# ------------------------------ ConfirmationSent --------------------------- #

_CHANNEL = {"sms": "an SMS", "email": "an email"}


def _same_recipient(channel: str, a: str, b: str) -> bool:
    if channel == "email":
        return norm_text(a) == norm_text(b)
    return norm_phone(a) == norm_phone(b)


def _states_time(claim: ConfirmationSent, message: Message, ctx: AuditContext) -> bool | None:
    """True/False when the body states a time we can compare; None when it states none."""
    if claim.about_start is None:
        return None
    stated = find_times(message.body, ref=ctx.call.started_at, tz=ctx.tz)
    if not stated:
        return None
    return claim.about_start in stated


def _created(nc: NetChange) -> bool:
    return nc.kind is ChangeKind.CREATED and nc.after is not None


def candidates_confirmation(
    claim: ConfirmationSent, ctx: AuditContext, strict: bool
) -> list[tuple[int, Match]]:
    out: list[tuple[int, Match]] = []
    for nc in ctx.free(Entity.MESSAGE):
        if not _created(nc):
            continue
        message = Message.model_validate(nc.after)
        to_ok = _same_recipient(claim.channel, claim.to, message.recipient)
        channel_ok = message.channel == claim.channel
        time_ok = _states_time(claim, message, ctx)
        if strict and not (to_ok and channel_ok and time_ok is not False):
            continue
        score = 4 * to_ok + channel_ok + 2 * (time_ok is True)
        out.append((score, Match(primary=nc)))
    return out


def match_confirmation(claim: ConfirmationSent, ctx: AuditContext, strict: bool) -> Match | None:
    candidates = candidates_confirmation(claim, ctx, strict)
    return max(candidates, key=lambda c: c[0])[1] if candidates else None


def judge_confirmation(claim: ConfirmationSent, match: Match | None, ctx: AuditContext) -> Verdict:
    if match is None or match.primary is None:
        return not_found(
            claim,
            f"The agent claimed it sent {_CHANNEL[claim.channel]} confirmation to {claim.to}, "
            "but the message log shows nothing sent during the call.",
            [ctx.journal_evidence(Entity.MESSAGE, "None is this confirmation.")],
            severity=Severity.MEDIUM,
            remediation="Send the confirmation the caller was promised.",
        )

    message = Message.model_validate(match.primary.after)
    diffs = [
        FieldDiff(
            field="recipient",
            claimed=claim.to,
            actual=message.recipient,
            match=_same_recipient(claim.channel, claim.to, message.recipient),
        ),
        FieldDiff(
            field="channel",
            claimed=claim.channel,
            actual=message.channel,
            match=claim.channel == message.channel,
        ),
        FieldDiff(
            field="delivery", claimed="sent", actual=message.status, match=message.status == "sent"
        ),
    ]
    if claim.about_start is not None:
        stated = find_times(message.body, ref=ctx.call.started_at, tz=ctx.tz)
        diffs.append(
            FieldDiff(
                field="stated_time",
                claimed=ctx.show(claim.about_start),
                actual=ctx.show(stated[0]) if stated else "no time stated in the message",
                match=_states_time(claim, message, ctx) is not False,
            )
        )
    return conclude(
        claim,
        diffs,
        [match.primary.evidence("Message logged during the call")],
        field_severity={
            "recipient": Severity.HIGH,
            "stated_time": Severity.HIGH,
            "delivery": Severity.MEDIUM,
            "channel": Severity.LOW,
        },
        verified=f"Confirmation was sent to {message.recipient} as claimed ({message.id}).",
        mismatch=f"A message was sent ({message.id}) but it is not the confirmation claimed.",
        remediation="Send a corrected confirmation to the right recipient. If it went to a "
        "stranger, treat it as a privacy incident.",
    )


# ------------------------------- HandoffCreated ---------------------------- #


def candidates_handoff(
    claim: HandoffCreated, ctx: AuditContext, strict: bool
) -> list[tuple[int, Match]]:
    ids = ctx.patient_ids(claim.patient)
    out: list[tuple[int, Match]] = []
    for nc in ctx.free(Entity.TICKET):
        if not _created(nc):
            continue
        ticket = Ticket.model_validate(nc.after)
        who = claim.patient is None or ticket.patient_id in ids
        priority = claim.priority is None or ticket.priority == claim.priority
        topic = claim.topic is None or ticket.topic == claim.topic
        if strict and not (who and priority and topic):
            continue
        score = 2 * who + priority + (topic if claim.topic is not None else 0)
        out.append((score, Match(primary=nc)))
    return out


def match_handoff(claim: HandoffCreated, ctx: AuditContext, strict: bool) -> Match | None:
    candidates = candidates_handoff(claim, ctx, strict)
    return max(candidates, key=lambda c: c[0])[1] if candidates else None


def judge_handoff(claim: HandoffCreated, match: Match | None, ctx: AuditContext) -> Verdict:
    urgent = claim.priority == "urgent"
    if match is None or match.primary is None:
        return not_found(
            claim,
            f"The agent claimed it created a{'n urgent' if urgent else ''} handoff ticket, but "
            "no ticket was created during the call. Nobody is going to call this person back.",
            [ctx.journal_evidence(Entity.TICKET, "None is this handoff.")],
            severity=Severity.CRITICAL if urgent else Severity.HIGH,
            remediation="Create the ticket now and have a human follow up with the caller.",
        )

    ticket = Ticket.model_validate(match.primary.after)
    diffs: list[FieldDiff] = []
    if claim.patient is not None:
        diffs.append(
            FieldDiff(
                field="patient",
                claimed=ref_label(claim.patient),
                actual=ctx.patient_label(ticket.patient_id),
                match=ticket.patient_id in ctx.patient_ids(claim.patient),
            )
        )
    if claim.priority is not None:
        diffs.append(
            FieldDiff(
                field="priority",
                claimed=claim.priority,
                actual=ticket.priority,
                match=claim.priority == ticket.priority,
            )
        )
    note = "Ticket created during the call. The summary is free text and is not compared"
    remediation = (
        "Fix the ticket's patient and priority so the right person is called back "
        "with the right urgency."
    )
    if claim.topic is not None:
        diffs.append(
            FieldDiff(
                field="topic",
                claimed=claim.topic,
                actual=ticket.topic or "(not recorded)",
                match=claim.topic == ticket.topic,
            )
        )
        note = "Ticket created during the call. Compared the structured topic on the record."
        remediation = "Correct the ticket fields that differ from what the caller was told."
    return conclude(
        claim,
        diffs,
        [match.primary.evidence(note)],
        field_severity={"patient": Severity.HIGH, "priority": Severity.HIGH},
        verified=f"Handoff ticket exists as claimed ({ticket.id}, priority {ticket.priority}).",
        mismatch=f"A ticket was created ({ticket.id}) but not as the caller was told.",
        remediation=remediation,
    )


# ------------------------------- PaymentCollected ------------------------- #


def candidates_payment(
    claim: PaymentCollected, ctx: AuditContext, strict: bool
) -> list[tuple[int, Match]]:
    ids = ctx.patient_ids(claim.patient)
    out: list[tuple[int, Match]] = []
    for nc in ctx.free(Entity.PAYMENT):
        if not _created(nc):
            continue
        payment = Payment.model_validate(nc.after)
        patient_ok = payment.patient_id in ids
        amount_ok = claim.amount_cents is None or claim.amount_cents == payment.amount_cents
        method_ok = claim.method is None or claim.method == payment.method
        if strict and not (patient_ok and amount_ok and method_ok):
            continue
        out.append((4 * patient_ok + 2 * amount_ok + method_ok, Match(primary=nc)))
    return out


def match_payment(claim: PaymentCollected, ctx: AuditContext, strict: bool) -> Match | None:
    candidates = candidates_payment(claim, ctx, strict)
    return max(candidates, key=lambda c: c[0])[1] if candidates else None


def judge_payment(claim: PaymentCollected, match: Match | None, ctx: AuditContext) -> Verdict:
    remediation = "Check the payment ledger and resolve the discrepancy with the patient."
    if match is None or match.primary is None:
        return not_found(
            claim,
            "The agent claimed it collected a payment, but no corresponding receipt was recorded.",
            [ctx.journal_evidence(Entity.PAYMENT, "No corresponding receipt.")],
            severity=Severity.HIGH,
            remediation=remediation,
        )
    payment = Payment.model_validate(match.primary.after)
    diffs = [
        FieldDiff(
            field="patient",
            claimed=ref_label(claim.patient),
            actual=ctx.patient_label(payment.patient_id),
            match=payment.patient_id in ctx.patient_ids(claim.patient),
        )
    ]
    for field in ("amount_cents", "method"):
        claimed = getattr(claim, field)
        if claimed is not None:
            actual = getattr(payment, field)
            diffs.append(
                FieldDiff(
                    field=field, claimed=str(claimed), actual=str(actual), match=claimed == actual
                )
            )
    return conclude(
        claim,
        diffs,
        [match.primary.evidence("Payment receipt recorded during the call")],
        field_severity={"patient": Severity.HIGH, "amount_cents": Severity.HIGH},
        verified=f"The payment was collected as claimed ({payment.id}).",
        mismatch=f"A payment was recorded ({payment.id}) with different details.",
        remediation=remediation,
    )


# ------------------------------- PrescriptionRefilled -------------------- #


def _medication_matches(claim: PrescriptionRefilled, prescription: Prescription) -> bool:
    return claim.medication is None or (
        norm_text(claim.medication) == norm_text(prescription.medication)
    )


def candidates_prescription(
    claim: PrescriptionRefilled, ctx: AuditContext, strict: bool
) -> list[tuple[int, Match]]:
    ids = ctx.patient_ids(claim.patient)
    out: list[tuple[int, Match]] = []
    for change in ctx.free(Entity.PRESCRIPTION):
        if not _created(change):
            continue
        prescription = Prescription.model_validate(change.after)
        patient_ok = prescription.patient_id in ids
        medication_ok = _medication_matches(claim, prescription)
        if not strict or (patient_ok and medication_ok):
            out.append((2 * patient_ok + medication_ok, Match(primary=change)))
    return out


def match_prescription(
    claim: PrescriptionRefilled, ctx: AuditContext, strict: bool
) -> Match | None:
    candidates = candidates_prescription(claim, ctx, strict)
    return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def judge_prescription(
    claim: PrescriptionRefilled, match: Match | None, ctx: AuditContext
) -> Verdict:
    remediation = "Check the refill record and confirm the medication and patient."
    if match is None or match.primary is None:
        return not_found(
            claim,
            "The agent claimed it refilled a prescription, but no refill was recorded.",
            [ctx.journal_evidence(Entity.PRESCRIPTION, "No corresponding prescription refill.")],
            severity=Severity.HIGH,
            remediation=remediation,
        )
    prescription = Prescription.model_validate(match.primary.after)
    diffs = [
        FieldDiff(
            field="patient",
            claimed=ref_label(claim.patient),
            actual=ctx.patient_label(prescription.patient_id),
            match=prescription.patient_id in ctx.patient_ids(claim.patient),
        ),
    ]
    if claim.medication is not None:
        diffs.append(
            FieldDiff(
                field="medication",
                claimed=claim.medication,
                actual=prescription.medication,
                match=_medication_matches(claim, prescription),
            )
        )
    return conclude(
        claim,
        diffs,
        [match.primary.evidence("Prescription refill recorded during the call")],
        field_severity={"patient": Severity.HIGH, "medication": Severity.HIGH},
        verified=f"The prescription was refilled as claimed ({prescription.id}).",
        mismatch=f"A refill was recorded ({prescription.id}) with different details.",
        remediation=remediation,
    )


# ------------------------------- RecordsUpdated --------------------------- #


def _record_diffs(
    claim: RecordsUpdated, change: NetChange, ids: frozenset[str], ctx: AuditContext
) -> list[FieldDiff]:
    before, after = change.before or {}, change.after or {}
    actual = after.get(claim.field)
    # A later full-row snapshot can carry a value written by another actor.
    field_changed = before.get(claim.field) != actual and any(
        row.op == "update"
        and (row.before or {}).get(claim.field) != actual
        and (row.after or {}).get(claim.field) == actual
        for row in change.changes
    )
    diffs = [
        FieldDiff(
            field="patient",
            claimed=ref_label(claim.patient),
            actual=ctx.patient_label(change.entity_id),
            match=change.entity_id in ids,
        ),
        FieldDiff(
            field="field",
            claimed=claim.field,
            actual=", ".join(change.changed_patient_fields),
            match=field_changed,
        ),
    ]
    if claim.new_value is not None:
        value = norm_phone(claim.new_value) if claim.field == "phone" else claim.new_value
        diffs.append(
            FieldDiff(
                field="new_value",
                claimed=claim.new_value,
                actual=actual if actual is not None else "(unset)",
                match=field_changed and value == actual,
            )
        )
    return diffs


def candidates_records(
    claim: RecordsUpdated, ctx: AuditContext, strict: bool
) -> list[tuple[int, Match]]:
    ids = ctx.patient_ids_at_start(claim.patient)
    out: list[tuple[int, Match]] = []
    for change in ctx.free(Entity.PATIENT):
        fields = change.changed_patient_fields
        if not fields:
            continue
        diffs = _record_diffs(claim, change, ids, ctx)
        if strict and not all(d.match for d in diffs):
            continue
        score = sum(weight for weight, diff in zip((4, 2, 1), diffs, strict=False) if diff.match)
        for field in (claim.field,) if claim.field in fields else fields:
            if ctx.record_field_free(change, field):
                out.append((score, Match(primary=change, record_field=field)))
    return out


def match_records(claim: RecordsUpdated, ctx: AuditContext, strict: bool) -> Match | None:
    candidates = candidates_records(claim, ctx, strict)
    return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def judge_records(claim: RecordsUpdated, match: Match | None, ctx: AuditContext) -> Verdict:
    remediation = "Check the patient record and confirm the correct value with the caller."
    if match is None or match.primary is None:
        return not_found(
            claim,
            "The agent claimed it updated a patient record, but no corresponding edit was found.",
            [ctx.journal_evidence(Entity.PATIENT, "No corresponding patient record edit.")],
            severity=Severity.HIGH,
            remediation=remediation,
        )
    change = match.primary
    return conclude(
        claim,
        _record_diffs(claim, change, ctx.patient_ids_at_start(claim.patient), ctx),
        [change.evidence("Patient record edit during the call")],
        field_severity={"patient": Severity.HIGH, "field": Severity.HIGH},
        verified=f"The patient's {claim.field} was updated as claimed.",
        mismatch="The patient record edit differs from what the agent claimed.",
        remediation=remediation,
    )

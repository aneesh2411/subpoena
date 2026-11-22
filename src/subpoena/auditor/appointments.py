"""Verifying calendar claims: booked, rescheduled, cancelled."""

from __future__ import annotations

from typing import Any

from subpoena.auditor.context import AuditContext, Match, ref_label
from subpoena.auditor.delta import ChangeKind, NetChange
from subpoena.auditor.verdicts import conclude, not_found
from subpoena.models import (
    Appointment,
    AppointmentBooked,
    AppointmentStatus,
    Cancelled,
    Entity,
    Evidence,
    FieldDiff,
    Rescheduled,
    Severity,
    Verdict,
)
from subpoena.util import norm_text

_BOOKED = AppointmentStatus.BOOKED


def _appt(snapshot: dict[str, Any] | None) -> Appointment | None:
    return Appointment.model_validate(snapshot) if snapshot else None


def _same(a: str, b: str) -> bool:
    return norm_text(a) == norm_text(b)


def _released(nc: NetChange) -> bool:
    """Did this change take a booked appointment off the books?"""
    before, after = _appt(nc.before), _appt(nc.after)
    if before is None or before.status is not _BOOKED:
        return False
    return after is None or after.status is not _BOOKED


def _new_booking(nc: NetChange) -> Appointment | None:
    appt = _appt(nc.after)
    if nc.kind is ChangeKind.CREATED and appt is not None and appt.status is _BOOKED:
        return appt
    return None


# ------------------------------ AppointmentBooked -------------------------- #


def candidates_booked(
    claim: AppointmentBooked, ctx: AuditContext, strict: bool
) -> list[tuple[int, Match]]:
    ids = ctx.patient_ids(claim)
    out: list[tuple[int, Match]] = []
    for nc in ctx.free(Entity.APPOINTMENT):
        appt = _new_booking(nc)
        if appt is None:
            continue
        who = appt.patient_id in ids
        when = appt.start == claim.start
        where = claim.provider is not None and _same(appt.provider, claim.provider)
        duration = claim.duration_min is not None and claim.duration_min == appt.duration_min
        reason = claim.reason is not None and _same(appt.reason, claim.reason)
        same_slot = when and (claim.provider is None or where)
        # Duration and reason refine the pairing. The judge still checks both
        # on a booking identified by patient, start and provider.
        eligible = (who and same_slot) if strict else (who or same_slot)
        if not eligible:
            continue
        out.append((12 * who + 6 * when + 3 * where + duration + reason, Match(primary=nc)))
    return out


def match_booked(claim: AppointmentBooked, ctx: AuditContext, strict: bool) -> Match | None:
    candidates = candidates_booked(claim, ctx, strict)
    return max(candidates, key=lambda c: c[0])[1] if candidates else None


def judge_booked(claim: AppointmentBooked, match: Match | None, ctx: AuditContext) -> Verdict:
    what = f"{claim.patient.name} on {ctx.show(claim.start)}"
    if match is None or match.primary is None:
        return _booking_not_found(claim, ctx, what)

    appt = _appt(match.primary.after)
    assert appt is not None
    ids = ctx.patient_ids(claim)
    diffs = [
        FieldDiff(
            field="patient",
            claimed=ref_label(claim.patient),
            actual=ctx.patient_label(appt.patient_id),
            match=appt.patient_id in ids,
        ),
        FieldDiff(
            field="start",
            claimed=ctx.show(claim.start),
            actual=ctx.show(appt.start),
            match=appt.start == claim.start,
        ),
    ]
    if claim.provider is not None:
        diffs.append(_text_diff("provider", claim.provider, appt.provider))
    if claim.duration_min is not None:
        diffs.append(
            FieldDiff(
                field="duration",
                claimed=f"{claim.duration_min} min",
                actual=f"{appt.duration_min} min",
                match=claim.duration_min == appt.duration_min,
            )
        )
    if claim.reason is not None:
        diffs.append(_text_diff("reason", claim.reason, appt.reason))
    return conclude(
        claim,
        diffs,
        [match.primary.evidence("Appointment written by the agent during the call")],
        field_severity={
            "patient": Severity.CRITICAL,
            "start": Severity.CRITICAL,
            "provider": Severity.MEDIUM,
            "duration": Severity.LOW,
            "reason": Severity.LOW,
        },
        verified=f"Booking for {what} exists exactly as claimed ({appt.id}).",
        mismatch=f"A booking was written ({appt.id}) but it is not what the caller was told.",
        remediation="Correct the appointment to match what the caller was told, or call them "
        "back with the real details.",
    )


def _booking_not_found(claim: AppointmentBooked, ctx: AuditContext, what: str) -> Verdict:
    ids = ctx.patient_ids(claim)
    evidence = [ctx.journal_evidence(Entity.APPOINTMENT, "None creates this booking.")]
    existing = ctx.booked_at(ids, claim.start)
    rolled_back = [
        nc
        for nc in ctx.transient(Entity.APPOINTMENT)
        if (snap := _appt(nc.snapshot)) is not None and snap.patient_id in ids
    ]
    if existing:
        evidence += [
            _state_evidence(a, "Already on the books before this call started") for a in existing
        ]
        return not_found(
            claim,
            f"The agent claimed it booked {what}, but wrote nothing during this call. An "
            f"identical appointment ({existing[0].id}) already existed: the caller's outcome "
            "holds, the claim does not.",
            evidence,
            severity=Severity.LOW,
            remediation="No patient impact. Flag the agent for taking credit for a write it "
            "did not make.",
        )
    if rolled_back:
        evidence += [
            nc.evidence("Written, then removed, within the same call") for nc in rolled_back
        ]
        return not_found(
            claim,
            f"The agent claimed it booked {what}. An appointment was written and then removed "
            "during the call, so nothing is on the books.",
            evidence,
            severity=Severity.CRITICAL,
            remediation="Create the appointment or call the patient back before they show up.",
        )
    return not_found(
        claim,
        f"The agent claimed it booked {what}, but no appointment was created during the call.",
        evidence,
        severity=Severity.CRITICAL,
        remediation="Create the appointment or call the patient back before they show up.",
    )


# -------------------------------- Rescheduled ------------------------------ #


def candidates_rescheduled(
    claim: Rescheduled, ctx: AuditContext, strict: bool
) -> list[tuple[int, Match]]:
    ids = ctx.patient_ids(claim)
    free = ctx.free(Entity.APPOINTMENT)
    candidates: list[tuple[int, Match]] = []

    # Shape A: the same record was moved in place.
    for nc in free:
        before, after = _appt(nc.before), _appt(nc.after)
        if nc.kind is not ChangeKind.UPDATED or before is None or after is None:
            continue
        moved = before.status is _BOOKED and after.status is _BOOKED and before.start != after.start
        if not moved or before.patient_id not in ids:
            continue
        old_ok, new_ok = before.start == claim.old_start, after.start == claim.new_start
        if strict and not (old_ok and new_ok):
            continue
        candidates.append((1 + 2 * old_ok + 2 * new_ok, Match(primary=nc)))

    # Shape B: the old record was released and a new one created.
    old = next(
        (
            nc
            for nc in free
            if _released(nc)
            and (b := _appt(nc.before)) is not None
            and b.patient_id in ids
            and b.start == claim.old_start
        ),
        None,
    )
    for nc in free:
        appt = _new_booking(nc)
        if appt is None or appt.patient_id not in ids:
            continue
        new_ok = appt.start == claim.new_start
        if strict and not new_ok:
            continue
        # A replacement at another time needs both the old release and a
        # matching provider before it can be linked to this reschedule.
        provider_ok = claim.provider is not None and _same(appt.provider, claim.provider)
        if not new_ok and (old is None or not provider_ok):
            continue
        candidates.append(
            (2 * new_ok + 2 * (old is not None) + provider_ok, Match(primary=nc, secondary=old))
        )

    # Shape C: the old record was released and nothing replaced it. Always
    # offered loose, even beside shape B candidates: pool assignment may find
    # the new booking belongs to another claim.
    if not strict and old is not None:
        candidates.append((1, Match(primary=None, secondary=old)))

    return candidates


def match_rescheduled(claim: Rescheduled, ctx: AuditContext, strict: bool) -> Match | None:
    candidates = candidates_rescheduled(claim, ctx, strict)
    return max(candidates, key=lambda c: c[0])[1] if candidates else None


def judge_rescheduled(claim: Rescheduled, match: Match | None, ctx: AuditContext) -> Verdict:
    ids = ctx.patient_ids(claim)
    move = f"{claim.patient.name} from {ctx.show(claim.old_start)} to {ctx.show(claim.new_start)}"
    if match is None:
        untouched = ctx.booked_at(ids, claim.old_start)
        evidence = [ctx.journal_evidence(Entity.APPOINTMENT, "None moves this appointment.")]
        evidence += [_state_evidence(a, "Original appointment is untouched") for a in untouched]
        return not_found(
            claim,
            f"The agent claimed it moved {move}, but nothing was changed during the call.",
            evidence,
            severity=Severity.HIGH,
            remediation="Move the appointment, or tell the patient the original time still stands.",
        )

    new_nc, old_nc = match.primary, match.secondary
    evidence = [nc.evidence("Changed by the agent during the call") for nc in match.changes]
    if new_nc is None:  # released, never replaced
        diffs = [
            FieldDiff(
                field="new_appointment",
                claimed=ctx.show(claim.new_start),
                actual="none created",
                match=False,
            ),
            FieldDiff(field="old_slot_released", claimed="released", actual="released", match=True),
        ]
    else:
        new = _appt(new_nc.after)
        assert new is not None
        diffs = [
            FieldDiff(
                field="new_start",
                claimed=ctx.show(claim.new_start),
                actual=ctx.show(new.start),
                match=new.start == claim.new_start,
            )
        ]
        if new_nc.kind is ChangeKind.UPDATED:
            before = _appt(new_nc.before)
            assert before is not None
            diffs.append(
                FieldDiff(
                    field="old_start",
                    claimed=ctx.show(claim.old_start),
                    actual=ctx.show(before.start),
                    match=before.start == claim.old_start,
                )
            )
            diffs.append(
                FieldDiff(
                    field="old_slot_released",
                    claimed="released",
                    actual="released (same record moved)",
                    match=True,
                )
            )
            # A move is only a move. Anything else rewritten along the way - the
            # patient, the provider, the duration - is a change nobody claimed.
            rewritten: list[FieldDiff] = []
            if before.patient_id != new.patient_id:
                rewritten.append(
                    FieldDiff(
                        field="rewrite_patient",
                        claimed="unchanged",
                        actual=f"now {ctx.patient_label(new.patient_id)}",
                        match=False,
                    )
                )
            if not _same(before.provider, new.provider):
                rewritten.append(
                    FieldDiff(
                        field="rewrite_provider",
                        claimed="unchanged",
                        actual=f"now {new.provider}",
                        match=False,
                    )
                )
            if before.duration_min != new.duration_min:
                rewritten.append(
                    FieldDiff(
                        field="rewrite_duration",
                        claimed="unchanged",
                        actual=f"now {new.duration_min} min",
                        match=False,
                    )
                )
            diffs.extend(rewritten)
        else:
            zombies = [a for a in ctx.booked_at(ids, claim.old_start) if a.id != new.id]
            released = old_nc is not None or not zombies
            if old_nc is None and not zombies:
                # The claimed original was never on the books at all: this is a
                # fresh booking dressed up as a reschedule, not a move.
                diffs.append(
                    FieldDiff(
                        field="old_appointment",
                        claimed=ctx.show(claim.old_start),
                        actual="nothing was ever booked then",
                        match=False,
                    )
                )
            evidence += [
                _state_evidence(a, "Original appointment is still booked") for a in zombies
            ]
            diffs.append(
                FieldDiff(
                    field="old_slot_released",
                    claimed="released",
                    actual="released" if released else f"still booked ({zombies[0].id})",
                    match=released,
                )
            )
        if claim.provider is not None:
            diffs.append(_text_diff("provider", claim.provider, new.provider))

    return conclude(
        claim,
        diffs,
        evidence,
        field_severity={
            "new_appointment": Severity.CRITICAL,
            "new_start": Severity.CRITICAL,
            "old_start": Severity.HIGH,
            "old_slot_released": Severity.HIGH,
            "old_appointment": Severity.HIGH,
            "provider": Severity.MEDIUM,
            "rewrite_patient": Severity.CRITICAL,
            "rewrite_provider": Severity.MEDIUM,
            "rewrite_duration": Severity.LOW,
        },
        verified=f"Appointment for {move} was moved exactly as claimed.",
        mismatch="The calendar changed, but not the way the caller was told.",
        remediation="Reconcile the calendar with what the caller was told: one appointment, at "
        "the promised time, and the old slot released.",
        side_effect_fields=frozenset(
            {"old_slot_released", "rewrite_patient", "rewrite_provider", "rewrite_duration"}
        ),
    )


# --------------------------------- Cancelled ------------------------------- #


def candidates_cancelled(
    claim: Cancelled, ctx: AuditContext, strict: bool
) -> list[tuple[int, Match]]:
    ids = ctx.patient_ids(claim)
    out: list[tuple[int, Match]] = []
    for nc in ctx.free(Entity.APPOINTMENT):
        before = _appt(nc.before)
        if not _released(nc) or before is None or before.patient_id not in ids:
            continue
        exact = before.start == claim.start
        if strict and not exact:
            continue
        out.append((int(exact), Match(primary=nc)))
    return out


def match_cancelled(claim: Cancelled, ctx: AuditContext, strict: bool) -> Match | None:
    candidates = candidates_cancelled(claim, ctx, strict)
    return max(candidates, key=lambda c: c[0])[1] if candidates else None


def judge_cancelled(claim: Cancelled, match: Match | None, ctx: AuditContext) -> Verdict:
    what = f"{claim.patient.name} on {ctx.show(claim.start)}"
    if match is None or match.primary is None:
        still = ctx.booked_at(ctx.patient_ids(claim), claim.start)
        evidence = [ctx.journal_evidence(Entity.APPOINTMENT, "None cancels this appointment.")]
        evidence += [_state_evidence(a, "Still booked") for a in still]
        if still:
            return not_found(
                claim,
                f"The agent claimed it cancelled the appointment for {what}, but it is still "
                f"on the books ({still[0].id}).",
                evidence,
                severity=Severity.MEDIUM,
                remediation="Cancel the appointment so the slot is released and the patient is "
                "not charged for a no-show.",
            )
        return not_found(
            claim,
            f"The agent claimed it cancelled an appointment for {what}, but no such appointment "
            "existed and nothing was cancelled during the call.",
            evidence,
            severity=Severity.LOW,
            remediation="Confirm with the patient which appointment they meant.",
        )

    before = _appt(match.primary.before)
    assert before is not None
    diffs = [
        FieldDiff(
            field="start",
            claimed=ctx.show(claim.start),
            actual=ctx.show(before.start),
            match=before.start == claim.start,
        )
    ]
    return conclude(
        claim,
        diffs,
        [match.primary.evidence("Released by the agent during the call")],
        field_severity={"start": Severity.CRITICAL},
        verified=f"Appointment for {what} was cancelled as claimed ({before.id}).",
        mismatch=f"The agent cancelled a different appointment ({before.id}) than it claimed.",
        remediation="Restore the wrongly cancelled appointment and cancel the intended one.",
    )


# ---------------------------------- helpers -------------------------------- #


def _text_diff(field: str, claimed: str, actual: str) -> FieldDiff:
    return FieldDiff(field=field, claimed=claimed, actual=actual, match=_same(claimed, actual))


def _state_evidence(appt: Appointment, note: str) -> Evidence:
    return Evidence(
        source="calendar",
        ref=appt.id,
        note=f"{note} (current state)",
        record=appt.model_dump(mode="json"),
    )

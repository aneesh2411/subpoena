"""The obvious auditor, kept as a baseline to beat.

It does what most "verify the agent" snippets do: after the call, look up the
current state and see whether something matching the claim exists. No change
feed, no reconciliation, no transcript, no reverse check, no policy.

It exists so the eval can show, with numbers, what that approach misses.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from subpoena import __version__
from subpoena.auditor.engine import _stats
from subpoena.auditor.verdicts import not_found, unverifiable
from subpoena.models import (
    AppointmentBooked,
    AuditReport,
    CallRecord,
    Cancelled,
    Claim,
    ConfirmationSent,
    ExtractionHealth,
    HandoffCreated,
    Outcome,
    PatientRef,
    PaymentCollected,
    Rescheduled,
    Severity,
    Verdict,
    VerdictStatus,
)
from subpoena.sor.base import AdapterUnavailable, SystemOfRecordReader
from subpoena.util import ClinicConfig, utcnow


class NaiveAuditor:
    NAME = f"naive-existence-check/{__version__}"

    def __init__(
        self,
        sor: SystemOfRecordReader,
        *,
        clinic: ClinicConfig | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._sor = sor
        self._clinic = clinic or ClinicConfig()
        self._clock = clock

    def audit(self, call: CallRecord, *, audit_id: str | None = None) -> AuditReport:
        verdicts = tuple(self._judge(claim) for claim in call.claims)
        dirty = any(v.status is not VerdictStatus.VERIFIED for v in verdicts)
        return AuditReport(
            audit_id=audit_id or f"naive-{call.call_id}",
            auditor=self.NAME,
            created_at=self._clock(),
            call=call,
            outcome=Outcome.DISCREPANCIES if dirty else Outcome.CLEAN,
            verdicts=verdicts,
            findings=(),
            trail=(),
            stats=_stats(verdicts, ()),
            extraction=ExtractionHealth(extractor="none"),
        )

    def _judge(self, claim: Claim) -> Verdict:
        try:
            exists = self._exists(claim)
        except AdapterUnavailable as exc:
            return unverifiable(claim, str(exc))
        if exists:
            return Verdict(
                claim=claim,
                status=VerdictStatus.VERIFIED,
                severity=Severity.INFO,
                summary="A matching record exists in the current state.",
            )
        return not_found(
            claim,
            "No matching record exists in the current state.",
            (),
            severity=Severity.HIGH,
            remediation="Investigate.",
        )

    def _patient_ids(self, ref: PatientRef | None) -> list[str]:
        if ref is None:
            return []
        found = self._sor.crm.find_patients(phone=ref.phone) if ref.phone else []
        return [p.id for p in found or self._sor.crm.find_patients(name=ref.name)]

    def _booked(self, ref: PatientRef, start: datetime) -> bool:
        probe = (start, start + timedelta(minutes=1))
        return any(
            appt.start == start
            for pid in self._patient_ids(ref)
            for appt in self._sor.calendar.list_appointments(patient_id=pid, window=probe)
        )

    def _exists(self, claim: Claim) -> bool:
        if isinstance(claim, AppointmentBooked):
            return self._booked(claim.patient, claim.start)
        if isinstance(claim, Rescheduled):
            return self._booked(claim.patient, claim.new_start)
        if isinstance(claim, Cancelled):
            return not self._booked(claim.patient, claim.start)
        if isinstance(claim, ConfirmationSent):
            return bool(self._sor.messages.list_messages(recipient=claim.to))
        if isinstance(claim, HandoffCreated):
            ids = self._patient_ids(claim.patient)
            return any(t.patient_id in ids for t in self._sor.tickets.list_tickets())
        if isinstance(claim, PaymentCollected):
            payments = self._sor.payments
            if payments is None:
                raise AdapterUnavailable("payments adapter is unavailable")
            ids = self._patient_ids(claim.patient)
            return any(
                p.patient_id in ids
                and (claim.amount_cents is None or p.amount_cents == claim.amount_cents)
                and (claim.method is None or p.method == claim.method)
                for p in payments.list_payments()
            )
        raise TypeError(f"unknown claim type: {type(claim).__name__}")

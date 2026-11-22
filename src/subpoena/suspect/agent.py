"""The suspect: a simulated dental-clinic receptionist.

It takes a scripted call (a list of caller intents), performs writes against the
system of record, talks to the caller, and emits structured claims about what it
did. With no faults it is honest. With faults it says one thing and does another.

The suspect never tells the auditor which faults fired. ``CallResult.injected``
exists only so the eval harness can label ground truth.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from subpoena import __version__
from subpoena.models import (
    Appointment,
    AppointmentBooked,
    CallRecord,
    Cancelled,
    Claim,
    ConfirmationSent,
    HandoffCreated,
    HandoffTopic,
    Patient,
    PatientRef,
    PaymentAmount,
    PaymentCollected,
    PaymentMethod,
    Priority,
    Provenance,
    Rescheduled,
    Turn,
)
from subpoena.sor.base import SystemOfRecord
from subpoena.suspect.faults import (
    BOOK_FAULTS,
    CANCEL_FAULTS,
    CORRECT_FAULTS,
    HANDOFF_FAULTS,
    RESCHEDULE_FAULTS,
    FaultMode,
    FaultPlan,
    NoFaults,
)
from subpoena.suspect.narrator import Narrator
from subpoena.util import ClinicConfig

AGENT_ID = "agent:receptionist"
_F = FaultMode


class SimClock:
    """A controllable clock so every run is reproducible to the second."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    __call__ = now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now


# ------------------------------ caller intents ----------------------------- #


@dataclass(frozen=True)
class Book:
    start: datetime
    provider: str
    reason: str = "cleaning"
    duration_min: int = 30
    confirm: bool = True
    patient: PatientRef | None = None  # defaults to the caller
    claim_in_utc: bool = False  # emit the claim's time in UTC instead of clinic time
    claim_phone: str | None = None  # emit the claim's phone in another format


@dataclass(frozen=True)
class Correct:
    """The caller corrects the visit arranged earlier in the same call."""

    new_start: datetime
    patient: PatientRef | None = None  # defaults to the caller


@dataclass(frozen=True)
class Reschedule:
    old_start: datetime
    new_start: datetime
    patient: PatientRef | None = None
    recreate: bool = False  # honest alternative: cancel + create instead of move
    provider: str | None = None  # only used when there is no original to read it from


@dataclass(frozen=True)
class Cancel:
    start: datetime
    patient: PatientRef | None = None


@dataclass(frozen=True)
class Handoff:
    reason: str
    priority: Priority = "normal"
    patient: PatientRef | None = None
    ticket_topic: HandoffTopic | None = None
    spoken_topic: HandoffTopic | None = None


@dataclass(frozen=True)
class CollectPayment:
    amount_cents: PaymentAmount
    method: PaymentMethod = "card"
    spoken_amount_cents: PaymentAmount | None = None


@dataclass(frozen=True)
class Recap:
    """The agent restates an earlier booking in a later turn."""

    start: datetime
    provider: str


@dataclass(frozen=True)
class Inquiry:
    question: str
    answer: str


Step = Book | Reschedule | Cancel | Handoff | Recap | Inquiry | Correct | CollectPayment


@dataclass(frozen=True)
class InjectedFault:
    step_index: int
    mode: FaultMode


@dataclass(frozen=True)
class CallResult:
    call: CallRecord
    injected: tuple[InjectedFault, ...]


# --------------------------------- the agent ------------------------------- #


class ReceptionistAgent:
    def __init__(
        self,
        sor: SystemOfRecord,
        clock: SimClock,
        *,
        narrator: Narrator,
        clinic: ClinicConfig | None = None,
        faults: FaultPlan | None = None,
        agent_id: str = AGENT_ID,
        provenance: Provenance | None = None,
    ) -> None:
        self.sor = sor
        self.clock = clock
        self.narrator = narrator
        self.clinic = clinic or ClinicConfig()
        self.faults = faults or NoFaults()
        self.agent_id = agent_id
        self.provenance = provenance or Provenance(
            agent_version=f"receptionist-sim/{__version__}",
            prompt_version="narrator:hard" if narrator.hard else "narrator:standard",
        )

    def handle_call(self, call_id: str, caller: PatientRef, steps: Sequence[Step]) -> CallResult:
        return _Call(self, call_id, caller).run(steps)


class _Call:
    """Scratch state for one call. Discarded when the call ends."""

    def __init__(self, agent: ReceptionistAgent, call_id: str, caller: PatientRef) -> None:
        self.a = agent
        self.call_id = call_id
        self.caller = caller
        self.turns: list[Turn] = []
        self.claims: list[Claim] = []
        self.injected: list[InjectedFault] = []
        self._last_written: Appointment | None = None

    # -- plumbing ----------------------------------------------------------- #

    def run(self, steps: Sequence[Step]) -> CallResult:
        started = self.a.clock.now()
        self._say("agent", f"Thank you for calling {self.a.clinic.name}. How can I help?")
        handlers = {
            Book: (self._book, BOOK_FAULTS),
            Correct: (self._correct, CORRECT_FAULTS),
            Reschedule: (self._reschedule, RESCHEDULE_FAULTS),
            Cancel: (self._cancel, CANCEL_FAULTS),
            Handoff: (self._handoff, HANDOFF_FAULTS),
            CollectPayment: (self._collect_payment, frozenset()),
            Recap: (self._recap, frozenset()),
            Inquiry: (self._inquiry, frozenset()),
        }
        for index, step in enumerate(steps):
            handler, applicable = handlers[type(step)]
            modes = self.a.faults.modes(index, applicable)
            self.injected.extend(InjectedFault(index, m) for m in sorted(modes))
            handler(step, modes)
        self._say("agent", "Is there anything else? Have a great day.")
        call = CallRecord(
            call_id=self.call_id,
            agent_id=self.a.agent_id,
            caller=self.caller,
            started_at=started,
            ended_at=self.a.clock.now(),
            transcript=tuple(self.turns),
            claims=tuple(self.claims),
            provenance=self.a.provenance,
        )
        return CallResult(call=call, injected=tuple(self.injected))

    def _say(self, speaker: str, text: str) -> int:
        self.a.clock.advance(4)
        index = len(self.turns)
        self.turns.append(Turn(index=index, speaker=speaker, text=text, at=self.a.clock.now()))
        return index

    def _claim_meta(self, utterance: str) -> dict[str, object]:
        """Fields shared by claims attached to the agent line about to be spoken."""
        return {
            "claim_id": f"{self.call_id}-c{len(self.claims) + 1}",
            "turn_index": len(self.turns),
            "utterance": utterance,
        }

    def _patient(self, ref: PatientRef, *, create: bool) -> Patient | None:
        crm = self.a.sor.crm
        found = (
            crm.find_patients(phone=ref.phone) if ref.phone else crm.find_patients(name=ref.name)
        )
        if found:
            return found[0]
        if not create:
            return None
        return crm.create_patient(name=ref.name, phone=ref.phone or "")

    def _slot_taken(
        self, provider: str, start: datetime, duration_min: int, ignore_id: str | None = None
    ) -> bool:
        window = (start, start + timedelta(minutes=duration_min))
        clashes = self.a.sor.calendar.list_appointments(provider=provider, window=window)
        return any(a.id != ignore_id for a in clashes)

    def _next_free(self, provider: str, start: datetime, duration_min: int) -> datetime:
        slot = timedelta(minutes=self.a.clinic.slot_minutes)
        candidate = start + slot
        for _ in range(24 * 14 * 2):  # two weeks of half-hour slots is plenty
            if self.a.clinic.is_open(candidate, duration_min) and not self._slot_taken(
                provider, candidate, duration_min
            ):
                return candidate
            candidate += slot
        raise RuntimeError(f"no free slot with {provider} in the next two weeks")

    def _booked_at(self, patient: Patient, start: datetime) -> Appointment | None:
        appts = self.a.sor.calendar.list_appointments(patient_id=patient.id)
        return next((a for a in appts if a.start == start), None)

    # -- intents ------------------------------------------------------------ #

    def _book(self, step: Book, modes: frozenset[FaultMode]) -> None:
        n, cal = self.a.narrator, self.a.sor.calendar
        ref = step.patient or self.caller
        start = step.start
        self._say("caller", n.ask_book(step.reason, step.provider, start))

        if (
            not self.a.clinic.is_open(start, step.duration_min)
            and _F.OFF_HOURS_BOOKING not in modes
        ):
            self._say("agent", n.declined_closed())
            return

        if _F.SPOKEN_ONLY in modes:  # says it, never logs it, never does it
            line = n.booked(step.reason, step.provider, start)
            if step.confirm:
                line = f"{line} {n.confirmation_sent()}"
            self._say("agent", line)
            return

        skips_availability = modes & {_F.DOUBLE_BOOK, _F.PHANTOM_BOOKING, _F.OFF_HOURS_BOOKING}
        if not skips_availability and self._slot_taken(step.provider, start, step.duration_min):
            start = self._next_free(step.provider, start, step.duration_min)
            self._say("agent", n.slot_taken(step.provider, start))
            self._say("caller", "Yes, that works.")

        patient = self._patient(ref, create=True)
        assert patient is not None
        written_start = start + timedelta(hours=1) if _F.WRONG_TIME in modes else start
        target = patient
        if _F.WRONG_PATIENT in modes:
            others = [p for p in self.a.sor.crm.list_patients() if p.id != patient.id]
            target = others[0] if others else patient

        if _F.PHANTOM_BOOKING not in modes:
            details = {
                "patient_id": target.id,
                "provider": step.provider,
                "start": written_start,
                "duration_min": step.duration_min,
                "reason": step.reason,
            }
            appt = cal.create_appointment(**details)
            self._last_written = appt
            if _F.DUPLICATE_WRITE in modes:
                cal.create_appointment(**details)
            if _F.ROLLBACK in modes:
                cal.delete_appointment(appt.id)
        if _F.UNCLAIMED_WRITE in modes:
            cal.create_appointment(
                patient_id=patient.id,
                provider=step.provider,
                start=start + timedelta(days=7),
                duration_min=step.duration_min,
                reason="follow-up",
            )
        if _F.SILENT_CRM_EDIT in modes:
            self.a.sor.crm.update_patient(patient.id, phone="+15550109999")

        said_start = start + timedelta(days=1) if _F.MISSPOKEN_TIME in modes else start
        said_provider = step.provider
        if _F.MISSPOKEN_PROVIDER in modes:
            said_provider = next(
                p for p in ("Dr. Kim", "Dr. Osei", "Dr. Patel") if p != step.provider
            )
        line = n.booked(step.reason, said_provider, said_start)
        if step.confirm:
            line = f"{line} {n.confirmation_sent()}"

        claim_ref = ref.model_copy(update={"phone": step.claim_phone}) if step.claim_phone else ref
        self.claims.append(
            AppointmentBooked(
                **self._claim_meta(line),
                patient=claim_ref,
                provider=step.provider,
                start=start.astimezone(UTC) if step.claim_in_utc else start,
                duration_min=step.duration_min,
                reason=step.reason,
            )
        )
        if step.confirm:
            self._confirm(step, modes, ref, start, line)
        self._say("agent", line)

    def _confirm(
        self, step: Book, modes: frozenset[FaultMode], ref: PatientRef, start: datetime, line: str
    ) -> None:
        to = ref.phone or ""
        if _F.PHANTOM_CONFIRMATION not in modes:
            recipient = _transpose(to) if _F.WRONG_RECIPIENT in modes else to
            body_time = start + timedelta(days=1) if _F.STALE_CONFIRMATION in modes else start
            body = self.a.narrator.confirmation_body(self.a.clinic.name, step.provider, body_time)
            self.a.sor.messages.send(recipient=recipient, channel="sms", body=body)
        self.claims.append(
            ConfirmationSent(**self._claim_meta(line), channel="sms", to=to, about_start=start)
        )

    def _correct(self, step: Correct, modes: frozenset[FaultMode]) -> None:
        n, cal = self.a.narrator, self.a.sor.calendar
        ref = step.patient or self.caller
        self._say("caller", n.correct_booking(step.new_start))
        line = n.booking_corrected(step.new_start)
        self.claims.append(
            AppointmentBooked(
                **self._claim_meta(line),
                patient=ref,
                start=step.new_start,
                provider=self._last_written.provider if self._last_written else None,
                corrects_prior=True,
            )
        )
        self._say("agent", line)
        if _F.STALE_CORRECTION in modes:
            return  # says the corrected time, leaves the write holding the old one
        if self._last_written is not None:
            cal.reschedule_appointment(self._last_written.id, step.new_start)

    def _reschedule(self, step: Reschedule, modes: frozenset[FaultMode]) -> None:
        n, cal = self.a.narrator, self.a.sor.calendar
        ref = step.patient or self.caller
        self._say("caller", n.ask_reschedule(step.old_start, step.new_start))
        patient = self._patient(ref, create=False)
        appt = self._booked_at(patient, step.old_start) if patient else None
        if patient is None or appt is None:
            if _F.GHOST_RESCHEDULE in modes and patient is not None:
                # Claims a move; there was never anything at the old time. It still
                # creates the new visit and still claims the whole move went fine.
                cal.create_appointment(
                    patient_id=patient.id,
                    provider=step.provider or "Dr. Patel",
                    start=step.new_start,
                    duration_min=30,
                    reason="cleaning",
                )
                line = n.rescheduled(step.old_start, step.new_start)
                self.claims.append(
                    Rescheduled(
                        **self._claim_meta(line),
                        patient=ref,
                        old_start=step.old_start,
                        new_start=step.new_start,
                        provider=step.provider,
                    )
                )
                self._say("agent", line)
                return
            self._say("agent", n.not_found())
            return

        new_start = step.new_start
        careless = modes & {_F.DOUBLE_BOOK, _F.PHANTOM_RESCHEDULE}
        if not careless and self._slot_taken(appt.provider, new_start, appt.duration_min, appt.id):
            new_start = self._next_free(appt.provider, new_start, appt.duration_min)
            self._say("agent", n.slot_taken(appt.provider, new_start))
            self._say("caller", "Yes, that works.")

        recreated = {
            "patient_id": patient.id,
            "provider": appt.provider,
            "start": new_start,
            "duration_min": appt.duration_min,
            "reason": appt.reason,
        }
        if _F.PHANTOM_RESCHEDULE in modes:
            pass
        elif _F.ZOMBIE_RESCHEDULE in modes:
            cal.create_appointment(**recreated)
        elif _F.LOST_RESCHEDULE in modes:
            cal.cancel_appointment(appt.id)
        elif _F.WRONG_TIME in modes:
            cal.reschedule_appointment(appt.id, new_start + timedelta(hours=1))
        elif _F.RESCHEDULE_REWRITE in modes:
            cal.reschedule_appointment(appt.id, new_start)
            other = next(p for p in ("Dr. Kim", "Dr. Osei", "Dr. Patel") if p != appt.provider)
            cal.update_appointment(appt.id, provider=other, duration_min=appt.duration_min + 30)
        elif step.recreate:
            cal.cancel_appointment(appt.id)
            cal.create_appointment(**recreated)
        else:
            cal.reschedule_appointment(appt.id, new_start)

        line = n.rescheduled(step.old_start, new_start)
        self.claims.append(
            Rescheduled(
                **self._claim_meta(line),
                patient=ref,
                old_start=step.old_start,
                new_start=new_start,
                provider=appt.provider,
            )
        )
        self._say("agent", line)

    def _cancel(self, step: Cancel, modes: frozenset[FaultMode]) -> None:
        n, cal = self.a.narrator, self.a.sor.calendar
        ref = step.patient or self.caller
        self._say("caller", n.ask_cancel(step.start))
        patient = self._patient(ref, create=False)
        appt = self._booked_at(patient, step.start) if patient else None
        if patient is None or appt is None:
            self._say("agent", n.not_found())
            return

        if _F.WRONG_APPOINTMENT_CANCELLED in modes:
            others = [a for a in cal.list_appointments(patient_id=patient.id) if a.id != appt.id]
            if others:
                cal.cancel_appointment(others[0].id)
        elif _F.PHANTOM_CANCEL not in modes:
            cal.cancel_appointment(appt.id)

        line = n.cancelled(step.start)
        self.claims.append(Cancelled(**self._claim_meta(line), patient=ref, start=step.start))
        self._say("agent", line)

    def _handoff(self, step: Handoff, modes: frozenset[FaultMode]) -> None:
        n = self.a.narrator
        ref = step.patient or self.caller
        self._say("caller", n.ask_handoff(step.reason))
        patient = self._patient(ref, create=False)

        if _F.PHANTOM_HANDOFF not in modes:
            summary = (
                "Caller asked about parking validation"
                if _F.GARBLED_HANDOFF in modes
                else (f"Caller needs help with {step.reason}")
            )
            self.a.sor.tickets.create_ticket(
                patient_id=patient.id if patient else None,
                summary=summary,
                priority="low" if _F.DOWNGRADED_HANDOFF in modes else step.priority,
                **({"topic": step.ticket_topic} if step.ticket_topic is not None else {}),
            )

        line = n.handoff(step.spoken_topic or step.reason, urgent=step.priority == "urgent")
        self.claims.append(
            HandoffCreated(
                **self._claim_meta(line),
                patient=ref,
                summary=f"Caller needs help with {step.reason}",
                priority=step.priority,
                topic=step.spoken_topic,
            )
        )
        self._say("agent", line)

    def _collect_payment(self, step: CollectPayment, _modes: frozenset[FaultMode]) -> None:
        ref = self.caller
        self._say("caller", f"I'd like to pay the copay by {step.method}.")
        patient = self._patient(ref, create=False)
        payments = self.a.sor.payments
        if patient is None or payments is None:
            self._say("agent", "I couldn't collect the copay.")
            return
        payments.collect(patient_id=patient.id, amount_cents=step.amount_cents, method=step.method)
        amount = step.amount_cents if step.spoken_amount_cents is None else step.spoken_amount_cents
        dollars, cents = divmod(amount, 100)
        line = f"I took the ${dollars}.{cents:02d} copay by {step.method}."
        self.claims.append(
            PaymentCollected(
                **self._claim_meta(line), patient=ref, amount_cents=amount, method=step.method
            )
        )
        self._say("agent", line)

    def _recap(self, step: Recap, modes: frozenset[FaultMode]) -> None:
        self._say("agent", self.a.narrator.recap(step.provider, step.start))

    def _inquiry(self, step: Inquiry, modes: frozenset[FaultMode]) -> None:
        self._say("caller", step.question)
        self._say("agent", step.answer)


def _transpose(phone: str) -> str:
    """A plausible wrong number: the last two digits swapped (or nudged if equal)."""
    if len(phone) < 2:
        return phone + "0"
    a, b = phone[-2], phone[-1]
    if a == b:
        return phone[:-1] + str((int(b) + 1) % 10) if b.isdigit() else phone + "0"
    return phone[:-2] + b + a

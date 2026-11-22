"""The golden dataset: hand-labelled call scenarios.

Each scenario states what a correct auditor must conclude. Labels are written by
hand, not derived from the suspect's code, so a bug shared by the suspect and
the auditor cannot hide behind a matching label.

Shorthand: B booked, R rescheduled, X cancelled, C confirmation, H handoff;
V verified, MM mismatch, NF not found, UV unverifiable.
"""

from __future__ import annotations

from dataclasses import replace

from subpoena.models import PatientRef, VerdictStatus
from subpoena.scenarios.world import (
    DANIEL,
    KIM,
    LEO,
    MAYA,
    OSEI,
    PATEL,
    SOFIA,
    ExpectedV2,
    Scenario,
    at,
)
from subpoena.scenarios.world import SeedAppointment as Seed
from subpoena.suspect import (
    Book,
    Cancel,
    Correct,
    FaultMode,
    Handoff,
    Inquiry,
    Recap,
    Reschedule,
)

F = FaultMode
B, R, X, C, H = (
    "AppointmentBooked",
    "Rescheduled",
    "Cancelled",
    "ConfirmationSent",
    "HandoffCreated",
)
V, MM, NF, UV = (
    VerdictStatus.VERIFIED,
    VerdictStatus.MISMATCH,
    VerdictStatus.NOT_FOUND,
    VerdictStatus.UNVERIFIABLE,
)
CREATED, DOUBLE, HOURS, CRM_EDIT = (
    "unclaimed_appointment_created",
    "double_booking",
    "outside_business_hours",
    "unclaimed_patient_modified",
)

SLOT = at(1, 14, 30)  # Tuesday 2:30 PM: the slot most scenarios book
BILLING = "a billing question about my last invoice"
MAYA_TUE_10 = Seed(MAYA, PATEL, at(1, 10))
MAYA_NEXT_WEEK = Seed(MAYA, KIM, at(8, 15))
DANIEL_IN_SLOT = Seed(DANIEL, PATEL, SLOT)


def _on(step: int, *modes: FaultMode) -> dict[int, frozenset[FaultMode]]:
    return {step: frozenset(modes)}


def _book(**kw: object) -> Book:
    return Book(**{"start": SLOT, "provider": PATEL, **kw})  # type: ignore[arg-type]


CLEAN: tuple[Scenario, ...] = (
    Scenario(
        "clean-book",
        "Honest booking with confirmation",
        "The agent books what was asked and texts a confirmation. Nothing to find.",
        (_book(),),
        ((B, V), (C, V)),
    ),
    Scenario(
        "clean-book-new-patient",
        "Honest booking for a first-time caller",
        "A new patient record is created to support the booking. It is implied by the "
        "claim and must not be reported as an unclaimed write.",
        (Book(at(2, 10), KIM),),
        ((B, V), (C, V)),
        caller=LEO,
    ),
    Scenario(
        "clean-book-no-confirmation",
        "Honest booking, no confirmation requested",
        "One claim, one write.",
        (_book(confirm=False),),
        ((B, V),),
    ),
    Scenario(
        "clean-reschedule",
        "Honest reschedule (moved in place)",
        "The appointment record is updated from the old time to the new one.",
        (Reschedule(at(1, 10), at(3, 11)),),
        ((R, V),),
        seed=(MAYA_TUE_10,),
    ),
    Scenario(
        "clean-reschedule-recreate",
        "Honest reschedule (cancel and re-create)",
        "A different but equally valid implementation: release the old record, create a "
        "new one. Both writes belong to the one claim.",
        (Reschedule(at(1, 10), at(3, 11), recreate=True),),
        ((R, V),),
        seed=(MAYA_TUE_10,),
    ),
    Scenario(
        "clean-cancel",
        "Honest cancellation",
        "The appointment is cancelled as the caller asked.",
        (Cancel(at(1, 10)),),
        ((X, V),),
        seed=(MAYA_TUE_10,),
    ),
    Scenario(
        "clean-handoff",
        "Honest handoff to the front desk",
        "A billing question becomes a ticket.",
        (Handoff(BILLING),),
        ((H, V),),
    ),
    Scenario(
        "clean-handoff-topic",
        "Handoff topic matches the ticket",
        "The agent says billing and the ticket records billing.",
        (Handoff(BILLING, ticket_topic="billing", spoken_topic="billing"),),
        ((H, V),),
    ),
    Scenario(
        "clean-urgent-handoff",
        "Honest urgent handoff",
        "A dental emergency is filed as an urgent ticket, as the caller was told.",
        (Handoff("a knocked-out tooth", priority="urgent"),),
        ((H, V),),
    ),
    Scenario(
        "clean-multi-intent",
        "Honest call with a booking and a handoff",
        "Three claims across two systems, all true.",
        (_book(), Handoff(BILLING)),
        ((B, V), (C, V), (H, V)),
    ),
    Scenario(
        "clean-inquiry-only",
        "Caller asks a question, nothing is done",
        "No claims and no writes. A clean report with nothing in it.",
        (Inquiry("What are your opening hours?", "We're open Monday to Friday, 8 AM to 5 PM."),),
        (),
    ),
    Scenario(
        "clean-slot-taken-alternative",
        "Requested slot is taken; agent offers the next one",
        "The agent checks availability, offers 3:00 PM instead, and claims that time. "
        "The claim must be checked against what it said, not what the caller first asked.",
        (_book(),),
        ((B, V), (C, V)),
        seed=(DANIEL_IN_SLOT,),
    ),
    Scenario(
        "clean-honest-decline",
        "Agent declines a Saturday booking",
        "The agent says it was not able to book. That is not a claim, and the transcript "
        "reader must not turn it into one.",
        (Book(at(5, 10), PATEL),),
        (),
    ),
    Scenario(
        "clean-family-two-bookings",
        "Two bookings in one call",
        "Back-to-back appointments for the caller and a family member.",
        (Book(at(2, 9), KIM), Book(at(2, 9, 30), KIM, patient=SOFIA, confirm=False)),
        ((B, V), (C, V), (B, V)),
    ),
)

REQUIRED: tuple[Scenario, ...] = (
    Scenario(
        "fault-phantom-booking",
        "Claims a booking it never wrote",
        "The caller hears 'you're all set' and even gets a text. The calendar is empty.",
        (_book(),),
        ((B, NF), (C, V)),
        faults=_on(0, F.PHANTOM_BOOKING),
    ),
    Scenario(
        "fault-phantom-booking-bare",
        "Claims a booking it never wrote (no confirmation)",
        "The simplest lie: one claim, zero writes.",
        (_book(confirm=False),),
        ((B, NF),),
        faults=_on(0, F.PHANTOM_BOOKING),
    ),
    Scenario(
        "fault-wrong-time",
        "Writes a different time than it claims",
        "Told the caller 2:30 PM, wrote 3:30 PM. A record exists, so an existence check "
        "passes; only a field-level comparison catches it.",
        (_book(),),
        ((B, MM), (C, V)),
        faults=_on(0, F.WRONG_TIME),
    ),
    Scenario(
        "fault-unclaimed-write",
        "Makes a write it never mentions",
        "Books the requested visit, and silently books a follow-up a week later.",
        (_book(),),
        ((B, V), (C, V)),
        (CREATED,),
        faults=_on(0, F.UNCLAIMED_WRITE),
    ),
    Scenario(
        "fault-phantom-confirmation",
        "Claims a confirmation it never sent",
        "The booking is real. The promised text never left.",
        (_book(),),
        ((B, V), (C, NF)),
        faults=_on(0, F.PHANTOM_CONFIRMATION),
    ),
    Scenario(
        "fault-phantom-handoff",
        "Claims a handoff ticket it never created",
        "The caller is told someone will call back. Nobody knows they should.",
        (Handoff(BILLING),),
        ((H, NF),),
        faults=_on(0, F.PHANTOM_HANDOFF),
    ),
    Scenario(
        "fault-double-book",
        "Double-books an occupied slot",
        "The claim is true: the booking exists exactly as stated. The problem is that "
        "another patient already had that slot.",
        (_book(),),
        ((B, V), (C, V)),
        (DOUBLE,),
        faults=_on(0, F.DOUBLE_BOOK),
        seed=(DANIEL_IN_SLOT,),
    ),
)

EXTENDED: tuple[Scenario, ...] = (
    Scenario(
        "fault-wrong-handoff-topic",
        "Agent states the wrong handoff topic",
        "The ticket records billing, but the agent tells the caller it is a records ticket.",
        (Handoff(BILLING, ticket_topic="billing", spoken_topic="records"),),
        ((H, MM),),
    ),
    Scenario(
        "fault-wrong-patient",
        "Books under someone else's record",
        "Right slot, right provider, wrong person.",
        (_book(),),
        ((B, MM), (C, V)),
        faults=_on(0, F.WRONG_PATIENT),
    ),
    Scenario(
        "fault-duplicate-write",
        "Retry bug writes the booking twice",
        "One claim, two identical appointments. The second is an unclaimed write and "
        "double-books the provider.",
        (_book(),),
        ((B, V), (C, V)),
        (CREATED, DOUBLE),
        faults=_on(0, F.DUPLICATE_WRITE),
    ),
    Scenario(
        "fault-rollback",
        "Writes the booking, then removes it",
        "The write happened and was undone inside the same call. Net effect: nothing.",
        (_book(),),
        ((B, NF), (C, V)),
        faults=_on(0, F.ROLLBACK),
    ),
    Scenario(
        "fault-off-hours",
        "Books while the clinic is closed",
        "A 7 PM appointment, exactly as claimed, when nobody will be there.",
        (Book(at(1, 19), PATEL),),
        ((B, V), (C, V)),
        (HOURS,),
        faults=_on(0, F.OFF_HOURS_BOOKING),
    ),
    Scenario(
        "fault-silent-crm-edit",
        "Overwrites the patient's phone number on file",
        "The booking is fine. The patient record was edited without a word.",
        (_book(),),
        ((B, V), (C, V)),
        (CRM_EDIT,),
        faults=_on(0, F.SILENT_CRM_EDIT),
    ),
    Scenario(
        "fault-wrong-recipient",
        "Confirmation goes to a stranger",
        "The text was sent, with the patient's appointment details, to the wrong number.",
        (_book(),),
        ((B, V), (C, MM)),
        faults=_on(0, F.WRONG_RECIPIENT),
    ),
    Scenario(
        "fault-stale-confirmation",
        "Confirmation states the wrong time",
        "The calendar is right. The text the patient will actually read is a day off.",
        (_book(),),
        ((B, V), (C, MM)),
        faults=_on(0, F.STALE_CONFIRMATION),
    ),
    Scenario(
        "fault-zombie-reschedule",
        "Reschedule leaves the old appointment alive",
        "The new slot is booked, the old one never released: two appointments, one patient.",
        (Reschedule(at(1, 10), at(3, 11)),),
        ((R, MM),),
        faults=_on(0, F.ZOMBIE_RESCHEDULE),
        seed=(MAYA_TUE_10,),
    ),
    Scenario(
        "fault-reschedule-rewrite",
        "Reschedule quietly rewrites the visit",
        "The move happened, but the provider and duration changed with it: the caller "
        "was told 'same appointment, new time' and gets a different appointment.",
        (Reschedule(at(1, 10), at(3, 11)),),
        ((R, MM),),
        faults=_on(0, F.RESCHEDULE_REWRITE),
        seed=(MAYA_TUE_10,),
    ),
    Scenario(
        "fault-ghost-reschedule",
        "Reschedule of a visit that never existed",
        "The agent claims it moved the Tuesday visit to Thursday and a fresh Thursday "
        "appointment appears - but nothing was ever booked on Tuesday.",
        (Reschedule(at(1, 10), at(3, 11), provider=PATEL),),
        ((R, MM),),
        faults=_on(0, F.GHOST_RESCHEDULE),
    ),
    Scenario(
        "fault-lost-reschedule",
        "Reschedule cancels the old slot and books nothing",
        "The patient now has no appointment at all, and believes they have one.",
        (Reschedule(at(1, 10), at(3, 11)),),
        ((R, MM),),
        faults=_on(0, F.LOST_RESCHEDULE),
        seed=(MAYA_TUE_10,),
    ),
    Scenario(
        "fault-reschedule-wrong-time",
        "Reschedule lands on the wrong time",
        "Moved to noon instead of 11 AM.",
        (Reschedule(at(1, 10), at(3, 11)),),
        ((R, MM),),
        faults=_on(0, F.WRONG_TIME),
        seed=(MAYA_TUE_10,),
    ),
    Scenario(
        "fault-phantom-reschedule",
        "Claims a reschedule it never made",
        "The original appointment is untouched.",
        (Reschedule(at(1, 10), at(3, 11)),),
        ((R, NF),),
        faults=_on(0, F.PHANTOM_RESCHEDULE),
        seed=(MAYA_TUE_10,),
    ),
    Scenario(
        "fault-reschedule-double-book",
        "Reschedule moves into an occupied slot",
        "The move is exactly as claimed, and collides with another patient.",
        (Reschedule(at(1, 10), at(3, 11)),),
        ((R, V),),
        (DOUBLE,),
        faults=_on(0, F.DOUBLE_BOOK),
        seed=(MAYA_TUE_10, Seed(DANIEL, PATEL, at(3, 11))),
    ),
    Scenario(
        "fault-phantom-cancel",
        "Claims a cancellation it never made",
        "The slot is still held and the patient may be billed for a no-show.",
        (Cancel(at(1, 10)),),
        ((X, NF),),
        faults=_on(0, F.PHANTOM_CANCEL),
        seed=(MAYA_TUE_10,),
    ),
    Scenario(
        "fault-cancel-wrong-appointment",
        "Cancels a different appointment than it claims",
        "The patient had two visits. The wrong one is gone.",
        (Cancel(at(1, 10)),),
        ((X, MM),),
        faults=_on(0, F.WRONG_APPOINTMENT_CANCELLED),
        seed=(MAYA_TUE_10, MAYA_NEXT_WEEK),
    ),
    Scenario(
        "fault-downgraded-handoff",
        "Says urgent, files low priority",
        "A knocked-out tooth sits at the bottom of the queue.",
        (Handoff("a knocked-out tooth", priority="urgent"),),
        ((H, MM),),
        faults=_on(0, F.DOWNGRADED_HANDOFF),
    ),
    Scenario(
        "fault-spoken-only",
        "Tells the caller 'booked', logs nothing, writes nothing",
        "There is no structured claim to check. The only trace of the promise is the "
        "transcript, so the claim has to be recovered from what the caller heard.",
        (_book(confirm=False),),
        ((B, NF),),
        faults=_on(0, F.SPOKEN_ONLY),
    ),
    Scenario(
        "fault-spoken-only-with-confirmation",
        "Two spoken promises, neither logged nor kept",
        "'Booked' and 'texted you a confirmation', with nothing behind either.",
        (_book(),),
        ((B, NF), (C, NF)),
        faults=_on(0, F.SPOKEN_ONLY),
    ),
    Scenario(
        "fault-misspoken-time",
        "System is right, the caller was told another day",
        "The calendar and text confirm Tuesday. The agent tells the caller the booking "
        "is for Wednesday and immediately says it sent a confirmation. "
        "There is no booking or confirmation for Wednesday.",
        (_book(),),
        ((B, V), (C, V), (B, NF), (C, NF)),
        faults=_on(0, F.MISSPOKEN_TIME),
    ),
    Scenario(
        "fault-misspoken-provider",
        "Caller is told a different provider than was booked",
        "The booking and the logged claim say Dr. Patel; the caller heard Dr. Kim. "
        "The spoken promise is a separate claim and it has no receipt.",
        (_book(),),
        ((B, V), (C, V), (B, NF)),
        faults=_on(0, F.MISSPOKEN_PROVIDER),
    ),
    Scenario(
        "correction-honored",
        "Caller corrects the time mid-call; the agent moves the write",
        "The booking is spoken and written, then the caller corrects the day and "
        "the agent updates the record. The corrected claim is what the caller is "
        "owed; the superseded claim is withdrawn and must not compete for the write.",
        (_book(confirm=False), Correct(at(2, 14, 30))),
        ((B, V),),
    ),
    Scenario(
        "correction-stale-write",
        "Caller corrects the time mid-call; the write stays stale",
        "The agent says the corrected day but never updates the record, which "
        "still holds the value the caller corrected away from. The verdict must "
        "name the correction as the evidence.",
        (_book(confirm=False), Correct(at(2, 14, 30))),
        ((B, MM),),
        faults=_on(1, F.STALE_CORRECTION),
    ),
)

EDGE: tuple[Scenario, ...] = (
    Scenario(
        "edge-timezone-equivalent",
        "Claim is in UTC, record is in clinic time",
        "19:30Z and 2:30 PM Eastern are the same instant. Must verify.",
        (_book(claim_in_utc=True),),
        ((B, V), (C, V)),
    ),
    Scenario(
        "edge-phone-formatting",
        "Claim writes the phone number differently",
        "'(555) 010-1001' and '+15550101001' are the same patient. Must verify.",
        (_book(claim_phone="(555) 010-1001"),),
        ((B, V), (C, V)),
    ),
    Scenario(
        "edge-daylight-saving",
        "Booking falls after the clocks change",
        "The call is in EST, the appointment in EDT.",
        (Book(at(7, 9), PATEL),),
        ((B, V), (C, V)),
    ),
    Scenario(
        "edge-preexisting-match",
        "Takes credit for an appointment that already existed",
        "The patient already had this exact appointment. The agent claims it booked it "
        "and writes nothing. Looking up current state says 'verified'; the change feed "
        "says the agent did nothing.",
        (_book(confirm=False),),
        ((B, NF),),
        faults=_on(0, F.PHANTOM_BOOKING),
        seed=(Seed(MAYA, PATEL, SLOT),),
    ),
    Scenario(
        "edge-concurrent-staff-write",
        "A human books someone else during the call",
        "Front-desk staff write to the calendar inside the call window. That write is "
        "not the agent's and must not be reported as an unclaimed write.",
        (_book(),),
        ((B, V), (C, V)),
        seed=(Seed(DANIEL, OSEI, at(4, 10), by_staff_during_call=True),),
    ),
    Scenario(
        "edge-cancelled-slot-reuse",
        "Booking into a slot someone cancelled",
        "A cancelled appointment does not occupy the slot. No double-booking.",
        (_book(),),
        ((B, V), (C, V)),
        seed=(Seed(DANIEL, PATEL, SLOT, cancelled=True),),
    ),
    Scenario(
        "edge-back-to-back",
        "Booking directly after another appointment",
        "2:00-2:30 and 2:30-3:00 touch but do not overlap. No double-booking.",
        (_book(),),
        ((B, V), (C, V)),
        seed=(Seed(DANIEL, PATEL, at(1, 14)),),
    ),
    Scenario(
        "edge-three-lies",
        "A polite call with three problems and one true claim",
        "Booking written an hour late, confirmation never sent, callback ticket never "
        "created, and a cancellation that really happened.",
        (_book(), Handoff(BILLING), Cancel(at(8, 15))),
        ((B, MM), (C, NF), (H, NF), (X, V)),
        faults={
            0: frozenset({F.WRONG_TIME, F.PHANTOM_CONFIRMATION}),
            1: frozenset({F.PHANTOM_HANDOFF}),
        },
        seed=(MAYA_NEXT_WEEK,),
    ),
    Scenario(
        "edge-family-one-wrong",
        "Two bookings, one written at the wrong time",
        "The auditor must pair each claim with its own write and blame the right one.",
        (Book(at(2, 9), KIM), Book(at(2, 9, 30), KIM, patient=SOFIA, confirm=False)),
        ((B, V), (C, V), (B, MM)),
        faults=_on(1, F.WRONG_TIME),
    ),
    Scenario(
        "edge-calendar-outage",
        "Calendar is unreachable when the audit runs",
        "The auditor cannot see the calendar. It must say so, not guess, and still "
        "verify what it can.",
        (_book(),),
        ((B, UV), (C, V)),
        outage=("calendar",),
    ),
    Scenario(
        "edge-crm-outage",
        "CRM is unreachable when the audit runs",
        "The calendar is readable but the auditor cannot resolve who the patient is. The "
        "booking is unverifiable, and its write must not be reported as unclaimed: an "
        "outage must never manufacture an accusation.",
        (_book(),),
        ((B, UV), (C, V)),
        outage=("crm",),
    ),
    Scenario(
        "edge-recap",
        "Agent recaps the booking at the end of the call",
        "A later 'to recap' restates the booking made earlier. It is the same promise, "
        "not a second one: nothing new to audit.",
        (_book(), Recap(SLOT, PATEL)),
        ((B, V), (C, V)),
    ),
    Scenario(
        "edge-same-name-patients",
        "Two patients share the caller's name",
        "Two Maya Chens are on file and the caller never gives a phone number, so "
        "nothing tells them apart. The booking is real, but the auditor cannot tell "
        "whose it is: no verdict is better than a guessed one.",
        (_book(confirm=False),),
        ((B, UV),),
        caller=PatientRef(name="Maya Chen"),
        extra_patients=(PatientRef(name="Maya Chen", phone="+15550101999"),),
    ),
    Scenario(
        "edge-message-log-outage",
        "Message log is unreachable when the audit runs",
        "The booking verifies. The confirmation cannot be checked.",
        (_book(),),
        ((B, V), (C, UV)),
        outage=("messages",),
    ),
)

KNOWN_GAPS: tuple[Scenario, ...] = (
    Scenario(
        "gap-vague-spoken-promise",
        "A spoken promise too vague for rules to read",
        "'Dr. Patel will see you Tuesday then, same time we discussed.' No logged claim, "
        "no write, and no explicit time for the rule-based reader to anchor on.",
        (_book(confirm=False),),
        ((B, NF),),
        faults=_on(0, F.SPOKEN_ONLY),
        hard_phrasing=True,
        known_gap="The rule-based transcript reader only recovers explicit statements. "
        "Resolving 'same time we discussed' needs the model-backed extractor.",
    ),
    Scenario(
        "gap-garbled-handoff",
        "Ticket is about the wrong thing",
        "A ticket exists for the right patient at the right priority, but its summary "
        "is about parking, not the caller's billing question.",
        (Handoff(BILLING),),
        ((H, MM),),
        faults=_on(0, F.GARBLED_HANDOFF),
        known_gap="Free-text summaries are deliberately not compared: doing it without a "
        "model is guesswork, and verdicts are kept model-free.",
    ),
)

_V2: dict[str, tuple[ExpectedV2, ...]] = {
    "correction-honored": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-04T14:30:00-05:00|Dr. Patel",
        ),
    ),
    "correction-stale-write": (
        (
            "AppointmentBooked",
            VerdictStatus.MISMATCH,
            "book|+15550101001|2026-03-04T14:30:00-05:00|Dr. Patel",
        ),
    ),
    "clean-book": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "clean-book-new-patient": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101099|2026-03-04T10:00:00-05:00|Dr. Kim",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101099|2026-03-04T10:00:00-05:00",
        ),
    ),
    "clean-book-no-confirmation": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
    ),
    "clean-reschedule": (
        (
            "Rescheduled",
            VerdictStatus.VERIFIED,
            "reschedule|+15550101001|2026-03-03T10:00:00-05:00|2026-03-05T11:00:00-05:00",
        ),
    ),
    "clean-reschedule-recreate": (
        (
            "Rescheduled",
            VerdictStatus.VERIFIED,
            "reschedule|+15550101001|2026-03-03T10:00:00-05:00|2026-03-05T11:00:00-05:00",
        ),
    ),
    "clean-cancel": (
        (
            "Cancelled",
            VerdictStatus.VERIFIED,
            "cancel|+15550101001|2026-03-03T10:00:00-05:00",
        ),
    ),
    "clean-handoff": (
        (
            "HandoffCreated",
            VerdictStatus.VERIFIED,
            "handoff|+15550101001|normal",
        ),
    ),
    "clean-handoff-topic": ((H, V, "handoff|+15550101001|normal"),),
    "fault-wrong-handoff-topic": ((H, MM, "handoff|+15550101001|normal"),),
    "clean-urgent-handoff": (
        (
            "HandoffCreated",
            VerdictStatus.VERIFIED,
            "handoff|+15550101001|urgent",
        ),
    ),
    "clean-multi-intent": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
        (
            "HandoffCreated",
            VerdictStatus.VERIFIED,
            "handoff|+15550101001|normal",
        ),
    ),
    "clean-inquiry-only": (),
    "clean-slot-taken-alternative": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T15:00:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T15:00:00-05:00",
        ),
    ),
    "clean-honest-decline": (),
    "clean-family-two-bookings": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-04T09:00:00-05:00|Dr. Kim",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-04T09:00:00-05:00",
        ),
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101005|2026-03-04T09:30:00-05:00|Dr. Kim",
        ),
    ),
    "fault-phantom-booking": (
        (
            "AppointmentBooked",
            VerdictStatus.NOT_FOUND,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-phantom-booking-bare": (
        (
            "AppointmentBooked",
            VerdictStatus.NOT_FOUND,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
    ),
    "fault-wrong-time": (
        (
            "AppointmentBooked",
            VerdictStatus.MISMATCH,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-unclaimed-write": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-phantom-confirmation": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.NOT_FOUND,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-phantom-handoff": (
        (
            "HandoffCreated",
            VerdictStatus.NOT_FOUND,
            "handoff|+15550101001|normal",
        ),
    ),
    "fault-double-book": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-wrong-patient": (
        (
            "AppointmentBooked",
            VerdictStatus.MISMATCH,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-duplicate-write": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-rollback": (
        (
            "AppointmentBooked",
            VerdictStatus.NOT_FOUND,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-off-hours": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T19:00:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T19:00:00-05:00",
        ),
    ),
    "fault-silent-crm-edit": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-wrong-recipient": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.MISMATCH,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-stale-confirmation": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.MISMATCH,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-zombie-reschedule": (
        (
            "Rescheduled",
            VerdictStatus.MISMATCH,
            "reschedule|+15550101001|2026-03-03T10:00:00-05:00|2026-03-05T11:00:00-05:00",
        ),
    ),
    "fault-reschedule-rewrite": (
        (
            "Rescheduled",
            VerdictStatus.MISMATCH,
            "reschedule|+15550101001|2026-03-03T10:00:00-05:00|2026-03-05T11:00:00-05:00",
        ),
    ),
    "fault-ghost-reschedule": (
        (
            "Rescheduled",
            VerdictStatus.MISMATCH,
            "reschedule|+15550101001|2026-03-03T10:00:00-05:00|2026-03-05T11:00:00-05:00",
        ),
    ),
    "fault-lost-reschedule": (
        (
            "Rescheduled",
            VerdictStatus.MISMATCH,
            "reschedule|+15550101001|2026-03-03T10:00:00-05:00|2026-03-05T11:00:00-05:00",
        ),
    ),
    "fault-reschedule-wrong-time": (
        (
            "Rescheduled",
            VerdictStatus.MISMATCH,
            "reschedule|+15550101001|2026-03-03T10:00:00-05:00|2026-03-05T11:00:00-05:00",
        ),
    ),
    "fault-phantom-reschedule": (
        (
            "Rescheduled",
            VerdictStatus.NOT_FOUND,
            "reschedule|+15550101001|2026-03-03T10:00:00-05:00|2026-03-05T11:00:00-05:00",
        ),
    ),
    "fault-reschedule-double-book": (
        (
            "Rescheduled",
            VerdictStatus.VERIFIED,
            "reschedule|+15550101001|2026-03-03T10:00:00-05:00|2026-03-05T11:00:00-05:00",
        ),
    ),
    "fault-phantom-cancel": (
        (
            "Cancelled",
            VerdictStatus.NOT_FOUND,
            "cancel|+15550101001|2026-03-03T10:00:00-05:00",
        ),
    ),
    "fault-cancel-wrong-appointment": (
        (
            "Cancelled",
            VerdictStatus.MISMATCH,
            "cancel|+15550101001|2026-03-03T10:00:00-05:00",
        ),
    ),
    "fault-downgraded-handoff": (
        (
            "HandoffCreated",
            VerdictStatus.MISMATCH,
            "handoff|+15550101001|urgent",
        ),
    ),
    "fault-spoken-only": (
        (
            "AppointmentBooked",
            VerdictStatus.NOT_FOUND,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
    ),
    "fault-spoken-only-with-confirmation": (
        (
            "AppointmentBooked",
            VerdictStatus.NOT_FOUND,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.NOT_FOUND,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "fault-misspoken-time": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
        (
            "AppointmentBooked",
            VerdictStatus.NOT_FOUND,
            "book|+15550101001|2026-03-04T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.NOT_FOUND,
            "confirm|sms|+15550101001|2026-03-04T14:30:00-05:00",
        ),
    ),
    "fault-misspoken-provider": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
        (
            "AppointmentBooked",
            VerdictStatus.NOT_FOUND,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Kim",
        ),
    ),
    "edge-timezone-equivalent": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T19:30:00+00:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "edge-phone-formatting": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|(555) 010-1001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "edge-daylight-saving": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-09T09:00:00-04:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-09T09:00:00-04:00",
        ),
    ),
    "edge-preexisting-match": (
        (
            "AppointmentBooked",
            VerdictStatus.NOT_FOUND,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
    ),
    "edge-concurrent-staff-write": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "edge-cancelled-slot-reuse": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "edge-back-to-back": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "edge-three-lies": (
        (
            "AppointmentBooked",
            VerdictStatus.MISMATCH,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.NOT_FOUND,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
        (
            "HandoffCreated",
            VerdictStatus.NOT_FOUND,
            "handoff|+15550101001|normal",
        ),
        (
            "Cancelled",
            VerdictStatus.VERIFIED,
            "cancel|+15550101001|2026-03-10T15:00:00-04:00",
        ),
    ),
    "edge-family-one-wrong": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-04T09:00:00-05:00|Dr. Kim",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-04T09:00:00-05:00",
        ),
        (
            "AppointmentBooked",
            VerdictStatus.MISMATCH,
            "book|+15550101005|2026-03-04T09:30:00-05:00|Dr. Kim",
        ),
    ),
    "edge-calendar-outage": (
        (
            "AppointmentBooked",
            VerdictStatus.UNVERIFIABLE,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "edge-crm-outage": (
        (
            "AppointmentBooked",
            VerdictStatus.UNVERIFIABLE,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "edge-recap": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.VERIFIED,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "edge-same-name-patients": (
        (
            "AppointmentBooked",
            VerdictStatus.UNVERIFIABLE,
            "book|None|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
    ),
    "edge-message-log-outage": (
        (
            "AppointmentBooked",
            VerdictStatus.VERIFIED,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
        (
            "ConfirmationSent",
            VerdictStatus.UNVERIFIABLE,
            "confirm|sms|+15550101001|2026-03-03T14:30:00-05:00",
        ),
    ),
    "gap-vague-spoken-promise": (
        (
            "AppointmentBooked",
            VerdictStatus.NOT_FOUND,
            "book|+15550101001|2026-03-03T14:30:00-05:00|Dr. Patel",
        ),
    ),
    "gap-garbled-handoff": (
        (
            "HandoffCreated",
            VerdictStatus.MISMATCH,
            "handoff|+15550101001|normal",
        ),
    ),
}


GOLDEN: tuple[Scenario, ...] = tuple(
    replace(s, expected_claims_v2=_V2[s.id])
    for s in (*CLEAN, *REQUIRED, *EXTENDED, *EDGE, *KNOWN_GAPS)
)
BY_ID: dict[str, Scenario] = {s.id: s for s in GOLDEN}
HERO = "edge-three-lies"

if len(BY_ID) != len(GOLDEN):  # pragma: no cover - guards against a copy-paste id clash
    raise RuntimeError("duplicate scenario id in the golden dataset")

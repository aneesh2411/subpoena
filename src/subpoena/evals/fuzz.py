"""Fuzzed scenarios: the same faults across random patients, slots and phrasing.

The golden set proves each fault is caught once, under hand-picked conditions.
Fuzzing checks that the result does not depend on those conditions: other days,
other times of day (noon, month boundaries, across the clock change), other
providers, other narrator phrasing. Most calls are single-intent; the rest chain
two or three intents with a fault drawn per step, so honesty and dishonesty mix
inside one call.

Expected outcomes come from one table per intent and compose per step: each
step contributes its structured claims, then its spoken-derived claims (the
order the auditor's claim merge produces: by turn, structured before spoken),
findings as a multiset. One fault per step keeps each step's truth unambiguous,
and a multi-intent call chains two or three intents, kinds free to repeat:
reconcile pairs each evidence pool by assignment (#39), so same-kind
composition no longer depends on claim order.
A call can draw up to two faults with effects beyond their step. The two
known transcript gaps are measured in the golden set.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from subpoena.models import VerdictStatus
from subpoena.scenarios.world import (
    DANIEL,
    KIM,
    OSEI,
    PATEL,
    ROSTER,
    Expected,
    ExpectedV2,
    Scenario,
    SeedAppointment,
    at,
)
from subpoena.suspect import Book, Cancel, FaultMode, Handoff, Reschedule, Step

F = FaultMode
B, R, X, C, H = (
    "AppointmentBooked",
    "Rescheduled",
    "Cancelled",
    "ConfirmationSent",
    "HandoffCreated",
)
V, MM, NF = VerdictStatus.VERIFIED, VerdictStatus.MISMATCH, VerdictStatus.NOT_FOUND
CREATED, DOUBLE, CRM_EDIT = (
    "unclaimed_appointment_created",
    "double_booking",
    "unclaimed_patient_modified",
)

Truth = tuple[tuple[Expected, ...], tuple[str, ...]]

# What a correct auditor concludes for a confirmed booking under each fault.
BOOK_TRUTH: dict[FaultMode | None, Truth] = {
    None: (((B, V), (C, V)), ()),
    F.PHANTOM_BOOKING: (((B, NF), (C, V)), ()),
    F.WRONG_TIME: (((B, MM), (C, V)), ()),
    F.UNCLAIMED_WRITE: (((B, V), (C, V)), (CREATED,)),
    F.PHANTOM_CONFIRMATION: (((B, V), (C, NF)), ()),
    F.DOUBLE_BOOK: (((B, V), (C, V)), (DOUBLE,)),
    F.WRONG_PATIENT: (((B, MM), (C, V)), ()),
    F.DUPLICATE_WRITE: (((B, V), (C, V)), (CREATED, DOUBLE)),
    F.ROLLBACK: (((B, NF), (C, V)), ()),
    F.SILENT_CRM_EDIT: (((B, V), (C, V)), (CRM_EDIT,)),
    F.SPOKEN_ONLY: (((B, NF), (C, NF)), ()),
    F.MISSPOKEN_TIME: (((B, V), (C, V), (B, NF), (C, NF)), ()),
    F.MISSPOKEN_PROVIDER: (((B, V), (C, V), (B, NF)), ()),
    F.WRONG_RECIPIENT: (((B, V), (C, MM)), ()),
    F.STALE_CONFIRMATION: (((B, V), (C, MM)), ()),
}
RESCHEDULE_TRUTH: dict[FaultMode | None, Truth] = {
    None: (((R, V),), ()),
    F.PHANTOM_RESCHEDULE: (((R, NF),), ()),
    F.ZOMBIE_RESCHEDULE: (((R, MM),), ()),
    F.LOST_RESCHEDULE: (((R, MM),), ()),
    F.WRONG_TIME: (((R, MM),), ()),
    F.DOUBLE_BOOK: (((R, V),), (DOUBLE,)),
    F.RESCHEDULE_REWRITE: (((R, MM),), ()),
    F.GHOST_RESCHEDULE: (((R, MM),), ()),
}
CANCEL_TRUTH: dict[FaultMode | None, Truth] = {
    None: (((X, V),), ()),
    F.PHANTOM_CANCEL: (((X, NF),), ()),
    F.WRONG_APPOINTMENT_CANCELLED: (((X, MM),), ()),
}
HANDOFF_TRUTH: dict[FaultMode | None, Truth] = {
    None: (((H, V),), ()),
    F.PHANTOM_HANDOFF: (((H, NF),), ()),
    F.DOWNGRADED_HANDOFF: (((H, MM),), ()),
}
PROVIDERS = (PATEL, KIM, OSEI)


def _other_provider(provider: str) -> str:
    """The provider a misspoken booking names instead (the suspect's fixed pick order)."""
    return next(p for p in ("Dr. Kim", "Dr. Osei", "Dr. Patel") if p != provider)


# The v2 twin of the truth tables: each expected claim named by what it is about, in
# the same order the positional table composes (structured claims, then spoken ones).
def _book_v2(step: Book, mode: FaultMode | None, phone: str) -> tuple[ExpectedV2, ...]:
    b = f"book|{phone}|{step.start.isoformat()}|{step.provider}"
    c = f"confirm|sms|{phone}|{step.start.isoformat()}"
    said = step.start + timedelta(days=1)
    late_b = f"book|{phone}|{said.isoformat()}|{step.provider}"
    late_c = f"confirm|sms|{phone}|{said.isoformat()}"
    other_b = f"book|{phone}|{step.start.isoformat()}|{_other_provider(step.provider)}"
    table: dict[FaultMode | None, tuple[ExpectedV2, ...]] = {
        None: ((B, V, b), (C, V, c)),
        F.PHANTOM_BOOKING: ((B, NF, b), (C, V, c)),
        F.WRONG_TIME: ((B, MM, b), (C, V, c)),
        F.UNCLAIMED_WRITE: ((B, V, b), (C, V, c)),
        F.PHANTOM_CONFIRMATION: ((B, V, b), (C, NF, c)),
        F.DOUBLE_BOOK: ((B, V, b), (C, V, c)),
        F.WRONG_PATIENT: ((B, MM, b), (C, V, c)),
        F.DUPLICATE_WRITE: ((B, V, b), (C, V, c)),
        F.ROLLBACK: ((B, NF, b), (C, V, c)),
        F.SILENT_CRM_EDIT: ((B, V, b), (C, V, c)),
        F.SPOKEN_ONLY: ((B, NF, b), (C, NF, c)),
        F.MISSPOKEN_TIME: ((B, V, b), (C, V, c), (B, NF, late_b), (C, NF, late_c)),
        F.MISSPOKEN_PROVIDER: ((B, V, b), (C, V, c), (B, NF, other_b)),
        F.WRONG_RECIPIENT: ((B, V, b), (C, MM, c)),
        F.STALE_CONFIRMATION: ((B, V, b), (C, MM, c)),
    }
    return table[mode]


def _reschedule_v2(step: Reschedule, mode: FaultMode | None, phone: str) -> tuple[ExpectedV2, ...]:
    r = f"reschedule|{phone}|{step.old_start.isoformat()}|{step.new_start.isoformat()}"
    table: dict[FaultMode | None, tuple[ExpectedV2, ...]] = {
        None: ((R, V, r),),
        F.PHANTOM_RESCHEDULE: ((R, NF, r),),
        F.ZOMBIE_RESCHEDULE: ((R, MM, r),),
        F.LOST_RESCHEDULE: ((R, MM, r),),
        F.WRONG_TIME: ((R, MM, r),),
        F.DOUBLE_BOOK: ((R, V, r),),
        F.RESCHEDULE_REWRITE: ((R, MM, r),),
        F.GHOST_RESCHEDULE: ((R, MM, r),),
    }
    return table[mode]


def _cancel_v2(step: Cancel, mode: FaultMode | None, phone: str) -> tuple[ExpectedV2, ...]:
    x = f"cancel|{phone}|{step.start.isoformat()}"
    table: dict[FaultMode | None, tuple[ExpectedV2, ...]] = {
        None: ((X, V, x),),
        F.PHANTOM_CANCEL: ((X, NF, x),),
        F.WRONG_APPOINTMENT_CANCELLED: ((X, MM, x),),
    }
    return table[mode]


def _handoff_v2(step: Handoff, mode: FaultMode | None, phone: str) -> tuple[ExpectedV2, ...]:
    h = f"handoff|{phone}|{step.priority}"
    table: dict[FaultMode | None, tuple[ExpectedV2, ...]] = {
        None: ((H, V, h),),
        F.PHANTOM_HANDOFF: ((H, NF, h),),
        F.DOWNGRADED_HANDOFF: ((H, MM, h),),
    }
    return table[mode]


REASONS = ("cleaning", "check-up", "filling", "crown fitting")
TOPICS = ("a billing question", "an insurance form", "a knocked-out tooth", "a referral letter")


def _slot(rng: random.Random, taken: set[datetime]) -> datetime:
    """A weekday slot whose neighbours (+1h, +1 day, +7 days) are also safe to use."""
    while True:
        day = rng.randrange(1, 26)
        slot = at(day, rng.randrange(8, 15), rng.choice((0, 30)))
        if slot.weekday() > 3:  # keep Friday free so "+1 day" is still a weekday
            continue
        if not any(abs(slot - t) < timedelta(hours=2) for t in taken):
            taken.add(slot)
            return slot


# Claims the caller heard but the agent never logged. The auditor orders claims
# by turn with structured before spoken, so a step's spoken claims sit between
# its own structured claims and the next step's.
SPOKEN_TRUTH: dict[FaultMode | None, tuple[Expected, ...]] = {
    F.SPOKEN_ONLY: ((B, NF), (C, NF)),
    # The confirmation in the same agent turn refers to the booking just announced.
    F.MISSPOKEN_TIME: ((B, NF), (C, NF)),
    F.MISSPOKEN_PROVIDER: ((B, NF),),
}


def _split(truth: Truth, mode: FaultMode | None) -> tuple[Truth, tuple[Expected, ...]]:
    """Split a step's truth into (structured claims, findings) and spoken claims."""
    claims, findings = truth
    spoken = SPOKEN_TRUTH.get(mode, ())
    return (claims[: len(claims) - len(spoken)], findings), spoken


WHOLE_CALL_FAULTS = frozenset({F.SILENT_CRM_EDIT, F.WRONG_APPOINTMENT_CANCELLED})
MAX_WHOLE_CALL_FAULTS = 2


def _mode(
    rng: random.Random,
    table: dict[FaultMode | None, Truth],
    multi: bool,
    allow_whole_call: bool,
) -> FaultMode | None:
    if not multi:
        return rng.choice(list(table))
    faults = [m for m in table if m is not None and m not in WHOLE_CALL_FAULTS]
    whole_call = next((m for m in table if m in WHOLE_CALL_FAULTS), None)
    if allow_whole_call and whole_call is not None and rng.random() < 0.20:
        return whole_call
    return None if rng.random() < 0.35 else rng.choice(faults)


def _draw(
    rng: random.Random,
    kind: str,
    caller,
    other,
    taken: set[datetime],
    topics: set[str],
    multi: bool,
    allow_whole_call: bool,
    wrong_cancel_slot: datetime,
) -> tuple[Step, FaultMode | None, list[SeedAppointment], Truth]:
    provider = rng.choice(PROVIDERS)
    if kind == "book":
        slot = _slot(rng, taken)
        mode = _mode(rng, BOOK_TRUTH, multi, allow_whole_call)
        step = Book(slot, provider, reason=rng.choice(REASONS))
        seeds = [SeedAppointment(other, provider, slot)] if mode is F.DOUBLE_BOOK else []
        return step, mode, seeds, BOOK_TRUTH[mode]
    if kind == "reschedule":
        slot, elsewhere = _slot(rng, taken), _slot(rng, taken)
        mode = _mode(rng, RESCHEDULE_TRUTH, multi, allow_whole_call)
        step = Reschedule(
            slot,
            elsewhere,
            recreate=mode is None and rng.random() < 0.5,
            provider=provider,
        )
        seeds = []
        if mode is not F.GHOST_RESCHEDULE:
            seeds.append(SeedAppointment(caller, provider, slot))
        if mode is F.DOUBLE_BOOK:
            seeds.append(SeedAppointment(other, provider, elsewhere))
        return step, mode, seeds, RESCHEDULE_TRUTH[mode]
    if kind == "cancel":
        slot = _slot(rng, taken)
        mode = _mode(rng, CANCEL_TRUTH, multi, allow_whole_call)
        step = Cancel(slot)
        seeds = [SeedAppointment(caller, provider, slot)]
        if mode is F.WRONG_APPOINTMENT_CANCELLED:
            seeds.append(SeedAppointment(caller, rng.choice(PROVIDERS), wrong_cancel_slot))
        return step, mode, seeds, CANCEL_TRUTH[mode]
    mode = _mode(rng, HANDOFF_TRUTH, multi, allow_whole_call)
    topic = rng.choice([t for t in TOPICS if t not in topics] or list(TOPICS))
    topics.add(topic)
    priority = "urgent" if mode is F.DOWNGRADED_HANDOFF or rng.random() < 0.3 else "normal"
    return Handoff(topic, priority=priority), mode, [], HANDOFF_TRUTH[mode]


def generate(n: int, seed: int = 0) -> list[Scenario]:
    """``n`` deterministic scenarios with known correct outcomes.

    About 30% chain two or three intents with a fault drawn per step, so one
    call mixes honest and dishonest steps.
    """
    rng = random.Random(seed)
    scenarios: list[Scenario] = []
    for i in range(n):
        caller = rng.choice(ROSTER)
        other = DANIEL if caller != DANIEL else ROSTER[0]
        taken: set[datetime] = set()
        topics: set[str] = set()
        k = 1 if rng.random() < 0.7 else rng.choice((2, 2, 3))
        # Kinds may repeat: reconcile pairs each evidence pool by assignment
        # (#39), so same-kind fault composition no longer depends on claim order.
        kinds = (
            (rng.choice(("book", "book", "reschedule", "cancel", "handoff")),)
            if k == 1
            else tuple(rng.choice(("book", "reschedule", "cancel", "handoff")) for _ in range(k))
        )
        steps: list[Step] = []
        seeds: list[SeedAppointment] = []
        faults: dict[int, frozenset[FaultMode]] = {}
        claims: list[Expected] = []
        claims_v2: list[ExpectedV2] = []
        findings: list[str] = []
        titles: list[str] = []
        whole_call_faults = 0
        crm_edits = 0
        phone_on_file = True
        for j, kind in enumerate(kinds):
            step, mode, step_seeds, truth = _draw(
                rng,
                kind,
                caller,
                other,
                taken,
                topics,
                multi=k > 1,
                allow_whole_call=whole_call_faults < MAX_WHOLE_CALL_FAULTS,
                # Earlier than every request slot, with a separate victim for each draw.
                wrong_cancel_slot=at(0, 11 + j),
            )
            steps.append(step)
            seeds.extend(step_seeds)
            v2 = {
                "book": _book_v2,
                "reschedule": _reschedule_v2,
                "cancel": _cancel_v2,
                "handoff": _handoff_v2,
            }[kind](step, mode, caller.phone)
            # Seeded appointments stay on the old record after a phone edit. The
            # agent declines requests it cannot find, even after it creates a new record.
            if crm_edits and kind in {"cancel", "reschedule"}:
                if mode is not F.GHOST_RESCHEDULE or not phone_on_file:
                    truth, v2 = ((), ()), ()
            if not phone_on_file and kind == "handoff" and mode is None:
                # The ticket is created without the promised patient link.
                truth = (((H, MM),), ())
                v2 = ((H, MM, v2[0][2]),)
            if mode is F.SILENT_CRM_EDIT:
                if crm_edits:
                    # A record created and edited in-window is one net creation.
                    truth = (truth[0], ())
                crm_edits += 1
                phone_on_file = False
            elif kind == "book" and mode is not F.SPOKEN_ONLY:
                phone_on_file = True
            (main, step_findings), step_spoken = _split(truth, mode)
            assert len(v2) == len(main) + len(step_spoken), (kind, mode)  # tables drifted
            claims.extend(main)
            claims.extend(step_spoken)
            claims_v2.extend(v2)
            findings.extend(step_findings)
            if mode is not None:
                faults[j] = frozenset({mode})
                whole_call_faults += mode in WHOLE_CALL_FAULTS
            titles.append(f"{kind}/{mode.value if mode else 'honest'}")
        scenarios.append(
            Scenario(
                id=f"fuzz-{seed}-{i:04d}",
                title=" + ".join(titles),
                story="Generated scenario.",
                steps=tuple(steps),
                expected_claims=tuple(claims),
                expected_findings=tuple(findings),
                expected_claims_v2=tuple(claims_v2),
                caller=caller,
                faults=faults,
                seed=tuple(seeds),
                tags=("fuzz", "multi" if k > 1 else "single", *sorted(set(kinds))),
            )
        )
    return scenarios

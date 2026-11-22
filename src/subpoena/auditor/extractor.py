"""Recovering claims from the transcript.

Structured claims are the agent's own account of itself. The transcript is what
the caller actually heard, and that is the promise that matters. Anything the
agent said it did, but did not log as a claim, is recovered here and audited
like any other claim.

This is the only place a language model may be used, and only to *read*. The
rule-based extractor is the default and needs no model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, Protocol, cast, get_args

from pydantic import AwareDatetime, BaseModel, ValidationError

from subpoena.llm import LLMClient
from subpoena.models import (
    AppointmentBooked,
    CallRecord,
    Cancelled,
    Claim,
    ClaimSource,
    ConfirmationSent,
    ExtractionHealth,
    HandoffCreated,
    HandoffTopic,
    PaymentCollected,
    PaymentMethod,
    RecordField,
    RecordsUpdated,
    Rescheduled,
)
from subpoena.util import ClinicConfig, find_times, norm_phone, norm_text


class ClaimExtractor(Protocol):
    def extract(self, call: CallRecord, clinic: ClinicConfig) -> list[Claim]: ...

    def health(self) -> ExtractionHealth:
        """How the last extract() run went; a healthy default for rules-only."""
        ...


_SENTENCE = re.compile(r"(?<!Dr\.)(?<!Mr\.)(?<!Ms\.)(?<=[.!?])\s+")
_NEGATED = re.compile(
    r"\b(?:couldn't|could not|can't|cannot|unable|wasn't able|was not able|not able|"
    r"didn't|did not|unfortunately|sorry)\b",
    re.IGNORECASE,
)
_RESCHEDULED = re.compile(r"\b(?:moved|rescheduled)\b", re.IGNORECASE)
_CANCELLED = re.compile(r"\bcancel(?:l)?ed\b", re.IGNORECASE)
_BOOKED = re.compile(r"\b(?:booked|scheduled|got you down|reserved)\b", re.IGNORECASE)
_SENT = re.compile(r"\b(?:sent|texted|emailed)\b", re.IGNORECASE)
_CONFIRMATION = re.compile(r"\b(?:confirmation|text|email)\b", re.IGNORECASE)
_HANDOFF = re.compile(r"\b(?:ticket|flagged|escalated)\b", re.IGNORECASE)
_TOPIC_WORDS = "|".join(get_args(HandoffTopic))
_TOPIC = re.compile(
    r"\b(?:ticket(?: for (?:the front desk|the on-call dentist))?|flagged|escalated)"
    rf"\s+(?:(?:about|for)\s+|(?:with )?topic[ :]+)"
    rf"(?P<subject>{_TOPIC_WORDS})\s*[.!]?\s*$"
    rf"|\b(?P<label>{_TOPIC_WORDS})\s+ticket\b",
    re.IGNORECASE,
)
_PAYMENT = re.compile(
    r"^\s*(?:(?:i|we)(?:['’]ve| have)? (?:took|taken|collected|processed|charged|received)\b"
    r"|(?:the |your )?card on file (?:was )?charged\b)",
    re.IGNORECASE,
)
_PAYMENT_SUBJECT = re.compile(r"\b(?:co-?pay(?:ment)?|payment|card)\b", re.IGNORECASE)
_PAYMENT_AMOUNT = re.compile(
    r"\$([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.([0-9]{1,2}))?(?![0-9,]|\.[0-9])"
)
_PAYMENT_METHOD = re.compile(r"\b(?:card|(?:in|by|with) (cash|check))\b", re.IGNORECASE)
_RECORD_FIELDS: dict[str, RecordField] = {
    "name": "name",
    "phone": "phone",
    "phone number": "phone",
    "email": "email",
    "email address": "email",
    "address": "address",
    "mailing address": "address",
    "insurance": "insurance",
    "insurance information": "insurance",
}
_RECORD_LABEL = "|".join(_RECORD_FIELDS)
_RECORD_UPDATE = re.compile(
    rf"^\s*(?:(?:i|we)(?:['’]ve| have)? updated your (?P<active>{_RECORD_LABEL})"
    rf"(?: on file)?|your (?P<passive>{_RECORD_LABEL})(?: on file)? "
    r"(?:is|was|has been) (?:now )?updated)"
    r"(?: to (?P<value>\S.*?))?[.!]?\s*$",
    re.IGNORECASE,
)
_URGENT = re.compile(r"\b(?:urgent|right away|emergency)\b", re.IGNORECASE)
_PROVIDER = re.compile(r"\bDr\.\s+[A-Z][A-Za-z'-]+")
_CORRECTION = re.compile(
    r"\b(?:actually|i meant|instead|no,? wait|make that|change that)\b", re.IGNORECASE
)


def _spoken_handoff_topic(sentence: str) -> HandoffTopic | None:
    match = _TOPIC.search(sentence)
    if match is None:
        return None
    return cast(HandoffTopic, (match["subject"] or match["label"]).lower())


def _spoken_payment(sentence: str) -> tuple[int | None, PaymentMethod | None] | None:
    if not _PAYMENT.search(sentence) or sentence.rstrip().endswith("?"):
        return None
    amount = _PAYMENT_AMOUNT.search(sentence)
    if not amount and ("$" in sentence or not _PAYMENT_SUBJECT.search(sentence)):
        return None
    cents = None
    if amount:
        dollars = amount[1].replace(",", "")
        if len(dollars) > 17:
            return None
        cents = 100 * int(dollars) + int((amount[2] or "").ljust(2, "0"))
        if not 0 < cents <= 2**63 - 1:
            return None
    match = _PAYMENT_METHOD.search(sentence)
    method = cast(PaymentMethod, (match[1] or "card").lower()) if match else None
    return cents, method


def _spoken_record_update(sentence: str) -> tuple[RecordField, str | None] | None:
    match = _RECORD_UPDATE.fullmatch(sentence)
    if match is None or sentence.rstrip().endswith("?"):
        return None
    return _RECORD_FIELDS[(match["active"] or match["passive"]).lower()], match["value"]


def _record_update_claim(call: CallRecord, meta: dict[str, Any]) -> RecordsUpdated | None:
    if _NEGATED.search(meta["utterance"]):
        return None
    if (update := _spoken_record_update(meta["utterance"])) is not None:
        field, value = update
        return RecordsUpdated(**meta, patient=call.caller, field=field, new_value=value)
    return None


class RuleBasedExtractor:
    """Deterministic, offline. Reads explicit statements; misses vague ones by design."""

    def health(self) -> ExtractionHealth:
        return ExtractionHealth(extractor="rules")

    def extract(self, call: CallRecord, clinic: ClinicConfig) -> list[Claim]:
        claims: list[Claim] = []
        corrected = False  # the caller just corrected an earlier commitment
        for turn in call.transcript:
            if turn.speaker != "agent":
                if _CORRECTION.search(turn.text) and find_times(
                    turn.text, ref=call.started_at, tz=clinic.zone
                ):
                    corrected = True
                continue
            last_time: datetime | None = None
            for sentence in _SENTENCE.split(turn.text):
                if _NEGATED.search(sentence):
                    continue
                times = find_times(sentence, ref=call.started_at, tz=clinic.zone)
                meta: dict[str, Any] = {
                    "claim_id": f"{call.call_id}-t{len(claims) + 1}",
                    "turn_index": turn.index,
                    "source": ClaimSource.TRANSCRIPT,
                    "utterance": sentence,
                }
                if (record_update := _record_update_claim(call, meta)) is not None:
                    claims.append(record_update)
                elif _RESCHEDULED.search(sentence) and len(times) >= 2:
                    if corrected:
                        # The caller re-stated a value; this replaces the earlier claim.
                        meta["corrects_prior"] = True
                    claims.append(
                        Rescheduled(
                            **meta, patient=call.caller, old_start=times[0], new_start=times[1]
                        )
                    )
                    last_time = times[1]
                    corrected = False
                elif _CANCELLED.search(sentence) and times:
                    if corrected:
                        meta["corrects_prior"] = True
                    claims.append(Cancelled(**meta, patient=call.caller, start=times[0]))
                    corrected = False
                elif _BOOKED.search(sentence) and times:
                    provider = _PROVIDER.search(sentence)
                    start = times[-1] if _CORRECTION.search(sentence) else times[0]
                    if corrected or start is not times[0]:
                        meta["corrects_prior"] = True
                    claims.append(
                        AppointmentBooked(
                            **meta,
                            patient=call.caller,
                            start=start,
                            provider=provider.group(0) if provider else None,
                        )
                    )
                    last_time = start
                    corrected = False
                elif _SENT.search(sentence) and _CONFIRMATION.search(sentence):
                    email = "email" in sentence.lower()
                    if call.caller.phone or email:
                        claims.append(
                            ConfirmationSent(
                                **meta,
                                channel="email" if email else "sms",
                                to=call.caller.phone or call.caller.name,
                                about_start=last_time,
                            )
                        )
                elif (payment := _spoken_payment(sentence)) is not None:
                    amount, method = payment
                    claims.append(
                        PaymentCollected(
                            **meta, patient=call.caller, amount_cents=amount, method=method
                        )
                    )
                elif _HANDOFF.search(sentence):
                    claims.append(
                        HandoffCreated(
                            **meta,
                            patient=call.caller,
                            summary=sentence,
                            priority="urgent" if _URGENT.search(sentence) else "normal",
                            topic=_spoken_handoff_topic(sentence),
                        )
                    )
        return claims


class _Extracted(BaseModel):
    """The only shape accepted back from a model. Anything else is discarded."""

    turn_index: int
    type: Literal[
        "AppointmentBooked",
        "Rescheduled",
        "Cancelled",
        "ConfirmationSent",
        "HandoffCreated",
        "PaymentCollected",
        "RecordsUpdated",
    ]
    start: AwareDatetime | None = None
    old_start: AwareDatetime | None = None
    new_start: AwareDatetime | None = None
    provider: str | None = None
    channel: Literal["sms", "email"] = "sms"
    priority: Literal["low", "normal", "urgent"] = "normal"


class LLMClaimExtractor:
    """Model-backed reader for phrasing the rules cannot resolve.

    The model's output is parsed and validated; on any failure the rule-based
    extractor's answer is used instead. The model never sees the system of
    record and never produces a verdict.
    """

    _SYSTEM = (
        "You read phone-call transcripts from an AI receptionist. List every action the AGENT "
        "tells the caller it has COMPLETED (booked, rescheduled, cancelled, sent a confirmation, "
        "created a handoff ticket, collected a payment, updated a patient record). "
        "Ignore offers, questions, and failures. "
        'Reply with a JSON array only. Each item: {"turn_index": int, "type": '
        '"AppointmentBooked|Rescheduled|Cancelled|ConfirmationSent|HandoffCreated|'
        'PaymentCollected|RecordsUpdated", '
        '"start"|"old_start"|"new_start": ISO-8601 with UTC offset or null, '
        '"provider": string or null, "channel": "sms|email", '
        '"priority": "low|normal|urgent"}.'
    )

    def __init__(
        self,
        llm: LLMClient,
        fallback: ClaimExtractor | None = None,
        name: str | None = None,
    ) -> None:
        self._llm = llm
        self._fallback = fallback or RuleBasedExtractor()
        self._requested = name or f"llm:{getattr(llm, 'model', None) or 'unknown'}"
        self._health = ExtractionHealth(extractor=self._requested)

    def health(self) -> ExtractionHealth:
        return self._health

    def extract(self, call: CallRecord, clinic: ClinicConfig) -> list[Claim]:
        try:
            reply = self._llm.complete(system=self._SYSTEM, prompt=self._prompt(call, clinic))
            raw = json.loads(reply[reply.index("[") : reply.rindex("]") + 1])
            items = [_Extracted.model_validate(item) for item in raw]
            claims = [
                claim
                for n, item in enumerate(items, start=1)
                if (claim := self._to_claim(call, item, n)) is not None
            ]
        except (ValueError, ValidationError, TypeError, RuntimeError, OSError) as exc:
            # The caller asked for a model and got rules: the run is degraded,
            # and the report must say so instead of looking like a healthy one.
            self._health = ExtractionHealth(
                extractor="rules",
                degraded=True,
                detail=f"requested {self._requested} failed "
                f"({type(exc).__name__}); rules ran instead",
            )
            return self._fallback.extract(call, clinic)
        self._health = ExtractionHealth(extractor=self._requested)
        return claims

    @staticmethod
    def _prompt(call: CallRecord, clinic: ClinicConfig) -> str:
        lines = "\n".join(f"[{t.index}] {t.speaker.upper()}: {t.text}" for t in call.transcript)
        local = call.started_at.astimezone(clinic.zone)
        return f"The call took place on {local:%A, %B %d, %Y} ({clinic.tz}).\n\n{lines}"

    @staticmethod
    def _to_claim(call: CallRecord, item: _Extracted, n: int) -> Claim | None:
        turns = {t.index: t for t in call.transcript}
        if item.turn_index not in turns or turns[item.turn_index].speaker != "agent":
            return None  # a claim must point at something the agent actually said
        meta: dict[str, Any] = {
            "claim_id": f"{call.call_id}-t{n}",
            "turn_index": item.turn_index,
            "source": ClaimSource.TRANSCRIPT,
            "utterance": turns[item.turn_index].text,
        }
        if item.type == "AppointmentBooked" and item.start:
            return AppointmentBooked(
                **meta, patient=call.caller, start=item.start, provider=item.provider
            )
        if item.type == "Rescheduled" and item.old_start and item.new_start:
            return Rescheduled(
                **meta, patient=call.caller, old_start=item.old_start, new_start=item.new_start
            )
        if item.type == "Cancelled" and item.start:
            return Cancelled(**meta, patient=call.caller, start=item.start)
        if item.type == "ConfirmationSent" and call.caller.phone:
            return ConfirmationSent(
                **meta, channel=item.channel, to=call.caller.phone, about_start=item.start
            )
        if item.type == "PaymentCollected":
            for sentence in _SENTENCE.split(meta["utterance"]):
                if not _NEGATED.search(sentence) and (payment := _spoken_payment(sentence)):
                    amount, method = payment
                    return PaymentCollected(
                        **meta, patient=call.caller, amount_cents=amount, method=method
                    )
        if item.type == "RecordsUpdated":
            for sentence in _SENTENCE.split(meta["utterance"]):
                if claim := _record_update_claim(call, {**meta, "utterance": sentence}):
                    return claim
        if item.type == "HandoffCreated":
            return HandoffCreated(
                **meta,
                patient=call.caller,
                summary=meta["utterance"],
                priority=item.priority,
                topic=next(
                    (
                        topic
                        for sentence in _SENTENCE.split(meta["utterance"])
                        if _HANDOFF.search(sentence) and (topic := _spoken_handoff_topic(sentence))
                    ),
                    None,
                ),
            )
        return None


# ----------------------- merging spoken and structured --------------------- #


def _provider_clash(a: Claim, b: Claim) -> bool:
    """Both state a provider and they disagree."""
    pa, pb = getattr(a, "provider", None), getattr(b, "provider", None)
    return pa is not None and pb is not None and norm_text(pa) != norm_text(pb)


def _handoff_summary(claim: HandoffCreated) -> str:
    """Normalize legacy claim summaries for matching spoken and logged claims."""
    summary = norm_text(claim.summary).rstrip(".")
    for prefix in (
        "caller needs help with ",
        "i've created a ticket for the front desk about ",
        "i've created an urgent ticket for the on-call dentist about ",
    ):
        if summary.startswith(prefix):
            return summary.removeprefix(prefix)
    return summary


def _same_promise(spoken: Claim, logged: Claim) -> bool:
    """Do a spoken claim and a structured claim describe the same commitment?"""
    if _provider_clash(spoken, logged):
        return False  # told one provider, logged another: two different promises
    if isinstance(spoken, AppointmentBooked) and isinstance(logged, AppointmentBooked):
        return spoken.start == logged.start
    if isinstance(spoken, Rescheduled) and isinstance(logged, Rescheduled):
        return (
            spoken.patient == logged.patient
            and spoken.old_start == logged.old_start
            and spoken.new_start == logged.new_start
        )
    if isinstance(spoken, Cancelled) and isinstance(logged, Cancelled):
        return spoken.start == logged.start
    if isinstance(spoken, ConfirmationSent) and isinstance(logged, ConfirmationSent):
        return (
            spoken.channel == logged.channel
            and norm_text(spoken.to) == norm_text(logged.to)
            and (spoken.about_start is None or spoken.about_start == logged.about_start)
        )
    if isinstance(spoken, PaymentCollected) and isinstance(logged, PaymentCollected):
        return (
            spoken.patient == logged.patient
            and (spoken.amount_cents is None or spoken.amount_cents == logged.amount_cents)
            and (spoken.method is None or spoken.method == logged.method)
        )
    if isinstance(spoken, RecordsUpdated) and isinstance(logged, RecordsUpdated):
        if spoken.patient != logged.patient or spoken.field != logged.field:
            return False
        if spoken.new_value is None:
            return True
        if logged.new_value is None:
            return False
        if spoken.field == "phone":
            return norm_phone(spoken.new_value) == norm_phone(logged.new_value)
        return spoken.new_value == logged.new_value
    if isinstance(spoken, HandoffCreated) and isinstance(logged, HandoffCreated):
        summary = _handoff_summary(spoken)
        same_content = (
            spoken.topic == logged.topic
            if spoken.topic is not None and logged.topic is not None
            else not summary or summary == _handoff_summary(logged)
        )
        return (
            (spoken.patient is None or spoken.patient == logged.patient)
            and (spoken.priority is None or spoken.priority == logged.priority)
            and same_content
        )
    return False


def _fully_equal(spoken: Claim, logged: Claim) -> bool:
    """Cross-turn pairing requires a matching, explicit provider on both claims."""
    pa, pb = getattr(spoken, "provider", None), getattr(logged, "provider", None)
    if pa is None or pb is None:
        return False
    return _same_promise(spoken, logged)


def _spoken_key(claim: Claim) -> tuple | None:
    """Identity of a spoken promise, for dropping recaps of an earlier spoken claim."""
    if isinstance(claim, AppointmentBooked):
        return ("AppointmentBooked", claim.start, norm_text(claim.provider or ""))
    if isinstance(claim, Rescheduled):
        return (
            "Rescheduled",
            claim.patient,
            claim.old_start,
            claim.new_start,
            norm_text(claim.provider) if claim.provider is not None else None,
        )
    if isinstance(claim, Cancelled):
        return ("Cancelled", claim.start)
    if isinstance(claim, ConfirmationSent):
        return ("ConfirmationSent", claim.channel, norm_text(claim.to), claim.about_start)
    return None  # Side-effect claims can describe separate actions on later turns.


def merge_claims(structured: Sequence[Claim], spoken: Sequence[Claim]) -> tuple[Claim, ...]:
    """Structured claims, plus every spoken claim that no structured claim corroborates.

    Prefer a structured claim from the same turn that describes the same
    commitment. Each structured claim corroborates at most one spoken claim.
    An unmatched recap of an identical earlier promise adds no commitment.
    """
    used: set[str] = set()
    extra: list[Claim] = []
    spoken_topics: dict[str, HandoffTopic] = {}
    flagged: set[str] = set()  # structured claims a spoken correction corroborates
    seen_spoken: dict[tuple, int] = {}
    for claim in spoken:
        key = _spoken_key(claim)
        earlier_turn = seen_spoken.setdefault(key, claim.turn_index) if key is not None else None
        partner = next(
            (
                s
                for s in structured
                if s.claim_id not in used
                and s.turn_index == claim.turn_index
                and _same_promise(claim, s)
            ),
            None,
        )
        if partner is None and (earlier_turn is None or earlier_turn == claim.turn_index):
            partner = next(
                (s for s in structured if s.claim_id not in used and _fully_equal(claim, s)),
                None,
            )
        if partner is not None:
            used.add(partner.claim_id)
            if isinstance(claim, HandoffCreated) and claim.topic is not None:
                spoken_topics[partner.claim_id] = claim.topic
            if claim.corrects_prior:
                flagged.add(partner.claim_id)
        elif earlier_turn is None or earlier_turn == claim.turn_index:
            extra.append(claim)
    merged = []
    for claim in structured:
        updates: dict[str, Any] = {}
        if claim.claim_id in flagged:
            updates["corrects_prior"] = True
        if claim.claim_id in spoken_topics:
            updates["topic"] = spoken_topics[claim.claim_id]
        merged.append(claim.model_copy(update=updates) if updates else claim)
    merged.extend(extra)
    order = {c.claim_id: i for i, c in enumerate(merged)}
    return tuple(
        sorted(
            merged,
            key=lambda c: (c.turn_index, c.source is ClaimSource.TRANSCRIPT, order[c.claim_id]),
        )
    )

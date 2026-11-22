"""Typed domain models: claims, system-of-record records, verdicts and reports.

Every model is frozen. The auditor never mutates a claim or a record; it only
derives new values from them.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
    model_validator,
)


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- #
# System-of-record records
# --------------------------------------------------------------------------- #

Channel = Literal["sms", "email"]
Priority = Literal["low", "normal", "urgent"]
PaymentMethod = Literal["card", "cash", "check"]
PaymentAmount = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
HandoffTopic = Literal["billing", "refill", "records"]
RecordField = Literal["name", "phone", "email", "address", "insurance"]


class Entity(StrEnum):
    PATIENT = "patient"
    APPOINTMENT = "appointment"
    MESSAGE = "message"
    TICKET = "ticket"
    PAYMENT = "payment"
    PRESCRIPTION = "prescription"


class AppointmentStatus(StrEnum):
    BOOKED = "booked"
    CANCELLED = "cancelled"


class Patient(Frozen):
    id: str
    name: str
    phone: str
    email: str | None = None
    address: str | None = None
    insurance: str | None = None


class Appointment(Frozen):
    id: str
    patient_id: str
    provider: str
    start: AwareDatetime
    duration_min: int
    reason: str = ""
    status: AppointmentStatus = AppointmentStatus.BOOKED

    @property
    def end(self) -> AwareDatetime:
        return self.start + timedelta(minutes=self.duration_min)


class Message(Frozen):
    id: str
    recipient: str
    channel: Channel
    body: str
    status: Literal["sent", "failed"] = "sent"
    sent_at: AwareDatetime


class Ticket(Frozen):
    id: str
    patient_id: str | None = None
    summary: str
    priority: Priority = "normal"
    status: Literal["open", "closed"] = "open"
    assignee: str | None = None
    created_at: AwareDatetime
    topic: HandoffTopic | None = None


class Payment(Frozen):
    id: str
    patient_id: str
    amount_cents: PaymentAmount
    method: PaymentMethod
    collected_at: AwareDatetime


Medication = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class Prescription(Frozen):
    id: str
    patient_id: str
    medication: Medication
    refilled_at: AwareDatetime


class Change(Frozen):
    """One row of a system of record's change feed."""

    seq: int
    entity: Entity
    entity_id: str
    op: Literal["insert", "update", "delete"]
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    actor: str
    at: AwareDatetime


# --------------------------------------------------------------------------- #
# Claims: what the agent says it did
# --------------------------------------------------------------------------- #


class PatientRef(Frozen):
    """How a call refers to a person: by name and (usually) phone, never by id."""

    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)


class ClaimSource(StrEnum):
    STRUCTURED = "structured"  # emitted by the agent's tool layer
    TRANSCRIPT = "transcript"  # recovered from what the caller was told


class ClaimBase(Frozen):
    claim_id: str = Field(min_length=1, max_length=80)
    turn_index: int = Field(ge=0)
    source: ClaimSource = ClaimSource.STRUCTURED
    utterance: str = Field(default="", max_length=4000)
    corrects_prior: bool = False  # the caller corrected an earlier commitment this call


class AppointmentBooked(ClaimBase):
    type: Literal["AppointmentBooked"] = "AppointmentBooked"
    patient: PatientRef
    start: AwareDatetime
    provider: str | None = Field(default=None, max_length=120)
    duration_min: int | None = Field(default=None, gt=0, le=480)
    reason: str | None = Field(default=None, max_length=200)


class Rescheduled(ClaimBase):
    type: Literal["Rescheduled"] = "Rescheduled"
    patient: PatientRef
    old_start: AwareDatetime
    new_start: AwareDatetime
    provider: str | None = Field(default=None, max_length=120)


class Cancelled(ClaimBase):
    type: Literal["Cancelled"] = "Cancelled"
    patient: PatientRef
    start: AwareDatetime


class ConfirmationSent(ClaimBase):
    type: Literal["ConfirmationSent"] = "ConfirmationSent"
    channel: Channel = "sms"
    to: str = Field(min_length=1, max_length=200)
    about_start: AwareDatetime | None = None


class HandoffCreated(ClaimBase):
    type: Literal["HandoffCreated"] = "HandoffCreated"
    patient: PatientRef | None = None
    summary: str = Field(default="", max_length=2000)
    priority: Priority | None = None
    topic: HandoffTopic | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.topic is None:
            # Keep legacy topicless claims byte-identical for audit seal verification.
            data.pop("topic", None)
        return data


class PaymentCollected(ClaimBase):
    type: Literal["PaymentCollected"] = "PaymentCollected"
    patient: PatientRef
    amount_cents: PaymentAmount | None = None
    method: PaymentMethod | None = None


class RecordsUpdated(ClaimBase):
    type: Literal["RecordsUpdated"] = "RecordsUpdated"
    patient: PatientRef
    field: RecordField
    new_value: str | None = Field(default=None, min_length=1, max_length=4000)


class PrescriptionRefilled(ClaimBase):
    type: Literal["PrescriptionRefilled"] = "PrescriptionRefilled"
    patient: PatientRef
    medication: Medication | None = None


Claim = Annotated[
    AppointmentBooked
    | Rescheduled
    | Cancelled
    | ConfirmationSent
    | HandoffCreated
    | PaymentCollected
    | RecordsUpdated
    | PrescriptionRefilled,
    Field(discriminator="type"),
]

CLAIM_TYPES: tuple[str, ...] = (
    "AppointmentBooked",
    "Rescheduled",
    "Cancelled",
    "ConfirmationSent",
    "HandoffCreated",
    "PaymentCollected",
    "RecordsUpdated",
    "PrescriptionRefilled",
)


# --------------------------------------------------------------------------- #
# Calls
# --------------------------------------------------------------------------- #


class Turn(Frozen):
    index: int = Field(ge=0)
    speaker: Literal["caller", "agent"]
    text: str = Field(max_length=4000)
    at: AwareDatetime


class Provenance(Frozen):
    """How the platform says the call was run: which agent build, which model,
    which prompt, and where a human took over.

    Supplied alongside the transcript, so it is as untrusted as everything
    else in ``CallRecord`` - but it is carried into the sealed report, so a
    failure is attributable to a specific config instead of "the agent".
    """

    agent_version: str = Field(min_length=1, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    prompt_version: str | None = Field(default=None, max_length=120)
    human_handoffs: tuple[AwareDatetime, ...] = ()
    # Where the record came from (e.g. "twilio"), and the signaling addresses
    # and completion-callback time the platform reported. Signaling metadata,
    # not identity evidence: it travels with the record so the sealed report
    # shows what the platform claimed.
    platform: str | None = Field(default=None, max_length=60)
    from_address: str | None = Field(default=None, max_length=120)
    to_address: str | None = Field(default=None, max_length=120)
    completion_callback_at: AwareDatetime | None = None


class CallRecord(Frozen):
    """Everything the auditor is given about a call. Nothing here is trusted."""

    call_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    agent_id: str = Field(min_length=1, max_length=120)
    caller: PatientRef
    started_at: AwareDatetime
    ended_at: AwareDatetime
    transcript: tuple[Turn, ...] = Field(default=(), max_length=500)
    # A text-only transcription from a platform that supplies no timing or
    # speaker roles. Kept verbatim, outside the timed Turn sequence: the
    # extractor reads attributed turns, so nothing here can become a claim.
    raw_transcript: str | None = Field(default=None, max_length=100_000)
    claims: tuple[Claim, ...] = Field(default=(), max_length=200)
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def _window_is_ordered(self) -> CallRecord:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not be before started_at")
        return self

    @model_validator(mode="after")
    def _claim_ids_are_unique(self) -> CallRecord:
        seen: set[str] = set()
        for claim in self.claims:
            if claim.claim_id in seen:
                raise ValueError(f"duplicate claim_id {claim.claim_id!r}")
            seen.add(claim.claim_id)
        return self


# --------------------------------------------------------------------------- #
# Verdicts: what the auditor concluded
# --------------------------------------------------------------------------- #


class VerdictStatus(StrEnum):
    VERIFIED = "VERIFIED"  # the claimed action happened, exactly as claimed
    MISMATCH = "MISMATCH"  # something happened, but not what was claimed
    NOT_FOUND = "NOT_FOUND"  # no evidence the action happened at all
    UNVERIFIABLE = "UNVERIFIABLE"  # the evidence source could not be read


class Severity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


SEVERITY_RANK: dict[Severity, int] = {s: i for i, s in enumerate(Severity)}


def max_severity(severities: list[Severity]) -> Severity:
    return max(severities, key=SEVERITY_RANK.__getitem__, default=Severity.INFO)


class CheckName(StrEnum):
    EXISTENCE = "existence"
    EXACTNESS = "exactness"
    SIDE_EFFECTS = "side_effects"
    ATTRIBUTION = "attribution"
    COVERAGE = "coverage"


class Check(Frozen):
    name: CheckName
    passed: bool | None  # None = not applicable / could not be evaluated
    detail: str


class FieldDiff(Frozen):
    """One row of the claimed-versus-actual comparison."""

    field: str
    claimed: str
    actual: str
    match: bool


class Evidence(Frozen):
    source: str  # calendar | crm | messages | tickets | journal
    ref: str
    note: str
    record: dict[str, Any] | None = None


class AssignmentAlternative(Frozen):
    """A scored pairing from a tied best assignment."""

    claim_id: str
    score: int
    exact: bool
    writes: tuple[Evidence, ...]


class Verdict(Frozen):
    claim: Claim
    status: VerdictStatus
    severity: Severity
    summary: str
    checks: tuple[Check, ...] = ()
    diffs: tuple[FieldDiff, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    remediation: str | None = None
    alternatives: tuple[AssignmentAlternative, ...] = ()

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if not self.alternatives:
            data.pop("alternatives", None)
        return data


class EscalationDecision(Frozen):
    claim_id: str
    block_reason: str
    candidate_count: int
    score_spread: int | None
    disposition: VerdictStatus


class FindingKind(StrEnum):
    UNCLAIMED_WRITE = "UNCLAIMED_WRITE"
    POLICY_VIOLATION = "POLICY_VIOLATION"


class Finding(Frozen):
    """A discrepancy that is not attached to any claim."""

    finding_id: str
    kind: FindingKind
    code: str
    severity: Severity
    summary: str
    evidence: tuple[Evidence, ...] = ()
    remediation: str | None = None


class CoverageGap(Frozen):
    """One system whose evidence could not be fully observed for this call.

    First-class output: incompleteness is part of the deliverable. Every claim
    a gap degraded is listed by id and stays UNVERIFIABLE, never a forced
    pass or fail.
    """

    source: str  # calendar | crm | messages | tickets
    kind: Literal["incomplete", "unavailable"]
    detail: str
    claim_ids: tuple[str, ...] = ()


class IncidentStatus(StrEnum):
    """Whether a cross-call request is still owed, from linked fulfillments."""

    PENDING = "pending"
    FULFILLED = "fulfilled"
    INCONCLUSIVE = "inconclusive"


class LinkedCall(Frozen):
    """One call's contribution to an incident."""

    call_id: str
    audit_id: str
    claim_ids: tuple[str, ...]


class DuplicateWrite(Frozen):
    """A second system-of-record record for a request one record already covers."""

    source: str  # tickets | calendar | messages
    ref: str
    audit_id: str
    note: str


class ReviewCandidate(Frozen):
    """A merge the grouper refused to make on its own."""

    reason: str
    audits: tuple[str, ...]
    detail: str


class Incident(Frozen):
    """One caller's repeated request, grouped across calls.

    Merging requires strong identity (name and phone together) plus a matching
    request; ambiguous matches stay unmerged and surface as review candidates.
    """

    incident_id: str
    caller: str
    request: str
    calls: tuple[LinkedCall, ...]
    repeated_promises: int
    duplicate_writes: tuple[DuplicateWrite, ...] = ()
    handoff_status: IncidentStatus | None = None
    summary: str


class FulfillmentState(StrEnum):
    """Lifecycle of a handoff promise. Acknowledged is not fulfilled: only a
    closed ticket, evidenced by a journal row, completes the promise."""

    COMPLETED = "completed"  # closed, with the closing row as evidence
    ACKNOWLEDGED = "acknowledged"  # someone other than the creating agent touched it
    ASSIGNED = "assigned"  # an assignee is named
    RECORDED = "recorded"  # created; nothing further observed
    INCONCLUSIVE = "inconclusive"  # the lifecycle evidence could not be read


class Fulfillment(Frozen):
    """Evidence-backed lifecycle state of one verified handoff ticket."""

    ticket_id: str
    state: FulfillmentState
    pending: bool  # the promise is still open
    summary: str
    evidence: tuple[Evidence, ...] = ()


class Outcome(StrEnum):
    CLEAN = "CLEAN"
    DISCREPANCIES = "DISCREPANCIES"
    INCONCLUSIVE = "INCONCLUSIVE"


class TrailEvent(Frozen):
    seq: int
    stage: str
    message: str


class AuditStats(Frozen):
    claims: int
    verified: int
    mismatch: int
    not_found: int
    unverifiable: int
    findings: int
    trust_score: float | None
    max_severity: Severity


class ExtractionHealth(Frozen):
    """How the transcript claims were obtained.

    ``extractor`` names what actually produced the claims: ``rules``,
    ``llm:<model>``, or ``none`` when the transcript reader was off. An
    intentional rules-only run is healthy. ``degraded`` means a requested model
    failed and the rules ran instead - the claims are still usable, but the
    operator asked for a model and did not get one, which ``detail`` explains.
    """

    extractor: str
    degraded: bool = False
    detail: str | None = None


class Seal(Frozen):
    algorithm: Literal["sha256"] = "sha256"
    content_hash: str
    prev_hash: str
    chain_hash: str
    signature: str | None = None
    key_id: str | None = None


class AuditReport(Frozen):
    audit_id: str
    auditor: str
    created_at: AwareDatetime
    call: CallRecord
    outcome: Outcome
    verdicts: tuple[Verdict, ...]
    findings: tuple[Finding, ...]
    trail: tuple[TrailEvent, ...]
    stats: AuditStats
    extraction: ExtractionHealth
    fulfillments: tuple[Fulfillment, ...] = ()
    coverage_gaps: tuple[CoverageGap, ...] = ()
    seal: Seal | None = None
    escalation_log: tuple[EscalationDecision, ...] = ()

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if not self.escalation_log:
            # Older reports must still serialize to their original sealed contents.
            data.pop("escalation_log", None)
        return data

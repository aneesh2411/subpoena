"""Duplicate-aware incident grouping across calls.

One call's audit stands alone; a caller who phones back three times about the
same unresolved request is one incident, not three. This module groups the
claims of stored audit reports into incidents so repeated promises and
duplicate writes (two tickets for one problem) are visible in one place.

Merging is conservative, and every rule is a pure function of the reports:

* Identity needs name **and** phone together. A shared phone number with
  different names, or a shared name with the phone missing, goes to the
  review list. Nothing ever merges on a phone number alone.
* Requests match on the claim's target: the appointment being moved, the day
  and provider being booked, or the topic of the handoff. Handoff topics are
  free text, so they compare by token overlap; a partial overlap is ambiguous
  and goes to review rather than merging.
* Ambiguous groups are never merged. They surface as review candidates with
  the reason, for a human to decide.

Incidents span at least two calls. A single call with two writes for one
claim is already the unclaimed-write finding's job.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from subpoena.auditor import fulfillment
from subpoena.models import (
    AppointmentBooked,
    AuditReport,
    Cancelled,
    Claim,
    ConfirmationSent,
    DuplicateWrite,
    Fulfillment,
    FulfillmentState,
    HandoffCreated,
    Incident,
    IncidentStatus,
    LinkedCall,
    PatientRef,
    PaymentCollected,
    Rescheduled,
    ReviewCandidate,
    VerdictStatus,
)
from subpoena.sor.base import TicketQueueReader
from subpoena.util import norm_phone, norm_text

_WRITE_SOURCES = {"tickets", "calendar", "messages"}
_WRITE_STATUSES = {VerdictStatus.VERIFIED, VerdictStatus.MISMATCH}
_SAME_TOPIC = 0.6
_MAYBE_TOPIC = 0.3

_STOPWORDS = frozenset(
    "a an the to about for need needs help with call back callback caller please me "
    "his her their they them i my we our on in of and or it is was be".split()
)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in norm_text(text).split() if t not in _STOPWORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _identity(ref: PatientRef | None) -> tuple[str, str] | None:
    """The strong identity key, or None when the phone is missing."""
    if ref is None or not ref.phone:
        return None
    return (norm_phone(ref.phone), norm_text(ref.name))


def _signature(claim: Claim) -> str:
    """What the caller asked for, independent of which call asked."""
    match claim:
        case AppointmentBooked(start=start, provider=provider):
            return f"book:{norm_text(provider or '')}:{start.date().isoformat()}"
        case Rescheduled(old_start=old):
            return f"reschedule:{old.isoformat()}"
        case Cancelled(start=start):
            return f"cancel:{start.isoformat()}"
        case ConfirmationSent(channel=channel, about_start=about):
            day = about.date().isoformat() if about is not None else ""
            return f"confirm:{channel}:{day}"
        case HandoffCreated():
            return "handoff"
    raise AssertionError(f"unknown claim type: {claim.type}")


def _family(signature: str) -> str:
    return signature.split(":", 1)[0]


def _request_label(claim: Claim) -> str:
    match claim:
        case AppointmentBooked(start=start, provider=provider):
            who = f" with {provider}" if provider else ""
            return f"booking{who} on {start.date().isoformat()}"
        case Rescheduled(old_start=old):
            return f"rescheduling the {old.date().isoformat()} appointment"
        case Cancelled(start=start):
            return f"cancelling the {start.date().isoformat()} appointment"
        case ConfirmationSent(channel=channel):
            return f"a {channel} confirmation"
        case HandoffCreated(summary=summary):
            topic = " ".join(sorted(_tokens(summary))) or "unspecified topic"
            return f"a callback about {topic}"
    raise AssertionError(f"unknown claim type: {claim.type}")


@dataclass
class _Entry:
    report: AuditReport
    claim: Claim
    writes: list[tuple[str, str]]  # (source, journal id) the verdict pinned
    identity: tuple[str, str] | None
    signature: str


@dataclass
class _Group:
    identity: tuple[str, str]
    signature: str
    entries: list[_Entry] = field(default_factory=list)


def _collect(reports: list[AuditReport]) -> list[_Entry]:
    entries: list[_Entry] = []
    for report in reports:
        for verdict in report.verdicts:
            # Separate calls can collect independent payments of the same amount.
            if isinstance(verdict.claim, PaymentCollected):
                continue
            writes = [
                (e.source, e.ref)
                for e in verdict.evidence
                if e.source in _WRITE_SOURCES and verdict.status in _WRITE_STATUSES
            ]
            entries.append(
                _Entry(
                    report=report,
                    claim=verdict.claim,
                    writes=writes,
                    identity=_identity(getattr(verdict.claim, "patient", None)),
                    signature=_signature(verdict.claim),
                )
            )
    return entries


def _cluster_handoffs(entries: list[_Entry]) -> tuple[list[list[_Entry]], list[_Entry]]:
    """Split one identity's handoff entries by topic: (clusters, ambiguous)."""
    clusters: list[list[_Entry]] = []
    topics: list[frozenset[str]] = []
    ambiguous: list[_Entry] = []
    for entry in entries:
        topic = _tokens(entry.claim.summary) if isinstance(entry.claim, HandoffCreated) else None
        topic = topic or frozenset()
        best, best_score = -1, 0.0
        for i, existing in enumerate(topics):
            score = _jaccard(topic, existing)
            if score > best_score:
                best, best_score = i, score
        if best >= 0 and best_score >= _SAME_TOPIC:
            clusters[best].append(entry)
            topics[best] = topics[best] | topic
        elif best >= 0 and best_score >= _MAYBE_TOPIC:
            ambiguous.append(entry)
        else:
            clusters.append([entry])
            topics.append(topic)
    return clusters, ambiguous


def group_incidents(
    reports: list[AuditReport],
    *,
    tickets_feed: TicketQueueReader | None = None,
    now: datetime | None = None,
) -> tuple[tuple[Incident, ...], tuple[ReviewCandidate, ...]]:
    """Group stored audit reports into cross-call incidents.

    Deterministic: reports are ordered by call start, then audit id, so the
    input order does not change the result.

    Each report's fulfillment entries are snapshots from its own audit time.
    When ``tickets_feed`` and ``now`` are given, linked tickets are re-graded
    against the journal as of ``now``: a close that landed after an earlier
    audit still counts, because grouping asks about now, not about what that
    audit could see then.
    """
    ordered = sorted(reports, key=lambda r: (r.call.started_at, r.audit_id))
    entries = _collect(ordered)
    reviews: list[ReviewCandidate] = []

    by_identity: dict[tuple[str, str], list[_Entry]] = {}
    weak: list[_Entry] = []
    for entry in entries:
        if entry.identity is None:
            weak.append(entry)
        else:
            by_identity.setdefault(entry.identity, []).append(entry)

    # A phone shared under two names is a review item, never a merge.
    by_phone: dict[str, set[str]] = {}
    for phone, name in by_identity:
        by_phone.setdefault(phone, set()).add(name)
    for phone, names in sorted(by_phone.items()):
        if len(names) > 1:
            audits = sorted(
                {
                    e.report.audit_id
                    for (p, _), group in by_identity.items()
                    if p == phone
                    for e in group
                }
            )
            reviews.append(
                ReviewCandidate(
                    reason="same-phone-different-names",
                    audits=tuple(audits),
                    detail=f"Phone {phone} appears as {', '.join(sorted(names))}. "
                    "These calls were not merged; confirm the caller is one person.",
                )
            )

    # A name that matches a strong identity but arrives without a phone number
    # is a review item, not a merge.
    for entry in weak:
        patient = getattr(entry.claim, "patient", None)
        if patient is None:
            continue
        name = norm_text(patient.name)
        audits = sorted(
            {entry.report.audit_id}
            | {
                e2.report.audit_id
                for key, group in by_identity.items()
                if key[1] == name
                for e2 in group
                if _family(e2.signature) == _family(entry.signature)
            }
        )
        if len(audits) > 1:
            reviews.append(
                ReviewCandidate(
                    reason="same-name-no-phone",
                    audits=tuple(audits),
                    detail=f"{patient.name} appears without a verified number in audit "
                    f"{entry.report.audit_id}. Not merged with the matching named caller.",
                )
            )

    groups: list[_Group] = []
    for identity, identity_entries in sorted(by_identity.items()):
        by_sig: dict[str, list[_Entry]] = {}
        for entry in identity_entries:
            by_sig.setdefault(entry.signature, []).append(entry)
        for signature, sig_entries in sorted(by_sig.items()):
            if signature != "handoff":
                groups.append(_Group(identity, signature, sig_entries))
                continue
            clusters, ambiguous = _cluster_handoffs(sig_entries)
            for amb in ambiguous:
                others = sorted({e.report.audit_id for c in clusters for e in c})
                reviews.append(
                    ReviewCandidate(
                        reason="ambiguous-handoff-topic",
                        audits=tuple(sorted({amb.report.audit_id} | set(others))),
                        detail=f"The handoff summary in audit {amb.report.audit_id} partly "
                        "overlaps another request from this caller. Left unmerged.",
                    )
                )
            groups.extend(_Group(identity, signature, cluster) for cluster in clusters)

    incidents = tuple(
        _build(group, tickets_feed, now)
        for group in groups
        if len({e.report.call.call_id for e in group.entries}) >= 2
    )
    return incidents, tuple(reviews)


def _build(group: _Group, tickets_feed: TicketQueueReader | None, now: datetime | None) -> Incident:
    entries = group.entries
    first = entries[0]
    phone, _ = group.identity

    by_call: dict[str, tuple[str, list[str]]] = {}
    for entry in entries:
        audit_id, claim_ids = by_call.get(entry.report.call.call_id, (entry.report.audit_id, []))
        claim_ids.append(entry.claim.claim_id)
        by_call[entry.report.call.call_id] = (audit_id, claim_ids)
    calls = tuple(
        LinkedCall(call_id=call_id, audit_id=audit_id, claim_ids=tuple(sorted(claim_ids)))
        for call_id, (audit_id, claim_ids) in by_call.items()
    )

    seen: list[tuple[str, str, str]] = []  # (source, ref, audit_id) in call order
    duplicates: list[DuplicateWrite] = []
    for entry in entries:
        for source, ref in entry.writes:
            if any(ref == r for _, r, _ in seen):
                continue
            if seen:
                first_source, first_ref, first_audit = seen[0]
                duplicates.append(
                    DuplicateWrite(
                        source=source,
                        ref=ref,
                        audit_id=entry.report.audit_id,
                        note="A second record for the same request; the first is "
                        f"{first_ref} ({first_source}) from audit {first_audit}.",
                    )
                )
            seen.append((source, ref, entry.report.audit_id))

    status = _handoff_status(entries, {ref for _, ref, _ in seen}, tickets_feed, now)
    n_calls = len({e.report.call.call_id for e in entries})
    summary = (
        f"This caller raised the same request {len(entries)} time(s) across {n_calls} "
        f"calls ({_request_label(first.claim)})."
    )
    if duplicates:
        summary += f" {len(duplicates)} duplicate record(s) were created."
    if status is IncidentStatus.PENDING:
        summary += " The request is still pending."
    elif status is IncidentStatus.FULFILLED:
        summary += " Every linked handoff is completed."
    elif status is IncidentStatus.INCONCLUSIVE:
        summary += " Fulfillment could not be proven for every linked handoff."

    patient = getattr(first.claim, "patient", None)
    claim_keys = sorted(f"{e.report.audit_id}:{e.claim.claim_id}" for e in entries)
    raw = f"{group.identity}|{first.signature}|{claim_keys}"
    return Incident(
        incident_id="inc_" + hashlib.sha256(raw.encode()).hexdigest()[:12],
        caller=f"{patient.name} ({phone})" if patient else group.identity[1],
        request=_request_label(first.claim),
        calls=calls,
        repeated_promises=len(entries),
        duplicate_writes=tuple(duplicates),
        handoff_status=status,
        summary=summary,
    )


def _handoff_status(
    entries: list[_Entry],
    ticket_refs: set[str],
    tickets_feed: TicketQueueReader | None,
    now: datetime | None,
) -> IncidentStatus | None:
    linked: list[Fulfillment] = []
    if tickets_feed is not None and now is not None:
        by_id = {t.id: t for t in tickets_feed.list_tickets()}
        for ref in sorted(ticket_refs):
            ticket = by_id.get(ref)
            if ticket is not None:
                linked.append(fulfillment.assess(ticket, "", tickets_feed, now))
    if not linked:
        linked = [
            f for entry in entries for f in entry.report.fulfillments if f.ticket_id in ticket_refs
        ]
    if not linked:
        return None
    if any(f.pending for f in linked):
        return IncidentStatus.PENDING
    if any(f.state is FulfillmentState.INCONCLUSIVE for f in linked):
        return IncidentStatus.INCONCLUSIVE
    return IncidentStatus.FULFILLED

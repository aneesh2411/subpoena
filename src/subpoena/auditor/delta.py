"""Turn a raw change feed into the net effect of a call.

A call's journal may touch the same record several times (create, then move,
then delete). What matters for the caller is the *net* change between the state
before the call and the state after it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from subpoena.models import Change, Entity, Evidence

SOURCE: dict[Entity, str] = {
    Entity.APPOINTMENT: "calendar",
    Entity.PATIENT: "crm",
    Entity.MESSAGE: "messages",
    Entity.TICKET: "tickets",
    Entity.PAYMENT: "payments",
}


class ChangeKind(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    TRANSIENT = "transient"  # no net effect: created-then-deleted, or changed back


class Attribution(StrEnum):
    """How firmly a journal row can be pinned to the call being audited.

    DIRECT: the row carries this call's own stamp (``agent:call-id``), written by
    the platform session. SOLE: only the shared agent identity, and no concurrent
    call was observed. AMBIGUOUS: the shared agent identity while another call was
    in progress - correlation by identity and window cannot say which call wrote it,
    and an agent-stamped call id is a hint, not independent evidence.
    """

    DIRECT = "direct"
    SOLE = "sole"
    AMBIGUOUS = "ambiguous"


RANK: dict[Attribution, int] = {
    Attribution.AMBIGUOUS: 0,
    Attribution.SOLE: 1,
    Attribution.DIRECT: 2,
}


def dedupe(changes: Sequence[Change]) -> tuple[list[Change], int]:
    """Drop exact replays: same record, op, images, actor and timestamp, new seq.

    A redelivered batch must not count twice - replaying an insert after a delete
    would otherwise resurrect a removed record in the net change.
    """
    seen: set[tuple[Any, ...]] = set()
    rows: list[Change] = []
    dropped = 0
    for change in sorted(changes, key=lambda c: c.seq):
        sig = (
            change.entity,
            change.entity_id,
            change.op,
            _freeze(change.before),
            _freeze(change.after),
            change.actor,
            change.at,
        )
        if sig in seen:
            dropped += 1
            continue
        seen.add(sig)
        rows.append(change)
    return rows, dropped


def _freeze(image: dict[str, Any] | None) -> str | None:
    return json.dumps(image, sort_keys=True, default=str) if image is not None else None


def content_signature(image: dict[str, Any] | None) -> str | None:
    """A write's content minus its record id.

    Two writes with the same signature are the same fact written twice. A
    leftover copy is not new evidence for another claim: it is itself the
    anomaly an unclaimed-write finding exists to report.
    """
    if image is None:
        return None
    return _freeze({k: v for k, v in image.items() if k != "id"})


@dataclass(frozen=True)
class NetChange:
    entity: Entity
    entity_id: str
    kind: ChangeKind
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    changes: tuple[Change, ...]

    @property
    def key(self) -> tuple[Entity, str]:
        return (self.entity, self.entity_id)

    @property
    def snapshot(self) -> dict[str, Any] | None:
        """The most informative image of the record, even for a transient change."""
        if self.after is not None:
            return self.after
        if self.before is not None:
            return self.before
        return next((c.after for c in self.changes if c.after is not None), None)

    def evidence(self, note: str) -> Evidence:
        seqs = ", ".join(f"#{c.seq}" for c in self.changes)
        return Evidence(
            source=SOURCE[self.entity],
            ref=self.entity_id,
            note=f"{note} (journal {seqs}; net: {self.kind.value})",
            record=self.snapshot,
        )


def collapse(changes: Sequence[Change]) -> list[NetChange]:
    """Group journal rows by record and reduce each group to its net change."""
    grouped: dict[tuple[Entity, str], list[Change]] = {}
    for change in sorted(changes, key=lambda c: c.seq):
        grouped.setdefault((change.entity, change.entity_id), []).append(change)

    net: list[NetChange] = []
    for (entity, entity_id), rows in grouped.items():
        before, after = rows[0].before, rows[-1].after
        if before is None and after is None:
            kind = ChangeKind.TRANSIENT
        elif before is None:
            kind = ChangeKind.CREATED
        elif after is None:
            kind = ChangeKind.DELETED
        elif before == after:
            kind = ChangeKind.TRANSIENT
        else:
            kind = ChangeKind.UPDATED
        net.append(NetChange(entity, entity_id, kind, before, after, tuple(rows)))
    return net

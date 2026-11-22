"""Human remediation decisions stored alongside the sealed audit ledger."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from subpoena.util import utcnow

APPROVAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals(
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL REFERENCES audits(audit_id),
    body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS approvals_audit_seq ON approvals(audit_id, seq);
CREATE TRIGGER IF NOT EXISTS approvals_no_update BEFORE UPDATE ON approvals
    BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_delete BEFORE DELETE ON approvals
    BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END;
"""

ApprovalAction = Literal["acknowledge", "resolve", "dismiss"]
NonemptyText = Annotated[str, Field(min_length=1)]


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: NonemptyText | None = None
    finding_id: NonemptyText | None = None
    action: ApprovalAction
    actor: NonemptyText
    note: str

    @model_validator(mode="after")
    def validate_target(self) -> ApprovalRequest:
        if (self.claim_id is None) == (self.finding_id is None):
            raise ValueError("provide exactly one of claim_id or finding_id")
        return self

    @property
    def target(self) -> tuple[str, str]:
        if self.claim_id is not None:
            return "claim", self.claim_id
        assert self.finding_id is not None
        return "finding", self.finding_id


class Approval(ApprovalRequest):
    audit_id: str
    created_at: datetime


def append_approval(conn: sqlite3.Connection, audit_id: str, request: ApprovalRequest) -> Approval:
    approval = Approval(**request.model_dump(), audit_id=audit_id, created_at=utcnow())
    conn.execute(
        "INSERT INTO approvals(audit_id, body) VALUES (?, ?)",
        (audit_id, approval.model_dump_json()),
    )
    return approval


def list_approvals(conn: sqlite3.Connection, audit_id: str) -> list[Approval]:
    return [
        Approval.model_validate_json(body)
        for (body,) in conn.execute(
            "SELECT body FROM approvals WHERE audit_id = ? ORDER BY seq", (audit_id,)
        )
    ]

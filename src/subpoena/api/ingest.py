"""Parse generic end-of-call webhook deliveries into call records."""

from __future__ import annotations

import json
from typing import Annotated
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from subpoena.models import CallRecord, Frozen, PatientRef, Provenance, Turn


class CallMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    caller: PatientRef = PatientRef(name="Unknown")
    agent_version: Annotated[str, Field(min_length=1, max_length=120)] = "unknown"
    model: Annotated[str, Field(max_length=120)] | None = None
    prompt_version: Annotated[str, Field(max_length=120)] | None = None


class CallDelivery(Frozen):
    platform: str = Field(min_length=1, max_length=60)
    call_id: str = Field(min_length=1, max_length=255)
    agent_id: str = Field(min_length=1, max_length=120)
    started_at: AwareDatetime
    ended_at: AwareDatetime
    transcript: tuple[Turn, ...] = Field(max_length=500)
    metadata: CallMetadata

    @model_validator(mode="after")
    def _validate_transcript(self) -> CallDelivery:
        previous = self.started_at
        for index, turn in enumerate(self.transcript):
            if turn.index != index:
                raise ValueError(f"transcript.{index}.index must be {index}")
            if not previous <= turn.at <= self.ended_at:
                raise ValueError(f"transcript.{index}.at must be ordered within the call window")
            previous = turn.at
        return self

    def to_record(self) -> CallRecord:
        return CallRecord(
            call_id=f"ingest-{uuid4().hex}",
            agent_id=self.agent_id,
            caller=self.metadata.caller,
            started_at=self.started_at,
            ended_at=self.ended_at,
            transcript=self.transcript,
            provenance=Provenance(
                platform=self.platform,
                agent_version=self.metadata.agent_version,
                model=self.metadata.model,
                prompt_version=self.metadata.prompt_version,
            ),
        )


def _invalid_constant(value: str) -> None:
    raise ValueError(f"{value} is not valid JSON")


def parse_delivery(body: bytes) -> tuple[CallDelivery, CallRecord]:
    # Reject nonstandard JSON constants even in metadata that is only retained for audit.
    payload = json.loads(body.decode("utf-8"), parse_constant=_invalid_constant)
    delivery = CallDelivery.model_validate(payload)
    return delivery, delivery.to_record()

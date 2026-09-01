from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.ledger.models import canonical_json


class ViewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    program: str
    version: int = Field(default=1, ge=1)
    as_of: datetime | None = None
    query: str | None = None
    max_bytes: int = Field(default=16_000, ge=128, le=1_000_000)


class ViewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    view_id: str
    task_id: str
    program: str
    version: int
    watermark: int = Field(ge=0)
    data: dict[str, Any]
    evidence_event_ids: tuple[str, ...]
    content_hash: str = ""
    truncated: bool = False

    @model_validator(mode="after")
    def validate_hash(self) -> ViewResult:
        body = {
            "task_id": self.task_id,
            "program": self.program,
            "version": self.version,
            "watermark": self.watermark,
            "data": self.data,
            "evidence_event_ids": self.evidence_event_ids,
            "truncated": self.truncated,
        }
        expected = hashlib.sha256(canonical_json(body).encode()).hexdigest()
        if self.content_hash and self.content_hash != expected:
            raise ValueError("content_hash does not match view result")
        if not self.content_hash:
            object.__setattr__(self, "content_hash", expected)
        return self

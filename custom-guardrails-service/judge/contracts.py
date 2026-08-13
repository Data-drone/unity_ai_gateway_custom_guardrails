"""Pydantic contracts for the custom LLM judge."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class JudgeDecision(BaseModel):
    """JSON contract Databricks appends for LLM-as-a-Judge evaluators."""

    flagged: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value: Any) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, score))

    def to_json_text(self) -> str:
        return self.model_dump_json()


class JudgeRequest(BaseModel):
    """Normalized judge inputs extracted from Chat Completions or Responses payloads."""

    criteria: str = ""
    content: str = ""
    model: str = "guardrail-judge"
    source_api: Literal["chat.completions", "responses"] = "chat.completions"


class JudgeError(BaseModel):
    error_type: str
    message: str
    fail_closed: bool = True

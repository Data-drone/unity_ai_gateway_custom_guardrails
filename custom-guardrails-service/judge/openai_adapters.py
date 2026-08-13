"""Build OpenAI-compatible Chat Completions and Responses payloads from judge decisions."""

from __future__ import annotations

import time
import uuid
from typing import Any

from .contracts import JudgeDecision


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def to_chat_completion(decision: JudgeDecision, *, model: str) -> dict[str, Any]:
    text = decision.to_json_text()
    return {
        "id": _id("chatcmpl"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def to_responses(decision: JudgeDecision, *, model: str) -> dict[str, Any]:
    text = decision.to_json_text()
    response_id = _id("resp")
    item_id = _id("msg")
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": [
            {
                "id": item_id,
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                    }
                ],
            }
        ],
        "output_text": text,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
    }

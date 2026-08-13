"""Normalize OpenAI Chat Completions and Responses payloads into judge inputs."""

from __future__ import annotations

from typing import Any

from .contracts import JudgeRequest


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    if isinstance(value, dict):
        text = value.get("text") or value.get("content") or ""
        return text.strip() if isinstance(text, str) else ""
    return str(value).strip()


def _message_text(message: dict[str, Any]) -> str:
    return _as_text(message.get("content"))


def from_chat_completions(body: dict[str, Any]) -> JudgeRequest:
    messages = body.get("messages") or []
    criteria_parts: list[str] = []
    content_parts: list[str] = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = (message.get("role") or "").lower()
        text = _message_text(message)
        if not text:
            continue
        if role in {"system", "developer"}:
            criteria_parts.append(text)
        elif role == "user":
            content_parts.append(text)
        elif role == "assistant":
            # ON RESULT evaluations may arrive as assistant content under review.
            content_parts.append(text)

    return JudgeRequest(
        criteria="\n\n".join(criteria_parts).strip(),
        content="\n\n".join(content_parts).strip(),
        model=str(body.get("model") or "guardrail-judge"),
        source_api="chat.completions",
    )


def from_responses(body: dict[str, Any]) -> JudgeRequest:
    criteria_parts: list[str] = []
    content_parts: list[str] = []

    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        criteria_parts.append(instructions.strip())

    raw_input = body.get("input")
    items: list[Any]
    if isinstance(raw_input, str):
        items = [{"role": "user", "content": [{"type": "input_text", "text": raw_input}]}]
    elif isinstance(raw_input, list):
        items = raw_input
    else:
        items = []

    for item in items:
        if isinstance(item, str):
            content_parts.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        role = (item.get("role") or "").lower()
        text = _as_text(item.get("content"))
        if not text:
            continue
        if role in {"system", "developer"}:
            criteria_parts.append(text)
        else:
            content_parts.append(text)

    return JudgeRequest(
        criteria="\n\n".join(criteria_parts).strip(),
        content="\n\n".join(content_parts).strip(),
        model=str(body.get("model") or "guardrail-judge"),
        source_api="responses",
    )

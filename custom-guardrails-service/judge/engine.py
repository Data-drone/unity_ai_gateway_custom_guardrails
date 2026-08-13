"""Core custom LLM judge engine.

UAIG LLM-as-a-Judge sends:
  - system/developer: criteria prompt (+ appended JSON contract from Databricks)
  - user/assistant: content under evaluation

This service returns only the judge JSON contract inside an OpenAI-compatible response.
It never returns ALLOW/DENY; the gateway maps flagged=true → DENY.
"""

from __future__ import annotations

import re
from typing import Iterable

from .contracts import JudgeDecision, JudgeRequest

# High-precision deny patterns for the pilot scaffold. Semantic coverage can later
# call an upstream LLM; keep these deterministic and fail-closed on engine errors.
_DENY_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"\b(socially\s+engineer|social\s+engineering)\b",
        "Request asks for social-engineering guidance against bank staff or customers.",
    ),
    (
        r"\b(phish(ing)?|credential\s+harvest|steal\s+(a\s+)?password)\b",
        "Request asks for phishing or credential-harvesting guidance.",
    ),
    (
        r"\b(take\s+over|account\s+takeover|bypass\s+(bank\s+)?security)\b",
        "Request asks how to take over an account or bypass bank security controls.",
    ),
    (
        r"\b(otp|sms\s+code|netbank\s+password).{0,40}\b(phish|trick|steal|harvest)\b|"
        r"\b(phish|trick|steal|harvest).{0,40}\b(otp|sms\s+code|netbank\s+password)\b",
        "Request targets customer authentication secrets (password/OTP).",
    ),
)


def _matches(patterns: Iterable[tuple[str, str]], text: str) -> JudgeDecision | None:
    lowered = text.lower()
    for pattern, reason in patterns:
        if re.search(pattern, lowered, flags=re.IGNORECASE | re.DOTALL):
            return JudgeDecision(flagged=True, confidence=0.95, reason=reason)
    return None


def evaluate(request: JudgeRequest) -> JudgeDecision:
    """Evaluate normalized judge input and return a schema-valid decision."""
    try:
        content = (request.content or "").strip()
        if not content:
            return JudgeDecision(
                flagged=True,
                confidence=1.0,
                reason="Judge received empty content to evaluate.",
            )

        denied = _matches(_DENY_PATTERNS, content)
        if denied is not None:
            return denied

        return JudgeDecision(
            flagged=False,
            confidence=0.7,
            reason="No prohibited fraud, phishing, or security-bypass intent detected.",
        )
    except Exception as exc:  # noqa: BLE001 - fail closed for any engine fault
        return JudgeDecision(
            flagged=True,
            confidence=1.0,
            reason=f"Judge engine failure (fail-closed): {type(exc).__name__}",
        )

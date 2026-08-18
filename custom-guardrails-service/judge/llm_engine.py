"""LLM-backed judge for personal financial advice risk.

Uses a Databricks OpenAI-compatible mini model (Haiku / GPT-mini class).
On Databricks Apps, auth is the app service principal (auto-injected
DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET via WorkspaceClient) — grant
CAN_QUERY on the serving endpoint resource rather than a separate API key.

Returns the same JudgeDecision contract as the deterministic engine.
Fail-closed on empty input, timeouts, HTTP errors, or unparseable output.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from .contracts import JudgeDecision, JudgeRequest

LLM_MODEL_ID = "guardrail-judge-llm"

_JSON_CONTRACT = (
    "Respond with a single JSON object only (no markdown), exactly matching:\n"
    '{"flagged": true|false, "confidence": 0.0-1.0, "reason": "<short string>"}'
)

_DEFAULT_CRITERIA = (
    "You are reviewing messages for an Australian banking assistant pilot. "
    "Flag only when the message asks for or would constitute personal financial "
    "advice: a recommendation or opinion that considers (or that a reasonable "
    "person would expect to consider) the person's objectives, financial "
    "situation, or needs. Do not flag factual product information, rates, "
    "how-to banking support, balances/transfers, or clearly general educational "
    "content. This is an operational pilot heuristic, not a legal determination."
)


def is_llm_judge_model(model: str | None) -> bool:
    """True for guardrail-judge-llm and workspace-prefixed variants (e.g. westpac-…)."""
    name = (model or "").strip().lower().rsplit("/", 1)[-1]
    return name == LLM_MODEL_ID or name.endswith("-" + LLM_MODEL_ID) or name.endswith("_llm")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _workspace_host() -> str:
    host = _env("DATABRICKS_HOST")
    if not host:
        return ""
    if not host.startswith("http"):
        host = f"https://{host}"
    return host.rstrip("/")


def _resolve_bearer_token() -> str | None:
    """Prefer explicit override; otherwise use App / workspace identity."""
    explicit = _env("LLM_JUDGE_API_KEY") or _env("DATABRICKS_TOKEN")
    if explicit:
        return explicit
    try:
        from databricks.sdk import WorkspaceClient

        # On Apps: picks up DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET.
        # Locally: uses CLI profile / env auth.
        client = WorkspaceClient()
        creds = client.config.authenticate()
        token = getattr(creds, "token", None) or getattr(creds, "access_token", None)
        if token:
            return str(token)
        if isinstance(creds, dict):
            auth = str(creds.get("Authorization") or creds.get("authorization") or "")
            if auth.lower().startswith("bearer "):
                return auth.split(" ", 1)[1].strip()
            if auth:
                return auth
        if isinstance(creds, str) and creds:
            return creds[7:].strip() if creds.lower().startswith("bearer ") else creds
    except Exception:  # noqa: BLE001 - fall through to fail-closed at call site
        return None
    return None


def _chat_completions_url() -> str:
    """Resolve OpenAI-compatible chat completions URL for the judge model."""
    explicit = _env("LLM_JUDGE_BASE_URL")
    if explicit:
        url = explicit.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        return url

    # Preferred: AI Gateway OpenAI path (workspace-scoped).
    gateway = _env("AI_GATEWAY_URL")
    if gateway:
        base = gateway.rstrip("/")
        if base.endswith("/mlflow/v1"):
            return f"{base}/chat/completions"
        return f"{base}/chat/completions"

    # Fallback: workspace serving-endpoints OpenAI-compatible route.
    host = _workspace_host()
    if host:
        return f"{host}/serving-endpoints/chat/completions"
    return ""


def _llm_model_name() -> str:
    # valueFrom: serving-endpoint injects the endpoint name; that is also the
    # model id for many Databricks foundation-model endpoints.
    return (
        _env("LLM_JUDGE_MODEL")
        or _env("SERVING_ENDPOINT")
        or "databricks-claude-haiku-4-5"
    )


def _timeout_seconds() -> float:
    return float(_env("LLM_JUDGE_TIMEOUT_SECONDS", "8") or "8")


def _fail_closed(reason: str, confidence: float = 1.0) -> JudgeDecision:
    return JudgeDecision(flagged=True, confidence=confidence, reason=reason)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _decision_from_payload(payload: dict[str, Any]) -> JudgeDecision:
    return JudgeDecision(
        flagged=bool(payload.get("flagged")),
        confidence=payload.get("confidence", 0.5),
        reason=str(payload.get("reason") or "").strip() or "LLM judge returned no reason.",
    )


async def evaluate_llm(
    request: JudgeRequest,
    *,
    client: httpx.AsyncClient | None = None,
) -> JudgeDecision:
    """Evaluate content with a mini LLM; always returns a schema-valid decision."""
    content = (request.content or "").strip()
    if not content:
        return _fail_closed("Judge received empty content to evaluate.")

    url = _chat_completions_url()
    if not url:
        return _fail_closed(
            "LLM judge misconfigured: set AI_GATEWAY_URL or LLM_JUDGE_BASE_URL "
            "(or run on a Databricks App with DATABRICKS_HOST)."
        )

    api_key = _resolve_bearer_token()
    if not api_key:
        return _fail_closed(
            "LLM judge auth failed: App SP credentials unavailable. "
            "Ensure the App has a serving-endpoint resource with CAN_QUERY "
            "(or set LLM_JUDGE_API_KEY for local/dev only)."
        )

    criteria = (request.criteria or "").strip() or _DEFAULT_CRITERIA
    system = f"{criteria}\n\n{_JSON_CONTRACT}"
    model = _llm_model_name()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "max_tokens": 160,
    }

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_timeout_seconds())
    try:
        response = await http.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
        message = (((data.get("choices") or [{}])[0]).get("message") or {})
        text = message.get("content") or ""
        if isinstance(text, list):
            text = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in text
            )
        payload = _extract_json_object(str(text))
        if payload is None:
            return _fail_closed("LLM judge returned unparseable decision JSON.")
        return _decision_from_payload(payload)
    except httpx.TimeoutException:
        return _fail_closed("LLM judge timed out (fail-closed).")
    except httpx.HTTPError as exc:
        return _fail_closed(f"LLM judge HTTP failure (fail-closed): {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001 - fail closed for any engine fault
        return _fail_closed(f"LLM judge failure (fail-closed): {type(exc).__name__}")
    finally:
        if owns_client:
            await http.aclose()

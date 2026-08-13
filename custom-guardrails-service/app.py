"""FastAPI OpenAI-compatible judge service for Unity AI Gateway LLM-as-a-Judge.

Register this service as a CUSTOM model provider base_url (typically https://<host>/v1),
create a UC Model Service that routes to it, then select that Model Service as the
policy evaluator.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from judge.engine import evaluate
from judge.normalize import from_chat_completions, from_responses
from judge.openai_adapters import to_chat_completion, to_responses

APP_NAME = "guardrail-judge"
API_KEY_ENV = "JUDGE_API_KEY"


def _expected_api_key() -> str | None:
    value = os.getenv(API_KEY_ENV, "").strip()
    return value or None


async def require_api_key(authorization: str | None = Header(default=None)) -> None:
    expected = _expected_api_key()
    if expected is None:
        # Local/dev convenience. For CUSTOM provider registration, set JUDGE_API_KEY.
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    provided = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")


app = FastAPI(title=APP_NAME, version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": APP_NAME}


@app.get("/v1/models", dependencies=[Depends(require_api_key)])
async def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "guardrail-judge",
                "object": "model",
                "owned_by": "custom-guardrails",
            }
        ],
    }


@app.post("/v1/chat/completions", dependencies=[Depends(require_api_key)])
@app.post("/chat/completions", dependencies=[Depends(require_api_key)])
async def chat_completions(request: Request) -> JSONResponse:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    judge_request = from_chat_completions(body)
    decision = evaluate(judge_request)
    payload = to_chat_completion(decision, model=judge_request.model)
    return JSONResponse(payload)


@app.post("/v1/responses", dependencies=[Depends(require_api_key)])
@app.post("/responses", dependencies=[Depends(require_api_key)])
async def responses(request: Request) -> JSONResponse:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    judge_request = from_responses(body)
    decision = evaluate(judge_request)
    payload = to_responses(decision, model=judge_request.model)
    return JSONResponse(payload)

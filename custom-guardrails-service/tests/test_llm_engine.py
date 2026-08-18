"""Tests for the LLM financial-advice judge."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from judge.contracts import JudgeRequest
from judge.llm_engine import evaluate_llm, is_llm_judge_model


def test_is_llm_judge_model():
    assert is_llm_judge_model("guardrail-judge-llm") is True
    assert is_llm_judge_model("westpac-guardrail-judge-llm") is True
    assert is_llm_judge_model("catalog/guardrail-judge-llm") is True
    assert is_llm_judge_model("guardrail-judge") is False
    assert is_llm_judge_model("westpac-guardrail-judge") is False


def _mock_response(content: str, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error",
            request=MagicMock(),
            response=response,
        )
    response.json.return_value = {
        "choices": [{"message": {"content": content}}],
    }
    return response


@pytest.mark.asyncio
async def test_llm_allow_factual_rates(monkeypatch):
    monkeypatch.setenv("LLM_JUDGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_JUDGE_API_KEY", "test-key")
    monkeypatch.setenv("LLM_JUDGE_MODEL", "mini-model")

    decision_json = json.dumps(
        {
            "flagged": False,
            "confidence": 0.82,
            "reason": "Factual product fee question; not personal advice.",
        }
    )
    client = AsyncMock()
    client.post = AsyncMock(return_value=_mock_response(decision_json))

    decision = await evaluate_llm(
        JudgeRequest(
            criteria="Flag personal financial advice only.",
            content="What are the bank's standard savings account fees?",
            model="guardrail-judge-llm",
        ),
        client=client,
    )
    assert decision.flagged is False
    assert decision.confidence == pytest.approx(0.82)
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_deny_personal_advice(monkeypatch):
    monkeypatch.setenv("LLM_JUDGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_JUDGE_API_KEY", "test-key")

    decision_json = json.dumps(
        {
            "flagged": True,
            "confidence": 0.93,
            "reason": "Asks for a product recommendation tailored to personal circumstances.",
        }
    )
    client = AsyncMock()
    client.post = AsyncMock(return_value=_mock_response(decision_json))

    decision = await evaluate_llm(
        JudgeRequest(
            content=(
                "I earn $95k, have $40k HECS and want to buy a home in 2 years — "
                "which savings account and offset product should I choose?"
            ),
            model="guardrail-judge-llm",
        ),
        client=client,
    )
    assert decision.flagged is True
    assert decision.confidence >= 0.9


@pytest.mark.asyncio
async def test_llm_unparseable_fail_closed(monkeypatch):
    monkeypatch.setenv("LLM_JUDGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_JUDGE_API_KEY", "test-key")

    client = AsyncMock()
    client.post = AsyncMock(return_value=_mock_response("not json at all"))

    decision = await evaluate_llm(
        JudgeRequest(content="Should I refinance my mortgage?"),
        client=client,
    )
    assert decision.flagged is True
    assert "unparseable" in decision.reason.lower()


@pytest.mark.asyncio
async def test_llm_missing_config_fail_closed(monkeypatch):
    monkeypatch.delenv("LLM_JUDGE_BASE_URL", raising=False)
    monkeypatch.delenv("AI_GATEWAY_URL", raising=False)
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("LLM_JUDGE_API_KEY", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

    decision = await evaluate_llm(
        JudgeRequest(content="What is an offset account?"),
    )
    assert decision.flagged is True
    assert "misconfigured" in decision.reason.lower() or "auth failed" in decision.reason.lower()


@pytest.mark.asyncio
async def test_llm_empty_content_fail_closed():
    decision = await evaluate_llm(JudgeRequest(content=""))
    assert decision.flagged is True


@pytest.mark.asyncio
async def test_llm_timeout_fail_closed(monkeypatch):
    monkeypatch.setenv("LLM_JUDGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LLM_JUDGE_API_KEY", "test-key")

    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.TimeoutException("slow"))

    decision = await evaluate_llm(
        JudgeRequest(content="Is a term deposit right for me at age 28?"),
        client=client,
    )
    assert decision.flagged is True
    assert "timed out" in decision.reason.lower()

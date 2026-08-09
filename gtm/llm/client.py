from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from gtm.config import get_settings
from gtm.cost import can_spend, estimate_llm_cost, record_cost

logger = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


class LLMClient:
    """Chat client supporting Anthropic + OpenAI-compatible APIs, with soft daily budget."""

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def provider(self) -> str:
        p = (self.settings.llm_provider or "").lower().strip()
        if p:
            return p
        key = self.settings.llm_api_key or ""
        if key.startswith("sk-ant-"):
            return "anthropic"
        base = (self.settings.llm_base_url or "").lower()
        if "anthropic" in base:
            return "anthropic"
        return "openai"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "cheap",
        temperature: float = 0.3,
        response_json: bool = False,
        max_tokens: int = 800,
    ) -> str:
        if not self.settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY is required")

        model = (
            self.settings.llm_model_cheap if tier == "cheap" else self.settings.llm_model_quality
        )
        if not can_spend(0.002):
            raise BudgetExceeded("Daily LLM/enrichment budget exhausted")

        if response_json:
            messages = list(messages) + [
                {
                    "role": "user",
                    "content": "Respond with a single valid JSON object only. No markdown.",
                }
            ]

        if self.provider == "anthropic":
            content, prompt_tokens, completion_tokens = self._chat_anthropic(
                messages, model=model, temperature=temperature, max_tokens=max_tokens
            )
            provider = "anthropic"
        else:
            content, prompt_tokens, completion_tokens = self._chat_openai(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_json=response_json,
            )
            provider = "openai_compatible"

        cost = estimate_llm_cost(model, prompt_tokens, completion_tokens)
        record_cost(
            kind="llm",
            estimated_usd=cost,
            provider=provider,
            model=model,
            units=prompt_tokens + completion_tokens,
            note=f"tier={tier}",
        )
        logger.info(
            "LLM %s/%s tokens=%s cost≈$%.5f",
            provider,
            model,
            prompt_tokens + completion_tokens,
            cost,
        )
        return content

    def _chat_openai(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        response_json: bool,
    ) -> tuple[str, int, int]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_json:
            payload["response_format"] = {"type": "json_object"}
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        return (
            content,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )

    def _chat_anthropic(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, int, int]:
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        chat_msgs = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") in ("user", "assistant")
        ]
        if not chat_msgs:
            chat_msgs = [{"role": "user", "content": "Continue."}]

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": chat_msgs,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)

        headers = {
            "x-api-key": self.settings.llm_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        url = "https://api.anthropic.com/v1/messages"
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        usage = data.get("usage") or {}
        return (
            text,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
        )

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        tier: str = "cheap",
        temperature: float = 0.2,
        max_tokens: int = 900,
    ) -> dict[str, Any]:
        raw = self.chat(
            messages,
            tier=tier,
            temperature=temperature,
            response_json=True,
            max_tokens=max_tokens,
        )
        return _extract_json(raw)


def get_llm() -> LLMClient:
    return LLMClient()

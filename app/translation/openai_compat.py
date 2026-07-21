"""Transport adapter for any OpenAI-compatible chat-completions API.

One adapter covers OpenAI, OpenRouter, Azure-style gateways, Ollama, vLLM,
LM Studio and custom REST proxies — new providers are configuration, not code.

Hardened against real-world gateway quirks observed in production:
- stray SSE terminators (``data: [DONE]``) appended to JSON bodies
- reasoning models that put output in ``reasoning_content``/``reasoning``
- "model not supported" errors disguised as authentication errors
- error payloads returned with HTTP 200
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

import httpx

from ..config import ProviderConfig

log = logging.getLogger("app.translate.transport")

_MODEL_ERROR_MARKERS = ("not supported", "not found", "does not exist",
                        "modelerror", "invalid model", "no such model",
                        "model_not_found")


class RetryableAPIError(Exception):
    """Temporary failure: 429/408/5xx/network — retry or rotate model."""

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ModelUnavailableError(Exception):
    """This model is rejected by the provider — try the next model."""


class ProviderAuthError(Exception):
    """Provider rejected credentials — cool the provider down."""


def _lenient_json(text: str) -> Optional[dict]:
    """Parse JSON bodies that may carry SSE junk or trailing garbage."""
    if not text:
        return None
    cleaned = text.strip()
    # Strip a trailing SSE terminator some gateways append to non-stream bodies.
    if cleaned.endswith("data: [DONE]"):
        cleaned = cleaned[: cleaned.rfind("data: [DONE]")].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # SSE-style body: concatenate the data chunks' content.
    if cleaned.startswith("data:"):
        merged = ""
        last_obj = None
        for line in cleaned.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]" or not payload:
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            last_obj = obj
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or choice.get("message") or {}
                merged += delta.get("content") or ""
        if merged and last_obj is not None:
            return {"choices": [{"message": {"content": merged},
                                 "finish_reason": "stop"}]}
        return last_obj
    # Last resort: cut at the outermost closing brace.
    end = cleaned.rfind("}")
    if end != -1:
        try:
            return json.loads(cleaned[: end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _looks_like_model_error(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _MODEL_ERROR_MARKERS)


class OpenAICompatClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def chat(self, provider: ProviderConfig, model: str,
                   messages: List[dict], max_tokens: int,
                   temperature: Optional[float] = None) -> str:
        url = provider.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = "Bearer %s" % provider.api_key
        payload = {
            "model": model,
            "messages": messages,
            "temperature": provider.temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            response = await self._http.post(
                url, json=payload, headers=headers,
                timeout=httpx.Timeout(provider.timeout, connect=20.0),
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RetryableAPIError("network error: %s" % exc)

        body_text = response.text or ""

        if response.status_code in (429, 408) or response.status_code >= 500:
            retry_after: Optional[float] = None
            header = response.headers.get("retry-after")
            if header:
                try:
                    retry_after = float(header)
                except ValueError:
                    retry_after = None
            raise RetryableAPIError(
                "HTTP %d from %s" % (response.status_code, provider.name),
                retry_after=retry_after,
            )
        if response.status_code in (401, 403):
            # Some gateways report unsupported models as auth errors.
            if _looks_like_model_error(body_text):
                raise ModelUnavailableError(
                    "model %s rejected: %s" % (model, body_text[:200]))
            raise ProviderAuthError("HTTP %d from %s" % (response.status_code,
                                                         provider.name))
        if response.status_code in (400, 404, 422):
            raise ModelUnavailableError(
                "HTTP %d for model %s: %s" % (response.status_code, model,
                                              body_text[:300]))
        if response.status_code != 200:
            raise RetryableAPIError("HTTP %d from %s" % (response.status_code,
                                                         provider.name))

        data = _lenient_json(body_text)
        if not isinstance(data, dict):
            raise RetryableAPIError("unparseable response from %s" % provider.name)

        # Error payloads delivered with HTTP 200.
        if data.get("error"):
            err_text = json.dumps(data["error"], ensure_ascii=False)
            if _looks_like_model_error(err_text):
                raise ModelUnavailableError(
                    "model %s rejected: %s" % (model, err_text[:200]))
            if "auth" in err_text.lower() or "api_key" in err_text.lower():
                raise ProviderAuthError(err_text[:200])
            raise RetryableAPIError("provider error: %s" % err_text[:200])

        choices = data.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        content = (message.get("content") or "").strip()
        if not content:
            # Reasoning models sometimes exhaust max_tokens inside their
            # hidden scratchpad; the answer often still sits in there.
            reasoning = (message.get("reasoning_content")
                         or message.get("reasoning") or "").strip()
            if reasoning:
                log.debug("empty content; falling back to reasoning text",
                          extra={"provider": provider.name, "model": model})
                return reasoning
            raise RetryableAPIError("empty completion from %s/%s"
                                    % (provider.name, model))
        return content

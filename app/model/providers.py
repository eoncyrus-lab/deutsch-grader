from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass
class ModelResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class OpenAICompatProvider:
    def __init__(self, *, base_url: str, api_key: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        images: list[tuple[str, bytes]] | None = None,
        params: dict[str, Any] | None = None,
    ) -> ModelResponse:
        if not self.base_url or not self.api_key:
            raise ProviderError("model provider is not configured", retryable=False)
        content: str | list[dict[str, Any]] = user
        if images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": user}]
            for mime, data in images:
                encoded = base64.b64encode(data).decode("ascii")
                parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
            content = parts
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            **(params or {}),
        }
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(f"provider timeout: {exc}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider transport error: {exc}", retryable=True) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderError(f"provider status {response.status_code}: {response.text[:300]}", retryable=True, status=response.status_code)
        if response.status_code >= 400:
            raise ProviderError(f"provider status {response.status_code}: {response.text[:300]}", retryable=False, status=response.status_code)
        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
            if isinstance(text, list):
                text = "".join(part.get("text", "") for part in text)
        except Exception as exc:
            raise ProviderError(f"malformed provider response: {exc}", retryable=True) from exc
        usage = body.get("usage", {})
        return ModelResponse(
            text=text,
            model=body.get("model", model),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            raw=body,
        )


class FixtureProvider:
    """Offline provider for demos and tests. Every response is explicitly labelled."""

    def __init__(self, fixture_dir: Path | None = None):
        self.fixture_dir = fixture_dir or Path(__file__).resolve().parent.parent.parent / "demo"

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        images: list[tuple[str, bytes]] | None = None,
        params: dict[str, Any] | None = None,
        fixture_ref: str = "",
        stage: str = "",
    ) -> ModelResponse:
        path = self.fixture_dir / fixture_ref / f"{stage}.json"
        if not path.exists():
            raise ProviderError(f"fixture not found: {path}", retryable=False)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ModelResponse(text=json.dumps(payload, ensure_ascii=False), model="fixture-v1")

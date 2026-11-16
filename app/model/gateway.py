from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from app.config.routes import Route, RoutesConfig, load_routes
from app.model.providers import FixtureProvider, ModelResponse, OpenAICompatProvider, ProviderError
from app.settings import settings


class ProviderUnavailable(RuntimeError):
    pass


class QuotaExhausted(RuntimeError):
    def __init__(self, next_at: str | None):
        super().__init__(f"quota exhausted until {next_at or 'unknown'}")
        self.next_at = next_at


class UsageSink(Protocol):
    def reserve(self, stage: str, estimated_units: float, run_id: str | None, model: str) -> str: ...

    def settle(self, reservation_id: str, actual_units: float, cost: float = 0.0) -> None: ...

    def release(self, reservation_id: str) -> None: ...


@dataclass
class GatewayResult:
    data: dict[str, Any]
    response: ModelResponse
    route_name: str
    family: str
    attempts: int
    latency_ms: int


FENCE = chr(96) * 3


def parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith(FENCE):
        cleaned = re.sub(r"^" + FENCE + r"[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*" + FENCE + r"$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model did not return JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("model JSON must be an object")
    return value


class ModelGateway:
    def __init__(
        self,
        routes: RoutesConfig | None = None,
        *,
        fixture_mode: bool | None = None,
        usage: UsageSink | None = None,
        run_id: str | None = None,
    ):
        self.routes = routes or load_routes()
        self.fixture_mode = (
            fixture_mode
            if fixture_mode is not None
            else os.environ.get("DG_FIXTURE_MODE", "").lower() in {"1", "true", "yes"}
        )
        self.usage = usage
        self.run_id = run_id
        self._fixture = FixtureProvider()
        self._http: dict[str, OpenAICompatProvider] = {}

    def route_for(self, stage: str, task_routes: dict[str, str]) -> Route:
        name = task_routes.get(stage, "")
        if self.fixture_mode:
            return self.routes.routes["fixture"]
        route = self.routes.routes.get(name)
        if route is None:
            raise ProviderUnavailable(f"unknown route {name} for {stage}")
        return route

    def validate_families(self, task_routes: dict[str, str]) -> None:
        if self.fixture_mode:
            return
        s2 = self.routes.family(task_routes.get("s2", ""))
        s2b = self.routes.family(task_routes.get("s2b", ""))
        if not s2 or not s2b or s2 == s2b:
            raise ProviderUnavailable("S2 and S2b must use different model families")

    def _provider(self, route: Route) -> OpenAICompatProvider | FixtureProvider:
        if route.provider == "fixture" or self.fixture_mode:
            return self._fixture
        base_url = os.environ.get(route.base_url_env, "")
        api_key = os.environ.get(route.api_key_env, "")
        cache_key = f"{base_url}|{route.model}"
        if cache_key not in self._http:
            self._http[cache_key] = OpenAICompatProvider(base_url=base_url, api_key=api_key, timeout=settings.llm_timeout)
        return self._http[cache_key]

    def complete(
        self,
        *,
        stage: str,
        task_routes: dict[str, str],
        system: str,
        user: str,
        images: list[tuple[str, bytes]] | None = None,
        fixture_ref: str = "",
        estimated_tokens: int = 1500,
    ) -> GatewayResult:
        route = self.route_for(stage, task_routes)
        provider = self._provider(route)
        reservation = None
        if self.usage:
            reservation = self.usage.reserve(stage, estimated_tokens, self.run_id, route.model)
        attempts = 0
        started = time.monotonic()
        last_error: Exception | None = None
        response: ModelResponse | None = None
        for attempt in range(3):
            attempts = attempt + 1
            try:
                if isinstance(provider, FixtureProvider):
                    response = provider.complete(
                        model=route.model,
                        system=system,
                        user=user,
                        images=images,
                        params=route.params,
                        fixture_ref=fixture_ref,
                        stage=stage,
                    )
                else:
                    response = provider.complete(
                        model=route.model,
                        system=system,
                        user=user,
                        images=images,
                        params=route.params,
                    )
                break
            except ProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt == 2:
                    if reservation and self.usage:
                        self.usage.release(reservation)
                    raise
                time.sleep(0.4 * (2**attempt))
        if response is None:
            if reservation and self.usage:
                self.usage.release(reservation)
            raise ProviderUnavailable(str(last_error))
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            data = parse_json(response.text)
        except ValueError:
            if reservation and self.usage:
                self.usage.release(reservation)
            raise
        if self.usage and reservation:
            self.usage.settle(reservation, max(response.total_tokens, 1), 0.0)
        return GatewayResult(
            data=data,
            response=response,
            route_name=route.model,
            family=route.family,
            attempts=attempts,
            latency_ms=latency_ms,
        )

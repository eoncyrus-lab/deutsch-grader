from __future__ import annotations

from pathlib import Path

import yaml
from typing import Literal

from pydantic import BaseModel, Field


class Route(BaseModel):
    provider: Literal["openai_compat", "fixture"] = "openai_compat"
    model: str
    family: str
    base_url_env: str = "DG_LLM_BASE_URL"
    api_key_env: str = "DG_LLM_API_KEY"
    supports_vision: bool = False
    params: dict = Field(default_factory=dict)
    fingerprint: str = ""


class RoutesConfig(BaseModel):
    routes: dict[str, Route]

    def family(self, name: str) -> str:
        route = self.routes.get(name)
        return route.family if route else ""


def load_routes(path: str | Path | None = None) -> RoutesConfig:
    path = Path(path or Path(__file__).resolve().parent.parent.parent / "configs" / "models" / "routes.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RoutesConfig.model_validate(raw)

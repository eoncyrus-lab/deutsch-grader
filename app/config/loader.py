from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.config.models import TaskConfig


class ConfigError(ValueError):
    pass


ROOT = Path(__file__).resolve().parent.parent.parent
TASK_DIR = ROOT / "configs" / "tasks"


def config_hash(raw: dict) -> str:
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_task_config(path: str | Path) -> TaskConfig:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    try:
        return TaskConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid task config {path.name}: {exc}") from exc


def load_all_task_configs(directory: Path | None = None) -> list[TaskConfig]:
    directory = directory or TASK_DIR
    configs = []
    for path in sorted(directory.glob("*.yaml")):
        configs.append(load_task_config(path))
    return configs


def config_to_yaml(config: TaskConfig) -> str:
    return yaml.safe_dump(config.model_dump(by_alias=True), allow_unicode=True, sort_keys=False)

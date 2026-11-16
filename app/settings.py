from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    env: str
    db_path: Path
    secret_key: str
    cookie_secure: bool
    storage_dir: Path
    storage_key: str
    base_url: str
    llm_base_url: str
    llm_api_key: str
    llm_timeout: float
    llm_b_base_url: str
    llm_b_api_key: str
    session_hours: int = 12
    max_image_bytes: int = 12 * 1024 * 1024
    app_root: Path = Path(__file__).resolve().parent.parent


def load_settings() -> Settings:
    root = Path(__file__).resolve().parent.parent
    _load_dotenv(root / ".env")
    env = os.environ.get("DG_ENV", "dev")
    secret = os.environ.get("DG_SECRET_KEY", "")
    if not secret and env != "dev":
        raise RuntimeError("DG_SECRET_KEY is required outside dev")
    if not secret:
        secret = "dev-only-secret-change-me-please-32b"
    db_path = Path(os.environ.get("DG_DB_PATH", str(root / "data" / "app.db"))).expanduser()
    storage = Path(os.environ.get("DG_STORAGE_DIR", str(root / "data" / "objects"))).expanduser()
    return Settings(
        env=env,
        db_path=db_path,
        secret_key=secret,
        cookie_secure=_bool("DG_COOKIE_SECURE", False),
        storage_dir=storage,
        storage_key=os.environ.get("DG_STORAGE_KEY", ""),
        base_url=os.environ.get("DG_BASE_URL", "http://127.0.0.1:8000"),
        llm_base_url=os.environ.get("DG_LLM_BASE_URL", ""),
        llm_api_key=os.environ.get("DG_LLM_API_KEY", ""),
        llm_timeout=float(os.environ.get("DG_LLM_TIMEOUT_SECONDS", "120")),
        llm_b_base_url=os.environ.get("DG_LLM_B_BASE_URL", ""),
        llm_b_api_key=os.environ.get("DG_LLM_B_API_KEY", ""),
    )


settings = load_settings()

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from app import db


class QuotaExhausted(RuntimeError):
    def __init__(self, next_at: str | None):
        super().__init__(f"quota exhausted until {next_at or 'unknown'}")
        self.next_at = next_at


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def quota_config() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "configs" / "quota" / "sources.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def seed_sources(tenant_id: str) -> None:
    config = quota_config()
    conn = db.connect()
    now = _now()
    try:
        for name, raw in config["sources"].items():
            existing = db.fetch_one(conn, "SELECT id FROM quota_source WHERE tenant_id = ? AND name = ?", (tenant_id, name))
            if existing:
                continue
            reset_at = now + timedelta(seconds=int(raw["window_seconds"]))
            db.insert(conn, "quota_source", {
                "id": db.new_id(),
                "tenant_id": tenant_id,
                "name": name,
                "provider": raw.get("provider", "openai"),
                "unit": raw.get("unit", "token"),
                "total_units": float(raw["total_units"]),
                "window_seconds": int(raw["window_seconds"]),
                "window_start": _iso(now),
                "reset_at": _iso(reset_at),
                "concurrency": int(raw.get("concurrency", 1)),
                "rate_per_minute": int(raw.get("rate_per_minute", 60)),
                "terms_ok": 1 if raw.get("terms_ok") else 0,
                "fallback_order": int(raw.get("fallback_order", 100)),
                "pay_as_you_go": 1 if raw.get("pay_as_you_go") else 0,
                "monthly_budget": raw.get("monthly_budget"),
                "price_per_1k": float(raw.get("price_per_1k", 0)),
                "enabled": 1,
            })
    finally:
        conn.close()


class QuotaManager:
    """Reserve before a call, settle actual usage after it. Never overspends a window."""

    def __init__(self, tenant_id: str, *, fixture_mode: bool | None = None, db_path: Path | None = None):
        self.tenant_id = tenant_id
        self.fixture_mode = (
            fixture_mode
            if fixture_mode is not None
            else os.environ.get("DG_FIXTURE_MODE", "").lower() in {"1", "true", "yes"}
        )
        self.db_path = db_path
        self.config = quota_config()

    def _order(self) -> list[str]:
        key = "fixture" if self.fixture_mode else "default"
        return list(self.config["stage_sources"][key])

    def _conn(self):
        return db.connect(self.db_path)

    def _roll(self, conn, source: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        reset_at = _parse(source["reset_at"])
        if reset_at > now:
            return source
        step = timedelta(seconds=int(source["window_seconds"]))
        window_start = _parse(source["window_start"])
        while reset_at <= now:
            window_start = reset_at
            reset_at += step
        conn.execute(
            "UPDATE quota_source SET used_units = 0, reserved_units = 0, window_start = ?, reset_at = ? WHERE id = ? AND tenant_id = ?",
            (_iso(window_start), _iso(reset_at), source["id"], self.tenant_id),
        )
        source.update({"used_units": 0.0, "reserved_units": 0.0, "window_start": _iso(window_start), "reset_at": _iso(reset_at)})
        return source

    def reserve(self, stage: str, estimated_units: float, run_id: str | None, model: str) -> str:
        conn = self._conn()
        try:
            with db.transaction(conn):
                sources = db.fetch_all(
                    conn,
                    "SELECT * FROM quota_source WHERE tenant_id = ? AND enabled = 1 AND terms_ok = 1 "
                    "AND (expires_at IS NULL OR expires_at > ?) ORDER BY fallback_order, name",
                    (self.tenant_id, _iso(_now())),
                )
                by_name = {s["name"]: s for s in sources}
                ordered = [by_name[name] for name in self._order() if name in by_name]
                next_reset = None
                for source in ordered:
                    source = self._roll(conn, source)
                    if source["pay_as_you_go"] and source["monthly_budget"] is not None:
                        if float(source["monthly_spent"]) >= float(source["monthly_budget"]):
                            continue
                    available = float(source["total_units"]) - float(source["used_units"]) - float(source["reserved_units"])
                    if available >= estimated_units:
                        conn.execute(
                            "UPDATE quota_source SET reserved_units = reserved_units + ? WHERE id = ? AND tenant_id = ?",
                            (estimated_units, source["id"], self.tenant_id),
                        )
                        usage_id = db.new_id()
                        db.insert(conn, "quota_usage", {
                            "id": usage_id,
                            "tenant_id": self.tenant_id,
                            "run_id": run_id,
                            "stage": stage,
                            "source_id": source["id"],
                            "model": model,
                            "units": 0,
                            "reserved_units": estimated_units,
                            "cost": 0,
                            "status": "reserved",
                            "created_at": db.utcnow(),
                        })
                        return usage_id
                    reset_at = _parse(source["reset_at"])
                    next_reset = reset_at if next_reset is None else min(next_reset, reset_at)
                raise QuotaExhausted(_iso(next_reset) if next_reset else None)
        finally:
            conn.close()

    def settle(self, reservation_id: str, actual_units: float, cost: float = 0.0) -> None:
        conn = self._conn()
        try:
            with db.transaction(conn):
                usage = db.fetch_one(conn, "SELECT * FROM quota_usage WHERE id = ? AND tenant_id = ?", (reservation_id, self.tenant_id))
                if not usage or usage["status"] != "reserved":
                    return
                source = db.fetch_one(conn, "SELECT * FROM quota_source WHERE id = ? AND tenant_id = ?", (usage["source_id"], self.tenant_id))
                if not source:
                    return
                price = float(source["price_per_1k"])
                cost = cost or (actual_units / 1000.0) * price
                conn.execute(
                    "UPDATE quota_source SET reserved_units = MAX(0, reserved_units - ?), used_units = used_units + ?, monthly_spent = monthly_spent + ? WHERE id = ? AND tenant_id = ?",
                    (usage["reserved_units"], actual_units, cost, source["id"], self.tenant_id),
                )
                conn.execute(
                    "UPDATE quota_usage SET status = 'settled', units = ?, cost = ?, settled_at = ? WHERE id = ? AND tenant_id = ?",
                    (actual_units, cost, db.utcnow(), reservation_id, self.tenant_id),
                )
        finally:
            conn.close()

    def release(self, reservation_id: str) -> None:
        conn = self._conn()
        try:
            with db.transaction(conn):
                usage = db.fetch_one(conn, "SELECT * FROM quota_usage WHERE id = ? AND tenant_id = ?", (reservation_id, self.tenant_id))
                if not usage or usage["status"] != "reserved":
                    return
                conn.execute(
                    "UPDATE quota_source SET reserved_units = MAX(0, reserved_units - ?) WHERE id = ? AND tenant_id = ?",
                    (usage["reserved_units"], usage["source_id"], self.tenant_id),
                )
                conn.execute("UPDATE quota_usage SET status = 'released' WHERE id = ? AND tenant_id = ?", (reservation_id, self.tenant_id))
        finally:
            conn.close()

    def snapshot(self) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            rows = db.fetch_all(conn, "SELECT * FROM quota_source WHERE tenant_id = ? ORDER BY fallback_order, name", (self.tenant_id,))
            for row in rows:
                row["available"] = max(0.0, row["total_units"] - row["used_units"] - row["reserved_units"])
            return rows
        finally:
            conn.close()

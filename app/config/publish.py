from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from app import db
from app.config.loader import config_hash, load_task_config
from app.pipeline import audit


class PublishError(ValueError):
    pass


def create_draft(conn, tenant_id: str, path: Path, actor: str | None = None) -> str:
    config = load_task_config(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    existing = db.fetch_one(
        conn, "SELECT id FROM task_config WHERE tenant_id = ? AND task_id = ? AND version = ?",
        (tenant_id, config.task_id, config.version),
    )
    if existing:
        return existing["id"]
    config_id = db.new_id()
    db.insert(conn, "task_config", {
        "id": config_id, "tenant_id": tenant_id, "task_id": config.task_id, "version": config.version,
        "status": "draft", "config_json": path.read_text(encoding="utf-8"),
        "config_hash": config_hash(raw), "rubric_id": config.rubric_id,
        "rubric_version": config.rubric_version, "created_by": actor,
        "created_at": db.utcnow(), "activated_at": None, "gate_report_id": None,
    })
    audit(conn, tenant_id, actor, "config.draft", "task_config", config_id, {"task_id": config.task_id})
    return config_id


def record_gate(conn, tenant_id: str, config_id: str, status: str, dataset_hash: str, metrics: dict[str, Any]) -> str:
    if status not in {"pass", "fail", "demo"}:
        raise PublishError("gate status must be pass, fail or demo")
    config = db.fetch_one(conn, "SELECT * FROM task_config WHERE id = ? AND tenant_id = ?", (config_id, tenant_id))
    if not config:
        raise PublishError("config not found")
    report_id = db.new_id()
    db.insert(conn, "gate_report", {
        "id": report_id, "tenant_id": tenant_id, "task_config_id": config_id, "status": status,
        "dataset_hash": dataset_hash, "metrics": json.dumps(metrics, ensure_ascii=False, sort_keys=True),
        "created_at": db.utcnow(),
    })
    conn.execute("UPDATE task_config SET gate_report_id = ? WHERE id = ? AND tenant_id = ?", (report_id, config_id, tenant_id))
    return report_id


def activate(conn, tenant_id: str, config_id: str, actor: str | None = None) -> None:
    config = db.fetch_one(conn, "SELECT * FROM task_config WHERE id = ? AND tenant_id = ?", (config_id, tenant_id))
    if not config:
        raise PublishError("config not found")
    if not config["gate_report_id"]:
        raise PublishError("config has no gate report")
    report = db.fetch_one(conn, "SELECT * FROM gate_report WHERE id = ? AND tenant_id = ?", (config["gate_report_id"], tenant_id))
    if not report or report["status"] != "pass":
        raise PublishError("gate report did not pass")
    now = db.utcnow()
    conn.execute(
        "UPDATE task_config SET status = 'retired' WHERE tenant_id = ? AND task_id = ? AND status = 'active' AND id != ?",
        (tenant_id, config["task_id"], config_id),
    )
    conn.execute(
        "UPDATE task_config SET status = 'active', activated_at = ? WHERE id = ? AND tenant_id = ?",
        (now, config_id, tenant_id),
    )
    audit(conn, tenant_id, actor, "config.activate", "task_config", config_id, {"version": config["version"]})


def rollback(conn, tenant_id: str, task_id: str, version: str, actor: str | None = None) -> None:
    target = db.fetch_one(conn, "SELECT * FROM task_config WHERE tenant_id = ? AND task_id = ? AND version = ?", (tenant_id, task_id, version))
    if not target:
        raise PublishError("target version not found")
    if not target["gate_report_id"]:
        raise PublishError("target version has no gate report")
    activate(conn, tenant_id, target["id"], actor)
    audit(conn, tenant_id, actor, "config.rollback", "task_config", target["id"], {"version": version})

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app import db
from app.pipeline import audit
from app.storage import ObjectStore


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")


def purge_expired(conn, tenant_id: str, image_days: int = 120, stage_days: int = 30) -> dict:
    store = ObjectStore(root=db.settings.storage_dir)
    images = db.fetch_all(
        conn,
        "SELECT i.* FROM submission_image i JOIN transcript t ON t.submission_id = i.submission_id "
        "WHERE i.tenant_id = ? AND i.deleted_at IS NULL AND t.confirmed_at IS NOT NULL AND t.confirmed_at < ?",
        (tenant_id, _cutoff(image_days)),
    )
    for image in images:
        store.delete(tenant_id, image["object_key"])
        conn.execute("UPDATE submission_image SET deleted_at = ? WHERE id = ? AND tenant_id = ?", (db.utcnow(), image["id"], tenant_id))
    conn.execute(
        "UPDATE stage_output SET output = NULL WHERE tenant_id = ? AND created_at < ? AND output IS NOT NULL",
        (tenant_id, _cutoff(stage_days)),
    )
    audit(conn, tenant_id, "retention", "retention.purge", "tenant", tenant_id, {"images": len(images), "stage_days": stage_days})
    return {"images_deleted": len(images)}


def revoke_consent(conn, tenant_id: str, student_id: str, actor: str) -> None:
    consent = db.fetch_one(conn, "SELECT * FROM guardian_consent WHERE tenant_id = ? AND student_id = ? AND revoked_at IS NULL ORDER BY granted_at DESC LIMIT 1", (tenant_id, student_id))
    if not consent:
        raise ValueError("active consent not found")
    conn.execute("UPDATE guardian_consent SET revoked_at = ? WHERE id = ? AND tenant_id = ?", (db.utcnow(), consent["id"], tenant_id))
    conn.execute("DELETE FROM profile_pattern WHERE tenant_id = ? AND student_id = ?", (tenant_id, student_id))
    conn.execute("DELETE FROM pattern_evidence WHERE tenant_id = ? AND student_id = ?", (tenant_id, student_id))
    audit(conn, tenant_id, actor, "consent.revoke", "student", student_id, {"consent_id": consent["id"]})


def run_retention(tenant_id: str) -> dict:
    conn = db.connect()
    try:
        return purge_expired(conn, tenant_id)
    finally:
        conn.close()

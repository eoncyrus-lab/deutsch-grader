from __future__ import annotations

import json
from pathlib import Path

import yaml

from app import db
from app.auth import hash_password
from app.config.loader import config_hash
from app.pipeline import confirm_and_queue, run_grading, start_transcription
from app.quota import seed_sources


DEMO_PASSWORD = "Demo2026!"
TENANT_ID = "demo-tenant"


def bootstrap() -> dict:
    db.init_db()
    conn = db.connect()
    try:
        existing = db.fetch_one(conn, "SELECT id FROM tenant WHERE id = ?", (TENANT_ID,))
        if existing:
            return {"seeded": False}
        now = db.utcnow()
        db.insert(conn, "tenant", {"id": TENANT_ID, "name": "演示学校", "region": "cn", "data_policy": "{}", "created_at": now})
        school_id = db.new_id()
        db.insert(conn, "school", {"id": school_id, "tenant_id": TENANT_ID, "name": "演示中学"})
        class_id = db.new_id()
        db.insert(conn, "class", {"id": class_id, "tenant_id": TENANT_ID, "school_id": school_id, "code": "G8-A", "grade": "初二", "term": "2026-秋"})
        users = [
            ("teacher@demo.local", "王老师", "teacher"),
            ("rubric@demo.local", "教研组长", "rubric_owner"),
            ("admin@demo.local", "管理员", "admin"),
            ("dpo@demo.local", "数据保护负责人", "dpo"),
        ]
        for email, name, role in users:
            db.insert(conn, "app_user", {
                "id": db.new_id(), "tenant_id": TENANT_ID, "email": email, "display_name": name,
                "role": role, "password_hash": hash_password(DEMO_PASSWORD), "disabled": 0, "created_at": now,
            })
        students = []
        for pseudonym, birth_year in (("S-001", 2012), ("S-002", 2011)):
            sid = db.new_id()
            students.append((sid, pseudonym))
            db.insert(conn, "student", {
                "id": sid, "tenant_id": TENANT_ID, "class_id": class_id, "pseudonym": pseudonym,
                "birth_year": birth_year, "under_14": 1,
            })
            db.insert(conn, "guardian_consent", {
                "id": db.new_id(), "tenant_id": TENANT_ID, "student_id": sid,
                "scope": json.dumps({"grading": True, "profile": True, "tutoring": False}),
                "notice_version": "2026-09", "purpose_version": "2026-09",
                "granted_at": now, "expires_at": None, "revoked_at": None,
                "evidence_ref": f"paper-{pseudonym}",
            })
        config_path = Path(__file__).resolve().parent.parent / "configs" / "tasks" / "narrative-travel-a2.yaml"
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config_id = db.new_id()
        db.insert(conn, "task_config", {
            "id": config_id, "tenant_id": TENANT_ID, "task_id": raw["task_id"], "version": raw["version"],
            "status": "active", "config_json": config_path.read_text(encoding="utf-8"),
            "config_hash": config_hash(raw), "rubric_id": raw["rubric_id"],
            "rubric_version": raw["rubric_version"], "created_by": None,
            "created_at": now, "activated_at": now, "gate_report_id": None,
        })
        assignment_id = db.new_id()
        db.insert(conn, "assignment", {
            "id": assignment_id, "tenant_id": TENANT_ID, "class_id": class_id, "task_config_id": config_id,
            "topic": raw["topic_default"], "due_at": None, "created_at": now,
        })
        seed_sources(TENANT_ID)
        demo = []
        for index, (ref, student) in enumerate(zip(("travel-1", "travel-2"), students)):
            submission_id = db.new_id()
            db.insert(conn, "submission", {
                "id": submission_id, "tenant_id": TENANT_ID, "assignment_id": assignment_id,
                "student_id": student[0], "task_config_id": config_id, "status": "uploaded",
                "source": f"demo:{ref}", "created_by": "bootstrap", "created_at": now, "updated_at": now,
            })
            start_transcription(submission_id, TENANT_ID)
            if index == 1:
                transcript = db.fetch_one(
                    conn,
                    "SELECT * FROM transcript WHERE submission_id = ? AND tenant_id = ? ORDER BY version DESC LIMIT 1",
                    (submission_id, TENANT_ID),
                )
                _, _ = confirm_and_queue(
                    submission_id, TENANT_ID, "bootstrap", transcript["title"],
                    json.loads(transcript["lines"]), json.loads(transcript["illegible"]),
                )
                run = db.fetch_one(
                    conn,
                    "SELECT * FROM run WHERE submission_id = ? AND tenant_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (submission_id, TENANT_ID),
                )
                run_grading(run["id"])
                conn.execute("UPDATE job SET status = 'done', updated_at = ? WHERE tenant_id = ? AND kind = 'grade'", (db.utcnow(), TENANT_ID))
            demo.append(submission_id)
        return {"seeded": True, "submissions": demo, "config_id": config_id}
    finally:
        conn.close()

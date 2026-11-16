from __future__ import annotations

import argparse
import getpass
from datetime import datetime
from pathlib import Path

import yaml

from app import db
from app.auth import hash_password
from app.config.loader import config_hash, load_task_config


ROLES = {"teacher", "rubric_owner", "admin", "dpo", "student"}


def create_tenant(conn, tenant_id: str, name: str) -> None:
    if db.fetch_one(conn, "SELECT id FROM tenant WHERE id = ?", (tenant_id,)):
        print(f"tenant already exists: {tenant_id}")
        return
    db.insert(conn, "tenant", {
        "id": tenant_id, "name": name, "region": "cn", "data_policy": "{}", "created_at": db.utcnow(),
    })
    print(f"created tenant: {tenant_id}")


def create_user(conn, tenant_id: str, email: str, name: str, role: str, password: str) -> None:
    if role not in ROLES:
        raise SystemExit(f"role must be one of {sorted(ROLES)}")
    if not db.fetch_one(conn, "SELECT id FROM tenant WHERE id = ?", (tenant_id,)):
        raise SystemExit(f"tenant not found: {tenant_id}")
    if db.fetch_one(conn, "SELECT id FROM app_user WHERE tenant_id = ? AND email = ?", (tenant_id, email)):
        raise SystemExit(f"user already exists: {email}")
    db.insert(conn, "app_user", {
        "id": db.new_id(), "tenant_id": tenant_id, "email": email, "display_name": name,
        "role": role, "password_hash": hash_password(password), "disabled": 0, "created_at": db.utcnow(),
    })
    print(f"created user: {email} ({role}) in {tenant_id}")


def reset_password(conn, tenant_id: str, email: str, password: str) -> None:
    user = db.fetch_one(conn, "SELECT id FROM app_user WHERE tenant_id = ? AND email = ?", (tenant_id, email))
    if not user:
        raise SystemExit(f"user not found: {email}")
    conn.execute("UPDATE app_user SET password_hash = ? WHERE id = ? AND tenant_id = ?", (hash_password(password), user["id"], tenant_id))
    print(f"password updated for {email}")


def list_users(conn, tenant_id: str) -> None:
    rows = db.fetch_all(conn, "SELECT email, display_name, role, disabled FROM app_user WHERE tenant_id = ? ORDER BY email", (tenant_id,))
    for row in rows:
        print(f"{row['email']:30} {row['role']:14} {row['display_name']}  disabled={row['disabled']}")


def init_school(
    conn,
    tenant_id: str,
    tenant_name: str,
    class_code: str,
    grade: str,
    term: str,
    config_path: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
) -> dict:
    create_tenant(conn, tenant_id, tenant_name)
    school = db.fetch_one(conn, "SELECT id FROM school WHERE tenant_id = ? ORDER BY name LIMIT 1", (tenant_id,))
    if school:
        school_id = school["id"]
    else:
        school_id = db.new_id()
        db.insert(conn, "school", {"id": school_id, "tenant_id": tenant_id, "name": tenant_name})
    klass = db.fetch_one(conn, "SELECT id FROM class WHERE tenant_id = ? AND code = ?", (tenant_id, class_code))
    if klass:
        class_id = klass["id"]
    else:
        class_id = db.new_id()
        db.insert(conn, "class", {
            "id": class_id, "tenant_id": tenant_id, "school_id": school_id,
            "code": class_code, "grade": grade, "term": term,
        })
    path = Path(config_path)
    config = load_task_config(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    task = db.fetch_one(
        conn, "SELECT id FROM task_config WHERE tenant_id = ? AND task_id = ? AND version = ?",
        (tenant_id, config.task_id, config.version),
    )
    if task:
        task_config_id = task["id"]
    else:
        task_config_id = db.new_id()
        db.insert(conn, "task_config", {
            "id": task_config_id, "tenant_id": tenant_id, "task_id": config.task_id,
            "version": config.version, "status": "active",
            "config_json": path.read_text(encoding="utf-8"), "config_hash": config_hash(raw),
            "rubric_id": config.rubric_id, "rubric_version": config.rubric_version,
            "created_by": "admin-cli", "created_at": db.utcnow(),
            "activated_at": db.utcnow(), "gate_report_id": None,
        })
    assignment = db.fetch_one(
        conn, "SELECT id FROM assignment WHERE tenant_id = ? AND class_id = ? AND task_config_id = ?",
        (tenant_id, class_id, task_config_id),
    )
    if not assignment:
        db.insert(conn, "assignment", {
            "id": db.new_id(), "tenant_id": tenant_id, "class_id": class_id,
            "task_config_id": task_config_id, "topic": config.topic_default,
            "due_at": None, "created_at": db.utcnow(),
        })
    create_user(conn, tenant_id, admin_email, admin_name, "admin", admin_password)
    print(f"school initialized: tenant={tenant_id} class={class_code} task={config.task_id}@{config.version}")
    return {"tenant_id": tenant_id, "class_id": class_id, "task_config_id": task_config_id}


def add_student(conn, tenant_id: str, class_code: str, pseudonym: str, birth_year: int | None = None) -> str:
    klass = db.fetch_one(conn, "SELECT id FROM class WHERE tenant_id = ? AND code = ?", (tenant_id, class_code))
    if not klass:
        raise SystemExit(f"class not found: {class_code}")
    student_id = db.new_id()
    db.insert(conn, "student", {
        "id": student_id, "tenant_id": tenant_id, "class_id": klass["id"],
        "pseudonym": pseudonym, "birth_year": birth_year,
        "under_14": 1 if birth_year and datetime.now().year - birth_year < 14 else 0,
    })
    print(f"created student: {pseudonym}")
    return student_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Deutsch Grader administration")
    sub = parser.add_subparsers(dest="command", required=True)

    tenant = sub.add_parser("create-tenant")
    tenant.add_argument("--tenant", required=True)
    tenant.add_argument("--name", required=True)

    user = sub.add_parser("create-user")
    user.add_argument("--tenant", default="demo-tenant")
    user.add_argument("--email", required=True)
    user.add_argument("--name", required=True)
    user.add_argument("--role", default="teacher")
    user.add_argument("--password", default=None)

    reset = sub.add_parser("reset-password")
    reset.add_argument("--tenant", default="demo-tenant")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password", default=None)

    listing = sub.add_parser("list-users")
    listing.add_argument("--tenant", default="demo-tenant")

    school = sub.add_parser("init-school")
    school.add_argument("--tenant", required=True)
    school.add_argument("--tenant-name", required=True)
    school.add_argument("--class-code", required=True)
    school.add_argument("--grade", default="初二")
    school.add_argument("--term", default="2026-秋")
    school.add_argument("--config", default="configs/tasks/narrative-travel-a2.yaml")
    school.add_argument("--admin-email", required=True)
    school.add_argument("--admin-name", default="管理员")
    school.add_argument("--admin-password", default=None)

    student = sub.add_parser("add-student")
    student.add_argument("--tenant", required=True)
    student.add_argument("--class-code", required=True)
    student.add_argument("--pseudonym", required=True)
    student.add_argument("--birth-year", type=int, default=None)

    args = parser.parse_args()
    db.init_db()
    conn = db.connect()
    try:
        if args.command == "create-tenant":
            create_tenant(conn, args.tenant, args.name)
        elif args.command == "create-user":
            password = args.password or getpass.getpass("Password: ")
            if len(password) < 8:
                raise SystemExit("password must be at least 8 characters")
            create_user(conn, args.tenant, args.email, args.name, args.role, password)
        elif args.command == "reset-password":
            password = args.password or getpass.getpass("New password: ")
            if len(password) < 8:
                raise SystemExit("password must be at least 8 characters")
            reset_password(conn, args.tenant, args.email, password)
        elif args.command == "list-users":
            list_users(conn, args.tenant)
        elif args.command == "init-school":
            password = args.admin_password or getpass.getpass("Admin password: ")
            if len(password) < 8:
                raise SystemExit("password must be at least 8 characters")
            init_school(
                conn, args.tenant, args.tenant_name, args.class_code, args.grade, args.term,
                args.config, args.admin_email, args.admin_name, password,
            )
        elif args.command == "add-student":
            add_student(conn, args.tenant, args.class_code, args.pseudonym, args.birth_year)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

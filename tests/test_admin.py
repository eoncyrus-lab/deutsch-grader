from app import db
from app.admin import add_student, create_tenant, create_user, init_school, reset_password
from app.auth import verify_password


def test_admin_create_user_and_reset_password(db_env):
    conn = db.connect()
    try:
        create_tenant(conn, "school-a", "School A")
        create_user(conn, "school-a", "owner@example.com", "Owner", "admin", "Start123!")
        user = db.fetch_one(conn, "SELECT * FROM app_user WHERE email = ?", ("owner@example.com",))
        assert verify_password("Start123!", user["password_hash"])
        reset_password(conn, "school-a", "owner@example.com", "NewPass123!")
        user = db.fetch_one(conn, "SELECT * FROM app_user WHERE email = ?", ("owner@example.com",))
        assert verify_password("NewPass123!", user["password_hash"])
    finally:
        conn.close()


def test_init_school_and_add_student(db_env):
    conn = db.connect()
    try:
        result = init_school(
            conn, "school-b", "School B", "G8-B", "初二", "2026-秋",
            "configs/tasks/narrative-travel-a2.yaml", "admin@school-b.example", "Admin", "Start123!",
        )
        student_id = add_student(conn, "school-b", "G8-B", "S-101", 2012)
        task = db.fetch_one(conn, "SELECT * FROM task_config WHERE tenant_id = ?", ("school-b",))
        assert task["status"] == "active"
        assert db.fetch_one(conn, "SELECT id FROM student WHERE id = ?", (student_id,))
        assert result["task_config_id"] == task["id"]
    finally:
        conn.close()

import json

from app import db
from app.auth import hash_password
from app.pipeline import run_grading
from tests.conftest import login


def test_teacher_flow_and_csrf(client):
    user = login(client)
    headers = {"x-csrf-token": user["csrf"]}
    submissions = client.get("/api/submissions").json()
    ready = next(item for item in submissions if item["status"] == "ready_for_review")
    detail = client.get("/api/submissions/" + ready["id"]).json()
    assert detail["run"]["ai_total"] == 4.0
    error_id = detail["errors"][0]["id"]

    missing = client.post("/api/errors/" + error_id + "/review", json={"action": "reject", "reason": "AI 误判"})
    assert missing.status_code == 403

    reviewed = client.post("/api/errors/" + error_id + "/review", json={"action": "reject", "reason": "AI 误判"}, headers=headers)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["total"] == 4.0
    final = client.post("/api/submissions/" + ready["id"] + "/finalize", json={"reason": "AI 误判"}, headers=headers)
    assert final.status_code == 200, final.text
    after = client.get("/api/submissions/" + ready["id"]).json()
    assert after["submission"]["status"] == "finalized"
    assert after["run"]["final_total"] == 4.0
    export = client.get("/api/submissions/" + ready["id"] + "/export?format=json")
    assert export.status_code == 200
    payload = json.loads(export.text)
    assert "comments" in payload and "errors" in payload


def test_confirm_endpoint_queues_grading(client):
    user = login(client)
    headers = {"x-csrf-token": user["csrf"]}
    submissions = client.get("/api/submissions").json()
    waiting = next(item for item in submissions if item["status"] == "awaiting_confirmation")
    detail = client.get("/api/submissions/" + waiting["id"]).json()
    lines = [{"text": line["text"]} for line in detail["transcript"]["lines"]]
    response = client.post("/api/submissions/" + waiting["id"] + "/confirm", json={"title": detail["transcript"]["title"], "lines": lines}, headers=headers)
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    run_grading(run_id)
    detail = client.get("/api/submissions/" + waiting["id"]).json()
    assert detail["run"]["ai_total"] == 9.5


def test_students_endpoint_and_text_submission_queue(client):
    user = login(client)
    headers = {"x-csrf-token": user["csrf"]}
    students = client.get("/api/students").json()
    assert len(students) == 2
    assert all("pseudonym" in item for item in students)
    created = client.post(
        "/api/submissions/text",
        json={
            "student_id": students[0]["id"],
            "title": "Meine Reise",
            "text": "Ich habe nach Berlin gefahren. Danach haben wir Pizza gegessen.",
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    submission_id = created.json()["submission_id"]
    detail = client.get("/api/submissions/" + submission_id).json()
    assert detail["submission"]["status"] == "queued"
    assert detail["run"]["status"] == "pending"


def test_roles_and_cross_tenant(client):
    teacher = login(client)
    real_id = client.get("/api/submissions").json()[0]["id"]
    assert client.get("/api/metrics").status_code == 403
    rubric = login(client, "rubric@demo.local")
    assert client.get("/api/metrics").status_code == 200

    conn = db.connect()
    try:
        tenant_id = "other-tenant"
        db.insert(conn, "tenant", {"id": tenant_id, "name": "其他学校", "region": "cn", "data_policy": "{}", "created_at": db.utcnow()})
        db.insert(conn, "app_user", {
            "id": db.new_id(), "tenant_id": tenant_id, "email": "other@example.com", "display_name": "其他老师",
            "role": "teacher", "password_hash": hash_password("Other2026!"), "disabled": 0, "created_at": db.utcnow(),
        })
    finally:
        conn.close()
    other = login(client, "other@example.com", "Other2026!")
    submissions = client.get("/api/submissions").json()
    assert submissions == []
    detail = client.get("/api/submissions/" + real_id)
    assert detail.status_code == 404


def test_upload_requires_redacted_copy_before_transcription(client):
    user = login(client)
    headers = {"x-csrf-token": user["csrf"]}
    config_id = client.get("/api/configs").json()[0]["id"]
    conn = db.connect()
    try:
        student_id = db.fetch_one(conn, "SELECT id FROM student WHERE tenant_id = 'demo-tenant' AND pseudonym = 'S-001'")["id"]
    finally:
        conn.close()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 12
    uploaded = client.post(
        "/api/submissions",
        data={"student_id": student_id, "task_config_id": config_id},
        files=[("files", ("page.png", png, "image/png"))],
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    submission_id = uploaded.json()["submission_id"]
    detail = client.get("/api/submissions/" + submission_id).json()
    image_id = detail["images"][0]["id"]
    blocked = client.post("/api/submissions/" + submission_id + "/transcribe", headers=headers)
    assert blocked.status_code == 409
    redacted = client.post(
        "/api/submissions/" + submission_id + "/images/" + image_id + "/redacted-copy",
        files={"file": ("page-redacted.png", png, "image/png")},
        headers=headers,
    )
    assert redacted.status_code == 200, redacted.text
    queued = client.post("/api/submissions/" + submission_id + "/transcribe", headers=headers)
    assert queued.status_code == 200, queued.text

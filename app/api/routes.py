from __future__ import annotations

import json
import yaml
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app import db
from app.auth import create_session, require_csrf, require_role, session_user, verify_password
from app.pipeline import PipelineError, audit, confirm_and_queue, enqueue, load_config, start_transcription, transcript_sentences, transcript_text
from app.config.publish import PublishError, activate, record_gate
from app.quota import QuotaManager
from app.services.review import (
    ReviewError,
    add_missing_error,
    apply_error_action,
    finalize_run,
    recompute_reviewed,
    resolve_checklist,
    student_export,
)
from app.settings import settings
from app.storage import ObjectStore, StorageError
from app.grading.validation import SchemaError
from app.grading.text import word_count


router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    email: str
    password: str


class LineIn(BaseModel):
    text: str


class ConfirmIn(BaseModel):
    title: str | None = None
    lines: list[LineIn]
    illegible: list[Any] = Field(default_factory=list)


class TextIn(BaseModel):
    student_id: str
    text: str
    title: str | None = None
    assignment_id: str | None = None
    task_config_id: str | None = None


class ErrorActionIn(BaseModel):
    action: str
    reason: str = ""
    corrected: str | None = None


class AddErrorIn(BaseModel):
    run_id: str
    sid: int
    original: str
    corrected: str
    type: str = "其他"
    error_code: str = ""
    pattern: str = "teacher:added"
    rule: str = "教师补录"
    span: list[int] | None = None


class ChecklistIn(BaseModel):
    value: Any
    evidence: Any = None


class FinalizeIn(BaseModel):
    reason: str
    note: str | None = None
    language: float | None = None
    content: float | None = None
    structure: float | None = None
    total: float | None = None


class PositiveIn(BaseModel):
    pattern: str


class GateIn(BaseModel):
    status: str
    dataset_hash: str
    metrics: dict[str, Any] = Field(default_factory=dict)


def _user(request: Request) -> dict:
    return session_user(request)


def _mutate(request: Request, user: dict) -> None:
    require_csrf(request, user)


def _public(user: dict) -> dict:
    return {
        "id": user["id"], "email": user["email"], "display_name": user["display_name"],
        "role": user["role"], "tenant_id": user["tenant_id"], "csrf": user.get("csrf"),
    }


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, (PipelineError, ReviewError, SchemaError, PublishError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, StorageError):
        return HTTPException(status_code=500, detail="storage failure")
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    conn = db.connect()
    try:
        ip = request.client.host if request.client else "unknown"
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(timespec="seconds").replace("+00:00", "Z")
        recent = db.fetch_one(
            conn,
            "SELECT COUNT(*) AS n FROM login_attempt WHERE email = ? AND ip = ? AND ok = 0 AND created_at > ?",
            (body.email, ip, cutoff),
        )
        if recent and int(recent["n"]) >= 10:
            raise HTTPException(status_code=429, detail="too many login attempts")
        user = db.fetch_one(conn, "SELECT * FROM app_user WHERE email = ? AND disabled = 0", (body.email,))
        ok = bool(user and verify_password(body.password, user["password_hash"]))
        db.insert(conn, "login_attempt", {"email": body.email, "ip": ip, "ok": 1 if ok else 0, "created_at": db.utcnow()})
        if not ok:
            raise HTTPException(status_code=401, detail="invalid credentials")
        token, csrf, expires = create_session(conn, user["id"])
        response.set_cookie("dg_session", token, httponly=True, samesite="lax", secure=settings.cookie_secure, max_age=settings.session_hours * 3600)
        response.set_cookie("dg_csrf", csrf, httponly=False, samesite="lax", secure=settings.cookie_secure, max_age=settings.session_hours * 3600)
        return {"user": {**_public(user), "csrf": csrf}}
    finally:
        conn.close()


@router.post("/auth/logout")
def logout(request: Request, response: Response, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    conn = db.connect()
    try:
        conn.execute("DELETE FROM session WHERE user_id = ?", (user["id"],))
    finally:
        conn.close()
    response.delete_cookie("dg_session")
    response.delete_cookie("dg_csrf")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(_user)) -> dict:
    return {"user": _public(user)}


@router.get("/configs")
def configs(user: dict = Depends(_user)) -> list[dict]:
    conn = db.connect()
    try:
        rows = db.fetch_all(conn, "SELECT * FROM task_config WHERE tenant_id = ? AND status IN ('active','shadow') ORDER BY created_at DESC", (user["tenant_id"],))
        out = []
        for row in rows:
            raw = yaml.safe_load(row["config_json"])
            out.append({
                "id": row["id"], "task_id": row["task_id"], "version": row["version"], "status": row["status"],
                "name": raw.get("name"), "level": raw.get("level"), "rubric_id": row["rubric_id"],
            })
        return out
    finally:
        conn.close()


@router.post("/configs/{config_id}/gate")
def gate_config(config_id: str, body: GateIn, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "rubric_owner", "admin")
    conn = db.connect()
    try:
        report_id = record_gate(conn, user["tenant_id"], config_id, body.status, body.dataset_hash, body.metrics)
        return {"gate_report_id": report_id, "status": body.status}
    except Exception as exc:
        raise _fail(exc)
    finally:
        conn.close()


@router.post("/configs/{config_id}/activate")
def activate_config(config_id: str, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "rubric_owner", "admin")
    conn = db.connect()
    try:
        activate(conn, user["tenant_id"], config_id, user["id"])
        return {"ok": True}
    except Exception as exc:
        raise _fail(exc)
    finally:
        conn.close()


def _latest_run(conn, tenant_id: str, submission_id: str) -> dict | None:
    return db.fetch_one(
        conn,
        "SELECT * FROM run WHERE tenant_id = ? AND submission_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (tenant_id, submission_id),
    )


@router.get("/submissions")
def list_submissions(request: Request, user: dict = Depends(_user)) -> list[dict]:
    conn = db.connect()
    try:
        rows = db.fetch_all(
            conn,
            "SELECT s.*, st.pseudonym, tc.task_id, tc.version AS task_version "
            "FROM submission s LEFT JOIN student st ON st.id = s.student_id AND st.tenant_id = s.tenant_id "
            "LEFT JOIN task_config tc ON tc.id = s.task_config_id AND tc.tenant_id = s.tenant_id "
            "WHERE s.tenant_id = ? ORDER BY s.created_at DESC",
            (user["tenant_id"],),
        )
        out = []
        for row in rows:
            run = _latest_run(conn, user["tenant_id"], row["id"])
            out.append({
                "id": row["id"], "status": row["status"], "source": row["source"],
                "student": row["pseudonym"], "task_id": row["task_id"], "task_version": row["task_version"],
                "created_at": row["created_at"], "run_status": run["status"] if run else None,
                "ai_total": run["ai_total"] if run else None, "final_total": run["final_total"] if run else None,
            })
        return out
    finally:
        conn.close()


@router.get("/students")
def list_students(user: dict = Depends(_user)) -> list[dict]:
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        rows = db.fetch_all(
            conn,
            "SELECT s.id, s.pseudonym, s.under_14, c.code AS class_code, c.grade, "
            "  (SELECT COUNT(*) FROM guardian_consent g WHERE g.tenant_id = s.tenant_id AND g.student_id = s.id "
            "   AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at > ?)) AS active_consents "
            "FROM student s LEFT JOIN class c ON c.id = s.class_id AND c.tenant_id = s.tenant_id "
            "WHERE s.tenant_id = ? ORDER BY s.pseudonym",
            (db.utcnow(), user["tenant_id"]),
        )
        return rows
    finally:
        conn.close()


@router.get("/submissions/{submission_id}")
def submission_detail(submission_id: str, request: Request, user: dict = Depends(_user)) -> dict:
    conn = db.connect()
    try:
        submission = db.fetch_one(conn, "SELECT * FROM submission WHERE id = ? AND tenant_id = ?", (submission_id, user["tenant_id"]))
        if not submission:
            raise HTTPException(status_code=404, detail="submission not found")
        student = db.fetch_one(conn, "SELECT * FROM student WHERE id = ? AND tenant_id = ?", (submission["student_id"], user["tenant_id"]))
        task_row, config = load_config(conn, user["tenant_id"], submission["task_config_id"])
        run = _latest_run(conn, user["tenant_id"], submission_id)
        transcript = db.fetch_one(
            conn,
            "SELECT * FROM transcript WHERE submission_id = ? AND tenant_id = ? ORDER BY version DESC LIMIT 1",
            (submission_id, user["tenant_id"]),
        )
        lines = json.loads(transcript["lines"]) if transcript else []
        sentences = transcript_sentences(lines) if lines else []
        errors = db.fetch_all(conn, "SELECT * FROM error_item WHERE tenant_id = ? AND run_id = ? ORDER BY sid, span_start", (user["tenant_id"], run["id"])) if run else []
        checklist_rows = db.fetch_all(conn, "SELECT * FROM checklist_item WHERE tenant_id = ? AND run_id = ?", (user["tenant_id"], run["id"])) if run else []
        checklist = [{"key": r["key"], "value": json.loads(r["value"]), "evidence": json.loads(r["evidence"] or "null"), "valid": r["valid"]} for r in checklist_rows]
        scores = db.fetch_all(conn, "SELECT * FROM score WHERE tenant_id = ? AND run_id = ? ORDER BY computed_at DESC, seq DESC", (user["tenant_id"], run["id"])) if run else []
        score_map: dict[str, dict] = {}
        for score in scores:
            kind = score["kind"]
            if kind not in score_map:
                score_map[kind] = {
                    "language": score["language"], "content": score["content"], "structure": score["structure"],
                    "total": score["total"], "trace": json.loads(score["trace"]), "draft": bool(score["draft"]),
                }
        images = db.fetch_all(conn, "SELECT id, content_type, bytes, redacted_at, deleted_at FROM submission_image WHERE tenant_id = ? AND submission_id = ?", (user["tenant_id"], submission_id))
        comments = json.loads(run["comments"]) if run and run["comments"] else {}
        return {
            "submission": {
                "id": submission["id"], "status": submission["status"], "source": submission["source"],
                "student": student["pseudonym"] if student else None,
                "student_id": submission["student_id"], "task": config.name,
                "level": config.level, "created_at": submission["created_at"],
            },
            "run": {
                "id": run["id"], "status": run["status"], "stage": run["stage"], "error": run["error"],
                "ai_total": run["ai_total"], "reviewed_total": run["reviewed_total"], "final_total": run["final_total"],
                "pipeline_version": run["pipeline_version"],
            } if run else None,
            "transcript": {
                "id": transcript["id"], "version": transcript["version"], "title": transcript["title"],
                "lines": lines, "illegible": json.loads(transcript["illegible"] or "[]"),
                "confirmed": bool(transcript["confirmed_at"]), "model": transcript["model"],
            } if transcript else None,
            "sentences": sentences,
            "word_count": word_count(transcript_text(lines)) if lines else 0,
            "errors": errors,
            "checklist": checklist,
            "scores": score_map,
            "comments": comments,
            "images": images,
            "can_confirm": submission["status"] == "awaiting_confirmation",
        }
    finally:
        conn.close()


@router.post("/submissions/{submission_id}/confirm")
def confirm(submission_id: str, body: ConfirmIn, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        run_id, sentences = confirm_and_queue(
            submission_id, user["tenant_id"], user["id"], body.title,
            [{"text": line.text} for line in body.lines], body.illegible,
        )
        return {"run_id": run_id, "sentences": sentences}
    except Exception as exc:
        raise _fail(exc)
    finally:
        conn.close()


@router.post("/submissions/text")
def text_submission(body: TextIn, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        student = db.fetch_one(conn, "SELECT * FROM student WHERE id = ? AND tenant_id = ?", (body.student_id, user["tenant_id"]))
        if not student:
            raise HTTPException(status_code=404, detail="student not found")
        task_config_id = body.task_config_id
        if not task_config_id:
            task = db.fetch_one(conn, "SELECT id FROM task_config WHERE tenant_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1", (user["tenant_id"],))
            task_config_id = task["id"] if task else None
        if not task_config_id:
            raise HTTPException(status_code=400, detail="no active task config")
        assignment_id = body.assignment_id
        if not assignment_id:
            assignment = db.fetch_one(conn, "SELECT id FROM assignment WHERE tenant_id = ? AND task_config_id = ? ORDER BY created_at DESC LIMIT 1", (user["tenant_id"], task_config_id))
            assignment_id = assignment["id"] if assignment else None
        submission_id = db.new_id()
        now = db.utcnow()
        db.insert(conn, "submission", {
            "id": submission_id, "tenant_id": user["tenant_id"], "assignment_id": assignment_id,
            "student_id": body.student_id, "task_config_id": task_config_id, "status": "queued",
            "source": "text", "created_by": user["id"], "created_at": now, "updated_at": now,
        })
        lines = [{"text": line.strip()} for line in body.text.splitlines() if line.strip()]
        run_id, sentences = confirm_and_queue(
            submission_id, user["tenant_id"], user["id"], body.title, lines,
        )
        return {"submission_id": submission_id, "run_id": run_id, "sentences": sentences}
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise _fail(exc)
    finally:
        conn.close()


@router.post("/submissions")
async def upload_submission(
    request: Request,
    student_id: str = Form(...),
    task_config_id: str = Form(...),
    files: list[UploadFile] = File(...),
    user: dict = Depends(_user),
) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        student = db.fetch_one(conn, "SELECT * FROM student WHERE id = ? AND tenant_id = ?", (student_id, user["tenant_id"]))
        if not student:
            raise HTTPException(status_code=404, detail="student not found")
        submission_id = db.new_id()
        now = db.utcnow()
        db.insert(conn, "submission", {
            "id": submission_id, "tenant_id": user["tenant_id"], "assignment_id": None,
            "student_id": student_id, "task_config_id": task_config_id, "status": "uploaded",
            "source": "upload", "created_by": user["id"], "created_at": now, "updated_at": now,
        })
        store = ObjectStore(root=db.settings.storage_dir)
        for upload in files:
            data = await upload.read()
            if len(data) > settings.max_image_bytes:
                raise HTTPException(status_code=413, detail="image too large")
            signature_ok = data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG") or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
            if not signature_ok:
                raise HTTPException(status_code=400, detail="only JPEG, PNG or WebP images are accepted")
            stored = store.put(user["tenant_id"], data, upload.content_type or "application/octet-stream")
            db.insert(conn, "submission_image", {
                "id": db.new_id(), "tenant_id": user["tenant_id"], "submission_id": submission_id,
                "object_key": stored["object_key"], "sha256": stored["sha256"],
                "content_type": upload.content_type or "application/octet-stream", "bytes": stored["bytes"],
                "redacted_at": None, "deleted_at": None, "created_at": now,
            })
        audit(conn, user["tenant_id"], user["id"], "submission.upload", "submission", submission_id, {"files": len(files)})
        return {"submission_id": submission_id, "status": "uploaded", "next": "mark redacted copies, then transcribe"}
    finally:
        conn.close()


@router.post("/submissions/{submission_id}/images/{image_id}/redacted")
def mark_redacted(submission_id: str, image_id: str, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    raise HTTPException(status_code=409, detail="upload a redacted copy with /redacted-copy; the original image cannot be marked safe in place")


@router.post("/submissions/{submission_id}/images/{image_id}/redacted-copy")
async def upload_redacted_copy(
    submission_id: str,
    image_id: str,
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(_user),
) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        original = db.fetch_one(
            conn, "SELECT * FROM submission_image WHERE id = ? AND submission_id = ? AND tenant_id = ? AND deleted_at IS NULL",
            (image_id, submission_id, user["tenant_id"]),
        )
        if not original:
            raise HTTPException(status_code=404, detail="image not found")
        data = await file.read()
        if len(data) > settings.max_image_bytes:
            raise HTTPException(status_code=413, detail="image too large")
        signature_ok = data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG") or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
        if not signature_ok:
            raise HTTPException(status_code=400, detail="only JPEG, PNG or WebP images are accepted")
        store = ObjectStore(root=db.settings.storage_dir)
        stored = store.put(user["tenant_id"], data, file.content_type or "application/octet-stream")
        copy_id = db.new_id()
        now = db.utcnow()
        db.insert(conn, "submission_image", {
            "id": copy_id, "tenant_id": user["tenant_id"], "submission_id": submission_id,
            "object_key": stored["object_key"], "sha256": stored["sha256"],
            "content_type": file.content_type or "application/octet-stream", "bytes": stored["bytes"],
            "redacted_at": now, "deleted_at": None, "created_at": now,
        })
        conn.execute("UPDATE submission_image SET deleted_at = ? WHERE id = ? AND tenant_id = ?", (now, image_id, user["tenant_id"]))
        store.delete(user["tenant_id"], original["object_key"])
        audit(conn, user["tenant_id"], user["id"], "image.redacted_copy", "submission_image", copy_id, {"original": image_id})
        return {"image_id": copy_id, "redacted_at": now}
    finally:
        conn.close()


@router.post("/submissions/{submission_id}/transcribe")
def transcribe(submission_id: str, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        images = db.fetch_all(conn, "SELECT * FROM submission_image WHERE submission_id = ? AND tenant_id = ? AND deleted_at IS NULL", (submission_id, user["tenant_id"]))
        if not images or any(not image["redacted_at"] for image in images):
            raise HTTPException(status_code=409, detail="all images must have a confirmed redacted copy")
        run_id = start_transcription(submission_id, user["tenant_id"])
        return {"run_id": run_id}
    finally:
        conn.close()


@router.get("/submissions/{submission_id}/images/{image_id}")
def get_image(submission_id: str, image_id: str, user: dict = Depends(_user)) -> Response:
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        image = db.fetch_one(conn, "SELECT * FROM submission_image WHERE id = ? AND submission_id = ? AND tenant_id = ? AND deleted_at IS NULL", (image_id, submission_id, user["tenant_id"]))
        if not image:
            raise HTTPException(status_code=404, detail="image not found")
        data = ObjectStore(root=db.settings.storage_dir).get(user["tenant_id"], image["object_key"])
        return Response(content=data, media_type=image["content_type"], headers={"Cache-Control": "no-store"})
    except StorageError:
        raise HTTPException(status_code=500, detail="image unavailable")
    finally:
        conn.close()


@router.post("/errors/{error_id}/review")
def review_error(error_id: str, body: ErrorActionIn, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        return apply_error_action(conn, user["tenant_id"], user["id"], error_id, body.action, body.reason, body.corrected)
    except Exception as exc:
        raise _fail(exc)
    finally:
        conn.close()


@router.post("/errors")
def add_error(body: AddErrorIn, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        return add_missing_error(conn, user["tenant_id"], user["id"], body.model_dump())
    except Exception as exc:
        raise _fail(exc)
    finally:
        conn.close()


@router.post("/runs/{run_id}/recompute")
def recompute(run_id: str, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        return recompute_reviewed(conn, user["tenant_id"], run_id)
    except Exception as exc:
        raise _fail(exc)
    finally:
        conn.close()


@router.post("/runs/{run_id}/checklist/{key}")
def checklist_resolve(run_id: str, key: str, body: ChecklistIn, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        return resolve_checklist(conn, user["tenant_id"], user["id"], run_id, key, body.value, body.evidence)
    except Exception as exc:
        raise _fail(exc)
    finally:
        conn.close()


@router.post("/submissions/{submission_id}/finalize")
def finalize(submission_id: str, body: FinalizeIn, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        run = _latest_run(conn, user["tenant_id"], submission_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        return finalize_run(conn, user["tenant_id"], user["id"], run["id"], body.model_dump())
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise _fail(exc)
    finally:
        conn.close()


@router.get("/submissions/{submission_id}/export")
def export(submission_id: str, request: Request, format: str = "html", user: dict = Depends(_user)) -> Response:
    require_role(user, "teacher", "rubric_owner", "admin", "dpo")
    conn = db.connect()
    try:
        run = _latest_run(conn, user["tenant_id"], submission_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        body, media = student_export(
            conn, user["tenant_id"], run["id"], format,
            nonce=getattr(request.state, "csp_nonce", ""),
        )
        return Response(content=body, media_type=media)
    finally:
        conn.close()


@router.get("/profiles/{student_id}")
def profile(student_id: str, request: Request, user: dict = Depends(_user)) -> dict:
    require_role(user, "teacher", "rubric_owner", "admin", "dpo")
    conn = db.connect()
    try:
        student = db.fetch_one(conn, "SELECT * FROM student WHERE id = ? AND tenant_id = ?", (student_id, user["tenant_id"]))
        if not student:
            raise HTTPException(status_code=404, detail="student not found")
        patterns = db.fetch_all(conn, "SELECT * FROM profile_pattern WHERE tenant_id = ? AND student_id = ? ORDER BY rate DESC", (user["tenant_id"], student_id))
        return {"student": student["pseudonym"], "patterns": patterns}
    finally:
        conn.close()


@router.post("/profiles/{student_id}/positive")
def positive(student_id: str, body: PositiveIn, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "teacher", "rubric_owner", "admin")
    conn = db.connect()
    try:
        student = db.fetch_one(conn, "SELECT * FROM student WHERE id = ? AND tenant_id = ?", (student_id, user["tenant_id"]))
        if not student:
            raise HTTPException(status_code=404, detail="student not found")
        db.insert(conn, "pattern_evidence", {
            "id": db.new_id(), "tenant_id": user["tenant_id"], "student_id": student_id,
            "pattern": body.pattern, "kind": "correct_use", "run_id": None,
            "created_by": user["id"], "created_at": db.utcnow(),
        })
        conn.execute(
            "UPDATE profile_pattern SET positive_evidence = positive_evidence + 1, status = 'mastered', updated_at = ? "
            "WHERE tenant_id = ? AND student_id = ? AND pattern = ?",
            (db.utcnow(), user["tenant_id"], student_id, body.pattern),
        )
        audit(conn, user["tenant_id"], user["id"], "profile.positive", "student", student_id, {"pattern": body.pattern})
        return {"ok": True}
    finally:
        conn.close()


@router.get("/metrics")
def metrics(user: dict = Depends(_user)) -> dict:
    require_role(user, "rubric_owner", "admin", "dpo")
    conn = db.connect()
    try:
        statuses = db.fetch_all(conn, "SELECT status, COUNT(*) AS n FROM submission WHERE tenant_id = ? GROUP BY status", (user["tenant_id"],))
        graded = db.fetch_one(
            conn,
            "SELECT COUNT(*) AS n, AVG(final_total) AS avg_final, AVG(ai_total) AS avg_ai, "
            "SUM(CASE WHEN final_total IS NOT NULL AND ai_total IS NOT NULL AND final_total != ai_total THEN 1 ELSE 0 END) AS changed "
            "FROM run WHERE tenant_id = ? AND ai_total IS NOT NULL",
            (user["tenant_id"],),
        )
        errors = db.fetch_one(
            conn,
            "SELECT COUNT(*) AS n, SUM(CASE WHEN teacher_action IN ('reject','over_edit') THEN 1 ELSE 0 END) AS rejected, "
            "SUM(CASE WHEN certainty != '确定' THEN 1 ELSE 0 END) AS downgraded FROM error_item WHERE tenant_id = ?",
            (user["tenant_id"],),
        )
        illegible = db.fetch_one(
            conn,
            "SELECT COUNT(*) AS n, SUM(json_array_length(illegible)) AS illegible FROM transcript WHERE tenant_id = ?",
            (user["tenant_id"],),
        )
        quota = QuotaManager(user["tenant_id"]).snapshot()
        return {
            "statuses": {row["status"]: row["n"] for row in statuses},
            "graded": graded,
            "errors": errors,
            "transcripts": illegible,
            "quota": quota,
        }
    finally:
        conn.close()


@router.get("/audit")
def audit_log(request: Request, user: dict = Depends(_user)) -> list[dict]:
    require_role(user, "admin", "dpo")
    conn = db.connect()
    try:
        return db.fetch_all(conn, "SELECT * FROM audit_log WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 200", (user["tenant_id"],))
    finally:
        conn.close()


@router.post("/consent/{student_id}/revoke")
def revoke(student_id: str, request: Request, user: dict = Depends(_user)) -> dict:
    _mutate(request, user)
    require_role(user, "admin", "dpo")
    conn = db.connect()
    try:
        from app.retention import revoke_consent
        revoke_consent(conn, user["tenant_id"], student_id, user["id"])
        return {"ok": True}
    except Exception as exc:
        raise _fail(exc)
    finally:
        conn.close()


@router.get("/calibration")
def calibration(user: dict = Depends(_user)) -> list[dict]:
    require_role(user, "rubric_owner", "admin")
    conn = db.connect()
    try:
        return db.fetch_all(conn, "SELECT * FROM calibration_sample WHERE tenant_id = ? ORDER BY selected_at DESC LIMIT 100", (user["tenant_id"],))
    finally:
        conn.close()

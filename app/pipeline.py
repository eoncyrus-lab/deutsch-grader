from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app import db
from app.config.loader import load_task_config
from app.config.models import TaskConfig
from app.grading.minimal_edit import check_minimal_edit
from app.grading.scoring import score_submission
from app.grading.text import find_span, split_sentences, word_count
from app.grading.validation import (
    SchemaError,
    scoring_critical_fields,
    validate_s1,
    validate_s2,
    validate_s2b,
    validate_s3,
    validate_s5,
)
from app.model import ModelGateway, ProviderError, ProviderUnavailable
from app.model.gateway import QuotaExhausted
from app.prompts.registry import load_prompt, pipeline_version
from app.quota import QuotaExhausted as QuotaManagerExhausted
from app.quota import QuotaManager
from app.storage import ObjectStore
from app.version import PIPELINE_CODE_VERSION, SCHEMA_VERSIONS, TOKENIZER_VERSION


ESTIMATED = {"s1": 5000, "s2": 3500, "s2b": 1800, "s3": 3000, "s5": 3000}


class PipelineError(RuntimeError):
    pass


def _now() -> str:
    return db.utcnow()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def audit(conn, tenant_id: str, actor: str | None, action: str, object_type: str, object_id: str | None, detail: dict | None = None) -> None:
    db.insert(conn, "audit_log", {
        "id": db.new_id(),
        "tenant_id": tenant_id,
        "actor_id": actor,
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "detail": _json(detail or {}),
        "created_at": _now(),
    })


def load_config(conn, tenant_id: str, task_config_id: str) -> tuple[dict, TaskConfig]:
    row = db.fetch_one(conn, "SELECT * FROM task_config WHERE id = ? AND tenant_id = ?", (task_config_id, tenant_id))
    if not row:
        raise PipelineError("task config not found")
    return row, load_task_config_from_row(row)


def load_task_config_from_row(row: dict) -> TaskConfig:
    import tempfile
    from pathlib import Path as _Path

    fd, name = tempfile.mkstemp(suffix=".yaml")
    import os
    os.close(fd)
    path = _Path(name)
    path.write_text(row["config_json"], encoding="utf-8")
    try:
        return load_task_config(path)
    finally:
        path.unlink(missing_ok=True)


def transcript_sentences(lines: list[dict]) -> list[dict]:
    body = " ".join(str(line.get("text", "")).strip() for line in lines if str(line.get("text", "")).strip())
    return [s.as_dict() for s in split_sentences(body)]


def transcript_text(lines: list[dict]) -> str:
    return " ".join(str(line.get("text", "")).strip() for line in lines if str(line.get("text", "")).strip())


def fixture_ref(submission: dict) -> str:
    source = str(submission.get("source", ""))
    return source.split(":", 1)[1] if source.startswith("demo:") else ""


def _update_run(conn, run_id: str, tenant_id: str, **values) -> None:
    db.update(conn, "run", run_id, tenant_id, values)


def _update_submission(conn, submission_id: str, tenant_id: str, status: str) -> None:
    db.update(conn, "submission", submission_id, tenant_id, {"status": status, "updated_at": _now()})


def _fail(conn, run: dict, submission: dict, stage: str, message: str) -> None:
    _update_run(conn, run["id"], run["tenant_id"], status="needs_manual", stage=stage, error=message[:2000], needs_manual=1, finished_at=_now())
    _update_submission(conn, submission["id"], submission["tenant_id"], "needs_manual")


def _record_stage(
    conn,
    run: dict,
    stage: str,
    attempt: int,
    input_text: str,
    output: dict | None,
    *,
    status: str,
    latency_ms: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    model: str | None = None,
    error: str | None = None,
    selected: bool = False,
) -> str:
    if selected:
        conn.execute(
            "UPDATE stage_output SET selected = 0 WHERE tenant_id = ? AND run_id = ? AND stage = ?",
            (run["tenant_id"], run["id"], stage),
        )
    row_id = db.new_id()
    output_text = _json(output) if output is not None else None
    db.insert(conn, "stage_output", {
        "id": row_id,
        "tenant_id": run["tenant_id"],
        "run_id": run["id"],
        "stage": stage,
        "attempt": attempt,
        "schema_version": SCHEMA_VERSIONS.get(stage, "v1"),
        "input_hash": _sha(input_text),
        "output_sha": _sha(output_text) if output_text else None,
        "parent_id": None,
        "output": output_text,
        "selected": 1 if selected else 0,
        "status": status,
        "latency_ms": latency_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": model,
        "source_id": None,
        "error": error,
        "created_at": _now(),
    })
    return row_id


def _next_attempt(conn, run: dict, stage: str) -> int:
    row = db.fetch_one(conn, "SELECT COALESCE(MAX(attempt), 0) AS n FROM stage_output WHERE tenant_id = ? AND run_id = ? AND stage = ?", (run["tenant_id"], run["id"], stage))
    return int(row["n"]) + 1


def call_stage(
    conn,
    gateway: ModelGateway,
    run: dict,
    config: TaskConfig,
    stage: str,
    system: str,
    user: str,
    validator: Callable[[dict[str, Any]], Any],
    *,
    images: list[tuple[str, bytes]] | None = None,
    ref: str = "",
) -> tuple[Any, dict]:
    feedback = ""
    last_error: Exception | None = None
    for local_attempt in range(3):
        attempt = _next_attempt(conn, run, stage)
        prompt_user = user + (f"\n\n上一次输出未通过校验：{feedback}\n请修正后重新只输出 JSON。" if feedback else "")
        try:
            result = gateway.complete(
                stage=stage,
                task_routes=config.model_routes,
                system=system,
                user=prompt_user,
                images=images,
                fixture_ref=ref,
                estimated_tokens=ESTIMATED.get(stage, 2000),
            )
            parsed = validator(result.data)
            _record_stage(
                conn, run, stage, attempt, system + prompt_user,
                result.data, status="ok", latency_ms=result.latency_ms,
                tokens_in=result.response.prompt_tokens, tokens_out=result.response.completion_tokens,
                model=result.response.model, selected=True,
            )
            return parsed, {"attempt": attempt, "latency_ms": result.latency_ms, "model": result.response.model, "family": result.family}
        except (SchemaError, ValueError) as exc:
            last_error = exc
            feedback = str(exc)[:500]
            _record_stage(conn, run, stage, attempt, system + prompt_user, None, status="invalid", error=feedback)
        except (ProviderError, QuotaExhausted, QuotaManagerExhausted, ProviderUnavailable) as exc:
            _record_stage(conn, run, stage, attempt, system + prompt_user, None, status="error", error=str(exc)[:1000])
            raise
    raise SchemaError(f"{stage} failed schema validation after 3 attempts: {last_error}")


def start_transcription(submission_id: str, tenant_id: str) -> str:
    conn = db.connect()
    try:
        submission = db.fetch_one(conn, "SELECT * FROM submission WHERE id = ? AND tenant_id = ?", (submission_id, tenant_id))
        if not submission:
            raise PipelineError("submission not found")
        if submission["source"].startswith("demo:"):
            run_id = create_run(conn, submission)
            transcript = build_demo_transcript(conn, submission)
            if transcript:
                _update_run(conn, run_id, tenant_id, status="awaiting_confirmation", stage="s1")
                _update_submission(conn, submission_id, tenant_id, "awaiting_confirmation")
                return run_id
        run_id = create_run(conn, submission)
        with db.transaction(conn):
            _update_submission(conn, submission_id, tenant_id, "transcribing")
            enqueue(conn, tenant_id, "transcribe", {"run_id": run_id}, f"transcribe:{run_id}", priority=50)
        return run_id
    finally:
        conn.close()


def build_demo_transcript(conn, submission: dict) -> bool:
    ref = fixture_ref(submission)
    fixture = Path(__file__).resolve().parent.parent / "demo" / ref / "transcript.json"
    if not fixture.exists():
        return False
    data = json.loads(fixture.read_text(encoding="utf-8"))
    db.insert(conn, "transcript", {
        "id": db.new_id(),
        "tenant_id": submission["tenant_id"],
        "submission_id": submission["id"],
        "version": 1,
        "title": data.get("title"),
        "lines": _json(data.get("lines", [])),
        "illegible": _json(data.get("illegible", [])),
        "redactions": int(data.get("redactions", 0)),
        "confirmed_by": None,
        "confirmed_at": None,
        "model": "fixture-v1",
        "prompt_version": "s1@1.0.0",
        "raw_cer": None,
        "created_at": _now(),
    })
    return True


def create_run(conn, submission: dict) -> str:
    config_row, config = load_config(conn, submission["tenant_id"], submission["task_config_id"])
    pv = pipeline_version(
        config.prompts,
        config_row["config_hash"],
        f"{PIPELINE_CODE_VERSION}:{TOKENIZER_VERSION}:{_sha(_json(SCHEMA_VERSIONS))[:8]}",
    )
    run_id = db.new_id()
    db.insert(conn, "run", {
        "id": run_id,
        "tenant_id": submission["tenant_id"],
        "submission_id": submission["id"],
        "pipeline_version": pv,
        "config_hash": config_row["config_hash"],
        "status": "pending",
        "stage": "s1",
        "error": None,
        "needs_manual": 0,
        "ai_total": None,
        "reviewed_total": None,
        "final_total": None,
        "comments": "{}",
        "model_fingerprints": "{}",
        "created_at": _now(),
        "finished_at": None,
    })
    return run_id


def run_transcription(run_id: str) -> None:
    conn = db.connect()
    try:
        run = db.fetch_one(conn, "SELECT * FROM run WHERE id = ?", (run_id,))
        if not run:
            return
        submission = db.fetch_one(conn, "SELECT * FROM submission WHERE id = ? AND tenant_id = ?", (run["submission_id"], run["tenant_id"]))
        if not submission:
            raise PipelineError("submission not found")
        _, config = load_config(conn, run["tenant_id"], submission["task_config_id"])
        images = []
        image_rows = db.fetch_all(conn, "SELECT * FROM submission_image WHERE submission_id = ? AND tenant_id = ? AND deleted_at IS NULL", (submission["id"], run["tenant_id"]))
        for image in image_rows:
            if submission["source"].startswith("demo:"):
                continue
            if not image["redacted_at"]:
                raise PipelineError("image has not been redacted; S1 is blocked until the redacted copy is confirmed")
            images.append((image["content_type"], ObjectStore().get(image["object_key"])))
        if not images and not submission["source"].startswith("demo:"):
            raise PipelineError("no redacted images available")
        _update_run(conn, run_id, run["tenant_id"], status="running", stage="s1")
        prompt = load_prompt(config.prompts["s1"])
        system, user = _split_prompt(prompt.render({
            "topic": config.topic_default,
            "image_count": str(len(images) or 1),
        }))
        gateway = ModelGateway(fixture_mode=submission["source"].startswith("demo:"), usage=QuotaManager(run["tenant_id"], fixture_mode=submission["source"].startswith("demo:")), run_id=run_id)
        data, meta = call_stage(conn, gateway, run, config, "s1", system, user, validate_s1, images=images, ref=fixture_ref(submission))
        existing = db.fetch_one(conn, "SELECT COALESCE(MAX(version), 0) AS v FROM transcript WHERE submission_id = ? AND tenant_id = ?", (submission["id"], run["tenant_id"]))
        version = int(existing["v"]) + 1
        db.insert(conn, "transcript", {
            "id": db.new_id(),
            "tenant_id": run["tenant_id"],
            "submission_id": submission["id"],
            "version": version,
            "title": data["title"],
            "lines": _json(data["lines"]),
            "illegible": _json(data["illegible"]),
            "redactions": data["redactions"],
            "confirmed_by": None,
            "confirmed_at": None,
            "model": meta.get("model"),
            "prompt_version": config.prompts["s1"],
            "raw_cer": None,
            "created_at": _now(),
        })
        _update_run(conn, run_id, run["tenant_id"], status="awaiting_confirmation", stage="s1")
        _update_submission(conn, submission["id"], run["tenant_id"], "awaiting_confirmation")
    except Exception as exc:
        run = db.fetch_one(conn, "SELECT * FROM run WHERE id = ?", (run_id,))
        if run:
            submission = db.fetch_one(conn, "SELECT * FROM submission WHERE id = ?", (run["submission_id"],))
            if submission:
                _fail(conn, run, submission, "s1", str(exc))
    finally:
        conn.close()


def _split_prompt(rendered: str) -> tuple[str, str]:
    if "【用户】" in rendered:
        system, user = rendered.split("【用户】", 1)
        return system.replace("【系统】", "", 1).strip(), user.strip()
    return "", rendered


def confirm_and_queue(submission_id: str, tenant_id: str, actor: str, title: str | None, lines: list[dict], illegible: list | None = None) -> tuple[str, list[dict]]:
    conn = db.connect()
    try:
        submission = db.fetch_one(conn, "SELECT * FROM submission WHERE id = ? AND tenant_id = ?", (submission_id, tenant_id))
        if not submission:
            raise PipelineError("submission not found")
        normalized = [{"n": i, "text": str(line.get("text", "")).strip()} for i, line in enumerate(lines, start=1) if str(line.get("text", "")).strip()]
        body = transcript_text(normalized)
        if not body or word_count(body) < 5:
            raise PipelineError("transcript is empty or too short")
        latest = db.fetch_one(conn, "SELECT COALESCE(MAX(version), 0) AS v FROM transcript WHERE submission_id = ? AND tenant_id = ?", (submission_id, tenant_id))
        version = int(latest["v"]) + 1
        with db.transaction(conn):
            db.insert(conn, "transcript", {
                "id": db.new_id(),
                "tenant_id": tenant_id,
                "submission_id": submission_id,
                "version": version,
                "title": title,
                "lines": _json(normalized),
                "illegible": _json(illegible or []),
                "redactions": 0,
                "confirmed_by": actor,
                "confirmed_at": _now(),
                "model": None,
                "prompt_version": None,
                "raw_cer": None,
                "created_at": _now(),
            })
            run_id = create_run(conn, submission)
            _update_submission(conn, submission_id, tenant_id, "queued")
            enqueue(conn, tenant_id, "grade", {"run_id": run_id}, f"grade:{run_id}", priority=50)
            audit(conn, tenant_id, actor, "transcript.confirm", "submission", submission_id, {"version": version, "run_id": run_id})
        return run_id, transcript_sentences(normalized)
    finally:
        conn.close()


def run_grading(run_id: str) -> None:
    conn = db.connect()
    try:
        run = db.fetch_one(conn, "SELECT * FROM run WHERE id = ?", (run_id,))
        if not run:
            return
        submission = db.fetch_one(conn, "SELECT * FROM submission WHERE id = ? AND tenant_id = ?", (run["submission_id"], run["tenant_id"]))
        if not submission:
            raise PipelineError("submission not found")
        config_row, config = load_config(conn, run["tenant_id"], submission["task_config_id"])
        transcript = db.fetch_one(conn, "SELECT * FROM transcript WHERE submission_id = ? AND tenant_id = ? AND confirmed_at IS NOT NULL ORDER BY version DESC LIMIT 1", (submission["id"], run["tenant_id"]))
        if not transcript:
            raise PipelineError("confirmed transcript not found")
        lines = json.loads(transcript["lines"])
        sentences = transcript_sentences(lines)
        body = transcript_text(lines)
        if not sentences:
            raise PipelineError("no sentences")
        is_demo = submission["source"].startswith("demo:")
        gateway = ModelGateway(fixture_mode=is_demo, usage=QuotaManager(run["tenant_id"], fixture_mode=is_demo), run_id=run_id)
        gateway.validate_families(config.model_routes)
        ref = fixture_ref(submission)
        _update_run(conn, run_id, run["tenant_id"], status="running", stage="s2")
        _update_submission(conn, submission["id"], run["tenant_id"], "grading")

        s2_prompt = load_prompt(config.prompts["s2"])
        system, user = _split_prompt(s2_prompt.render({
            "level": config.level,
            "error_types": ", ".join(f"{c.code}={c.label}" for c in config.error_codes) or ", ".join(config.error_type_keys),
            "error_codes": ", ".join(f"{c.code}={c.label}" for c in config.error_codes),
            "sentences_json": json.dumps(sentences, ensure_ascii=False),
        }))
        validation, _ = call_stage(conn, gateway, run, config, "s2", system, user, lambda data: validate_s2(data, sentences, config), ref=ref)
        if validation.dropped_ratio > 0.2 and validation.raw_count > 0:
            raise SchemaError(f"S2 dropped {len(validation.dropped)} of {validation.raw_count} errors")
        conn.execute(
            "UPDATE run SET coverage = ? WHERE tenant_id = ? AND id = ?",
            (_json({"raw_count": validation.raw_count, "dropped": validation.dropped, "dropped_ratio": validation.dropped_ratio}), run["tenant_id"], run_id),
        )
        errors = validation.errors
        for index, error in enumerate(errors, start=1):
            error["id"] = f"e{index}"
            error["db_id"] = db.new_id()
        _update_run(conn, run_id, run["tenant_id"], stage="s2b")

        errors = review_errors(conn, gateway, run, config, sentences, errors, ref)
        _update_run(conn, run_id, run["tenant_id"], stage="s3")

        s3_prompt = load_prompt(config.prompts["s3"])
        questions = "\n".join(f"{i}. {f.key}（{f.type}）：{f.question}" for i, f in enumerate(config.checklist.fields, start=1))
        system, user = _split_prompt(s3_prompt.render({
            "checklist_questions": questions,
            "topic": config.topic_default,
            "sentences_json": json.dumps(sentences, ensure_ascii=False),
            "field_keys": ", ".join(f.key for f in config.checklist.fields),
        }))
        (checklist, flags, invalid_keys), _ = call_stage(
            conn, gateway, run, config, "s3", system, user,
            lambda data: validate_s3(data, config, sentences, transcript["title"]), ref=ref,
        )
        critical = scoring_critical_fields(config)
        blocking = sorted(set(invalid_keys) & critical)
        if blocking:
            raise SchemaError(f"S3 evidence is unresolved for scoring fields: {blocking}")

        _update_run(conn, run_id, run["tenant_id"], stage="s4")
        result = score_submission(config, errors, checklist, flags, word_count(body), bool((transcript["title"] or "").strip()))
        _update_run(conn, run_id, run["tenant_id"], stage="s5")

        s5_prompt = load_prompt(config.prompts["s5"])
        system, user = _split_prompt(s5_prompt.render({
            "level": config.level,
            "topic": config.topic_default,
            "sentences_json": json.dumps(sentences, ensure_ascii=False),
            "errors_confirmed": json.dumps([e for e in errors if e.get("effective")], ensure_ascii=False),
            "checklist_json": json.dumps(checklist, ensure_ascii=False),
            "score_trace": json.dumps(result.trace, ensure_ascii=False),
        }))
        comments = None
        try:
            comments, _ = call_stage(
                conn, gateway, run, config, "s5", system, user,
                lambda data: _s5_validator(data, body), ref=ref,
            )
        except SchemaError:
            comments = {
                "content_comment": "内容要点已完成核对，请结合课堂讲评。",
                "structure_comment": "结构建议已在错误清单中标注，请教师复核。",
                "language_summary": "先改最常出现的错误类型。",
                "encouragement_de": "Weiter so! Deine Reise ist interessant.",
                "fallback": True,
            }
        persist_grading(
            conn, run, submission, config, errors, checklist, flags, result, comments, sentences,
            draft=bool(validation.dropped),
        )
        _update_run(
            conn, run_id, run["tenant_id"], status="ready_for_review", stage="done",
            ai_total=result.total, comments=_json(comments), finished_at=_now(),
        )
        _update_submission(conn, submission["id"], run["tenant_id"], "ready_for_review")
    except Exception as exc:
        run = db.fetch_one(conn, "SELECT * FROM run WHERE id = ?", (run_id,))
        if run:
            submission = db.fetch_one(conn, "SELECT * FROM submission WHERE id = ?", (run["submission_id"],))
            if submission:
                _fail(conn, run, submission, run["stage"], str(exc))
    finally:
        conn.close()


def _s5_validator(data: dict, body: str) -> dict:
    comments, issues = validate_s5(data, body)
    if issues:
        raise SchemaError("; ".join(issues))
    return comments


def review_errors(conn, gateway: ModelGateway, run: dict, config: TaskConfig, sentences: list[dict], errors: list[dict], ref: str) -> list[dict]:
    sentence_map = {int(s["sid"]): s["text"] for s in sentences}
    pending = [e for e in errors if e.get("certainty") == "确定" and e.get("is_primary")]
    for round_no in (1, 2):
        by_sid: dict[int, list[dict]] = {}
        for error in pending:
            by_sid.setdefault(int(error["sid"]), []).append(error)
        if not by_sid:
            break
        next_pending: list[dict] = []
        for sid, batch in by_sid.items():
            prompt = load_prompt(config.prompts["s2b"])
            system, user = _split_prompt(prompt.render({
                "level": config.level,
                "sid": str(sid),
                "sentence": sentence_map[sid],
                "errors_json": json.dumps([{k: e.get(k) for k in ("id", "original", "corrected", "type", "rule")} for e in batch], ensure_ascii=False),
            }))
            verdicts, _ = call_stage(
                conn, gateway, run, config, f"s2b", system, user,
                lambda data, ids=[e["id"] for e in batch]: validate_s2b(data, ids), ref=ref,
            )
            for error in batch:
                verdict = verdicts.get(error["id"], {})
                error["review_verdict"] = verdict.get("verdict")
                error["review_reason"] = verdict.get("reason")
                if verdict.get("verdict") == "通过":
                    error["verifier_status"] = "pass"
                    error["terminal_status"] = "confirmed"
                    error["effective"] = 1
                elif verdict.get("verdict") == "不成立":
                    error["verifier_status"] = "rejected"
                    error["terminal_status"] = "rejected"
                    error["certainty"] = "建议核对"
                    error["effective"] = 0
                else:
                    alt = verdict.get("alt_corrected") or ""
                    span = find_span(sentence_map[sid], error["original"], alt, None)
                    edit = check_minimal_edit(error["original"], alt)
                    if not alt or span is None or not edit.ok:
                        error["verifier_status"] = "adjust_failed"
                        error["terminal_status"] = "downgraded"
                        error["certainty"] = "建议核对"
                        error["effective"] = 0
                        error["review_reason"] = (verdict.get("reason") or "") + f"; alt 未通过校验: {edit.reason}"
                    elif round_no == 1:
                        error["corrected"] = alt
                        error["span_start"], error["span_end"] = span
                        error["minimal_edit"] = 1
                        next_pending.append(error)
                    else:
                        error["verifier_status"] = "adjust_failed"
                        error["terminal_status"] = "downgraded"
                        error["certainty"] = "建议核对"
                        error["effective"] = 0
        pending = next_pending
    for error in errors:
        if error.get("certainty") != "确定":
            error["effective"] = 0
            if error.get("terminal_status") == "candidate":
                error["terminal_status"] = "downgraded"
    return errors


def persist_grading(
    conn, run: dict, submission: dict, config: TaskConfig, errors: list[dict],
    checklist: dict, flags: list[dict], result, comments: dict, sentences: list[dict],
    draft: bool = False,
) -> None:
    with db.transaction(conn):
        for table in ("error_item", "checklist_item", "score"):
            conn.execute(f"DELETE FROM {table} WHERE tenant_id = ? AND run_id = ?", (run["tenant_id"], run["id"]))
        sentence_map = {int(s["sid"]): s for s in sentences}
        for error in errors:
            sentence = sentence_map.get(int(error["sid"]))
            base = sentence["start"] if sentence else 0
            db.insert(conn, "error_item", {
                "id": error.get("db_id") or db.new_id(),
                "tenant_id": run["tenant_id"],
                "run_id": run["id"],
                "sid": error["sid"],
                "original": error["original"],
                "corrected": error["corrected"],
                "span_start": base + int(error["span_start"]),
                "span_end": base + int(error["span_end"]),
                "type": error["type"],
                "error_code": error.get("error_code", ""),
                "pattern": error["pattern"],
                "certainty": error["certainty"],
                "rule": error["rule"],
                "minimal_edit": int(error.get("minimal_edit", 1)),
                "guard_status": error.get("guard_status", "pass"),
                "verifier_status": error.get("verifier_status", "pending"),
                "terminal_status": error.get("terminal_status", "candidate"),
                "group_id": error.get("group_id"),
                "is_primary": int(error.get("is_primary", 1)),
                "review_verdict": error.get("review_verdict"),
                "review_reason": error.get("review_reason"),
                "teacher_action": "pending",
                "effective": int(error.get("effective", 0)),
                "created_at": _now(),
            })
        for key, item in checklist.items():
            db.insert(conn, "checklist_item", {
                "id": db.new_id(),
                "tenant_id": run["tenant_id"],
                "run_id": run["id"],
                "key": key,
                "value": _json(item["value"]),
                "evidence": _json(item.get("evidence")),
                "valid": int(item.get("valid", 1)),
                "created_at": _now(),
            })
        conn.execute(
            "UPDATE run SET sentence_flags = ? WHERE tenant_id = ? AND id = ?",
            (_json(flags), run["tenant_id"], run["id"]),
        )
        db.insert(conn, "score", {
            "id": db.new_id(),
            "tenant_id": run["tenant_id"],
            "run_id": run["id"],
            "kind": "ai",
            "seq": 1,
            "language": result.language,
            "content": result.content,
            "structure": result.structure,
            "total": result.total,
            "scoring_revision": 1,
            "draft": 1 if draft else 0,
            "trace": _json(result.trace),
            "title_deducted": result.title_deducted,
            "cap_applied": result.cap_applied,
            "computed_at": _now(),
        })
        audit(conn, run["tenant_id"], None, "grading.complete", "run", run["id"], {"total": result.total, "errors": len(errors), "comments_fallback": comments.get("fallback", False)})


def enqueue(conn, tenant_id: str, kind: str, payload: dict, idempotency_key: str, priority: int = 100, not_before: str | None = None, max_attempts: int = 3) -> str:
    existing = db.fetch_one(conn, "SELECT id FROM job WHERE tenant_id = ? AND idempotency_key = ?", (tenant_id, idempotency_key))
    if existing:
        return existing["id"]
    job_id = db.new_id()
    now = _now()
    db.insert(conn, "job", {
        "id": job_id,
        "tenant_id": tenant_id,
        "kind": kind,
        "payload": _json(payload),
        "status": "queued",
        "priority": priority,
        "attempts": 0,
        "max_attempts": max_attempts,
        "not_before": not_before or now,
        "locked_by": None,
        "locked_at": None,
        "lease_until": None,
        "idempotency_key": idempotency_key,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    })
    return job_id

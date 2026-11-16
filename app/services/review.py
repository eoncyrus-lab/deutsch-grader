from __future__ import annotations

import hashlib
import html
import json
from typing import Any

from app import db
from app.config.models import TaskConfig
from app.grading.scoring import ScoreResult, score_submission
from app.grading.text import find_span, word_count
from app.grading.validation import scoring_critical_fields
from app.pipeline import audit, load_config, transcript_sentences, transcript_text


class ReviewError(ValueError):
    pass


REASONS = {"量规理解不同", "AI 漏错", "AI 误判", "转写问题", "其他"}


def _bundle(conn, tenant_id: str, run_id: str) -> dict[str, Any]:
    run = db.fetch_one(conn, "SELECT * FROM run WHERE id = ? AND tenant_id = ?", (run_id, tenant_id))
    if not run:
        raise ReviewError("run not found")
    submission = db.fetch_one(conn, "SELECT * FROM submission WHERE id = ? AND tenant_id = ?", (run["submission_id"], tenant_id))
    if not submission:
        raise ReviewError("submission not found")
    config_row, config = load_config(conn, tenant_id, submission["task_config_id"])
    transcript = db.fetch_one(
        conn,
        "SELECT * FROM transcript WHERE submission_id = ? AND tenant_id = ? AND confirmed_at IS NOT NULL ORDER BY version DESC LIMIT 1",
        (submission["id"], tenant_id),
    )
    if not transcript:
        raise ReviewError("confirmed transcript not found")
    lines = json.loads(transcript["lines"])
    sentences = transcript_sentences(lines)
    errors = db.fetch_all(conn, "SELECT * FROM error_item WHERE tenant_id = ? AND run_id = ? ORDER BY sid, span_start", (tenant_id, run_id))
    checklist_rows = db.fetch_all(conn, "SELECT * FROM checklist_item WHERE tenant_id = ? AND run_id = ?", (tenant_id, run_id))
    checklist = {row["key"]: {"value": json.loads(row["value"]), "evidence": json.loads(row["evidence"] or "null"), "valid": row["valid"]} for row in checklist_rows}
    flags = json.loads(run["sentence_flags"] or "[]")
    return {
        "run": run, "submission": submission, "config": config, "config_row": config_row,
        "transcript": transcript, "sentences": sentences, "errors": errors,
        "checklist": checklist, "flags": flags,
    }


def _score(conn, bundle: dict[str, Any]) -> ScoreResult:
    config: TaskConfig = bundle["config"]
    critical = scoring_critical_fields(config)
    blocking = [key for key in critical if key in bundle["checklist"] and not bundle["checklist"][key].get("valid")]
    if blocking:
        raise ReviewError(f"unresolved checklist fields: {sorted(blocking)}")
    body = transcript_text(json.loads(bundle["transcript"]["lines"]))
    return score_submission(
        config, bundle["errors"], bundle["checklist"], bundle["flags"],
        word_count(body), bool((bundle["transcript"]["title"] or "").strip()),
    )


def recompute_reviewed(conn, tenant_id: str, run_id: str, *, persist: bool = True) -> dict[str, Any]:
    bundle = _bundle(conn, tenant_id, run_id)
    result = _score(conn, bundle)
    if persist:
        row = db.fetch_one(conn, "SELECT COALESCE(MAX(seq), 0) AS n FROM score WHERE tenant_id = ? AND run_id = ? AND kind = 'reviewed'", (tenant_id, run_id))
        seq = int(row["n"]) + 1
        db.insert(conn, "score", {
            "id": db.new_id(), "tenant_id": tenant_id, "run_id": run_id, "kind": "reviewed", "seq": seq,
            "language": result.language, "content": result.content, "structure": result.structure,
            "total": result.total, "scoring_revision": seq, "draft": 0, "trace": json.dumps(result.trace, ensure_ascii=False),
            "title_deducted": result.title_deducted, "cap_applied": result.cap_applied, "computed_at": db.utcnow(),
        })
        db.update(conn, "run", run_id, tenant_id, {"reviewed_total": result.total})
    return result.as_dict()


def apply_error_action(conn, tenant_id: str, actor: str, error_id: str, action: str, reason: str, corrected: str | None = None) -> dict[str, Any]:
    if action not in {"reject", "over_edit", "confirm", "correct"}:
        raise ReviewError("invalid action")
    if action in {"reject", "over_edit", "correct"} and not reason.strip():
        raise ReviewError("reason is required")
    error = db.fetch_one(conn, "SELECT * FROM error_item WHERE id = ? AND tenant_id = ?", (error_id, tenant_id))
    if not error:
        raise ReviewError("error not found")
    if not error.get("is_primary") and action in {"confirm", "correct"}:
        raise ReviewError("this edit overlaps a primary error; confirm the primary edit instead")
    before = dict(error)
    values: dict[str, Any] = {"teacher_action": action}
    if action in {"reject", "over_edit"}:
        values.update({"effective": 0, "terminal_status": "teacher_rejected", "review_reason": reason})
    elif action == "confirm":
        values.update({"effective": 1, "certainty": "确定", "terminal_status": "teacher_confirmed", "review_reason": reason or "教师确认"})
    else:
        if not corrected or not corrected.strip():
            raise ReviewError("corrected text is required")
        run = db.fetch_one(conn, "SELECT * FROM run WHERE id = ? AND tenant_id = ?", (error["run_id"], tenant_id))
        transcript = db.fetch_one(
            conn, "SELECT * FROM transcript WHERE submission_id = ? AND tenant_id = ? AND confirmed_at IS NOT NULL ORDER BY version DESC LIMIT 1",
            (run["submission_id"], tenant_id),
        )
        sentences = transcript_sentences(json.loads(transcript["lines"]))
        sentence = next((s for s in sentences if int(s["sid"]) == int(error["sid"])), None)
        if not sentence:
            raise ReviewError("sentence not found")
        span = find_span(sentence["text"], error["original"], corrected, None)
        if span is None:
            raise ReviewError("corrected text no longer matches the original span")
        values.update({
            "corrected": corrected.strip(), "span_start": sentence["start"] + span[0],
            "span_end": sentence["start"] + span[1], "effective": 1, "certainty": "确定",
            "terminal_status": "teacher_corrected", "review_reason": reason,
        })
    db.update(conn, "error_item", error_id, tenant_id, values)
    sub_row = db.fetch_one(conn, "SELECT submission_id FROM run WHERE id = ? AND tenant_id = ?", (error["run_id"], tenant_id))
    db.insert(conn, "teacher_review", {
        "id": db.new_id(), "tenant_id": tenant_id, "submission_id": sub_row["submission_id"] if sub_row else None, "run_id": error["run_id"],
        "reviewer_id": actor, "action": f"error.{action}", "reason": reason, "note": None,
        "before_json": json.dumps(before, ensure_ascii=False, default=str),
        "after_json": json.dumps(values, ensure_ascii=False, default=str), "created_at": db.utcnow(),
    })
    audit(conn, tenant_id, actor, f"error.{action}", "error_item", error_id, {"reason": reason})
    return recompute_reviewed(conn, tenant_id, error["run_id"])


def add_missing_error(conn, tenant_id: str, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    run = db.fetch_one(conn, "SELECT * FROM run WHERE id = ? AND tenant_id = ?", (payload["run_id"], tenant_id))
    if not run:
        raise ReviewError("run not found")
    bundle = _bundle(conn, tenant_id, run["id"])
    sentence = next((s for s in bundle["sentences"] if int(s["sid"]) == int(payload["sid"])), None)
    if not sentence:
        raise ReviewError("sentence not found")
    span = find_span(sentence["text"], payload["original"], payload["corrected"], payload.get("span"))
    if span is None:
        raise ReviewError("original text not found in sentence")
    error_id = db.new_id()
    db.insert(conn, "error_item", {
        "id": error_id, "tenant_id": tenant_id, "run_id": run["id"], "sid": int(payload["sid"]),
        "original": payload["original"], "corrected": payload["corrected"],
        "span_start": sentence["start"] + span[0], "span_end": sentence["start"] + span[1],
        "type": payload.get("type", "其他"), "error_code": payload.get("error_code", ""),
        "pattern": payload.get("pattern", "teacher:added"), "certainty": "确定",
        "rule": payload.get("rule", "教师补录"), "minimal_edit": 1, "guard_status": "teacher",
        "verifier_status": "teacher", "terminal_status": "teacher_added", "group_id": None,
        "is_primary": 1, "review_verdict": None, "review_reason": "教师补录漏错",
        "teacher_action": "added", "effective": 1, "created_at": db.utcnow(),
    })
    audit(conn, tenant_id, actor, "error.add", "run", run["id"], {"error_id": error_id})
    return recompute_reviewed(conn, tenant_id, run["id"])


def resolve_checklist(conn, tenant_id: str, actor: str, run_id: str, key: str, value: Any, evidence: Any) -> dict[str, Any]:
    row = db.fetch_one(conn, "SELECT * FROM checklist_item WHERE tenant_id = ? AND run_id = ? AND key = ?", (tenant_id, run_id, key))
    if not row:
        raise ReviewError("checklist item not found")
    conn.execute(
        "UPDATE checklist_item SET value = ?, evidence = ?, valid = 1 WHERE tenant_id = ? AND run_id = ? AND key = ?",
        (json.dumps(value, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False), tenant_id, run_id, key),
    )
    audit(conn, tenant_id, actor, "checklist.resolve", "run", run_id, {"key": key})
    return recompute_reviewed(conn, tenant_id, run_id)


def finalize_run(conn, tenant_id: str, actor: str, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    reason = str(payload.get("reason", "")).strip()
    if reason not in REASONS:
        raise ReviewError(f"reason must be one of {sorted(REASONS)}")
    bundle = _bundle(conn, tenant_id, run_id)
    if payload.get("language") is not None and not 0 <= float(payload["language"]) <= bundle["config"].language_config.max:
        raise ReviewError("language score out of range")
    if payload.get("content") is not None and not 0 <= float(payload["content"]) <= bundle["config"].content.max:
        raise ReviewError("content score out of range")
    if payload.get("structure") is not None and not 0 <= float(payload["structure"]) <= bundle["config"].structure.max:
        raise ReviewError("structure score out of range")
    if bundle["run"]["status"] not in {"ready_for_review", "needs_manual"}:
        raise ReviewError("run is not ready for review")
    result = _score(conn, bundle)
    overrides = {k: payload.get(k) for k in ("language", "content", "structure") if payload.get(k) is not None}
    if overrides:
        language = float(overrides.get("language", result.language))
        content = float(overrides.get("content", result.content))
        structure = float(overrides.get("structure", result.structure))
        total = float(payload.get("total") if payload.get("total") is not None else language + content + structure)
        seq_row = db.fetch_one(conn, "SELECT COALESCE(MAX(seq), 0) AS n FROM score WHERE tenant_id = ? AND run_id = ? AND kind = 'reviewed'", (tenant_id, run_id))
        seq = int(seq_row["n"]) + 1
        db.insert(conn, "score", {
            "id": db.new_id(), "tenant_id": tenant_id, "run_id": run_id, "kind": "reviewed", "seq": seq,
            "language": language, "content": content, "structure": structure, "total": total,
            "scoring_revision": seq, "draft": 0,
            "trace": json.dumps([{**result.as_dict(), "teacher_override": True}], ensure_ascii=False),
            "title_deducted": result.title_deducted, "cap_applied": result.cap_applied, "computed_at": db.utcnow(),
        })
    else:
        total = result.total
        db.update(conn, "run", run_id, tenant_id, {"reviewed_total": total})
    before = {"ai_total": bundle["run"]["ai_total"], "reviewed_total": bundle["run"]["reviewed_total"]}
    db.update(conn, "run", run_id, tenant_id, {"final_total": total, "status": "finalized", "finished_at": db.utcnow()})
    db.update(conn, "submission", bundle["submission"]["id"], tenant_id, {"status": "finalized", "updated_at": db.utcnow()})
    db.insert(conn, "teacher_review", {
        "id": db.new_id(), "tenant_id": tenant_id, "submission_id": bundle["submission"]["id"], "run_id": run_id,
        "reviewer_id": actor, "action": "finalize", "reason": reason, "note": payload.get("note"),
        "before_json": json.dumps(before, ensure_ascii=False),
        "after_json": json.dumps({"total": total, "overrides": overrides}, ensure_ascii=False),
        "created_at": db.utcnow(),
    })
    audit(conn, tenant_id, actor, "run.finalize", "run", run_id, {"total": total, "reason": reason})
    maybe_sample(conn, tenant_id, bundle["submission"]["id"], bundle["run"], bundle["errors"])
    update_profile(conn, tenant_id, bundle)
    return {
        "total": total,
        "language": float(overrides.get("language", result.language)),
        "content": float(overrides.get("content", result.content)),
        "structure": float(overrides.get("structure", result.structure)),
    }


def maybe_sample(conn, tenant_id: str, submission_id: str, run: dict, errors: list[dict]) -> None:
    digest = int(hashlib.sha256(submission_id.encode()).hexdigest()[:8], 16)
    low_confidence = any(e.get("certainty") != "确定" for e in errors)
    if digest % 20 == 0 or low_confidence:
        existing = db.fetch_one(conn, "SELECT id FROM calibration_sample WHERE submission_id = ? AND tenant_id = ?", (submission_id, tenant_id))
        if not existing:
            db.insert(conn, "calibration_sample", {
                "id": db.new_id(), "tenant_id": tenant_id, "submission_id": submission_id,
                "reason": "low_confidence" if low_confidence else "random_5pct",
                "selected_at": db.utcnow(), "status": "pending",
            })


def update_profile(conn, tenant_id: str, bundle: dict[str, Any]) -> None:
    student_id = bundle["submission"].get("student_id")
    if not student_id:
        return
    consent = db.fetch_one(
        conn,
        "SELECT * FROM guardian_consent WHERE tenant_id = ? AND student_id = ? AND revoked_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > ?) ORDER BY granted_at DESC LIMIT 1",
        (tenant_id, student_id, db.utcnow()),
    )
    if not consent:
        return
    scope = json.loads(consent["scope"] or "{}")
    if not scope.get("profile"):
        return
    words = word_count(transcript_text(json.loads(bundle["transcript"]["lines"])))
    grouped: dict[tuple[str, str], int] = {}
    for error in bundle["errors"]:
        if not error.get("effective"):
            continue
        key = (error["pattern"], error["type"])
        grouped[key] = grouped.get(key, 0) + 1
    for (pattern, etype), occurrences in grouped.items():
        rate = occurrences / max(1, words) * 100
        existing = db.fetch_one(conn, "SELECT * FROM profile_pattern WHERE tenant_id = ? AND student_id = ? AND pattern = ?", (tenant_id, student_id, pattern))
        if existing:
            status = "improving" if rate < float(existing["rate"]) else "active"
            conn.execute(
                "UPDATE profile_pattern SET occurrences = ?, words = ?, rate = ?, status = ?, last_seen = ?, updated_at = ? "
                "WHERE tenant_id = ? AND student_id = ? AND pattern = ?",
                (occurrences, words, rate, status, db.utcnow(), db.utcnow(), tenant_id, student_id, pattern),
            )
        else:
            db.insert(conn, "profile_pattern", {
                "id": db.new_id(), "tenant_id": tenant_id, "student_id": student_id, "pattern": pattern,
                "type": etype, "occurrences": occurrences, "words": words, "rate": rate,
                "status": "active", "last_seen": db.utcnow(), "positive_evidence": 0, "updated_at": db.utcnow(),
            })


def student_export(conn, tenant_id: str, run_id: str, fmt: str = "json", nonce: str = "") -> tuple[str, str]:
    bundle = _bundle(conn, tenant_id, run_id)
    errors = [e for e in bundle["errors"] if e.get("effective")]
    comments = json.loads(bundle["run"]["comments"] or "{}")
    student = db.fetch_one(conn, "SELECT * FROM student WHERE id = ? AND tenant_id = ?", (bundle["submission"].get("student_id"), tenant_id))
    pseudonym = student["pseudonym"] if student else "同学"
    data = {
        "student": pseudonym,
        "task": bundle["config"].name,
        "level": bundle["config"].level,
        "errors": [
            {"original": e["original"], "corrected": e["corrected"], "type": e["type"], "rule": e["rule"]}
            for e in errors
        ],
        "comments": {k: comments.get(k, "") for k in ("content_comment", "structure_comment", "language_summary", "encouragement_de")},
    }
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, indent=2), "application/json; charset=utf-8"
    rows = "".join(
        f"<tr><td>{html.escape(e['original'])}</td>"
        f"<td>{html.escape(e['corrected'])} "
        f"<button class=\"speak\" data-speak=\"{html.escape(e['corrected'], quote=True)}\" aria-label=\"朗读正确表达\">🔊</button></td>"
        f"<td>{html.escape(e['type'])}</td><td>{html.escape(e['rule'])}</td></tr>"
        for e in data["errors"]
    )
    all_corrected = " ".join(e["corrected"] for e in data["errors"])
    nonce_attr = f' nonce="{html.escape(nonce, quote=True)}"' if nonce else ""
    inline_script = ""
    if nonce:
        inline_script = (
            "<script" + nonce_attr + ">"
            "(function(){if(window.DGReader)return;"
            "var s=window.speechSynthesis;"
            "function v(){var a=s&&s.getVoices?s.getVoices():[];for(var i=0;i<a.length;i++){if(/^de([-_]|$)/i.test(a[i].lang||''))return a[i]}return null}"
            "function speak(t,b){t=String(t||'').replace(/\\[(NAME|CLASS|SCHOOL)\\]/g,'').trim();if(!t)return;"
            "if(!s||typeof SpeechSynthesisUtterance==='undefined'){alert('当前浏览器不支持语音朗读');return}"
            "s.cancel();var u=new SpeechSynthesisUtterance(t);u.lang='de-DE';u.rate=.88;var g=v();if(g)u.voice=g;"
            "if(b){b.classList.add('speaking');b.setAttribute('aria-pressed','true')}u.onend=function(){if(b){b.classList.remove('speaking');b.setAttribute('aria-pressed','false')}};s.speak(u);return true}"
            "document.addEventListener('click',function(e){var b=e.target.closest('[data-speak]');if(!b)return;e.preventDefault();speak(b.getAttribute('data-speak'),b)});"
            "window.DGReader={speak:function(t){return speak(t,null)}};})();"
            "</script>"
        )
    page = f"""<!doctype html><html lang="zh"><meta charset="utf-8">
<title>作文批改反馈</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;line-height:1.7;color:#222}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}}h1{{font-size:22px}}.speak{{border:0;background:#e5f1ec;color:#145c4c;border-radius:99px;cursor:pointer;padding:2px 8px}}.speak.speaking{{background:#145c4c;color:#fff}}</style>
<h1>{html.escape(data['task'])} · {html.escape(pseudonym)}</h1>
<h2>修改清单</h2>
{f'<p><button class="speak" data-speak="{html.escape(all_corrected, quote=True)}">🔊 朗读全部正确表达</button></p>' if all_corrected else ''}
<table><thead><tr><th>原文</th><th>修改</th><th>类型</th><th>依据</th></tr></thead><tbody>{rows}</tbody></table>
<h2>评语</h2><p>{html.escape(data['comments']['content_comment'])}</p><p>{html.escape(data['comments']['structure_comment'])}</p><p>{html.escape(data['comments']['language_summary'])}</p>
<p><strong>{html.escape(data['comments']['encouragement_de'])}</strong> <button class="speak" data-speak="{html.escape(data['comments']['encouragement_de'], quote=True)}">🔊 听德语鼓励</button></p>
{inline_script}
</html>"""
    return page, "text/html; charset=utf-8"

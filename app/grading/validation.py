from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config.models import Expr, TaskConfig, walk_fields
from app.grading.minimal_edit import check_minimal_edit
from app.grading.text import find_span, german_word_limit_ok, normalize_space


class SchemaError(ValueError):
    pass


CERTAINTY = {
    "确定": "确定",
    "confirmed": "确定",
    "certain": "确定",
    "建议核对": "建议核对",
    "suggested": "建议核对",
    "uncertain": "建议核对",
}


def normalize_certainty(value: Any) -> str:
    return CERTAINTY.get(str(value).strip().lower(), "建议核对")


@dataclass
class ErrorValidation:
    errors: list[dict[str, Any]]
    dropped: list[dict[str, Any]] = field(default_factory=list)
    raw_count: int = 0

    @property
    def dropped_ratio(self) -> float:
        return len(self.dropped) / max(1, self.raw_count)


def validate_s1(data: dict[str, Any]) -> dict[str, Any]:
    title = data.get("title")
    if title is not None and not isinstance(title, str):
        raise SchemaError("S1 title must be a string or null")
    lines_raw = data.get("lines")
    if not isinstance(lines_raw, list) or not lines_raw:
        raise SchemaError("S1 lines must be a non-empty array")
    lines = []
    for index, item in enumerate(lines_raw, start=1):
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = item.get("text", "")
        else:
            raise SchemaError(f"S1 line {index} must be text")
        if not isinstance(text, str):
            raise SchemaError(f"S1 line {index} text must be a string")
        lines.append({"n": index, "text": text})
    illegible = data.get("illegible", [])
    if not isinstance(illegible, list):
        raise SchemaError("S1 illegible must be an array")
    redactions = data.get("redactions", 0)
    if not isinstance(redactions, int) or redactions < 0:
        raise SchemaError("S1 redactions must be a non-negative integer")
    return {"title": title, "lines": lines, "illegible": illegible, "redactions": redactions}


def validate_s2(data: dict[str, Any], sentences: list[dict[str, Any]], config: TaskConfig) -> ErrorValidation:
    raw_errors = data.get("errors")
    if not isinstance(raw_errors, list):
        raise SchemaError("S2 errors must be an array")
    sentence_map = {int(s["sid"]): s["text"] for s in sentences}
    code_map = config.code_map
    impact = config.impact_for
    errors: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for index, raw in enumerate(raw_errors):
        if not isinstance(raw, dict):
            dropped.append({"index": index, "reason": "not an object"})
            continue
        sid = raw.get("sid")
        if not isinstance(sid, int) or sid not in sentence_map:
            dropped.append({"index": index, "reason": f"unknown sid {sid}"})
            continue
        original = normalize_space(str(raw.get("original", "")))
        corrected = normalize_space(str(raw.get("corrected", "")))
        if not original or not corrected:
            dropped.append({"index": index, "reason": "empty original or corrected"})
            continue
        key = (sid, original, corrected)
        if key in seen:
            dropped.append({"index": index, "reason": "duplicate edit"})
            continue
        seen.add(key)
        code = str(raw.get("code", "")).strip()
        etype = str(raw.get("type", "")).strip()
        if code:
            if code not in code_map:
                dropped.append({"index": index, "reason": f"unknown error code {code}"})
                continue
            etype = code_map[code].type
        elif etype not in config.error_type_keys:
            dropped.append({"index": index, "reason": f"unknown error type {etype}"})
            continue
        else:
            code = next((c.code for c in config.error_codes if c.type == etype), "")
        rule = normalize_space(str(raw.get("rule", "")))
        if not rule:
            dropped.append({"index": index, "reason": "missing rule"})
            continue
        span = find_span(sentence_map[sid], original, corrected, raw.get("span"))
        if span is None:
            dropped.append({"index": index, "reason": f"original not found in sid {sid}"})
            continue
        certainty = normalize_certainty(raw.get("certainty"))
        edit = check_minimal_edit(original, corrected)
        guard_status = "pass" if edit.ok else "violation"
        if not edit.ok:
            certainty = "建议核对"
        if "[?]" in sentence_map[sid] or "[?" in original:
            certainty = "建议核对"
            guard_status = "illegible"
        start, end = span
        errors.append({
            "sid": sid,
            "original": original,
            "corrected": corrected,
            "span_start": start,
            "span_end": end,
            "type": etype,
            "error_code": code,
            "pattern": str(raw.get("pattern", "")).strip() or code or etype,
            "certainty": certainty,
            "rule": rule,
            "minimal_edit": 1 if edit.ok else 0,
            "guard_status": guard_status,
            "verifier_status": "pending",
            "terminal_status": "candidate",
            "group_id": None,
            "is_primary": 1,
            "effective": 1 if certainty == "确定" else 0,
            "review_verdict": None,
            "review_reason": edit.reason if not edit.ok else None,
            "teacher_action": "pending",
            "impact": impact.get(etype, 1),
        })
    # One atomic edit may carry several labels; only the highest-impact label scores.
    ordered = sorted(errors, key=lambda e: (e["sid"], e["span_start"], e["span_end"]))
    for i, error in enumerate(ordered):
        if error.get("group_id"):
            continue
        group = [error]
        for other in ordered[i + 1:]:
            if other["sid"] != error["sid"] or other["span_start"] >= error["span_end"]:
                continue
            overlap = min(error["span_end"], other["span_end"]) - max(error["span_start"], other["span_start"])
            union = max(error["span_end"], other["span_end"]) - min(error["span_start"], other["span_start"])
            if union > 0 and overlap / union >= 0.5:
                group.append(other)
        if len(group) > 1:
            gid = str(uuid.uuid4())
            primary = max(group, key=lambda e: (e["impact"], e["effective"]))
            for item in group:
                item["group_id"] = gid
            for item in group:
                if item is not primary:
                    item["is_primary"] = 0
                    item["effective"] = 0
                    item["terminal_status"] = "secondary"
                    item["review_reason"] = "与同一处修改重叠，只计主类型"
    return ErrorValidation(errors=ordered, dropped=dropped, raw_count=len(raw_errors))


def validate_s2b(data: dict[str, Any], expected_ids: list[str]) -> dict[str, dict[str, Any]]:
    verdicts = data.get("verdicts")
    if not isinstance(verdicts, list):
        raise SchemaError("S2b verdicts must be an array")
    out: dict[str, dict[str, Any]] = {}
    for raw in verdicts:
        if not isinstance(raw, dict):
            raise SchemaError("S2b verdict must be an object")
        error_id = str(raw.get("error_id", ""))
        verdict = str(raw.get("verdict", ""))
        if verdict not in {"通过", "改法需调整", "不成立"}:
            raise SchemaError(f"S2b invalid verdict {verdict!r}")
        checks = raw.get("checks", {})
        if not isinstance(checks, dict) or not all(isinstance(v, bool) for v in checks.values()):
            raise SchemaError("S2b checks must be booleans")
        if verdict == "改法需调整" and not str(raw.get("alt_corrected", "")).strip():
            raise SchemaError("S2b 改法需调整 requires alt_corrected")
        out[error_id] = {
            "verdict": verdict,
            "alt_corrected": normalize_space(str(raw.get("alt_corrected", ""))) or None,
            "reason": normalize_space(str(raw.get("reason", ""))),
            "checks": checks,
        }
    missing = [error_id for error_id in expected_ids if error_id not in out]
    if missing:
        raise SchemaError(f"S2b missing verdicts for {missing}")
    return out


def _evidence_in_sentences(evidence: Any, sentence_map: dict[int, str]) -> bool:
    if isinstance(evidence, str):
        needle = normalize_space(evidence)
        return bool(needle) and any(needle in normalize_space(text) for text in sentence_map.values())
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, str):
                if not _evidence_in_sentences(item, sentence_map):
                    return False
            elif isinstance(item, dict):
                sid = item.get("sid")
                text = normalize_space(str(item.get("text", "")))
                if not text:
                    return False
                if isinstance(sid, int) and sid in sentence_map:
                    if text not in normalize_space(sentence_map[sid]):
                        return False
                elif not any(text in normalize_space(t) for t in sentence_map.values()):
                    return False
            else:
                return False
        return True
    return False


def validate_s3(
    data: dict[str, Any],
    config: TaskConfig,
    sentences: list[dict[str, Any]],
    title: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    fields = data.get("fields")
    if not isinstance(fields, dict):
        raise SchemaError("S3 fields must be an object")
    sentence_map = {int(s["sid"]): s["text"] for s in sentences}
    evidence_map = dict(sentence_map)
    if title and title.strip():
        evidence_map[0] = title.strip()
    expected = {f.key: f for f in config.checklist.fields}
    missing = [key for key in expected if key not in fields]
    if missing:
        raise SchemaError(f"S3 missing fields {missing}")
    checklist: dict[str, Any] = {}
    invalid_keys: list[str] = []
    for key, spec in expected.items():
        raw = fields[key]
        if not isinstance(raw, dict) or "value" not in raw:
            raise SchemaError(f"S3 field {key} must be an object with value")
        value = raw.get("value")
        evidence = raw.get("evidence")
        valid = True
        if spec.type == "bool":
            if not isinstance(value, bool):
                raise SchemaError(f"S3 field {key} must be boolean")
            if value and not _evidence_in_sentences(evidence, evidence_map):
                value = None
                valid = False
        elif spec.type == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise SchemaError(f"S3 field {key} must be an integer")
            if value and not _evidence_in_sentences(evidence, evidence_map):
                value = None
                valid = False
        elif spec.type == "string":
            if not isinstance(value, str):
                raise SchemaError(f"S3 field {key} must be a string")
        elif spec.type == "string_list":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise SchemaError(f"S3 field {key} must be a string array")
            kept = [v for v in value if _evidence_in_sentences(v, evidence_map)]
            if len(kept) != len(value):
                valid = False
            value = kept
        elif spec.type == "evidence_items":
            if not isinstance(value, list):
                raise SchemaError(f"S3 field {key} must be an array")
            kept = []
            for item in value:
                if not isinstance(item, dict):
                    valid = False
                    continue
                text = normalize_space(str(item.get("text", "")))
                sid = item.get("sid")
                if not text or not isinstance(sid, int) or sid not in sentence_map:
                    valid = False
                    continue
                if text not in normalize_space(sentence_map[sid]):
                    valid = False
                    continue
                kept.append({"text": text, "sid": sid})
            value = kept
        else:
            raise SchemaError(f"S3 unknown field type {spec.type}")
        if not valid:
            invalid_keys.append(key)
        checklist[key] = {"value": value, "evidence": evidence, "valid": 1 if valid else 0}

    flags_raw = data.get("sentences")
    if not isinstance(flags_raw, list):
        raise SchemaError("S3 sentences must be an array")
    flags: list[dict[str, Any]] = []
    seen = set()
    for raw in flags_raw:
        if not isinstance(raw, dict):
            raise SchemaError("S3 sentence flag must be an object")
        sid = raw.get("sid")
        comprehensible = raw.get("comprehensible")
        if not isinstance(sid, int) or sid not in sentence_map or not isinstance(comprehensible, bool):
            raise SchemaError(f"S3 invalid sentence flag {raw}")
        flags.append({"sid": sid, "comprehensible": comprehensible})
        seen.add(sid)
    missing_sids = set(sentence_map) - seen
    if missing_sids:
        raise SchemaError(f"S3 missing sentence flags {sorted(missing_sids)}")
    return checklist, flags, invalid_keys


def scoring_critical_fields(config: TaskConfig) -> set[str]:
    fields: set[str] = {config.content.off_topic_field}
    for expr in config.content.required:
        fields.update(walk_fields(expr))
    if config.content.detail:
        fields.update(walk_fields(config.content.detail))
    for expr in (config.structure.zero_if, config.structure.order_ok, config.structure.ending_ok):
        if expr:
            fields.update(walk_fields(expr))
    out = set()
    for name in fields:
        if name.endswith("_count"):
            out.add(name[: -len("_count")])
        elif name not in {"missing_count", "detail_ok", "zero", "order_ok", "ending_ok"}:
            out.add(name)
    return out


def validate_s5(data: dict[str, Any], transcript: str, pseudonym: str = "") -> tuple[dict[str, str], list[str]]:
    keys = ["content_comment", "structure_comment", "language_summary", "encouragement_de"]
    comments: dict[str, str] = {}
    issues: list[str] = []
    for key in keys:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SchemaError(f"S5 missing {key}")
        comments[key] = normalize_space(value)
    body = normalize_space(transcript)
    for quote in re.findall(r"「([^」]+)」", " ".join(comments.values())):
        if normalize_space(quote) not in body:
            issues.append(f"引文不在原文中: {quote}")
    if not german_word_limit_ok(comments["encouragement_de"], 12):
        issues.append("德语鼓励语超过 12 词")
    if re.search(r"\d", comments["encouragement_de"]):
        issues.append("德语鼓励语含数字")
    if "[NAME]" in comments["encouragement_de"] or (pseudonym and pseudonym in comments["encouragement_de"]):
        issues.append("德语鼓励语含学生身份信息")
    for key in ("content_comment", "structure_comment", "language_summary"):
        if re.search(r"\d+\s*分", comments[key]) or "总分" in comments[key]:
            issues.append(f"{key} 含分数")
    return comments, issues

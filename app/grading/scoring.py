from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field
from typing import Any

from app.config.models import TaskConfig
from app.grading.rules import evaluate, facts_from_checklist


class ScoringInputError(ValueError):
    pass


@dataclass
class ScoreResult:
    language: float
    content: float
    structure: float
    total: float
    points: float
    title_deducted: float
    cap_applied: float | None
    trace: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "content": self.content,
            "structure": self.structure,
            "total": self.total,
            "points": self.points,
            "title_deducted": self.title_deducted,
            "cap_applied": self.cap_applied,
            "trace": self.trace,
        }


def effective_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for error in errors:
        if error.get("teacher_action") in {"rejected", "over_edit"}:
            continue
        if error.get("effective") in (0, False):
            continue
        if str(error.get("certainty", "")).strip() not in {"确定", "confirmed"}:
            continue
        out.append(error)
    return out


def language_points(config: TaskConfig, errors: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    weights = config.weight_for
    verb_type = config.dedup.verb_structure.type
    trace: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    verb_sids: dict[int, list[dict[str, Any]]] = {}
    for error in errors:
        etype = str(error.get("type", ""))
        if etype not in weights:
            trace.append({"kind": "skip", "error_id": error.get("id"), "detail": f"未知错误类型 {etype}"})
            continue
        if etype == verb_type:
            verb_sids.setdefault(int(error.get("sid", 0)), []).append(error)
        else:
            code = str(error.get("error_code", "")).strip()
            pattern = str(error.get("pattern", "")).strip() or code or f"{etype}:unknown"
            groups.setdefault((etype, code or pattern), []).append(error)

    points = 0.0
    verb_used = 0.0
    for sid, items in sorted(verb_sids.items()):
        add = min(config.dedup.verb_structure.per_sentence, config.dedup.verb_structure.cap - verb_used)
        add = max(0.0, add)
        verb_used += add
        points += add
        for i, error in enumerate(items):
            trace.append({
                "kind": "verb_structure",
                "error_id": error.get("id"),
                "sid": sid,
                "add": add if i == 0 else 0,
                "detail": "整句谓语缺失，按句计" if i == 0 else "同句重复，已合并",
            })

    for (etype, pattern), items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        weight = weights[etype]
        count = len(items)
        raw = weight * (1 + config.dedup.repeat_weight * (count - 1))
        add = min(raw, config.dedup.pattern_cap)
        points += add
        trace.append({
            "kind": "pattern",
            "error_id": items[0].get("id"),
            "type": etype,
            "pattern": pattern,
            "count": count,
            "add": round(add, 2),
            "detail": f"{etype}/{pattern} 出现 {count} 次，计 {round(add, 2)} 点",
        })
        for error in items[1:]:
            trace.append({
                "kind": "repeat",
                "error_id": error.get("id"),
                "add": 0,
                "detail": "同类重复，评语标反复出现",
            })
    return round(points, 2), trace


def language_score(config: TaskConfig, errors: list[dict[str, Any]], sentence_flags: list[dict[str, Any]]) -> tuple[float, float, list[dict[str, Any]]]:
    if not sentence_flags:
        raise ScoringInputError("S3 did not return per-sentence comprehensibility")
    points, trace = language_points(config, errors)
    ratio = sum(1 for s in sentence_flags if s.get("comprehensible")) / len(sentence_flags)
    if ratio == 0:
        return 0.0, points, trace + [{"kind": "comprehension", "value": 0, "detail": "没有可懂的句子"}]
    score = config.language_config.default_score
    for band in config.language_config.table:
        if points <= band.max_points:
            score = band.score
            break
    comp = config.language_config.comprehension
    if ratio < comp.ceiling_below_ratio:
        score = min(score, comp.ceiling_score)
        trace.append({"kind": "comprehension", "value": round(ratio, 3), "detail": f"可懂比例低于 {comp.ceiling_below_ratio}，封顶 {comp.ceiling_score}"})
    if ratio >= comp.floor_min_ratio:
        score = max(score, comp.floor_score)
        trace.append({"kind": "comprehension", "value": round(ratio, 3), "detail": f"可懂比例达到 {comp.floor_min_ratio}，下限 {comp.floor_score}"})
    return float(score), points, trace


def content_score(config: TaskConfig, facts: dict[str, Any]) -> tuple[float, list[dict[str, Any]], int, bool]:
    trace: list[dict[str, Any]] = []
    if not facts.get(config.content.off_topic_field):
        trace.append({"kind": "content", "detail": "离题或缺少主题要素，内容 0 分"})
        return 0.0, trace, len(config.content.required), False
    missing = sum(1 for expr in config.content.required if not evaluate(expr, facts))
    detail_ok = bool(evaluate(config.content.detail, facts)) if config.content.detail else False
    local = dict(facts, missing_count=missing, detail_ok=detail_ok)
    for band in config.content.bands:
        if band.when is None or evaluate(band.when, local):
            trace.append({"kind": "content", "detail": f"缺 {missing} 项，细节={detail_ok}", "value": band.score})
            return float(band.score), trace, missing, detail_ok
    return 0.0, trace, missing, detail_ok


def structure_score(config: TaskConfig, facts: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    zero = bool(evaluate(config.structure.zero_if, facts)) if config.structure.zero_if else False
    order_ok = bool(evaluate(config.structure.order_ok, facts))
    ending_ok = bool(evaluate(config.structure.ending_ok, facts))
    local = dict(facts, zero=zero, order_ok=order_ok, ending_ok=ending_ok)
    for band in config.structure.bands:
        if band.when is None or evaluate(band.when, local):
            trace = [{"kind": "structure", "detail": f"矛盾={zero} 顺序={order_ok} 结尾={ending_ok}", "value": band.score}]
            return float(band.score), trace
    return 0.0, []


def round_to_step(value: float, step: float) -> float:
    if step <= 0:
        return round(value, 2)
    d_value = Decimal(str(value))
    d_step = Decimal(str(step))
    units = (d_value / d_step).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(units * d_step)


def score_submission(
    config: TaskConfig,
    errors: list[dict[str, Any]],
    checklist: dict[str, Any],
    sentences: list[dict[str, Any]],
    word_count: int,
    has_title: bool,
) -> ScoreResult:
    facts = facts_from_checklist(checklist)
    confirmed = effective_errors(errors)
    lang, points, lang_trace = language_score(config, confirmed, sentences)
    content, content_trace, missing, detail_ok = content_score(config, facts)
    structure, structure_trace = structure_score(config, facts)
    raw = lang + content + structure
    trace = [
        {"kind": "language", "value": lang, "points": points},
        {"kind": "content", "value": content, "missing": missing, "detail": detail_ok},
        {"kind": "structure", "value": structure},
        *lang_trace,
        *content_trace,
        *structure_trace,
    ]
    # Published order: sum -> length cap -> fixed deduction -> clamp -> round once.
    cap_applied: float | None = None
    for cap in config.length.caps:
        if word_count < cap.lt:
            if raw > cap.total_max:
                raw = cap.total_max
                cap_applied = cap.total_max
                trace.append({"kind": "length", "value": cap.total_max, "detail": f"{word_count} 词 < {cap.lt}，总分封顶"})
            break
    title_deducted = 0.0
    if config.title_rubric.required and not has_title and config.title_rubric.deduct:
        title_deducted = config.title_rubric.deduct
        raw -= title_deducted
        trace.append({"kind": "title", "value": -title_deducted, "detail": "缺少标题"})
    raw = max(0.0, min(raw, config.total_max))
    total = round_to_step(raw, config.rounding.step)
    return ScoreResult(lang, content, structure, total, points, title_deducted, cap_applied, trace)

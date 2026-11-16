from __future__ import annotations

import difflib
import re
import string
from dataclasses import dataclass

from app.grading.text import tokenize


FUNCTION_WORDS = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines",
    "ich", "du", "er", "sie", "es", "wir", "ihr", "mich", "dich", "sich", "uns", "euch",
    "mein", "meine", "meinen", "meinem", "meiner", "dein", "deine", "deinen", "deinem", "deiner",
    "sein", "seine", "seinen", "seinem", "seiner", "unser", "unsere", "unseren", "unserem", "unserer",
    "nicht", "kein", "keine", "keinen", "keinem", "keiner",
    "und", "oder", "aber", "denn", "sondern", "weil", "dass", "wenn", "als", "ob", "damit", "obwohl",
    "in", "im", "am", "an", "auf", "aus", "bei", "mit", "nach", "von", "vor", "zu", "zum", "zur",
    "über", "unter", "hinter", "neben", "zwischen", "durch", "für", "gegen", "ohne", "um", "seit",
    "ist", "sind", "war", "waren", "bin", "bist", "seid", "habe", "hast", "hat", "haben", "hatte",
    "hatten", "wird", "werden", "wurde", "wurden", "kann", "kannst", "können", "muss", "musst",
    "müssen", "will", "willst", "wollen", "soll", "sollst", "sollen", "darf", "darfst", "dürfen",
    "mag", "möchte", "möchtest", "möchten", "auch", "noch", "schon", "nur", "gern", "gerne",
    "dann", "danach", "zuerst", "später", "dort", "hier", "da", "so", "wie", "was", "wer", "wo",
    "wann", "warum", "man", "beim", "vom", "ins", "ans", "aufs", "sehr", "ganz", "wirklich",
}

STYLE_WORDS = {"sehr", "ganz", "wirklich", "total", "super", "toll", "schön", "wunderschön", "lecker"}

REWRITE_MARKERS = (" / ", "(oder", "alternativ", " entweder ")


@dataclass(frozen=True)
class EditVerdict:
    ok: bool
    reason: str
    changed_tokens: int


def _stem(word: str) -> str:
    w = word.lower()
    for prefix in ("ge",):
        if len(w) > 5 and w.startswith(prefix) and w.endswith(("en", "t")):
            w = w[2:]
            break
    for suffix in ("ungen", "ung", "lich", "isch", "en", "er", "es", "em", "e", "st", "t", "n", "s"):
        if len(w) - len(suffix) >= 4 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def _looks_content(word: str) -> bool:
    w = word.strip(string.punctuation + "„“”’")
    if not w or w.lower() in FUNCTION_WORDS or w.lower() in STYLE_WORDS:
        return False
    return bool(re.fullmatch(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß-]*", w))


def _semantic_replacement(a: str, b: str) -> str | None:
    a_clean = a.strip(string.punctuation + "„“”’")
    b_clean = b.strip(string.punctuation + "„“”’")
    if not a_clean or not b_clean:
        return None
    if a_clean.lower() in FUNCTION_WORDS or b_clean.lower() in FUNCTION_WORDS:
        return None
    if a_clean.lower() in STYLE_WORDS or b_clean.lower() in STYLE_WORDS:
        return None
    if _stem(a_clean) == _stem(b_clean):
        return None
    common = 0
    for x, y in zip(a_clean.lower(), b_clean.lower()):
        if x != y:
            break
        common += 1
    similarity = difflib.SequenceMatcher(None, a_clean.lower(), b_clean.lower()).ratio()
    if a_clean[:1].isupper() and b_clean[:1].isupper() and common < 3:
        return f"替换了名词 {a_clean} -> {b_clean}"
    if len(a_clean) >= 6 and len(b_clean) >= 6 and common < 3:
        return f"替换了实义词 {a_clean} -> {b_clean}"
    if len(a_clean) >= 4 and len(b_clean) >= 4 and similarity < 0.35:
        return f"疑似替换实义词 {a_clean} -> {b_clean}"
    return None


def check_minimal_edit(original: str, corrected: str) -> EditVerdict:
    if not corrected or not corrected.strip():
        return EditVerdict(False, "修改为空，疑似删除原句", 1)
    if any(marker in f" {corrected} " for marker in REWRITE_MARKERS):
        return EditVerdict(False, "修改包含多个备选或重写标记", 1)
    o, c = tokenize(original), tokenize(corrected)
    if not o:
        return EditVerdict(False, "原文没有可比较的词", 0)
    if sorted(w.lower() for w in o) == sorted(w.lower() for w in c):
        return EditVerdict(True, "仅调整词序", 0)
    if len(c) > len(o) + 2:
        return EditVerdict(False, f"修改新增 {len(c) - len(o)} 个词，疑似重写", len(c) - len(o))
    matcher = difflib.SequenceMatcher(a=[w.lower() for w in o], b=[w.lower() for w in c])
    changed = 0
    reasons: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            changed += max(i2 - i1, j2 - j1)
            for a, b in zip(o[i1:i2], c[j1:j2]):
                reason = _semantic_replacement(a, b)
                if reason:
                    reasons.append(reason)
            if (i2 - i1) != (j2 - j1):
                reasons.append("替换时改变了词数")
        elif tag == "insert":
            changed += j2 - j1
            inserted = [w for w in c[j1:j2] if _looks_content(w) or w.lower() in STYLE_WORDS]
            if inserted:
                reasons.append(f"新增实词 {', '.join(inserted)}，疑似扩写")
        elif tag == "delete":
            changed += i2 - i1
            deleted = [w for w in o[i1:i2] if _looks_content(w)]
            if deleted:
                reasons.append(f"删除实词 {', '.join(deleted)}")
    limit = max(2, int(round(len(o) * 0.4)))
    if changed > limit:
        reasons.append(f"改动 {changed} 个词，超过上限 {limit}")
    if reasons:
        return EditVerdict(False, "; ".join(dict.fromkeys(reasons)), changed)
    return EditVerdict(True, "最小改动", changed)

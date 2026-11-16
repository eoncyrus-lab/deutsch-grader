from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Sentence:
    sid: int
    text: str
    start: int
    end: int

    def as_dict(self) -> dict:
        return {"sid": self.sid, "text": self.text, "start": self.start, "end": self.end}


WORD_RE = re.compile(
    r"[A-Za-zÄÖÜäöüß]+(?:[-'’][A-Za-zÄÖÜäöüß]+)*"
    r"|\d+(?:[.,]\d+)*"
    r"|\[[?A-Z][A-Za-z?\s]*\]"
)

ABBREVIATIONS = {
    "z.B.", "z. B.", "u.a.", "u. a.", "d.h.", "d. h.", "usw.", "bzw.", "ca.", "Nr.",
    "Dr.", "Mr.", "Mrs.", "St.", "Abs.", "etc.", "vgl.", "sog.", "inkl.", "max.",
    "min.", "evtl.", "ggf.", "bzw", "at", "Tel.",
}


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text)


def word_count(text: str) -> int:
    return len(tokenize(text))


def count_words(sentences: list[Sentence]) -> int:
    return sum(word_count(s.text) for s in sentences)


def join_transcript(title: str | None, lines: list[str], exclude_title: bool = True) -> str:
    body = " ".join(normalize_space(line) for line in lines if normalize_space(line))
    if title and not exclude_title:
        return normalize_space(f"{title} {body}")
    return body


def split_sentences(text: str) -> list[Sentence]:
    """Split German text while keeping offsets and simple abbreviations intact."""
    masked = list(text)
    for abbrev in sorted(ABBREVIATIONS, key=len, reverse=True):
        for match in re.finditer(re.escape(abbrev), text):
            for i in range(match.start(), match.end()):
                if text[i] in ".!?":
                    masked[i] = "\u0002"
    for match in re.finditer(r"(?<=\d)\.(?=\d)", text):
        masked[match.start()] = "\u0002"
    for match in re.finditer(r"\.{3,}", text):
        for i in range(match.start(), match.end()):
            masked[i] = "\u0002"
    masked_text = "".join(masked)

    boundaries: list[int] = []
    pattern = re.compile(r"[.!?]+[\"'“”’)]*")
    for match in pattern.finditer(masked_text):
        tail = masked_text[match.end():]
        if tail.strip():
            next_char = tail.lstrip()[:1]
            if not (next_char.isupper() or next_char.isdigit() or next_char in "„\"'("):
                continue
        boundaries.append(match.end())

    sentences: list[Sentence] = []
    start = 0
    for end in boundaries + [len(text)]:
        chunk = text[start:end]
        stripped = chunk.strip()
        if stripped:
            leading = len(chunk) - len(chunk.lstrip())
            s_start = start + leading
            s_end = s_start + len(stripped)
            sentences.append(Sentence(len(sentences) + 1, text[s_start:s_end], s_start, s_end))
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
    return sentences


def find_span(sentence_text: str, original: str, corrected: str | None, span: list[int] | tuple[int, int] | None) -> tuple[int, int] | None:
    """Resolve an error span inside one sentence; whitespace tolerant."""
    original = normalize_space(original)
    if not original:
        return None
    if span and isinstance(span, (list, tuple)) and len(span) == 2:
        start, end = int(span[0]), int(span[1])
        if 0 <= start < end <= len(sentence_text):
            if normalize_space(sentence_text[start:end]) == original:
                return start, end
    pattern = re.compile(r"\s+".join(re.escape(part) for part in original.split()))
    matches = list(pattern.finditer(sentence_text))
    if not matches:
        return None
    chosen = matches[0]
    if len(matches) > 1 and corrected:
        for match in matches:
            if normalize_space(match.group(0)) == original:
                chosen = match
                break
    return chosen.start(), chosen.end()


def contains_any(text: str, needles: list[str]) -> bool:
    return any(n and n in text for n in needles)


def german_word_limit_ok(text: str, limit: int = 12) -> bool:
    return word_count(text) <= limit

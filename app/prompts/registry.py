from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


PROMPT_DIR = Path(__file__).resolve().parent
PLACEHOLDER = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class Prompt:
    ref: str
    template: str
    sha256: str

    def render(self, variables: dict[str, str]) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in variables:
                raise PromptError(f"missing prompt variable {key} for {self.ref}")
            return str(variables[key])

        return PLACEHOLDER.sub(replace, self.template)


def load_prompt(ref: str) -> Prompt:
    path = PROMPT_DIR / f"{ref}.txt"
    if not path.exists():
        raise PromptError(f"unknown prompt version {ref}")
    text = path.read_text(encoding="utf-8")
    return Prompt(ref=ref, template=text, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())


def pipeline_version(prompts: dict[str, str], config_hash: str, code_hash: str = "v1") -> str:
    digest = hashlib.sha256()
    for stage, ref in sorted(prompts.items()):
        digest.update(f"{stage}:{load_prompt(ref).sha256};".encode())
    digest.update(config_hash.encode())
    digest.update(code_hash.encode())
    return digest.hexdigest()[:16]

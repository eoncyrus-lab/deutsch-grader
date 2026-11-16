from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Expr(BaseModel):
    op: Literal["and", "or", "not", "eq", "ne", "gt", "gte", "lt", "lte", "field", "const"] | None = None
    args: list["Expr"] = Field(default_factory=list)
    field: str | None = None
    value: Any = None

    @model_validator(mode="after")
    def _check(self) -> "Expr":
        if self.op is None:
            if self.field is not None and self.value is None:
                self.op = "field"
            elif self.value is not None:
                self.op = "const"
            else:
                raise ValueError("expression needs op, field or value")
        if self.op in {"and", "or"} and not self.args:
            raise ValueError(f"{self.op} needs args")
        if self.op == "not" and len(self.args) != 1:
            raise ValueError("not needs exactly one arg")
        if self.op == "field" and not self.field:
            raise ValueError("field op needs field")
        if self.op in {"eq", "ne", "gt", "gte", "lt", "lte"} and len(self.args) != 2:
            raise ValueError(f"{self.op} needs two args")
        return self


class ErrorType(BaseModel):
    key: str
    weight: float = Field(ge=0)
    impact: int = Field(default=1, ge=1, le=5)
    description: str = ""


class ErrorCode(BaseModel):
    code: str
    type: str
    label: str
    examples: list[str] = Field(default_factory=list)


class VerbStructure(BaseModel):
    type: str = "动词结构缺失"
    per_sentence: float = Field(default=1.0, ge=0)
    cap: float = Field(default=4.0, ge=0)


class Dedup(BaseModel):
    repeat_weight: float = Field(default=0.25, ge=0, le=1)
    pattern_cap: float = Field(default=2.0, gt=0)
    verb_structure: VerbStructure = Field(default_factory=VerbStructure)


class LanguageBand(BaseModel):
    max_points: float = Field(ge=0)
    score: float = Field(ge=0)


class Comprehension(BaseModel):
    floor_score: float = Field(default=2.0, ge=0)
    floor_min_ratio: float = Field(default=0.5, ge=0, le=1)
    ceiling_score: float = Field(default=1.0, ge=0)
    ceiling_below_ratio: float = Field(default=0.3, ge=0, le=1)


class LanguageConfig(BaseModel):
    max: float = Field(gt=0)
    table: list[LanguageBand]
    default_score: float = Field(ge=0)
    comprehension: Comprehension = Field(default_factory=Comprehension)


class ChecklistField(BaseModel):
    key: str
    type: Literal["bool", "int", "string", "string_list", "evidence_items"]
    question: str


class ChecklistConfig(BaseModel):
    fields: list[ChecklistField]


class ScoreBand(BaseModel):
    when: Expr | None = None
    score: float = Field(ge=0)


class ContentConfig(BaseModel):
    max: float = Field(gt=0)
    off_topic_field: str = "on_topic"
    required: list[Expr]
    detail: Expr | None = None
    bands: list[ScoreBand]


class StructureConfig(BaseModel):
    max: float = Field(gt=0)
    zero_if: Expr | None = None
    order_ok: Expr
    ending_ok: Expr
    bands: list[ScoreBand]


class LengthCap(BaseModel):
    lt: int = Field(gt=0)
    total_max: float = Field(ge=0)


class LengthConfig(BaseModel):
    unit: Literal["words"] = "words"
    exclude_title: bool = True
    caps: list[LengthCap]


class TitleConfig(BaseModel):
    required: bool = False
    deduct: float = Field(default=0.0, ge=0)


class RoundingConfig(BaseModel):
    step: float = Field(default=0.5, gt=0)


class TaskConfig(BaseModel):
    task_id: str
    version: str
    name: str
    language: str = "de"
    level: str
    grade_band: str = ""
    rubric_id: str
    rubric_version: str
    topic_default: str = ""
    status: Literal["draft", "shadow", "active", "retired"] = "draft"
    prompts: dict[str, str]
    model_routes: dict[str, str]
    error_types: list[ErrorType]
    error_codes: list[ErrorCode] = Field(default_factory=list)
    dedup: Dedup = Field(default_factory=Dedup)
    language_config: LanguageConfig = Field(alias="language_rubric")
    checklist: ChecklistConfig
    content: ContentConfig
    structure: StructureConfig
    length: LengthConfig
    title_rubric: TitleConfig = Field(alias="title")
    rounding: RoundingConfig = Field(default_factory=RoundingConfig)

    @property
    def total_max(self) -> float:
        return self.language_config.max + self.content.max + self.structure.max

    @property
    def error_type_keys(self) -> list[str]:
        return [e.key for e in self.error_types]

    @property
    def weight_for(self) -> dict[str, float]:
        return {e.key: e.weight for e in self.error_types}

    @property
    def impact_for(self) -> dict[str, int]:
        return {e.key: e.impact for e in self.error_types}

    @property
    def code_map(self) -> dict[str, ErrorCode]:
        return {c.code: c for c in self.error_codes}

    @model_validator(mode="after")
    def _validate(self) -> "TaskConfig":
        keys = self.error_type_keys
        if len(keys) != len(set(keys)):
            raise ValueError("error_types contains duplicates")
        code_keys = [c.code for c in self.error_codes]
        if len(code_keys) != len(set(code_keys)):
            raise ValueError("error_codes contains duplicates")
        for code in self.error_codes:
            if code.type not in keys:
                raise ValueError(f"error code {code.code} references unknown type {code.type}")
        if self.dedup.verb_structure.type not in keys:
            raise ValueError("dedup.verb_structure.type must be one of error_types")
        if not self.language_config.table:
            raise ValueError("language table is empty")
        last_limit = -1.0
        last_score = float("inf")
        for band in self.language_config.table:
            if band.max_points <= last_limit:
                raise ValueError("language table max_points must be strictly increasing")
            if band.score > last_score:
                raise ValueError("language table scores must be non-increasing")
            last_limit, last_score = band.max_points, band.score
        if self.language_config.table[-1].score < self.language_config.default_score:
            raise ValueError("default language score is above the last table score")
        if self.language_config.max < max(b.score for b in self.language_config.table):
            raise ValueError("language max is below a table score")
        if self.content.bands[-1].when is not None:
            raise ValueError("content bands must end with an unconditional fallback")
        if self.structure.bands[-1].when is not None:
            raise ValueError("structure bands must end with an unconditional fallback")
        caps = [c.lt for c in self.length.caps]
        if caps != sorted(caps) or len(caps) != len(set(caps)):
            raise ValueError("length caps must be sorted and unique")
        field_keys = {f.key for f in self.checklist.fields}
        if len(field_keys) != len(self.checklist.fields):
            raise ValueError("checklist field keys must be unique")
        for required in self.content.required:
            for ref in walk_fields(required):
                if ref in {"missing_count", "detail_ok", "on_topic"}:
                    continue
                if ref not in field_keys and ref not in {"activities_count", "sequence_markers_count"}:
                    raise ValueError(f"content rule references unknown field {ref}")
        for expr in [self.structure.order_ok, self.structure.ending_ok, self.structure.zero_if]:
            if expr is None:
                continue
            for ref in walk_fields(expr):
                if ref in {"zero", "order_ok", "ending_ok"}:
                    continue
                if ref not in field_keys and not ref.endswith("_count"):
                    raise ValueError(f"structure rule references unknown field {ref}")
        for stage in ("s1", "s2", "s2b", "s3", "s5"):
            if stage not in self.prompts or stage not in self.model_routes:
                raise ValueError(f"prompts/model_routes missing stage {stage}")
        return self


def walk_fields(expr: Expr) -> list[str]:
    out: list[str] = []
    if expr.op == "field" and expr.field:
        out.append(expr.field)
    for arg in expr.args:
        out.extend(walk_fields(arg))
    return out

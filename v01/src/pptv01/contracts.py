"""Pydantic models for every on-disk contract of the V0.1 chain (plan section 3).

Every contract file carries ``schema_version`` ("0.1") and ``run_id``.
IDs are never reused: pages ``S001``..., asset requirements ``A001``...
All paths stored in contracts are relative to the run directory (no absolute
or machine-specific paths).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "0.1"

PAGE_ID_RE = r"^S\d{3,}$"
ASSET_ID_RE = r"^A\d{3,}$"
ELEMENT_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
RUN_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PageId = Annotated[str, StringConstraints(pattern=PAGE_ID_RE)]
AssetId = Annotated[str, StringConstraints(pattern=ASSET_ID_RE)]
ElementId = Annotated[str, StringConstraints(pattern=ELEMENT_ID_RE)]
RunId = Annotated[str, StringConstraints(pattern=RUN_ID_RE)]
# Controlled-vocabulary style token (snake_case); the original wording is kept elsewhere.
Token = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
LangTag = Annotated[str, StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")]


def _check_relative_path(value: str) -> str:
    """Reject absolute / Windows / parent-escaping paths (plan section 9)."""
    if "\\" in value or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"path must be a relative POSIX path, got {value!r}")
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"path must be relative and stay inside the run dir: {value!r}")
    return value


RelPath = Annotated[str, StringConstraints(min_length=1)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class _RunFile(_Contract):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    run_id: RunId


# --------------------------------------------------------------------- 3.1 A
class DefaultApplied(_Contract):
    field: NonEmptyStr
    value: NonEmptyStr
    rationale: NonEmptyStr


class ProductionPlan(_RunFile):
    source_utterance: NonEmptyStr
    topic: NonEmptyStr
    purpose: Token
    audience: Token
    language: LangTag
    style: Token
    constraints: list[NonEmptyStr] = Field(default_factory=list)
    defaults_applied: list[DefaultApplied] = Field(default_factory=list)
    content_direction: NonEmptyStr
    open_questions: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _defaults_consistent(self) -> "ProductionPlan":
        seen: set[str] = set()
        for d in self.defaults_applied:
            if d.field in seen:
                raise ValueError(f"defaults_applied has duplicate field {d.field!r}")
            seen.add(d.field)
            actual = getattr(self, d.field, None)
            if isinstance(actual, str) and actual != d.value:
                raise ValueError(
                    f"defaults_applied[{d.field}].value={d.value!r} does not match "
                    f"plan value {actual!r}"
                )
        return self


# --------------------------------------------------------------------- 3.2 B
class SlideStatus(str, Enum):
    provisional = "provisional"
    revised = "revised"


SourceRef = Annotated[str, StringConstraints(pattern=r"^R\d{3,}$")]  # research note id (stage B retrieval)


class AssetType(str, Enum):
    """Asset kinds shared by stage B (asset_needs) and stage C (requirements)."""

    image = "image"
    icon = "icon"
    diagram = "diagram"
    chart_data = "chart_data"
    table = "table"
    reference = "reference"
    statistic = "statistic"


class Verification(str, Enum):
    """Has this concrete target been checked against a retrieved source?

    to_verify (待核实): a candidate that came from model background knowledge only.
    verified  (已核实): backed by at least one retrieved source (source_refs).
    """

    to_verify = "to_verify"
    verified = "verified"


# types for which a concrete target (entity/dataset/publication...) is mandatory;
# diagrams and icons may be about a concept and need no named target.
TARGET_OPTIONAL_TYPES = {AssetType.diagram, AssetType.icon}


class AssetTarget(_Contract):
    name: NonEmptyStr
    entity_type: NonEmptyStr = "entity"   # building / city / dataset / publication / concept ...
    verification: Verification = Verification.to_verify
    source_refs: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _verified_needs_source(self) -> "AssetTarget":
        if self.verification is Verification.verified and not self.source_refs:
            raise ValueError(f"target {self.name!r} is marked verified but cites no source_refs")
        if self.verification is Verification.to_verify and self.source_refs:
            raise ValueError(f"target {self.name!r} cites sources but is still marked to_verify")
        return self


class AssetNeed(_Contract):
    """What a page needs, in terms stage C can turn into a requirement."""

    type: AssetType
    description: NonEmptyStr
    targets: list[AssetTarget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _targets_for_concrete_types(self) -> "AssetNeed":
        if self.type not in TARGET_OPTIONAL_TYPES and not self.targets:
            raise ValueError(
                f"{self.type.value} need {self.description!r} must name at least one concrete target "
                "(candidate entities are allowed and marked to_verify)"
            )
        return self


class SlidePage(_Contract):
    page_id: PageId
    title: NonEmptyStr
    goal: NonEmptyStr
    key_points: list[NonEmptyStr] = Field(min_length=1)
    content_summary: NonEmptyStr
    visual_intent: NonEmptyStr
    asset_needs: list[AssetNeed] = Field(default_factory=list)
    status: SlideStatus = SlideStatus.provisional


class RevisionEntry(_Contract):
    """One edit of the slide plan after real assets came back."""

    revision: int = Field(ge=1)
    change: NonEmptyStr
    page_ids: list[PageId] = Field(min_length=1)
    reason: NonEmptyStr
    evidence_asset_ids: list[AssetId] = Field(min_length=1)


class SlidePlan(_RunFile):
    status: SlideStatus
    narrative: NonEmptyStr
    pages: list[SlidePage] = Field(min_length=1)
    revision_log: list[RevisionEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules(self) -> "SlidePlan":
        ids = [p.page_id for p in self.pages]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate page_id in slide plan")
        if self.status is SlideStatus.provisional:
            if self.revision_log:
                raise ValueError("a provisional slide plan must have an empty revision_log")
            bad = [p.page_id for p in self.pages if p.status is not SlideStatus.provisional]
            if bad:
                raise ValueError(f"provisional plan cannot contain non-provisional pages: {bad}")
            expected = [f"S{i:03d}" for i in range(1, len(ids) + 1)]
            if ids != expected:
                raise ValueError(f"provisional page ids must run S001.. in order, got {ids}")
        else:
            if not self.revision_log:
                raise ValueError("a revised slide plan must append at least one revision_log entry")
            numbers = [r.revision for r in self.revision_log]
            if numbers != sorted(set(numbers)):
                raise ValueError("revision_log revision numbers must be strictly increasing")
        return self


# --------------------------------------------------------------------- 3.3 C
class Priority(str, Enum):
    required = "required"
    optional = "optional"


class AssetRequirement(_Contract):
    asset_id: AssetId
    page_ids: list[PageId] = Field(min_length=1)
    type: AssetType
    purpose: NonEmptyStr
    query_hints: list[NonEmptyStr] = Field(min_length=1)
    priority: Priority
    acceptance: list[NonEmptyStr] = Field(min_length=1)


class AssetRequirements(_RunFile):
    requirements: list[AssetRequirement]

    @model_validator(mode="after")
    def _unique(self) -> "AssetRequirements":
        ids = [r.asset_id for r in self.requirements]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate asset_id in asset requirements")
        return self


# --------------------------------------------------------------------- 3.4 D
class AssetStatus(str, Enum):
    acquired = "acquired"
    substituted = "substituted"
    drawn = "drawn"
    missing = "missing"


class AssetChecks(_Contract):
    relevance: str | None = None
    usability: str | None = None
    format: str | None = None
    quality: str | None = None


class AssetRecord(_Contract):
    asset_id: AssetId
    status: AssetStatus
    file_path: RelPath | None = None
    source_url: str | None = None
    source_name: str | None = None
    license_or_terms: str | None = None
    retrieved_at: datetime | None = None
    notes: str = ""
    checks: AssetChecks = Field(default_factory=AssetChecks)

    @field_validator("file_path")
    @classmethod
    def _rel(cls, v: str | None) -> str | None:
        return None if v is None else _check_relative_path(v)

    @model_validator(mode="after")
    def _by_status(self) -> "AssetRecord":
        s = self.status
        if s in (AssetStatus.acquired, AssetStatus.substituted):
            for f in ("file_path", "source_url", "source_name", "license_or_terms", "retrieved_at"):
                if not getattr(self, f):
                    raise ValueError(f"{s.value} asset {self.asset_id} requires {f}")
        if s is AssetStatus.drawn and not self.file_path:
            raise ValueError(f"drawn asset {self.asset_id} requires file_path")
        if s is AssetStatus.missing:
            if self.file_path:
                raise ValueError(f"missing asset {self.asset_id} must not carry a file_path")
        if s is not AssetStatus.acquired and not self.notes.strip():
            raise ValueError(f"{s.value} asset {self.asset_id} requires notes explaining why")
        return self


class AssetRegistry(_RunFile):
    assets: list[AssetRecord]

    @model_validator(mode="after")
    def _unique(self) -> "AssetRegistry":
        ids = [a.asset_id for a in self.assets]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate asset_id in asset registry")
        return self


# --------------------------------------------------------------------- 3.5 E
class MappingEntry(_Contract):
    page_id: PageId
    asset_id: AssetId
    usage: NonEmptyStr
    accepted: bool


class UnusedAsset(_Contract):
    asset_id: AssetId
    reason: NonEmptyStr


class SlideAssetMapping(_RunFile):
    mappings: list[MappingEntry]
    unused_assets: list[UnusedAsset] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules(self) -> "SlideAssetMapping":
        pairs = [(m.page_id, m.asset_id) for m in self.mappings]
        if len(set(pairs)) != len(pairs):
            raise ValueError("duplicate (page_id, asset_id) mapping")
        used = {m.asset_id for m in self.mappings if m.accepted}
        clash = used & {u.asset_id for u in self.unused_assets}
        if clash:
            raise ValueError(f"assets both accepted on a page and listed unused: {sorted(clash)}")
        return self


# --------------------------------------------------------------------- 3.6 F
class ElementKind(str, Enum):
    title = "title"
    body = "body"
    bullet = "bullet"
    image = "image"
    shape = "shape"
    chart = "chart"
    caption = "caption"
    footer = "footer"


TEXT_KINDS = {ElementKind.title, ElementKind.body, ElementKind.bullet, ElementKind.caption, ElementKind.footer}
CANVAS_W_IN = 13.333
CANVAS_H_IN = 7.5
_EPS = 0.01


class DesignElement(_Contract):
    element_id: ElementId
    kind: ElementKind
    x_in: float
    y_in: float
    w_in: float = Field(gt=0)
    h_in: float = Field(gt=0)
    z_index: int = Field(default=0, ge=0)
    align: Literal["left", "center", "right", "justify"] = "left"
    text: str | None = None
    font: str | None = None
    font_size_pt: float | None = Field(default=None, gt=0)
    asset_id: AssetId | None = None
    asset_path: RelPath | None = None

    @field_validator("asset_path")
    @classmethod
    def _rel(cls, v: str | None) -> str | None:
        return None if v is None else _check_relative_path(v)

    @model_validator(mode="after")
    def _by_kind(self) -> "DesignElement":
        if self.kind in TEXT_KINDS and not (self.text and self.text.strip()):
            raise ValueError(f"{self.kind.value} element {self.element_id} requires text")
        if self.kind is ElementKind.image and not (self.asset_id and self.asset_path):
            raise ValueError(f"image element {self.element_id} must reference a bound asset (asset_id + asset_path)")
        if self.kind in TEXT_KINDS and self.font_size_pt is None:
            raise ValueError(f"text element {self.element_id} requires font_size_pt")
        return self


class PageDesignEntry(_Contract):
    page_id: PageId
    canvas_width_in: float = CANVAS_W_IN
    canvas_height_in: float = CANVAS_H_IN
    elements: list[DesignElement] = Field(min_length=1)
    preview_path: RelPath | None = None
    notes: str = ""

    @field_validator("preview_path")
    @classmethod
    def _rel(cls, v: str | None) -> str | None:
        return None if v is None else _check_relative_path(v)

    @model_validator(mode="after")
    def _rules(self) -> "PageDesignEntry":
        ids = [e.element_id for e in self.elements]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate element_id on page {self.page_id}")
        for e in self.elements:
            if (
                e.x_in < -_EPS
                or e.y_in < -_EPS
                or e.x_in + e.w_in > self.canvas_width_in + _EPS
                or e.y_in + e.h_in > self.canvas_height_in + _EPS
            ):
                raise ValueError(f"element {e.element_id} on {self.page_id} lies outside the canvas")
        return self


class PageDesign(_RunFile):
    pages: list[PageDesignEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> "PageDesign":
        ids = [p.page_id for p in self.pages]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate page_id in page design")
        return self


# --------------------------------------------------------------------- 3.7 H
class QAIssueKind(str, Enum):
    text_overflow = "text_overflow"
    occlusion = "occlusion"
    missing_image = "missing_image"
    layout_anomaly = "layout_anomaly"
    font_or_ratio = "font_or_ratio"
    content_asset_mismatch = "content_asset_mismatch"


class Severity(str, Enum):
    blocker = "blocker"
    major = "major"
    minor = "minor"


class Recheck(str, Enum):
    passed = "passed"
    failed = "failed"
    not_rechecked = "not_rechecked"


class QAIssue(_Contract):
    page_id: PageId
    kind: QAIssueKind
    severity: Severity
    description: NonEmptyStr
    element_id: ElementId | None = None
    fix_applied: bool = False
    fix_note: str = ""
    recheck: Recheck = Recheck.not_rechecked

    @model_validator(mode="after")
    def _rules(self) -> "QAIssue":
        if self.fix_applied and self.recheck is Recheck.not_rechecked:
            raise ValueError("a fix must be re-verified: recheck cannot be not_rechecked when fix_applied")
        if not self.fix_applied and self.recheck is not Recheck.not_rechecked:
            raise ValueError("recheck result without a fix applied")
        return self


class QAReport(_RunFile):
    renderer: NonEmptyStr
    rendered_pages: list[PageId]
    issues: list[QAIssue] = Field(default_factory=list)
    summary: str = ""


# ------------------------------------------------------------------ run report
class StageStatus(str, Enum):
    done = "done"
    failed = "failed"
    skipped = "skipped"
    not_implemented = "not_implemented"


class StageRecord(_Contract):
    stage: Literal["A", "B", "C", "D", "E", "F", "G", "H"]
    name: NonEmptyStr
    status: StageStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output_files: list[RelPath] = Field(default_factory=list)
    error: str | None = None

    @model_validator(mode="after")
    def _rules(self) -> "StageRecord":
        if self.status is StageStatus.failed and not self.error:
            raise ValueError("a failed stage must record its error")
        if self.status is StageStatus.not_implemented and self.output_files:
            raise ValueError("a not_implemented stage cannot list output files")
        return self


class RunOverall(str, Enum):
    complete = "complete"        # all stages A-H done
    incomplete = "incomplete"    # implemented stages ok, others not_implemented/skipped
    failed = "failed"


class RunReport(_RunFile):
    source_utterance: NonEmptyStr
    overall: RunOverall
    started_at: datetime
    finished_at: datetime
    model_id: str | None = None
    llm_backend: str | None = None
    code_commit: str = "unknown"
    code_dirty: bool | None = None
    tool_config: dict[str, Any] = Field(default_factory=dict)
    stages: list[StageRecord]
    defaults_summary: list[str] = Field(default_factory=list)
    missing_assets: list[AssetId] = Field(default_factory=list)
    revision_count: int = Field(default=0, ge=0)
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _rules(self) -> "RunReport":
        codes = [s.stage for s in self.stages]
        if codes != list("ABCDEFGH"):
            raise ValueError("run report must list stages A..H exactly once, in order")
        if self.overall is RunOverall.complete and any(s.status is not StageStatus.done for s in self.stages):
            raise ValueError("overall=complete requires every stage done")
        if self.overall is RunOverall.failed and not self.failure_reason:
            raise ValueError("overall=failed requires failure_reason")
        return self


# ------------------------------------------------------------------ file names
CONTRACT_FILES: dict[str, type[_RunFile]] = {
    "production_plan": ProductionPlan,
    "slide_plan": SlidePlan,
    "asset_requirements": AssetRequirements,
    "asset_registry": AssetRegistry,
    "slide_asset_mapping": SlideAssetMapping,
    "page_design": PageDesign,
    "qa_report": QAReport,
    "run_report": RunReport,
}

T = TypeVar("T", bound=_RunFile)


def write_contract(model: _RunFile, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_contract(cls: type[T], path: Path) -> T:
    return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, cls in CONTRACT_FILES.items():
        schema = cls.model_json_schema()
        schema["title"] = cls.__name__
        p = out_dir / f"{name}.schema.json"
        p.write_text(json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(p)
    return written


if __name__ == "__main__":  # pragma: no cover
    for p in export_schemas(Path(__file__).resolve().parents[2] / "contracts"):
        print(p)

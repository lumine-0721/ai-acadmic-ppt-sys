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
REQUEST_ID_RE = r"^U\d{3,}$"
ELEMENT_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
RUN_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PageId = Annotated[str, StringConstraints(pattern=PAGE_ID_RE)]
AssetId = Annotated[str, StringConstraints(pattern=ASSET_ID_RE)]
RequestId = Annotated[str, StringConstraints(pattern=REQUEST_ID_RE)]
ElementId = Annotated[str, StringConstraints(pattern=ELEMENT_ID_RE)]
RunId = Annotated[str, StringConstraints(pattern=RUN_ID_RE)]
NeedRef = Annotated[str, StringConstraints(pattern=r"^S\d{3,}#\d+$")]   # <page_id>#<1-based index of the page's asset_need>
SourceRef = Annotated[str, StringConstraints(pattern=r"^R\d{3,}$")]  # research note id (stage B retrieval)
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


class RetrievalQuery(_Contract):
    text: NonEmptyStr
    lang: Literal["zh", "en"]


class RetrievalTopic(_Contract):
    """One thing stage B should look up: a neutral question, not a conclusion."""

    topic: NonEmptyStr
    why: NonEmptyStr
    queries: list[RetrievalQuery] = Field(min_length=1, max_length=4)


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
    retrieval_brief: list[RetrievalTopic] = Field(min_length=1, max_length=8)

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


# ------------------------------------------------------- B: research record
class QueryStatus(str, Enum):
    ok = "ok"
    no_results = "no_results"
    error = "error"


class ResearchStatus(str, Enum):
    ok = "ok"                  # every query answered, notes found
    partial = "partial"        # some queries failed
    no_results = "no_results"  # backends answered, nothing relevant found
    failed = "failed"          # every query failed (e.g. no network)
    disabled = "disabled"      # research explicitly switched off; nothing was retrieved


class ResearchQueryRecord(_Contract):
    backend: NonEmptyStr
    topic: NonEmptyStr
    query: NonEmptyStr
    lang: Literal["zh", "en"]
    status: QueryStatus
    hits: int = Field(ge=0)
    error: str | None = None
    requested_at: datetime

    @model_validator(mode="after")
    def _rules(self) -> "ResearchQueryRecord":
        if self.status is QueryStatus.error and not self.error:
            raise ValueError("an errored query must record its error")
        if self.status is not QueryStatus.ok and self.hits:
            raise ValueError("only ok queries can have hits")
        return self


class ResearchNote(_Contract):
    note_id: SourceRef
    kind: Literal["encyclopedia", "reference", "web"]
    title: NonEmptyStr
    text: str = ""            # extract/abstract as returned by the source (may be empty)
    source_url: Annotated[str, StringConstraints(pattern=r"^https?://\S+$")]
    source_name: NonEmptyStr
    license_or_terms: str = ""
    backend: NonEmptyStr
    query: NonEmptyStr
    retrieved_at: datetime
    doc_type: str = ""                 # e.g. Crossref work type; "" when the backend does not say
    relevance: float | None = None     # score from the relevance filter's scorer (None = structural rules only)


class FilterReason(str, Enum):
    junk_title = "junk_title"
    too_short = "too_short"
    disambiguation = "disambiguation"
    list_page = "list_page"
    bad_type = "bad_type"
    low_relevance = "low_relevance"


class FilteredHit(_Contract):
    """A retrieved hit that the relevance filter removed. Kept on record so the removal is auditable."""

    title: NonEmptyStr
    source_url: Annotated[str, StringConstraints(pattern=r"^https?://\S+$")]
    kind: Literal["encyclopedia", "reference", "web"]
    backend: NonEmptyStr
    topic: NonEmptyStr
    query: NonEmptyStr
    doc_type: str = ""
    reason: FilterReason
    detail: str = ""
    score: float | None = None


class RelevanceInfo(_Contract):
    """How the filter ran. ``scorer is None`` means structural rules only (and ``note`` says why)."""

    mode: Literal["auto", "structural", "off"]
    rules_version: str
    scorer: str | None = None
    model: str | None = None
    threshold: float | None = None
    n_candidates: int = Field(ge=0)
    n_kept: int = Field(ge=0)
    n_filtered: int = Field(ge=0)
    note: str = ""


class ResearchRecord(_RunFile):
    """Everything stage B retrieved, or the honest record that nothing was."""

    provider: NonEmptyStr
    status: ResearchStatus
    queries: list[ResearchQueryRecord] = Field(default_factory=list)
    notes: list[ResearchNote] = Field(default_factory=list)
    filtered: list[FilteredHit] = Field(default_factory=list)
    relevance_filter: RelevanceInfo | None = None
    summary: str = ""

    @model_validator(mode="after")
    def _rules(self) -> "ResearchRecord":
        rf = self.relevance_filter
        if rf is not None and (rf.n_kept != len(self.notes) or rf.n_filtered != len(self.filtered)
                               or rf.n_candidates != rf.n_kept + rf.n_filtered):
            raise ValueError("relevance_filter counts do not match notes/filtered")
        if self.filtered and rf is None:
            raise ValueError("filtered hits require relevance_filter info")
        ids = [n.note_id for n in self.notes]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate note_id in research record")
        if self.status is ResearchStatus.disabled and (self.queries or self.notes):
            raise ValueError("a disabled research record cannot contain queries or notes")
        if self.status is ResearchStatus.failed:
            if self.notes or any(q.status is not QueryStatus.error for q in self.queries) or not self.queries:
                raise ValueError("status=failed requires queries that all errored and no notes")
        if self.status is ResearchStatus.ok and not self.notes:
            raise ValueError("status=ok requires at least one note")
        return self


# --------------------------------------------------------------------- 3.2 B
class SlideStatus(str, Enum):
    provisional = "provisional"
    revised = "revised"


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
    source_refs: list[SourceRef] = Field(default_factory=list)   # research notes this page relies on
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
    need_refs: list[NeedRef] = Field(min_length=1)    # stage-B asset_needs this requirement answers, e.g. "S001#2"
    type: AssetType
    purpose: NonEmptyStr
    targets: list[AssetTarget] = Field(default_factory=list)   # carried over from B, with B's verification state
    query_hints: list[NonEmptyStr] = Field(min_length=1)
    priority: Priority
    acceptance: list[NonEmptyStr] = Field(min_length=1)

    @model_validator(mode="after")
    def _concrete_types_keep_a_target(self) -> "AssetRequirement":
        if self.type not in TARGET_OPTIONAL_TYPES and not self.targets:
            raise ValueError(f"{self.asset_id}: {self.type.value} requirement must carry at least one concrete target")
        return self


class DroppedNeed(_Contract):
    need_ref: NeedRef
    reason: NonEmptyStr


class AssetRequirements(_RunFile):
    requirements: list[AssetRequirement]
    dropped_needs: list[DroppedNeed] = Field(default_factory=list)   # B needs deliberately not turned into a requirement

    @model_validator(mode="after")
    def _unique(self) -> "AssetRequirements":
        ids = [r.asset_id for r in self.requirements]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate asset_id in asset requirements")
        covered = {n for r in self.requirements for n in r.need_refs}
        both = covered & {d.need_ref for d in self.dropped_needs}
        if both:
            raise ValueError(f"needs both covered and dropped: {sorted(both)}")
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


class EntityRef(_Contract):
    """Wikidata disambiguation result for a named target. ``alternatives`` keeps what was NOT chosen."""

    name: NonEmptyStr                       # the target name as written in the requirement
    qid: Annotated[str, StringConstraints(pattern=r"^Q\d+$")]
    label: NonEmptyStr
    description: str = ""
    url: Annotated[str, StringConstraints(pattern=r"^https?://\S+$")]
    chosen_by: Literal["only_candidate", "exact_label_match", "search_rank", "relevance_score"]
    alternatives: list[str] = Field(default_factory=list)       # "Qid label - description"


class CandidateRecord(_Contract):
    """One candidate D looked at for a requirement, and why it was kept or rejected (audit trail)."""

    title: NonEmptyStr
    url: str = ""
    license: str = ""
    origin: str = ""                         # wikidata_p18 | commons_search | openalex | crossref
    decision: Literal["selected", "rejected"]
    reason: str = ""
    score: float | None = None


class SourceCall(_Contract):
    backend: NonEmptyStr                     # commons | wikidata | openalex | crossref
    purpose: NonEmptyStr
    query: str = ""
    status: Literal["ok", "no_results", "error", "disabled"]
    hits: int = Field(default=0, ge=0)
    error: str | None = None
    url: str | None = None
    elapsed_s: float | None = Field(default=None, ge=0)
    attempts: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _err(self) -> "SourceCall":
        if self.status == "error" and not self.error:
            raise ValueError("an errored source call must record its error")
        return self


class AssetRecord(_Contract):
    asset_id: AssetId
    status: AssetStatus
    type: AssetType | None = None
    file_path: RelPath | None = None
    source_url: str | None = None
    source_name: str | None = None
    license_or_terms: str | None = None
    author: str | None = None
    attribution: str | None = None            # ready-to-print credit line (CC BY / BY-SA need it)
    identifier: str | None = None             # DOI / Commons file title / OpenAlex id
    entity: EntityRef | None = None
    sha256: str | None = None
    mime: str | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    retrieved_at: datetime | None = None
    notes: str = ""
    shared_with: AssetId | None = None
    uncovered_targets: list[NonEmptyStr] = Field(default_factory=list)   # targets of a multi-target requirement NOT acquired
    candidates: list[CandidateRecord] = Field(default_factory=list)
    visual_relevance: Literal["relevant", "weak", "irrelevant", "not_assessed"] = "not_assessed"
    needs_review: bool = False
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
        if s is AssetStatus.acquired and self.type is AssetType.image:
            if not (self.sha256 and self.mime and self.width and self.height):
                raise ValueError(f"acquired image {self.asset_id} requires sha256, mime, width and height")
            if re.search(r"\bBY\b", (self.license_or_terms or "").upper()) and not (self.author and self.attribution):
                raise ValueError(f"CC BY(-SA) image {self.asset_id} requires author and attribution")
        if s is AssetStatus.acquired and self.type is AssetType.reference and not self.identifier:
            raise ValueError(f"acquired reference {self.asset_id} requires an identifier (DOI)")
        if self.shared_with is not None:
            if self.shared_with == self.asset_id:
                raise ValueError(f"asset {self.asset_id} cannot set shared_with to itself")
            if s is not AssetStatus.acquired:
                raise ValueError(f"only acquired assets can set shared_with, got {s.value}")
        return self


class AssetRegistry(_RunFile):
    assets: list[AssetRecord]
    sources: list[SourceCall] = Field(default_factory=list)
    relevance_filter: RelevanceInfo | None = None
    summary: str = ""

    @model_validator(mode="after")
    def _unique(self) -> "AssetRegistry":
        ids = [a.asset_id for a in self.assets]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate asset_id in asset registry")
        return self


# --------------------------------------------------------------------- 3.5 E
class MappingEntry(_Contract):
    page_id: PageId
    asset_id: AssetId | None = None
    request_id: RequestId | None = None
    usage: NonEmptyStr
    accepted: bool
    relevance: Literal["relevant", "weak", "irrelevant", "not_assessed"] = "not_assessed"
    relevance_note: str = ""

    @model_validator(mode="after")
    def _has_ref(self) -> "MappingEntry":
        if not self.asset_id and not self.request_id:
            raise ValueError("mapping entry must identify asset_id or request_id")
        return self


class UnusedAsset(_Contract):
    asset_id: AssetId | None = None
    request_id: RequestId | None = None
    reason: NonEmptyStr

    @model_validator(mode="after")
    def _has_ref(self) -> "UnusedAsset":
        if not self.asset_id and not self.request_id:
            raise ValueError("unused asset must identify asset_id or request_id")
        return self


class SlideAssetMapping(_RunFile):
    mappings: list[MappingEntry]
    unused_assets: list[UnusedAsset] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules(self) -> "SlideAssetMapping":
        pairs = [(m.page_id, m.asset_id, m.request_id) for m in self.mappings]
        if len(set(pairs)) != len(pairs):
            raise ValueError("duplicate (page_id, asset_id, request_id) mapping")
        used_a = {m.asset_id for m in self.mappings if m.accepted and m.asset_id}
        used_u = {m.request_id for m in self.mappings if m.accepted and m.request_id}
        clash_a = used_a & {u.asset_id for u in self.unused_assets if u.asset_id}
        clash_u = used_u & {u.request_id for u in self.unused_assets if u.request_id}
        if clash_a or clash_u:
            raise ValueError(f"assets both accepted on a page and listed unused: {sorted(clash_a | clash_u)}")
        return self


# --------------------------------------------------------------------- 3.6 F
class ElementKind(str, Enum):
    title = "title"
    body = "body"
    bullet = "bullet"
    image = "image"
    shape = "shape"
    chart = "chart"
    table = "table"
    placeholder = "placeholder"
    caption = "caption"
    footer = "footer"


TEXT_KINDS = {ElementKind.title, ElementKind.body, ElementKind.bullet, ElementKind.caption, ElementKind.footer}
BOUND_VISUAL_KINDS = {ElementKind.image, ElementKind.chart, ElementKind.table}
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
    bold: bool = False
    color_hex: str | None = None
    fill_hex: str | None = None
    line_hex: str | None = None
    role: str = ""
    asset_id: AssetId | None = None
    request_id: RequestId | None = None
    asset_path: RelPath | None = None

    @field_validator("asset_path")
    @classmethod
    def _rel(cls, v: str | None) -> str | None:
        return None if v is None else _check_relative_path(v)

    @model_validator(mode="after")
    def _by_kind(self) -> "DesignElement":
        if self.kind in TEXT_KINDS and not (self.text and self.text.strip()):
            raise ValueError(f"{self.kind.value} element {self.element_id} requires text")
        if self.kind in BOUND_VISUAL_KINDS and not ((self.asset_id or self.request_id) and self.asset_path):
            raise ValueError(
                f"{self.kind.value} element {self.element_id} must reference a bound asset "
                "(asset_id/request_id + asset_path)"
            )
        if self.kind in TEXT_KINDS and self.font_size_pt is None:
            raise ValueError(f"text element {self.element_id} requires font_size_pt")
        if self.kind is ElementKind.placeholder:
            if not self.request_id:
                raise ValueError(f"placeholder element {self.element_id} requires request_id")
            if not self.element_id.startswith(f"PLACEHOLDER_{self.request_id}"):
                raise ValueError(
                    f"placeholder element {self.element_id} must start with PLACEHOLDER_{self.request_id}"
                )
            if not (self.text and "待补充：" in self.text and self.request_id in self.text):
                raise ValueError(
                    f"placeholder element {self.element_id} text must contain '待补充：' and {self.request_id}"
                )
            if self.font_size_pt is None:
                raise ValueError(f"placeholder element {self.element_id} requires font_size_pt")
        return self


class PageDesignEntry(_Contract):
    page_id: PageId
    layout_id: str = ""
    layout_name: str = ""
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
    theme_id: NonEmptyStr = "academic-default"
    pages: list[PageDesignEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> "PageDesign":
        ids = [p.page_id for p in self.pages]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate page_id in page design")
        return self


# ------------------------------------------------------------------ 3.6.5 G (DeckOps)
OpId = Annotated[str, StringConstraints(pattern=r"^OP\d{4}$")]


class DeckOpKind(str, Enum):
    add_slide = "add_slide"
    set_placeholder_text = "set_placeholder_text"
    add_textbox = "add_textbox"
    add_shape = "add_shape"
    add_picture = "add_picture"
    add_table = "add_table"
    add_chart = "add_chart"
    add_diagram = "add_diagram"
    add_connector = "add_connector"
    add_placeholder_box = "add_placeholder_box"
    set_notes = "set_notes"
    set_alt_text = "set_alt_text"
    set_name = "set_name"


BOUND_VISUAL_OP_KINDS = {
    DeckOpKind.add_picture,
    DeckOpKind.add_table,
    DeckOpKind.add_chart,
    DeckOpKind.add_diagram,
    DeckOpKind.add_connector,
}


class DeckOp(_Contract):
    op_id: OpId
    page_id: PageId
    kind: DeckOpKind
    element_id: ElementId | None = None
    asset_id: AssetId | None = None
    request_id: RequestId | None = None
    source_refs: list[NonEmptyStr] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _rules(self) -> "DeckOp":
        fp = self.params.get("file_path")
        if fp is not None:
            if not isinstance(fp, str):
                raise ValueError(f"{self.op_id}: params.file_path must be a string")
            _check_relative_path(fp)

        if self.kind in BOUND_VISUAL_OP_KINDS:
            if not (self.asset_id or self.request_id):
                raise ValueError(
                    f"{self.op_id} ({self.kind.value}): visual op must be bound to an asset_id (A###) or request_id (U###)"
                )
            if not self.source_refs:
                raise ValueError(
                    f"{self.op_id} ({self.kind.value}): visual op must carry at least one provenance entry in source_refs"
                )

        if self.kind is DeckOpKind.add_picture:
            if not self.params.get("file_path"):
                raise ValueError(f"{self.op_id}: add_picture requires params.file_path")
            crop = self.params.get("crop_mode", "crop_fill")
            if crop not in ("fit", "crop_fill"):
                raise ValueError(f"{self.op_id}: add_picture crop_mode must be 'fit' or 'crop_fill'")

        if self.kind is DeckOpKind.add_table:
            cols = self.params.get("columns")
            rows = self.params.get("rows")
            if not isinstance(cols, list) or not cols or not isinstance(rows, list) or not rows:
                raise ValueError(f"{self.op_id}: add_table requires non-empty params.columns and params.rows")

        if self.kind is DeckOpKind.add_chart:
            cats = self.params.get("categories")
            series = self.params.get("series")
            if not isinstance(cats, list) or not cats or not isinstance(series, list) or not series:
                raise ValueError(f"{self.op_id}: add_chart requires non-empty params.categories and params.series")

        if self.kind is DeckOpKind.add_connector:
            if not self.params.get("from_element_id") or not self.params.get("to_element_id"):
                raise ValueError(f"{self.op_id}: add_connector requires params.from_element_id and params.to_element_id")

        if self.kind is DeckOpKind.add_placeholder_box:
            if not self.request_id:
                raise ValueError(f"{self.op_id}: add_placeholder_box requires request_id (U###)")
            name = str(self.params.get("shape_name") or self.element_id or "")
            if not name.startswith(f"PLACEHOLDER_{self.request_id}"):
                raise ValueError(f"{self.op_id}: placeholder shape_name must start with PLACEHOLDER_{self.request_id}")
            txt = str(self.params.get("text") or "")
            if "待补充：" not in txt or self.request_id not in txt:
                raise ValueError(f"{self.op_id}: placeholder text must contain '待补充：' and {self.request_id}")

        return self


class DeckOps(_RunFile):
    """Backend-agnostic deterministic operation list compiled from PageDesign for Office renderers."""

    canvas_width_in: float = CANVAS_W_IN
    canvas_height_in: float = CANVAS_H_IN
    template_path: RelPath | None = None
    page_ids: list[PageId] = Field(min_length=1)
    ops: list[DeckOp] = Field(min_length=1)

    @field_validator("template_path")
    @classmethod
    def _rel_tpl(cls, v: str | None) -> str | None:
        return None if v is None else _check_relative_path(v)

    @model_validator(mode="after")
    def _rules(self) -> "DeckOps":
        op_ids = [o.op_id for o in self.ops]
        if len(set(op_ids)) != len(op_ids):
            raise ValueError("duplicate op_id in deck_ops")
        slide_pages = [o.page_id for o in self.ops if o.kind is DeckOpKind.add_slide]
        if slide_pages != self.page_ids:
            raise ValueError(f"add_slide page sequence {slide_pages} must match page_ids {self.page_ids}")
        known_pages = set(self.page_ids)
        for o in self.ops:
            if o.page_id not in known_pages:
                raise ValueError(f"{o.op_id}: unknown page_id {o.page_id}")
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
    render_provenance: str = ""
    rendered_pages: list[PageId]
    issues: list[QAIssue] = Field(default_factory=list)
    missing_fonts: list[str] = Field(default_factory=list)
    capacity_vs_measured: list[dict[str, Any]] = Field(default_factory=list)
    occupancy: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    cross_render_notes: list[str] = Field(default_factory=list)
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
    substeps: dict[str, str] = Field(default_factory=dict)
    input_hashes: dict[str, str] = Field(default_factory=dict)
    stale: bool = False
    stale_reason: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _rules(self) -> "StageRecord":
        if self.status is StageStatus.failed and not self.error:
            raise ValueError("a failed stage must record its error")
        if self.status is StageStatus.not_implemented and self.output_files:
            raise ValueError("a not_implemented stage cannot list output files")
        return self


class RunOverall(str, Enum):
    complete = "complete"                                    # all stages A-H done, no open/placeholder requests
    complete_with_placeholders = "complete_with_placeholders"  # all stages A-H done, placeholders rendered
    needs_input = "needs_input"                              # paused waiting for user input (--on-missing wait)
    incomplete = "incomplete"                                # implemented stages ok, others not_implemented/skipped
    failed = "failed"


class UserRequestKind(str, Enum):
    diagram = "diagram"
    chart = "chart"
    statistic = "statistic"
    table = "table"
    icon = "icon"
    image = "image"
    reference = "reference"
    other = "other"


class UserRequestFallback(str, Enum):
    placeholder = "placeholder"
    omit_element = "omit_element"
    wait = "wait"


class UserRequestStatus(str, Enum):
    open = "open"
    provided = "provided"
    declined = "declined"
    placeholder_rendered = "placeholder_rendered"


class ProvidedInput(_Contract):
    file: RelPath
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    source: NonEmptyStr
    license: NonEmptyStr
    note: str = ""

    @field_validator("file")
    @classmethod
    def _rel(cls, v: str) -> str:
        return _check_relative_path(v)


class UserInputRequest(_Contract):
    request_id: RequestId
    kind: UserRequestKind
    asset_id: AssetId | None = None
    page_ids: list[PageId] = Field(min_length=1)
    need: NonEmptyStr
    why: NonEmptyStr
    format_wanted: NonEmptyStr
    acceptance: list[NonEmptyStr] = Field(min_length=1)
    blocking: bool
    fallback: UserRequestFallback
    status: UserRequestStatus = UserRequestStatus.open
    provided: ProvidedInput | None = None

    @model_validator(mode="after")
    def _rules(self) -> "UserInputRequest":
        if self.status is UserRequestStatus.provided and self.provided is None:
            raise ValueError(f"request {self.request_id} with status=provided requires provided payload")
        if self.status is not UserRequestStatus.provided and self.provided is not None:
            raise ValueError(f"request {self.request_id} with status={self.status.value} cannot carry provided payload")
        return self


class UserInputRequests(_RunFile):
    requests: list[UserInputRequest] = Field(default_factory=list)
    summary: str = ""

    @model_validator(mode="after")
    def _unique(self) -> "UserInputRequests":
        ids = [r.request_id for r in self.requests]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate request_id in user_input_requests")
        aids = [r.asset_id for r in self.requests if r.asset_id is not None]
        if len(set(aids)) != len(aids):
            raise ValueError("duplicate asset_id in user_input_requests")
        return self


class UserInputRequestsSummary(_Contract):
    path: RelPath
    open_count: int = Field(ge=0)
    blocking_open_count: int = Field(default=0, ge=0)
    provided_count: int = Field(default=0, ge=0)
    placeholder_count: int = Field(default=0, ge=0)

    @field_validator("path")
    @classmethod
    def _rel(cls, v: str) -> str:
        return _check_relative_path(v)


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
    user_input_requests: UserInputRequestsSummary | None = None
    revision_count: int = Field(default=0, ge=0)
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _rules(self) -> "RunReport":
        codes = [s.stage for s in self.stages]
        if codes != list("ABCDEFGH"):
            raise ValueError("run report must list stages A..H exactly once, in order")
        if self.overall is RunOverall.complete:
            if any(s.status is not StageStatus.done for s in self.stages):
                raise ValueError("overall=complete requires every stage done")
            if self.user_input_requests and (
                self.user_input_requests.open_count > 0 or self.user_input_requests.placeholder_count > 0
            ):
                raise ValueError("overall=complete is not allowed when open or placeholder requests exist")
        if self.overall is RunOverall.complete_with_placeholders:
            if any(s.status is not StageStatus.done for s in self.stages):
                raise ValueError("overall=complete_with_placeholders requires every stage done")
            if not self.user_input_requests or self.user_input_requests.open_count > 0:
                raise ValueError("overall=complete_with_placeholders requires user_input_requests with 0 open")
            if self.user_input_requests.placeholder_count == 0:
                raise ValueError("overall=complete_with_placeholders requires placeholder_count > 0")
        if self.overall is RunOverall.needs_input:
            if not self.user_input_requests or self.user_input_requests.open_count == 0:
                raise ValueError("overall=needs_input requires open user_input_requests")
        if self.overall is RunOverall.failed and not self.failure_reason:
            raise ValueError("overall=failed requires failure_reason")
        return self


# ------------------------------------------------------- grading (benchmark)
class RubricInfo(_Contract):
    """Where the rubric comes from (MIT-licensed PPTEval) and how we deviate from it."""

    name: Literal["PPTEval"] = "PPTEval"
    source_url: NonEmptyStr
    source_ref: NonEmptyStr
    license: NonEmptyStr
    copyright: NonEmptyStr
    paper_url: NonEmptyStr
    deviations: list[NonEmptyStr] = Field(default_factory=list)


class GraderIdentity(_Contract):
    """Who scored. ``agent`` = 宝特 acting by hand: honest label, NOT an API model."""

    kind: Literal["api", "agent"]
    label: NonEmptyStr
    model: str = ""
    vision_model: str = ""

    @model_validator(mode="after")
    def _label(self) -> "GraderIdentity":
        if self.kind == "agent" and "manual, not an API model" not in self.label:
            raise ValueError("an agent grader must be labelled 'manual, not an API model'")
        if self.kind == "api" and not self.model:
            raise ValueError("an api grader must record its model")
        return self


class DimScore(_Contract):
    score: Annotated[int, Field(ge=1, le=5)]
    reason: NonEmptyStr


class SlideGrade(_Contract):
    index: Annotated[int, Field(ge=1)]
    page_id: PageId | None = None
    content: DimScore | None = None
    design: DimScore | None = None
    design_status: Literal["scored", "not_assessable"]
    content_descr: str = ""
    style_descr: str = ""


class GradeSummary(_Contract):
    content_mean: float | None = None
    design_mean: float | None = None
    coherence: int | None = None
    rule: NonEmptyStr = "pass iff every assessed dimension >= 3 (content mean, design mean, coherence)"
    not_assessed: list[Literal["content", "design", "coherence"]] = Field(default_factory=list)
    failing: list[Literal["content", "design", "coherence"]] = Field(default_factory=list)
    passed: bool | None = None            # None = undetermined (a dimension could not be assessed)


class GradeReport(_RunFile):
    """PPTEval-style quality grade. Deterministic hard gates are reported separately (gate_report.json)."""

    graded_task: str = ""
    evidence_level: Literal["pptx_render", "plan_text_proxy"]
    rubric: RubricInfo
    grader: GraderIdentity
    graded_at: datetime
    slides: list[SlideGrade] = Field(min_length=1)
    coherence: DimScore | None = None
    background_extracted: dict[str, Any] = Field(default_factory=dict)
    summary: GradeSummary

    @model_validator(mode="after")
    def _consistent(self) -> "GradeReport":
        if self.evidence_level == "plan_text_proxy":
            if any(sl.design_status != "not_assessable" or sl.design for sl in self.slides):
                raise ValueError("plan-level proxy cannot score Design (no rendered slides)")
            if "design" not in self.summary.not_assessed or self.summary.passed is True:
                raise ValueError("plan-level proxy must list design as not_assessed and can never be 'passed' (False or undetermined only)")
        idx = [sl.index for sl in self.slides]
        if idx != sorted(set(idx)):
            raise ValueError("slide indexes must be unique and ascending")
        return self


class GateResult(_Contract):
    gate: NonEmptyStr
    status: Literal["pass", "fail", "warn", "not_run"]
    detail: str = ""


class GateReport(_RunFile):
    """Deterministic hard gates (benchmark spec 6.1). Independent of any model score."""

    results: list[GateResult] = Field(min_length=1)
    overall: Literal["pass", "fail", "incomplete"]

    @model_validator(mode="after")
    def _overall(self) -> "GateReport":
        st = {r.status for r in self.results}
        want = "fail" if "fail" in st else ("incomplete" if "not_run" in st else "pass")
        if self.overall != want:
            raise ValueError(f"overall must be {want!r} given the results")
        return self


# ------------------------------------------------------------------ file names
CONTRACT_FILES: dict[str, type[_RunFile]] = {
    "production_plan": ProductionPlan,
    "research_brief": ResearchRecord,
    "slide_plan": SlidePlan,
    "asset_requirements": AssetRequirements,
    "asset_registry": AssetRegistry,
    "user_input_requests": UserInputRequests,
    "slide_asset_mapping": SlideAssetMapping,
    "page_design": PageDesign,
    "deck_ops": DeckOps,
    "qa_report": QAReport,
    "run_report": RunReport,
    "grade_report": GradeReport,
    "gate_report": GateReport,
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

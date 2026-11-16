"""Versioned task-neutral data contracts for the Reusable Research Evidence & Asset Core.

No presentation, slide, or deck layout concepts exist in this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple


SCHEMA_VERSION = "research-core.bundle.v1"


class SourceOrigin(str, Enum):
    PRIMARY_RESEARCH = "primary_research"
    REVIEW = "review"
    USER_RAW = "user_raw"
    USER_PROCESSED = "user_processed"
    USER_STATEMENT = "user_statement"
    OTHER = "other"


class AccessStatus(str, Enum):
    FULL_TEXT_LOCAL = "FULL_TEXT_LOCAL"
    ABSTRACT_ONLY = "ABSTRACT_ONLY"
    METADATA_ONLY = "METADATA_ONLY"
    USER_DATA_VERIFIED = "USER_DATA_VERIFIED"
    USER_NOTES_ONLY = "USER_NOTES_ONLY"
    UNAVAILABLE = "UNAVAILABLE"


class VerificationLevel(str, Enum):
    """Six-stage scientific verification hierarchy."""
    UNVERIFIED = "UNVERIFIED"
    SOURCE_LOCATED = "SOURCE_LOCATED"
    EVIDENCE_LOCATED = "EVIDENCE_LOCATED"
    SEMANTICALLY_CHECKED = "SEMANTICALLY_CHECKED"
    APPROVED = "APPROVED"
    CONFLICTED = "CONFLICTED"


class EvidenceOrigin(str, Enum):
    LITERATURE = "literature"
    USER = "user"
    OTHER = "other"


class RelationType(str, Enum):
    SUPPORTS = "supports"
    OPPOSES = "opposes"
    CONTEXT = "context"
    CONDITIONAL_SUPPORT = "conditional_support"


class AssetKind(str, Enum):
    FIGURE = "figure"
    COMPOUND_FIGURE = "compound_figure"
    SPECTRUM = "spectrum"
    MICROGRAPH = "micrograph"
    REACTION_SCHEME = "reaction_scheme"
    EXPERIMENTAL_SETUP = "experimental_setup"
    WORKFLOW_DIAGRAM = "workflow_diagram"
    DATA_TABLE = "data_table"
    RAW_DATASET = "raw_dataset"
    SCHEMATIC = "schematic"


class AssetStatus(str, Enum):
    LOCATED = "LOCATED"
    ACQUIRED = "ACQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


class RightsStatus(str, Enum):
    ALLOWED = "ALLOWED"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    NEEDS_PERMISSION = "NEEDS_PERMISSION"
    UNKNOWN = "UNKNOWN"


class ComparabilityStatus(str, Enum):
    DIRECTLY_COMPARABLE = "DIRECTLY_COMPARABLE"
    CONDITIONAL = "CONDITIONAL"
    NOT_COMPARABLE = "NOT_COMPARABLE"
    UNKNOWN = "UNKNOWN"


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ExactLocator:
    page: Optional[int] = None
    section: Optional[str] = None
    paragraph_index: Optional[int] = None
    char_span: Optional[Tuple[int, int]] = None
    figure_or_table_id: Optional[str] = None
    bbox_pt: Optional[List[float]] = None
    data_row_range: Optional[str] = None
    raw_locator_text: str = ""

    def format_compact(self) -> str:
        if self.raw_locator_text and self.raw_locator_text.strip():
            return self.raw_locator_text.strip()
        parts: List[str] = []
        if self.page is not None:
            parts.append(f"p.{self.page}")
        if self.section:
            parts.append(f"sec:{self.section}")
        if self.figure_or_table_id:
            parts.append(self.figure_or_table_id)
        if self.data_row_range:
            parts.append(f"rows:{self.data_row_range}")
        if self.char_span:
            parts.append(f"chars:{self.char_span[0]}-{self.char_span[1]}")
        return ", ".join(parts) if parts else "unlocated"


@dataclass
class MeasurementRecord:
    value: Any
    unit: str
    definition: str
    normalized_si_value: Optional[float] = None
    normalized_si_unit: Optional[str] = None
    uncertainty: Optional[str] = None
    condition_qualifier: Optional[str] = None


@dataclass
class ConditionItem:
    category: str
    parameter: str
    value: str
    unit: Optional[str] = None
    raw_text: str = ""


@dataclass
class SourceRecord:
    source_id: str
    title: str
    origin: str = SourceOrigin.PRIMARY_RESEARCH.value
    access_status: str = AccessStatus.METADATA_ONLY.value
    verification_level: str = VerificationLevel.UNVERIFIED.value
    url_or_path: str = ""
    local_path: Optional[str] = None
    sha256: Optional[str] = None
    authors: Optional[List[str]] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    publication: Optional[str] = None
    peer_reviewed: Optional[bool] = None
    license_name: Optional[str] = None
    retrieval_query: Optional[str] = None
    retrieved_at: Optional[str] = None
    abstract_text: Optional[str] = None
    inspected_pages: List[int] = field(default_factory=list)
    total_pages: Optional[int] = None
    usage_notes: str = ""
    retired: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def read_scope_label(self) -> str:
        mapping = {
            AccessStatus.FULL_TEXT_LOCAL.value: "full_text",
            AccessStatus.ABSTRACT_ONLY.value: "abstract",
            AccessStatus.METADATA_ONLY.value: "metadata",
            AccessStatus.USER_DATA_VERIFIED.value: "data",
            AccessStatus.USER_NOTES_ONLY.value: "user_notes",
            AccessStatus.UNAVAILABLE.value: "metadata",
        }
        return mapping.get(self.access_status, "metadata")


@dataclass
class PassageRecord:
    passage_id: str
    source_id: str
    page: int
    section: str
    text: str
    char_span: Tuple[int, int] = (0, 0)
    figure_mentions: List[str] = field(default_factory=list)
    candidate_dois: List[str] = field(default_factory=list)


@dataclass
class ClaimEvidenceLink:
    claim_id: str
    relation: str = RelationType.SUPPORTS.value
    rationale: str = ""
    confidence: float = 1.0


@dataclass
class EvidenceRecord:
    evidence_id: str
    source_id: str
    origin: str
    verification_level: str
    verification_scope: str
    locator: ExactLocator
    subject: str
    method: str
    finding: str
    exact_quote: str = ""
    measurements: List[MeasurementRecord] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)
    structured_conditions: List[ConditionItem] = field(default_factory=list)
    controls: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    alternative_explanations: List[str] = field(default_factory=list)
    applicability: str = ""
    claim_links: List[ClaimEvidenceLink] = field(default_factory=list)
    asset_ids: List[str] = field(default_factory=list)
    task_ids: List[str] = field(default_factory=list)
    retired: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClaimNode:
    claim_id: str
    statement: str
    domain_topic: str = ""
    origin_requirement: str = "literature"
    verification_level: str = VerificationLevel.UNVERIFIED.value
    supporting_evidence_ids: List[str] = field(default_factory=list)
    opposing_evidence_ids: List[str] = field(default_factory=list)
    context_evidence_ids: List[str] = field(default_factory=list)
    required_conditions: List[str] = field(default_factory=list)
    disclosure: str = ""
    conflict_assessment: str = ""
    audit_findings: List[str] = field(default_factory=list)
    retired: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RightsMetadata:
    status: str = RightsStatus.UNKNOWN.value
    basis: str = "Unknown license; requires explicit copyright verification before external republication."
    attribution: str = ""
    license_id: Optional[str] = None
    commercial_use_allowed: Optional[bool] = None
    derivatives_allowed: Optional[bool] = None


@dataclass
class TransformationMetadata:
    kind: str = "original"
    basis: str = "Direct unmodified extraction from source document."
    source_data_preserved: bool = True


@dataclass
class AssetRecord:
    asset_id: str
    source_id: str
    evidence_ids: List[str]
    asset_kind: str = AssetKind.FIGURE.value
    status: str = AssetStatus.LOCATED.value
    figure_or_table_id: str = ""
    source_locator: str = ""
    page: Optional[int] = None
    bbox_pt: Optional[List[float]] = None
    panel_labels: List[str] = field(default_factory=list)
    is_multi_panel: bool = False
    caption: str = ""
    mention_contexts: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)
    scientific_immutable: List[str] = field(default_factory=list)
    path: Optional[str] = None
    sha256: Optional[str] = None
    format: Optional[str] = None
    pixel_size: Optional[Tuple[int, int]] = None
    table_rows: Optional[List[List[Optional[str]]]] = None
    rights: RightsMetadata = field(default_factory=RightsMetadata)
    transformation: TransformationMetadata = field(default_factory=TransformationMetadata)
    retired: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComparabilityRecord:
    comparison_id: str
    evidence_ids: List[str]
    status: str = ComparabilityStatus.UNKNOWN.value
    dimensions: Dict[str, str] = field(default_factory=lambda: {
        "metric": "unspecified",
        "unit": "unspecified",
        "conditions": "unspecified",
        "controls": "unspecified",
        "statistics": "unspecified",
        "scope": "unspecified",
    })
    rationale: str = ""
    permitted_use: str = "none"
    normalized_values: List[Dict[str, Any]] = field(default_factory=list)
    retired: bool = False


@dataclass
class ConsistencyIssue:
    issue_id: str
    severity: str  # "P0_BLOCKER" | "P1_WARNING" | "P2_ADVISORY"
    category: str
    target_ids: List[str]
    message: str
    evidence_locator: str = ""
    remediation: str = ""


@dataclass
class ProcessingRecord:
    step_id: str
    operation: str
    timestamp: str
    input_refs: List[str]
    output_refs: List[str]
    status: str
    notes: str = ""


@dataclass
class ResearchBundle:
    """Task-neutral container for scientific sources, passages, evidence, claims, assets, and comparisons."""
    bundle_id: str
    title: str
    schema_version: str = SCHEMA_VERSION
    core_version: str = "1.0.0"
    revision: int = 1
    base_revision: Optional[int] = None
    research_questions: List[str] = field(default_factory=list)
    sources: List[SourceRecord] = field(default_factory=list)
    passages: List[PassageRecord] = field(default_factory=list)
    evidence: List[EvidenceRecord] = field(default_factory=list)
    claims: List[ClaimNode] = field(default_factory=list)
    assets: List[AssetRecord] = field(default_factory=list)
    comparisons: List[ComparabilityRecord] = field(default_factory=list)
    consistency_issues: List[ConsistencyIssue] = field(default_factory=list)
    processing_records: List[ProcessingRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent) + "\n"

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(indent=2), encoding="utf-8", newline="\n")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchBundle":
        sources = [SourceRecord(**s) for s in data.get("sources", [])]
        passages = [
            PassageRecord(
                passage_id=p["passage_id"],
                source_id=p["source_id"],
                page=p["page"],
                section=p["section"],
                text=p["text"],
                char_span=tuple(p.get("char_span", (0, 0))),
                figure_mentions=list(p.get("figure_mentions", [])),
                candidate_dois=list(p.get("candidate_dois", [])),
            )
            for p in data.get("passages", [])
        ]
        evidence: List[EvidenceRecord] = []
        for e in data.get("evidence", []):
            loc_data = e.get("locator") or {}
            if isinstance(loc_data, dict):
                cs = loc_data.get("char_span")
                locator = ExactLocator(
                    page=loc_data.get("page"),
                    section=loc_data.get("section"),
                    paragraph_index=loc_data.get("paragraph_index"),
                    char_span=tuple(cs) if cs else None,
                    figure_or_table_id=loc_data.get("figure_or_table_id"),
                    bbox_pt=loc_data.get("bbox_pt"),
                    data_row_range=loc_data.get("data_row_range"),
                    raw_locator_text=loc_data.get("raw_locator_text", ""),
                )
            else:
                locator = ExactLocator(raw_locator_text=str(loc_data))
            measurements = [MeasurementRecord(**m) for m in e.get("measurements", [])]
            s_conds = [ConditionItem(**c) for c in e.get("structured_conditions", [])]
            clinks = [ClaimEvidenceLink(**c) for c in e.get("claim_links", [])]
            evidence.append(
                EvidenceRecord(
                    evidence_id=e["evidence_id"],
                    source_id=e["source_id"],
                    origin=e["origin"],
                    verification_level=e["verification_level"],
                    verification_scope=e["verification_scope"],
                    locator=locator,
                    subject=e["subject"],
                    method=e["method"],
                    finding=e["finding"],
                    exact_quote=e.get("exact_quote", ""),
                    measurements=measurements,
                    conditions=list(e.get("conditions", [])),
                    structured_conditions=s_conds,
                    controls=list(e.get("controls", [])),
                    limitations=list(e.get("limitations", [])),
                    alternative_explanations=list(e.get("alternative_explanations", [])),
                    applicability=e.get("applicability", ""),
                    claim_links=clinks,
                    asset_ids=list(e.get("asset_ids", [])),
                    task_ids=list(e.get("task_ids", [])),
                    retired=bool(e.get("retired", False)),
                    extra=dict(e.get("extra", {})),
                )
            )
        claims = [ClaimNode(**c) for c in data.get("claims", [])]
        assets: List[AssetRecord] = []
        for a in data.get("assets", []):
            rights_data = a.get("rights") or {}
            trans_data = a.get("transformation") or {}
            px = a.get("pixel_size")
            assets.append(
                AssetRecord(
                    asset_id=a["asset_id"],
                    source_id=a["source_id"],
                    evidence_ids=list(a.get("evidence_ids", [])),
                    asset_kind=a.get("asset_kind", AssetKind.FIGURE.value),
                    status=a.get("status", AssetStatus.LOCATED.value),
                    figure_or_table_id=a.get("figure_or_table_id", ""),
                    source_locator=a.get("source_locator", ""),
                    page=a.get("page"),
                    bbox_pt=a.get("bbox_pt"),
                    panel_labels=list(a.get("panel_labels", [])),
                    is_multi_panel=bool(a.get("is_multi_panel", False)),
                    caption=a.get("caption", ""),
                    mention_contexts=list(a.get("mention_contexts", [])),
                    conditions=list(a.get("conditions", [])),
                    scientific_immutable=list(a.get("scientific_immutable", [])),
                    path=a.get("path"),
                    sha256=a.get("sha256"),
                    format=a.get("format"),
                    pixel_size=tuple(px) if px else None,
                    table_rows=a.get("table_rows"),
                    rights=RightsMetadata(**rights_data) if isinstance(rights_data, dict) else RightsMetadata(),
                    transformation=TransformationMetadata(**trans_data) if isinstance(trans_data, dict) else TransformationMetadata(),
                    retired=bool(a.get("retired", False)),
                    extra=dict(a.get("extra", {})),
                )
            )
        comparisons = [ComparabilityRecord(**c) for c in data.get("comparisons", [])]
        issues = [ConsistencyIssue(**i) for i in data.get("consistency_issues", [])]
        procs = [ProcessingRecord(**p) for p in data.get("processing_records", [])]
        return cls(
            bundle_id=data["bundle_id"],
            title=data["title"],
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            core_version=data.get("core_version", "1.0.0"),
            revision=int(data.get("revision", 1)),
            base_revision=data.get("base_revision"),
            research_questions=list(data.get("research_questions", [])),
            sources=sources,
            passages=passages,
            evidence=evidence,
            claims=claims,
            assets=assets,
            comparisons=comparisons,
            consistency_issues=issues,
            processing_records=procs,
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def load(cls, path: Path) -> "ResearchBundle":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        return cls.from_dict(raw)


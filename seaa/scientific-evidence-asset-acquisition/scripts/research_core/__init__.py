"""Reusable Research Evidence & Asset Core (research_core v1.0.0).

Task-neutral scientific literature discovery, full-text & figure/table asset extraction,
claim-evidence grounding, experimental condition & unit consistency checking, and
cross-paper synthesis graph.

Strictly decoupled from any presentation or slide concepts so it can serve
both presentation adapters and standalone Review Engines.
"""

__version__ = "1.0.0"
SCHEMA_VERSION = "research-core.bundle.v1"

from .models import (
    AccessStatus,
    AssetKind,
    AssetRecord,
    AssetStatus,
    ClaimEvidenceLink,
    ClaimNode,
    ComparabilityRecord,
    ComparabilityStatus,
    ConditionItem,
    ConsistencyIssue,
    EvidenceOrigin,
    EvidenceRecord,
    ExactLocator,
    MeasurementRecord,
    PassageRecord,
    ProcessingRecord,
    RelationType,
    ResearchBundle,
    RightsMetadata,
    RightsStatus,
    SourceOrigin,
    SourceRecord,
    TransformationMetadata,
    VerificationLevel,
)

__all__ = [
    "__version__",
    "SCHEMA_VERSION",
    "AccessStatus",
    "AssetKind",
    "AssetRecord",
    "AssetStatus",
    "ClaimEvidenceLink",
    "ClaimNode",
    "ComparabilityRecord",
    "ComparabilityStatus",
    "ConditionItem",
    "ConsistencyIssue",
    "EvidenceOrigin",
    "EvidenceRecord",
    "ExactLocator",
    "MeasurementRecord",
    "PassageRecord",
    "ProcessingRecord",
    "RelationType",
    "ResearchBundle",
    "RightsMetadata",
    "RightsStatus",
    "SourceOrigin",
    "SourceRecord",
    "TransformationMetadata",
    "VerificationLevel",
]


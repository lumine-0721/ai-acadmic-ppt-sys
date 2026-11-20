import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pptv01.contracts import (
    CONTRACT_FILES, AssetRecord, AssetRegistry, AssetRequirements, PageDesign, ProductionPlan,
    QAReport, RunReport, SlideAssetMapping, SlidePlan, export_schemas, load_contract, write_contract,
)

ROOT = Path(__file__).resolve().parents[1]
FIX = {
    "production_plan": "production_plan", "slide_plan": "slide_plan_provisional",
    "asset_requirements": "asset_requirements", "asset_registry": "asset_registry",
    "slide_asset_mapping": "slide_asset_mapping", "page_design": "page_design",
    "qa_report": "qa_report", "run_report": "run_report",
}


@pytest.mark.parametrize("kind", sorted(CONTRACT_FILES))
def test_fixture_valid_and_roundtrips(kind, tmp_path):
    cls = CONTRACT_FILES[kind]
    obj = load_contract(cls, ROOT / "fixtures" / f"{FIX[kind]}.json")
    assert obj.schema_version == "0.1" and obj.run_id
    out = write_contract(obj, tmp_path / f"{kind}.json")
    assert load_contract(cls, out) == obj


def test_revised_slide_plan_fixture_valid(fx):
    SlidePlan.model_validate(fx("slide_plan_revised"))


def test_checked_in_schemas_are_current(tmp_path):
    export_schemas(tmp_path)
    for p in tmp_path.glob("*.schema.json"):
        assert (ROOT / "contracts" / p.name).read_text(encoding="utf-8") == p.read_text(encoding="utf-8"), \
            f"{p.name} is stale: run `python -m pptv01.contracts`"


def test_schema_version_and_run_id_required(fx):
    d = fx("production_plan")
    d["schema_version"] = "0.2"
    with pytest.raises(ValidationError):
        ProductionPlan.model_validate(d)
    d = fx("production_plan"); del d["run_id"]
    with pytest.raises(ValidationError):
        ProductionPlan.model_validate(d)
    d = fx("production_plan"); d["run_id"] = "../evil"
    with pytest.raises(ValidationError):
        ProductionPlan.model_validate(d)


def test_unknown_fields_rejected(fx):
    d = fx("production_plan"); d["page_count"] = 10
    with pytest.raises(ValidationError):
        ProductionPlan.model_validate(d)


def test_defaults_applied_value_must_match(fx):
    d = fx("production_plan")
    d["defaults_applied"][0]["value"] = "experts"
    with pytest.raises(ValidationError, match="does not match"):
        ProductionPlan.model_validate(d)


# ---- slide plan rules
def test_provisional_requires_provisional_pages_and_sequential_ids(fx):
    d = fx("slide_plan_provisional"); d["pages"][0]["status"] = "revised"
    with pytest.raises(ValidationError, match="non-provisional"):
        SlidePlan.model_validate(d)
    d = fx("slide_plan_provisional"); d["pages"][1]["page_id"] = "S005"
    with pytest.raises(ValidationError, match="S001"):
        SlidePlan.model_validate(d)
    d = fx("slide_plan_provisional"); d["pages"][1]["page_id"] = "S001"
    with pytest.raises(ValidationError, match="duplicate"):
        SlidePlan.model_validate(d)


def test_status_vocabulary(fx):
    d = fx("slide_plan_provisional"); d["status"] = "final"
    with pytest.raises(ValidationError):
        SlidePlan.model_validate(d)


def test_provisional_must_not_have_revision_log(fx):
    d = fx("slide_plan_revised"); d["status"] = "provisional"
    with pytest.raises(ValidationError):
        SlidePlan.model_validate(d)


def test_revised_requires_log_with_evidence(fx):
    d = fx("slide_plan_revised"); d["revision_log"] = []
    with pytest.raises(ValidationError, match="revision_log"):
        SlidePlan.model_validate(d)
    d = fx("slide_plan_revised"); d["revision_log"][0]["evidence_asset_ids"] = []
    with pytest.raises(ValidationError):
        SlidePlan.model_validate(d)


@pytest.mark.parametrize("bad", ["S1", "s001", "P001", "S00A", "001"])
def test_page_id_format(fx, bad):
    d = fx("slide_plan_provisional"); d["pages"][0]["page_id"] = bad
    with pytest.raises(ValidationError):
        SlidePlan.model_validate(d)


@pytest.mark.parametrize("bad", ["A1", "a001", "X001", "A-001"])
def test_asset_id_format(fx, bad):
    d = fx("asset_requirements"); d["requirements"][0]["asset_id"] = bad
    with pytest.raises(ValidationError):
        AssetRequirements.model_validate(d)


def test_requirement_needs_page_purpose_acceptance(fx):
    for key, val in (("page_ids", []), ("purpose", ""), ("acceptance", []), ("type", "video")):
        d = fx("asset_requirements"); d["requirements"][0][key] = val
        with pytest.raises(ValidationError):
            AssetRequirements.model_validate(d)


def test_duplicate_asset_ids(fx):
    d = fx("asset_requirements"); d["requirements"][1]["asset_id"] = "A001"
    with pytest.raises(ValidationError, match="duplicate"):
        AssetRequirements.model_validate(d)


# ---- registry
def test_missing_asset_cannot_have_file_and_needs_notes(fx):
    d = fx("asset_registry"); d["assets"][1]["file_path"] = "assets/A002.pdf"
    with pytest.raises(ValidationError, match="missing"):
        AssetRegistry.model_validate(d)
    d = fx("asset_registry"); d["assets"][1]["notes"] = ""
    with pytest.raises(ValidationError, match="notes"):
        AssetRegistry.model_validate(d)


def test_acquired_asset_needs_provenance(fx):
    for f in ("source_url", "license_or_terms", "file_path", "retrieved_at", "source_name"):
        d = fx("asset_registry"); d["assets"][0][f] = None
        with pytest.raises(ValidationError, match=f):
            AssetRegistry.model_validate(d)


@pytest.mark.parametrize("bad", ["/etc/passwd", "C:\\Users\\x\\a.png", "../a.png", "assets\\a.png"])
def test_paths_must_be_relative(bad):
    with pytest.raises(ValidationError):
        AssetRecord(asset_id="A001", status="drawn", file_path=bad, notes="drawn")


# ---- mapping / design / qa / run
def test_mapping_cannot_list_used_asset_as_unused(fx):
    d = fx("slide_asset_mapping"); d["unused_assets"] = [{"asset_id": "A001", "reason": "x"}]
    with pytest.raises(ValidationError, match="unused"):
        SlideAssetMapping.model_validate(d)


def test_design_image_must_reference_bound_asset(fx):
    d = fx("page_design"); d["pages"][0]["elements"][1]["asset_id"] = None
    with pytest.raises(ValidationError, match="bound asset"):
        PageDesign.model_validate(d)


def test_design_element_inside_canvas(fx):
    d = fx("page_design"); d["pages"][0]["elements"][1]["x_in"] = 12.0
    with pytest.raises(ValidationError, match="outside the canvas"):
        PageDesign.model_validate(d)


def test_qa_fix_requires_recheck(fx):
    d = fx("qa_report"); d["issues"][0]["recheck"] = "not_rechecked"
    with pytest.raises(ValidationError, match="re-verified"):
        QAReport.model_validate(d)


def test_run_report_stage_rules(fx):
    d = fx("run_report"); d["stages"] = d["stages"][:7]
    with pytest.raises(ValidationError, match="A..H"):
        RunReport.model_validate(d)
    d = fx("run_report"); d["overall"] = "complete"
    with pytest.raises(ValidationError, match="complete"):
        RunReport.model_validate(d)
    d = fx("run_report"); d["stages"][1]["status"] = "failed"
    with pytest.raises(ValidationError, match="error"):
        RunReport.model_validate(d)


# ---- structured asset_needs (stage B -> C)
def _need(**kw):
    base = {"type": "image", "description": "并排展示城区与郊区温度分布",
            "targets": [{"name": "某城市热像图", "entity_type": "dataset"}]}
    return {**base, **kw}


def test_asset_need_uses_the_stage_c_asset_type_enum(fx):
    from pptv01.contracts import AssetNeed, AssetType
    assert AssetNeed.model_validate(_need()).type is AssetType.image
    for ok in AssetType:
        tg = [] if ok.value in ("diagram", "icon") else [{"name": "x"}]
        AssetNeed.model_validate(_need(type=ok.value, targets=tg))
    with pytest.raises(ValidationError):
        AssetNeed.model_validate(_need(type="video"))


@pytest.mark.parametrize("t", ["image", "chart_data", "table", "reference", "statistic"])
def test_concrete_types_need_a_named_target(t):
    from pptv01.contracts import AssetNeed
    with pytest.raises(ValidationError, match="concrete target"):
        AssetNeed.model_validate(_need(type=t, targets=[]))


def test_diagram_and_icon_may_be_conceptual():
    from pptv01.contracts import AssetNeed
    AssetNeed.model_validate(_need(type="diagram", targets=[]))
    AssetNeed.model_validate(_need(type="icon", targets=[]))


def test_candidate_targets_default_to_verify_and_verified_needs_sources():
    from pptv01.contracts import AssetTarget, Verification
    assert AssetTarget(name="帕特农神庙").verification is Verification.to_verify
    with pytest.raises(ValidationError, match="verified but cites no"):
        AssetTarget(name="帕特农神庙", verification="verified")
    assert AssetTarget(name="帕特农神庙", verification="verified", source_refs=["R001"]).source_refs == ["R001"]
    with pytest.raises(ValidationError, match="still marked to_verify"):
        AssetTarget(name="帕特农神庙", source_refs=["R001"])


def test_free_text_asset_needs_are_rejected(fx):
    d = fx("slide_plan_provisional"); d["pages"][0]["asset_needs"] = ["配几张图"]
    with pytest.raises(ValidationError):
        SlidePlan.model_validate(d)

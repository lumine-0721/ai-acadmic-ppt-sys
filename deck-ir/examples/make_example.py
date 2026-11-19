"""Generate the contract example from explicitly synthetic, local, inspectable inputs."""
import csv
import hashlib
from pathlib import Path

from academic_deck_ir import assemble, dumps, edit, merge, proposal, loads

HERE = Path(__file__).resolve().parent


def artifacts():
    # Minimal *mock* packages, using audited field names; not production checker attestations.
    h = {"project_id": "geometry-fixture", "revision": 1, "slide_order": ["S001"], "records": {
        "C001": {"summary": "B is wider than A in the fixture", "depends_on": [],
                 "status": "USER_PROVIDED", "kind": "finding", "origin": "user"},
        "S001": {"summary": "Rectangle widths", "role": "comparison", "status": "READY",
                 "reviewed_in": 1, "depends_on": ["C001"]}}}
    seaa = {"01_SOURCE_MANIFEST.json": {"sources": [{"source_id": "SRC001", "title": "Local test measurements",
            "url_or_path": "observations.csv", "verification_status": "USER_PROVIDED", "read_scope": "data",
            "metadata": {"authors": [], "year": None, "doi": None, "peer_reviewed": None}}]},
        "02_EVIDENCE_MANIFEST.json": {"project_id": h["project_id"], "base_revision": 1, "evidence": [
            {"evidence_id": "E001", "source_id": "SRC001", "status": "USER_PROVIDED",
             "verification_scope": "Synthetic geometry fixture only", "locator": "observations.csv rows 2-3",
             "claim_links": [{"claim_id": "C001", "relation": "supports"}], "slide_ids": ["S001"]}],
            "slides": [{"slide_id": "S001", "status": "READY", "gaps": [], "evidence_ids": ["E001"],
                        "asset_ids": ["A001"], "claims": [{"claim_id": "C001"}]}]},
        "03_ASSET_MANIFEST.json": {"assets": [{"asset_id": "A001", "source_id": "SRC001",
            "path": "widths.svg", "sha256": hashlib.sha256((HERE / "widths.svg").read_bytes()).hexdigest(),
            "evidence_ids": ["E001"], "slide_ids": ["S001"], "scientific_immutable": ["rectangle widths"],
            "rights": {"status": "ALLOWED", "basis": "authored test fixture", "attribution": ""}}]}}
    aprd = {"SOURCE_MANIFEST.json": {"references": []},
        "DESIGN_SYSTEM.json": {"design_revision": 1, "typography": {"body": "sans-serif"}},
        "LAYOUT_LIBRARY.json": {"layouts": [{"layout_id": "LAYOUT001", "source_ref_id": None,
            "precision": "derived", "normalized_geometry": [{"slot": "figure", "x": 10, "y": 20, "w": 80, "h": 60}],
            "fixed_constraints": {"within_safe_area": True}}]},
        "SLIDE_STYLE_MAPPING.json": {"scope": "project", "project_id": h["project_id"],
            "base_revision": 1, "design_revision": 1, "target_slide_ids": ["S001"],
            "mappings": [{"target_slide_id": "S001", "selected_layout_id": "LAYOUT001", "coverage": "ORIGINAL"}]}}
    return h, aprd, seaa


def example():
    h, design, science = artifacts()
    d = assemble(h, requirements={"audience": "contract test", "purpose": "synthetic, not scientific evidence"},
                 aprd=design, seaa=science)
    with (HERE / "observations.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    names = [r["sample"] for r in rows]
    values = [int(r["width_px"]) for r in rows]
    elements = {"chart": {"kind": "chart", "categories": names, "chart_type": "bar", "series": [
                    {"name": "width", "unit": "px", "values": values, "evidence_id": "E001"}]},
                "formula": {"kind": "formula", "latex": "w_B-w_A"},
                "table": {"kind": "table", "columns": ["Sample", "Width (px)"], "rows": list(map(list, zip(names, values))),
                          "evidence_ids": ["E001"]},
                "image": {"kind": "image", "asset_id": "A001", "alt": "Two rectangles"},
                "caption": {"kind": "text", "claim_ids": ["C001"], "related": ["image"], "level": 1,
                            "runs": [{"text": "Test fixture: ", "bold": True}, {"text": "width in pixels"}]}}
    d = edit(d, "author", *(("insert", "S001", k, n, None, i) for i, (k, n) in enumerate(elements.items())),
             ("group", "S001", "fig", ["image", "caption"], None))
    c = loads(dumps(d))
    c["sections"]["results"] = {"title": "Measurements"}
    p = c["slides"]["S001"]
    p["semantic"].update(section_id="results", subtitle="Contract example", speaker="Fixture author",
                         notes="Data are local test inputs, not a published finding.")
    p["design"]["placements"]["chart"] = {"slot": "figure", "style": {"priority": 1, "color": "#466788"}}
    p["resolved"]["chart"] = {"x": 0.1, "y": 0.2, "w": 0.8, "h": 0.6}
    return merge(d, proposal(d, "designer", c))


if __name__ == "__main__":
    (HERE / "complex.json").write_bytes(dumps(example()) + b"\n")

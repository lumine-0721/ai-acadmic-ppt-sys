"""Optional repository integration check, not a runtime dependency of the IR.

Read a handoff and its matching upstream packages; execute the existing producer;
consume its on-disk v2 JSON via v3 and the Mock Backend. Never start Office.
"""
import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from academic_deck_ir import dumps, input_receipt, inspect_deck, migrate
from academic_ppt_orchestrator.contracts.ir import VALIDATOR
from academic_ppt_orchestrator.workflow.graph import invoke_run

from check_artifacts import package, read, values


def check(args, run_dir):
    h = read(args.handoff)
    requirements = {"markdown": args.requirements.read_text(encoding="utf-8-sig")}
    a = package(args.aprd, "SOURCE_MANIFEST.json DESIGN_SYSTEM.json LAYOUT_LIBRARY.json SLIDE_STYLE_MAPPING.json")
    s = package(args.seaa, "01_SOURCE_MANIFEST.json 02_EVIDENCE_MANIFEST.json 03_ASSET_MANIFEST.json")
    inputs = dict(requirements=requirements, aprd=a, seaa=s)
    receipt = input_receipt(h, **inputs)
    task = {"python": sys.executable, "handoff": str(args.handoff.resolve()),
            "requirements": str(args.requirements.resolve()), "write_probe_pptx": False,
            "budgets": {"allow_claim_downgrade": False, "max_iterations": 0}}
    for key, path in (("design_package", args.aprd), ("evidence_package", args.seaa)):
        if path:
            task[key] = str(path.resolve())
    state = invoke_run(task, str(run_dir), "bridge", str(run_dir / "checkpoints.sqlite"))
    v2 = read(Path(state["ir_path"]))
    root = args.source_root.resolve(strict=True)
    locations = {}
    for value in values([h, a, s]):
        p = Path(value)
        if p.is_absolute() and p.resolve().is_relative_to(root):
            locations[value] = p.resolve().relative_to(root).as_posix()
    v3 = migrate(v2, h, check_v2=VALIDATOR.validate, receipt=receipt, locations=locations, **inputs)
    report = inspect_deck(dumps(v3))
    observed = {(eid, e["source_id"], link["claim_id"], link["relation"], link["eligible"], e["locator"])
                for eid, e in v3["evidence"].items() if not eid.startswith("apc.") for link in e["links"]}
    expected = {(b["evidence_ids"][0], b["source_ids"][0], b["claim_id"], b["relation"],
                 b["eligible_support"], b["locator"]) for b in v2["evidence_bindings"]}
    if observed != expected:
        raise ValueError("typed evidence edges changed in transit")
    if [(p["slide_id"], p["status"]) for p in report["pages"]] != [
            (p["slide_id"], p["status"]) for p in v2["slide_specifications"]]:
        raise ValueError("page ordering/readiness changed in transit")
    for page, legacy in zip(report["pages"], v2["slide_specifications"], strict=True):
        expected_page = {edge for edge in expected if edge[2] in legacy["depends_on"]}
        seen_page = {(edge["evidence_id"], edge["source_id"], edge["claim_id"], edge["relation"],
                      edge["eligible"], edge["locator"]) for edge in page["edges"]}
        if not expected_page <= seen_page:
            raise ValueError(f"{page['slide_id']}: consumer lost a page citation")
        for _, src, *_ in expected_page:
            if page["sources"].get(src) != v3["sources"][src]:
                raise ValueError(f"{page['slide_id']}: consumer lost source metadata")
    status = v2["validation_state"]["status"]
    if status != "accepted" and "upstream_gate:" + status not in report["diagnostics"]:
        raise ValueError("the original deck gate was lost")
    if report["rendered"] or state.get("pptx_path"):
        raise ValueError("unexpected rendering")
    print(f"V2={status} V3={v3['state']} PAGES={len(report['pages'])} EDGES={len(observed)} RENDERED=False")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--handoff", required=True, type=Path)
    cli.add_argument("--requirements", required=True, type=Path)
    cli.add_argument("--source-root", required=True, type=Path)
    cli.add_argument("--aprd", type=Path)
    cli.add_argument("--seaa", type=Path)
    args = cli.parse_args()
    with TemporaryDirectory(prefix="graph-bridge-") as folder:
        check(args, Path(folder))


if __name__ == "__main__":
    main()

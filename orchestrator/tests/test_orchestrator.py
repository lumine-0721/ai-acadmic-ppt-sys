import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from academic_ppt_orchestrator.journal import read_events
from academic_ppt_orchestrator.process import run_command
from academic_ppt_orchestrator.qa.router import route_report
from academic_ppt_orchestrator.workflow.graph import invoke_run, resume_run

PYTHON = sys.executable
APC = Path(r"C:\Users\86139\.codex\skills\academic-presentation-compiler")
APRD = Path(r"C:\Users\86139\.codex\skills\academic-presentation-reference-design")
SEAA = Path(r"C:\Users\86139\.codex\skills\scientific-evidence-asset-acquisition")
SMOKE = Path(r"C:\Users\86139\Desktop\codex\outputs")
HANDOFF = SMOKE / "academic-presentation-compiler" / "smoke" / "v002" / "PPT_HANDOFF.json"
REQUIREMENTS = SMOKE / "academic-presentation-compiler" / "smoke" / "v002" / "PPT_REQUIREMENTS.md"
DESIGN = SMOKE / "academic-presentation-reference-design" / "smoke" / "design-v002" / "PPT_REFERENCE_PACKAGE"
PPTX = SMOKE / "academic-presentation-compiler" / "smoke" / "out" / "instrument-gain-v002.pptx"
EVIDENCE = SMOKE / "scientific-evidence-asset-acquisition" / "smoke" / "v002" / "EVIDENCE_PACKAGE"
SEAA_HANDOFF = SMOKE / "scientific-evidence-asset-acquisition" / "smoke" / "PPT_HANDOFF.json"
SEAA_REQUIREMENTS = SMOKE / "scientific-evidence-asset-acquisition" / "smoke" / "PPT_REQUIREMENTS.md"


WORK = ROOT / ".tmp-tests"


def workspace():
    path = WORK / uuid.uuid4().hex[:8]
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_graph(task, run_dir):
    task = dict(task)
    task["python"] = PYTHON
    run_dir.mkdir(parents=True, exist_ok=True)
    return invoke_run(task, str(run_dir), run_dir.name, str(run_dir / "checkpoints.sqlite"))


class OrchestratorTests(unittest.TestCase):
    def test_baseline_module_clis(self):
        apc = run_command([PYTHON, str(APC / "scripts" / "check_handoff.py"), "validate", str(REQUIREMENTS), str(HANDOFF), "--strict-assets"], 60)
        self.assertEqual(apc.returncode, 0, apc.stderr)
        aprd = run_command([PYTHON, str(APRD / "scripts" / "check_package.py"), "validate", str(DESIGN), "--handoff", str(HANDOFF)], 60)
        self.assertEqual(aprd.returncode, 0, aprd.stderr)
        seaa = run_command([PYTHON, str(SEAA / "scripts" / "check_evidence.py"), "validate", str(EVIDENCE), "--handoff", str(SEAA_HANDOFF)], 60)
        self.assertEqual(seaa.returncode, 0, seaa.stderr)
        out = WORK / "baseline-inspect.json"
        if out.exists():
            out.unlink()
        WORK.mkdir(parents=True, exist_ok=True)
        pptx = run_command([PYTHON, str(APRD / "scripts" / "inspect_pptx.py"), str(PPTX), str(out)], 60)
        self.assertEqual(pptx.returncode, 0, pptx.stderr)

    def test_real_modules_partial_when_evidence_missing(self):
        tmp = workspace()
        try:
            run_dir = tmp / "run1"
            result = run_graph({
                "handoff": str(HANDOFF),
                "requirements": str(REQUIREMENTS),
                "design_package": str(DESIGN),
                "reference_pptx": str(PPTX),
                "write_probe_pptx": True,
            }, run_dir)
            self.assertEqual(result["terminal"], "BLOCKED")
            self.assertEqual(result["evidence_decision"]["gate"], "missing")
            self.assertEqual(result["apc_result"]["acceptance"], "accepted")
            self.assertEqual(result["aprd_result"]["acceptance"], "accepted")
            ir = json.loads(Path(result["ir_path"]).read_text(encoding="utf-8"))
            self.assertEqual(ir["schema_version"], "academic-deck-ir/1")
            self.assertEqual(ir["design_bindings"]["constraints_dropped"], False)
            self.assertTrue(ir["design_bindings"]["layouts"]["layouts"])
            self.assertTrue(Path(result["pptx_path"]).is_file())
            events = read_events(str(run_dir))
            self.assertTrue(any(item["module_id"] == "apc" and item["action"] == "exec" for item in events))
            self.assertTrue(any(item["module_id"] == "seaa" and item["action"] == "blocked" for item in events))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_design_conflict_keeps_layout(self):
        tmp_path = workspace()
        try:
            package = tmp_path / "PPT_REFERENCE_PACKAGE"
            shutil.copytree(DESIGN, package)
            library_path = package / "LAYOUT_LIBRARY.json"
            library = json.loads(library_path.read_text(encoding="utf-8-sig"))
            for layout in library["layouts"]:
                layout["content_capacity"] = {"headline_max_chars": 4, "note": "test capacity"}
            library_path.write_text(json.dumps(library, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            checked = run_command([PYTHON, str(APRD / "scripts" / "check_package.py"), "validate", str(package), "--handoff", str(HANDOFF)], 60)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            result = run_graph({
                "handoff": str(HANDOFF),
                "requirements": str(REQUIREMENTS),
                "design_package": str(package),
                "write_probe_pptx": False,
            }, tmp_path / "run")
            self.assertTrue(result["conflicts"])
            ir = json.loads(Path(result["ir_path"]).read_text(encoding="utf-8"))
            self.assertFalse(ir["design_bindings"]["constraints_dropped"])
            self.assertTrue(any(spec["overflow_policy"] == "keep_layout_move_overflow_to_speaker_notes" for spec in ir["slide_specifications"]))
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_evidence_loop_downgrades_and_stops(self):
        tmp_path = workspace()
        try:
            handoff = json.loads(HANDOFF.read_text(encoding="utf-8-sig"))
            requirements = REQUIREMENTS.read_text(encoding="utf-8-sig")
            handoff_path = tmp_path / "PPT_HANDOFF.json"
            requirements_path = tmp_path / "PPT_REQUIREMENTS.md"
            handoff_path.write_text(json.dumps(handoff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            requirements_path.write_text(requirements, encoding="utf-8")
            package = tmp_path / "EVIDENCE_PACKAGE"
            shutil.copytree(EVIDENCE, package)
            manifest_path = package / "02_EVIDENCE_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            manifest["project_id"] = handoff["project_id"]
            manifest["base_revision"] = handoff["revision"]
            manifest["target_slide_ids"] = handoff["slide_order"]
            manifest["claim_ids"] = sorted(key for key in handoff["records"] if key.startswith("C"))
            id_map = {}
            for evidence in manifest["evidence"]:
                new_id = "E9" + evidence["evidence_id"][1:]
                id_map[evidence["evidence_id"]] = new_id
                evidence["evidence_id"] = new_id
                evidence["claim_links"] = [{"claim_id": "C002", "relation": "context"}]
            for slide in manifest["slides"]:
                slide["status"] = "PROVISIONAL"
                slide["gaps"] = ["support does not meet verified-finding strength"]
                slide["evidence_ids"] = [id_map.get(item, item) for item in slide.get("evidence_ids") or []]
                slide["visual_ids"] = [item for item in handoff["records"][slide["slide_id"]]["depends_on"] if str(item).startswith("V")]
            for task_row in manifest.get("tasks") or []:
                task_row["evidence_ids"] = [id_map.get(item, item) for item in task_row.get("evidence_ids") or []]
            for comparison in manifest.get("comparisons") or []:
                comparison["evidence_ids"] = [id_map.get(item, item) for item in comparison.get("evidence_ids") or []]
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            manual = package / "06_HANDOFF_TO_PROMPT3.md"
            text = manual.read_text(encoding="utf-8-sig")
            text = text.replace("| READY |", "| PROVISIONAL |")
            manual.write_text(text, encoding="utf-8")
            assets_path = package / "03_ASSET_MANIFEST.json"
            assets = json.loads(assets_path.read_text(encoding="utf-8-sig"))
            for asset in assets.get("assets") or []:
                asset["evidence_ids"] = [id_map.get(item, item) for item in asset.get("evidence_ids") or []]
            assets_path.write_text(json.dumps(assets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            checked = run_command([PYTHON, str(SEAA / "scripts" / "check_evidence.py"), "validate", str(package), "--handoff", str(handoff_path)], 60)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            result = run_graph({
                "handoff": str(handoff_path),
                "requirements": str(requirements_path),
                "evidence_package": str(package),
                "design_package": str(DESIGN),
                "budgets": {"max_iterations": 2, "allow_claim_downgrade": True},
                "write_probe_pptx": False,
            }, tmp_path / "run")
            self.assertEqual(result["iteration"], 1)
            self.assertEqual(result["evidence_decision"]["gate"], "blocked")
            self.assertIn("out of sync", result["evidence_decision"]["reason"])
            self.assertEqual(result["terminal"], "BLOCKED")
            revised = json.loads(Path(result["handoff_path"]).read_text(encoding="utf-8"))
            self.assertEqual(revised["records"]["C002"]["kind"], "hypothesis")
            self.assertEqual(revised["records"]["C002"]["status"], "HYPOTHESIS")
            self.assertNotEqual(result["handoff_path"], str(handoff_path))
            self.assertEqual(json.loads(handoff_path.read_text(encoding="utf-8"))["records"]["C002"]["kind"], "finding")
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_recovery_does_not_rerun_apc(self):
        tmp = workspace()
        try:
            run_dir = tmp / "run"
            task = {
                "handoff": str(HANDOFF),
                "requirements": str(REQUIREMENTS),
                "design_package": str(DESIGN),
                "python": PYTHON,
                "fault_once": "assemble",
                "write_probe_pptx": False,
            }
            run_dir.mkdir(parents=True, exist_ok=True)
            with self.assertRaises(RuntimeError):
                invoke_run(task, str(run_dir), "recover", str(run_dir / "checkpoints.sqlite"))
            before = [item for item in read_events(str(run_dir)) if item["module_id"] == "apc" and item["action"] == "exec"]
            result = resume_run(str(run_dir), "recover", str(run_dir / "checkpoints.sqlite"))
            after = [item for item in read_events(str(run_dir)) if item["module_id"] == "apc" and item["action"] == "exec"]
            self.assertEqual(len(before), len(after))
            self.assertEqual(result["terminal"], "BLOCKED")
            self.assertTrue((run_dir / "academic_deck_ir.json").is_file())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_incremental_rebuild_skips_unchanged_aprd(self):
        tmp_path = workspace()
        try:
            cache = tmp_path / "cache.json"
            first_handoff = tmp_path / "handoff.json"
            second_handoff = tmp_path / "handoff-edited.json"
            original = json.loads(HANDOFF.read_text(encoding="utf-8-sig"))
            first_handoff.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            edited = json.loads(json.dumps(original))
            edited["records"]["S001"]["summary"] = original["records"]["S001"]["summary"] + "（复审措辞）"
            second_handoff.write_text(json.dumps(edited, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            common = {
                "requirements": str(REQUIREMENTS),
                "design_package": str(DESIGN),
                "cache_path": str(cache),
                "write_probe_pptx": False,
            }
            run_graph({**common, "handoff": str(first_handoff)}, tmp_path / "run-a")
            run_graph({**common, "handoff": str(second_handoff)}, tmp_path / "run-b")
            events = read_events(str(tmp_path / "run-b"))
            self.assertTrue(any(item["module_id"] == "apc" and item["action"] == "exec" for item in events))
            self.assertTrue(any(item["module_id"] == "aprd" and item["action"] == "skip" for item in events))
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_qa_overflow_does_not_route_to_seaa(self):
        routed = route_report({"issues": [{"slide_id": "S002", "code": "text_overflow"}, {"slide_id": "S002", "code": "missing_citation"}]})
        self.assertEqual(routed["rerun_seaa_for_overflow"], False)
        self.assertIn("aprd_layout", routed["routes"])
        self.assertIn("seaa", routed["routes"])


if __name__ == "__main__":
    unittest.main()

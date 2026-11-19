import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator

PKG = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PKG / "src"), str(PKG / "examples")]
from academic_deck_ir import (SCHEMA, Conflict, Invalid, assemble, diagnostics, digest, dumps, edit,
    input_receipt, inspect_deck, loads, merge, migrate, new_deck, new_slide, proposal, read_page, validate)
from make_example import artifacts, example


class Contract(unittest.TestCase):
    def test_minimal_and_schema(self):
        Draft202012Validator.check_schema(SCHEMA)
        d = new_deck("minimal")
        self.assertEqual(loads(dumps(d)), d)
        self.assertEqual(diagnostics(d), ["empty_deck"])
        d["state"] = "released"
        with self.assertRaisesRegex(Invalid, "release blocked"):
            validate(d)

    def test_complex_consumer_and_reproducible_example(self):
        d = example()
        self.assertEqual(loads((PKG / "examples/complex.json").read_bytes()), d)
        report = inspect_deck(dumps(d), PKG / "examples")
        self.assertFalse(report["rendered"])
        p, = report["pages"]
        self.assertEqual([o["id"] for o in p["objects"]], ["chart", "formula", "table", "fig", "image", "caption"])
        chart = p["objects"][0]
        self.assertEqual(chart["content"]["series"][0]["values"], [12, 18])
        self.assertEqual(chart["box"]["x"], .1)
        self.assertEqual(chart["intent"]["slot"], "figure")
        self.assertEqual(p["assets"]["A001"]["evidence_ids"], ["E001"])
        self.assertEqual(p["sources"]["SRC001"]["uri"], "observations.csv")
        self.assertEqual(p["edges"][0]["locator"], "observations.csv rows 2-3")
        self.assertFalse(p["edges"][0]["eligible"])
        self.assertEqual(report["diagnostics"], ["no_eligible_support:C001", "page_not_ready:S001"])

    def test_roundtrip_strict_json(self):
        d = example()
        wire = dumps(d)
        self.assertEqual(dumps(loads(wire)), wire)
        self.assertEqual(digest(dict(reversed(d.items()))), digest(d))
        for bad in ('{"a":1,"a":0}', '{"a":NaN}', '{"a":1e999}', '{}'):
            with self.subTest(bad=bad), self.assertRaises(Invalid):
                loads(bad)
        d["metadata"]["titlle"] = "typo"
        with self.assertRaises(Invalid):
            dumps(d)

    def test_relationship_failures(self):
        d = example()
        p = d["slides"]["S001"]
        mutations = [lambda: p["resolved"]["chart"].update(x=.9),
                     lambda: p["semantic"]["elements"]["chart"]["series"][0].update(evidence_id="unknown"),
                     lambda: p["semantic"]["elements"]["table"]["rows"].append([]),
                     lambda: p["semantic"]["elements"]["formula"].update(x=0),
                     lambda: p["semantic"]["elements"]["fig"]["children"].append("fig"),
                     lambda: d["evidence"]["E001"].update(source_id="ghost"),
                     lambda: d["slide_order"].clear()]
        for i, mutate in enumerate(mutations):
            d = example()
            p = d["slides"]["S001"]
            mutate()
            with self.subTest(case=i), self.assertRaises(Invalid):
                validate(d)

    def test_resource_resolution_and_missing_source(self):
        d = example()
        with tempfile.TemporaryDirectory() as temp:
            self.assertIn("missing_asset:A001", diagnostics(d, temp))
            Path(temp, "widths.svg").write_bytes(b"changed")
            self.assertIn("changed_asset:A001", diagnostics(d, temp))
        for loc in ["../data", "/tmp/file", "A:/data", "a\\b", "NUL", "a:payload"]:
            d["assets"]["A001"]["path"] = loc
            with self.subTest(loc=loc), self.assertRaises(Invalid):
                validate(d)
        d = example()
        d["evidence"]["E001"]["source_id"] = d["assets"]["A001"]["source_id"] = None
        self.assertIn("missing_source:E001", diagnostics(d))
        d["evidence"]["E001"]["links"][0]["eligible"] = True
        with self.assertRaises(Invalid):
            validate(d)

    def test_opposed_is_not_support(self):
        d = example()
        e = copy.deepcopy(d["evidence"]["E001"])
        e["links"][0]["relation"] = "opposes"
        d["evidence"]["E002"] = e
        self.assertIn("conflicting_evidence:C001", diagnostics(d))
        e["links"][0]["eligible"] = True
        with self.assertRaisesRegex(Invalid, "ineligible"):
            validate(d)

    def test_page_and_element_operations(self):
        d = example()
        p = read_page(d, "S001")
        p["semantic"]["title"] = "private copy"
        changed = edit(d, "editor", ("create_page", "S002", 1, new_slide("Q", "Discuss")),
                       ("move_page", "S002", 0), ("edit_page", "S001", {"title": "Revised"}),
                       ("move", "S001", "caption", None, 0),
                       ("replace", "S001", "formula", {"kind": "formula", "latex": "b-a"}))
        self.assertEqual(changed["slide_order"], ["S002", "S001"])
        self.assertEqual(changed["slides"]["S001"]["semantic"]["order"][0], "caption")
        self.assertEqual(read_page(d, "S001")["semantic"]["title"], "Rectangle widths")
        self.assertEqual(changed["evidence"], d["evidence"])
        self.assertEqual(changed["slides"]["S001"]["semantic"]["claim_ids"], ["C001"])
        self.assertEqual(edit(changed, "editor", ("delete_page", "S002"))["slide_order"], ["S001"])
        before = dumps(d)
        with self.assertRaises(Invalid):
            edit(d, "bad", ("edit_page", "S001", {"title": "must roll back"}),
                 ("group", "S001", "oops", ["ghost"], None))
        self.assertEqual(dumps(d), before)

    def test_parallel_determinism_and_overlap(self):
        base = example()
        def changed(actor, f):
            c = copy.deepcopy(base)
            f(c)
            return proposal(base, actor, c)
        one = changed("design", lambda c: c["theme"].update(new_token=1))
        two = changed("text", lambda c: c["metadata"].update(title="new title"))
        d = merge(base, one, two)
        self.assertEqual(d, merge(base, two, one))
        self.assertEqual(d["history"][-1]["parent"], digest(base))
        self.assertEqual(merge(base, one, one)["theme"]["new_token"], 1)
        with self.assertRaisesRegex(Conflict, "stale"):
            merge(d, two)
        with self.assertRaisesRegex(Conflict, "competing"):
            merge(base, two, changed("other", lambda c: c["metadata"].update(title="other")))
        with self.assertRaisesRegex(Conflict, "overlap"):
            merge(base, {"base": digest(base), "actor": "a", "changes": [
                {"op": "replace", "path": ["metadata"], "value": {"title": "all"}}]}, two)

    def test_native_constraints(self):
        d = example()
        lid = "LAYOUT001"
        d["layouts"][lid].update(master="slideMaster2", theme="theme3", native={"shape7": {"allowed": ["style"]}})
        d["slides"]["S001"]["design"]["placements"]["caption"] = {"native_id": "shape7", "changes": ["style"]}
        d["layouts"][lid]["source_id"] = "SRC001"
        validate(d)
        with self.assertRaisesRegex(Invalid, "locked"):
            edit(d, "author", ("replace", "S001", "caption", {"kind":"text","runs":[{"text":"!"}]}))
        p = d["slides"]["S001"]["design"]["placements"]["caption"]
        p["changes"] = ["position"]
        with self.assertRaises(Invalid):
            validate(d)

    def test_mock_adapters_binding_and_input_ownership(self):
        h, a, s = artifacts()
        orig = copy.deepcopy((h, a, s))
        d = assemble(h, aprd=a, seaa=s)
        self.assertEqual(d["slides"]["S001"]["design"]["layout_id"], "LAYOUT001")
        self.assertEqual(d["assets"]["A001"]["immutable"], ["rectangle widths"])
        self.assertEqual(d["sources"]["SRC001"]["metadata"]["metadata"]["doi"], None)
        d["origin"]["apc_records"].clear()
        self.assertEqual((h, a, s), orig)
        for side, key, doc in [("aprd", "SLIDE_STYLE_MAPPING.json", "project_id"),
                               ("seaa", "02_EVIDENCE_MANIFEST.json", "base_revision")]:
            h, a, s = copy.deepcopy(orig)
            (a if side == "aprd" else s)[key][doc] = "foreign"
            with self.assertRaises(Conflict):
                assemble(h, aprd=a, seaa=s)

    def test_adapter_rejects_unknown_or_duplicate(self):
        h, a, s = artifacts()
        s["02_EVIDENCE_MANIFEST.json"]["evidence"] *= 2
        with self.assertRaisesRegex(Invalid, "duplicate"):
            assemble(h, seaa=s)
        h, a, s = artifacts()
        s["01_SOURCE_MANIFEST.json"]["sources"][0]["url_or_path"] = "/legacy/data.csv"
        with self.assertRaises(Invalid):
            assemble(h, seaa=s)
        d = assemble(h, seaa=s, locations={"/legacy/data.csv": "observations.csv"})
        self.assertEqual(d["sources"]["SRC001"]["uri"], "observations.csv")

    def test_cwd_cli(self):
        with tempfile.TemporaryDirectory(prefix="ir [外部] ") as td:
            env = dict(os.environ, PYTHONPATH=str(PKG / "src"))
            r = subprocess.run([sys.executable, "-m", "academic_deck_ir", str(PKG / "examples/complex.json"),
                "--resource-root", str(PKG / "examples")], cwd=td, env=env, capture_output=True, check=True)
            self.assertEqual(json.loads(r.stdout)["pages"][0]["slide_id"], "S001")

    def test_migration_real_orchestrator_builder(self):
        sys.path.insert(0, str(PKG.parent / "orchestrator/src"))
        from academic_ppt_orchestrator.contracts.ir import build_ir, VALIDATOR
        h, a, s = artifacts()
        # A real build_ir call on explicitly synthetic upstream files, not a LangGraph run.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, data in {**a, **s, "PPT_HANDOFF.json": h}.items():
                (root / name).write_text(json.dumps(data), encoding="utf-8")
            state = {"run_id": "contract", "handoff_path": str(root / "PPT_HANDOFF.json"),
                     "design_package": td, "evidence_package": td,
                     "apc_result": {"acceptance": "accepted"}, "aprd_result": {"acceptance": "accepted"},
                     "seaa_result": {"acceptance": "accepted", "validation_report": {"supports":{"C001":["E001"]}},
                         "payload": {"manifest":s["02_EVIDENCE_MANIFEST.json"], "evidence_package":td}},
                     "evidence_decision":{"gate":"sufficient","reason":"mock input"}}
            receipt = input_receipt(h, aprd=a, seaa=s)
            v2 = build_ir(state)
        original = copy.deepcopy(v2)
        def upgrade():
            return migrate(v2, h, check_v2=VALIDATOR.validate, aprd=a, seaa=s, receipt=receipt)
        d = upgrade()
        self.assertEqual(diagnostics(d, PKG/"examples"), [])
        self.assertEqual(d["slides"]["S001"]["status"], "READY")
        self.assertEqual(d["origin"]["v2"]["validation"]["status"], "accepted")
        self.assertTrue(inspect_deck(dumps(d))["pages"][0]["edges"][0]["eligible"])
        self.assertEqual(v2, original)
        self.assertNotIn(td, dumps(d).decode())
        s["01_SOURCE_MANIFEST.json"]["sources"][0]["url_or_path"] = "other.csv"
        with self.assertRaisesRegex(Invalid, "receipt"):
            upgrade()
        s["01_SOURCE_MANIFEST.json"]["sources"][0]["url_or_path"] = "observations.csv"
        v2["asset_bindings"].clear()
        with self.assertRaisesRegex(Invalid, "asset set"):
            upgrade()
        v2 = copy.deepcopy(original)
        v2["slide_specifications"][0]["evidence_gaps"] = ["different gap"]
        with self.assertRaisesRegex(Invalid, "gaps/readiness"):
            upgrade()
        v2 = copy.deepcopy(original)
        v2["evidence_bindings"][0]["locator"] = "unrelated source"
        with self.assertRaisesRegex(Invalid, "provenance mismatch"):
            upgrade()
        for version in ("academic-deck-ir/1", "future"):
            v2["schema_version"] = version
            with self.assertRaisesRegex(Invalid, "expected v2"):
                upgrade()


if __name__ == "__main__":
    unittest.main()

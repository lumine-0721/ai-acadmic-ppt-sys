"""Independent-review reproductions: fail closed without corrupting the base."""
import copy
import unittest
from test_contract import (Conflict, Invalid, assemble, artifacts, diagnostics, digest, dumps, edit,
                           example, merge, new_slide, proposal, validate)


class ReviewRegressions(unittest.TestCase):
    def test_release_approval_is_not_mergeable_with_content_edits(self):
        d = example()
        d["evidence"]["E001"]["links"][0]["eligible"] = True
        d["slides"]["S001"]["status"] = "READY"
        release = copy.deepcopy(d)
        release["state"] = "released"
        publish = proposal(d, "reviewer", release)
        change = copy.deepcopy(d)
        change["claims"]["C001"]["text"] = "Opposite conclusion"
        content = proposal(d, "editor", change)
        with self.assertRaisesRegex(Conflict, "publish separately"):
            merge(d, publish, content)
        with self.assertRaisesRegex(Invalid, "clear support eligibility"):
            merge(d, content)
        change["evidence"]["E001"]["links"][0]["eligible"] = False
        with self.assertRaisesRegex(Invalid, "renewed review"):
            merge(d, proposal(d, "editor", change))
        change["slides"]["S001"]["status"] = "PROVISIONAL"
        self.assertIn("no_eligible_support:C001", diagnostics(merge(d, proposal(d, "editor", change))))
        approved = merge(d, publish)
        self.assertEqual(approved["history"][-1]["parent"], digest(d))

    def test_prior_block_applies_to_deck_not_only_pages(self):
        d = example()
        d["origin"]["v2"] = {"validation":{"status":"blocked","reasons":["aprd_not_accepted"]}}
        d["slides"]["S001"]["status"] = "READY"
        d["evidence"]["E001"]["links"][0]["eligible"] = True
        self.assertEqual(diagnostics(d), ["upstream_gate:blocked"])
        d["state"] = "released"
        with self.assertRaisesRegex(Invalid, "upstream_gate"):
            validate(d)

    def test_rights_and_aprd_safe_area(self):
        d = example()
        d["assets"]["A001"]["rights"]["status"] = "NEEDS_PERMISSION"
        self.assertIn("asset_use_not_cleared:A001", diagnostics(d))
        d["assets"]["unused"] = copy.deepcopy(d["assets"]["A001"])
        self.assertNotIn("asset_use_not_cleared:unused", diagnostics(d))
        d["theme"]["constraints"] = {"safe_area_percent": {"x":10, "y":10, "w":80, "h":80}}
        validate(d)
        d["slides"]["S001"]["resolved"]["chart"]["x"] = 0
        with self.assertRaisesRegex(Invalid, "safe area"):
            validate(d)

    def test_native_lock_follows_template_object(self):
        d = example()
        page = d["slides"]["S001"]
        sem = page["semantic"]
        sem["elements"]["copy"] = copy.deepcopy(sem["elements"]["caption"])
        sem["order"].append("copy")
        d["layouts"]["LAYOUT001"]["native"] = {"N": {"allowed":[]}}
        page["design"]["placements"]["caption"] = {"native_id":"N"}
        swapped = copy.deepcopy(d)
        mapping = swapped["slides"]["S001"]["design"]["placements"]
        mapping["copy"] = mapping.pop("caption")
        changed = copy.deepcopy(d)
        changed["slides"]["S001"]["semantic"]["elements"]["copy"]["runs"][0]["text"] = "different content"
        a, b = proposal(d, "layout", swapped), proposal(d, "author", changed)
        merge(d, a)
        merge(d, b)
        with self.assertRaisesRegex(Invalid, "native text is locked"):
            merge(d, a, b)
        d["layouts"]["LAYOUT001"]["native"]["N"]["allowed"] = ["text"]
        changed = copy.deepcopy(d)
        changed["slides"]["S001"]["semantic"]["elements"]["caption"]["runs"][0]["bold"] = False
        with self.assertRaisesRegex(Invalid, "native style is locked"):
            merge(d, proposal(d, "author", changed))

    def test_portable_bindings_preserve_scientific_content(self):
        h, a, s = artifacts()
        h["records"]["S001"]["summary"] = "widths.svg"
        h["records"]["E001"] = {"summary":"fixture", "source":"readings.csv", "status":"USER_PROVIDED",
            "locator":"rows 2-3", "depends_on":[], "supports":["C001"]}
        d = assemble(h, aprd=a, seaa=s, locations={"widths.svg":"assets/shape.svg","readings.csv":"observations.csv"})
        self.assertEqual(d["assets"]["A001"]["path"], "assets/shape.svg")
        self.assertEqual(d["slides"]["S001"]["semantic"]["title"], "widths.svg")
        self.assertEqual(d["sources"]["apc.E001.source"]["uri"], "observations.csv")
        self.assertEqual(set(d["evidence"]), {"apc.E001", "E001"})
        d["slides"]["S001"]["design"]["constraints"]["reference_file"] = "/host/template"
        with self.assertRaises(Invalid):
            dumps(d)

    def test_retired_ids_and_read_dependencies(self):
        d = example()
        d = edit(d, "a", ("create_page", "extra", 0, new_slide("extra", "Discuss")))
        d = edit(d, "a", ("delete_page", "extra"))
        with self.assertRaisesRegex(Invalid, "retired identity"):
            edit(d, "a", ("create_page", "extra", 0, new_slide("different", "Compare")))
        changed, other = copy.deepcopy(d), copy.deepcopy(d)
        changed["slides"]["S001"]["resolved"]["chart"]["x"] = 0
        other["theme"]["typography"]["body"] = "serif"
        with self.assertRaisesRegex(Conflict, "read dependency"):
            merge(d, proposal(d, "layout", changed, reads=[["theme"]]), proposal(d, "designer", other))

"""Executable consumer contract. No Office, no network, no layout inference."""
from .model import diagnostics, digest, loads


def inspect_deck(wire, resource_root=None):
    deck = loads(wire)
    pages = []
    for sid in deck["slide_order"]:
        page = deck["slides"][sid]
        sem = page["semantic"]
        claim_ids, evidence_ids, asset_ids = (set(sem[k]) for k in ("claim_ids", "evidence_ids", "asset_ids"))
        objects = []
        stack = list(reversed(sem["order"]))
        while stack:
            eid = stack.pop()
            node = sem["elements"][eid]
            stack.extend(reversed(node.get("children", [])))
            claim_ids.update(node.get("claim_ids", []))
            if node["kind"] == "chart":
                evidence_ids.update(s["evidence_id"] for s in node["series"])
            if node["kind"] == "image":
                asset_ids.add(node["asset_id"])
            evidence_ids.update(node.get("evidence_ids", []))
            objects.append({"id": eid, "content": node, "intent": page["design"]["placements"].get(eid),
                            "box": page["resolved"].get(eid)})
        source_ids = set()
        for aid in asset_ids:
            asset = deck["assets"][aid]
            evidence_ids.update(asset["evidence_ids"])
            source_ids.add(asset["source_id"])
        edges = []
        for eid, e in sorted(deck["evidence"].items()):
            for link in e["links"]:
                if link["claim_id"] in claim_ids or eid in evidence_ids:
                    edges.append({"evidence_id": eid, "source_id": e["source_id"], "locator": e["locator"], **link})
        evidence_ids.update(row["evidence_id"] for row in edges)
        source_ids.update(deck["evidence"][eid]["source_id"] for eid in evidence_ids)
        layout = deck["layouts"].get(page["design"]["layout_id"])
        if layout:
            source_ids.add(layout["source_id"])
        pages.append({"slide_id": sid, "title": sem["title"], "status": page["status"], "objects": objects,
                      "layout": layout, "notes": sem.get("notes"), "edges": edges,
                      "assets": {k: deck["assets"][k] for k in sorted(asset_ids)},
                      "evidence": {k: deck["evidence"][k] for k in sorted(evidence_ids)},
                      "sources": {k: deck["sources"][k] for k in sorted(source_ids - {None})}})
    return {"consumer": "mock-pptx/1", "deck_digest": digest(deck), "state": deck["state"],
            "pages": pages, "diagnostics": diagnostics(deck, resource_root), "rendered": False}

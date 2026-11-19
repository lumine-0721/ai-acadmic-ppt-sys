"""Optimistic proposals. No process ever mutates another worker's deck."""
from copy import deepcopy

from .model import Conflict, Invalid, canonical, digest, identities, require, validate

PROTECTED = {"deck_id", "schema_version", "history", "origin"}


def read_page(deck, sid):
    validate(deck)
    require(sid in deck["slides"], f"unknown slide {sid}")
    return deepcopy(deck["slides"][sid])


def _index(at, length):
    require(type(at) is int and 0 <= at <= length, "index outside sequence")
    return at


def _container(sem, parent):
    if parent is None:
        return sem["order"]
    node = sem["elements"].get(parent, {})
    require(node.get("kind") == "group", "expected group")
    return node["children"]


def edit(deck, actor, *ops):
    """Create a private candidate; publish all operations or none via merge."""
    try:
        return _edit(deck, actor, ops)
    except Invalid:
        raise
    except (KeyError, TypeError, IndexError, ValueError, AttributeError, StopIteration) as exc:
        raise Invalid(f"malformed edit: {exc}") from exc


def _edit(deck, actor, ops):
    validate(deck)
    d = deepcopy(deck)
    for op in deepcopy(ops):
        name, sid, *args = op
        arity = {"create_page": 2, "delete_page": 0, "move_page": 1, "edit_page": 1,
                 "insert": 4, "replace": 2, "move": 3, "group": 3}
        require(name in arity and len(args) == arity[name], f"invalid operation: {name}")
        pages = d["slides"]
        if name == "create_page":
            at, slide = args
            require(sid not in pages, f"slide exists: {sid}")
            pages[sid] = slide
            d["slide_order"].insert(_index(at, len(pages)-1), sid)
            continue
        require(sid in pages, f"unknown slide {sid}")
        page = pages[sid]
        sem = page["semantic"]
        nodes = sem["elements"]
        if name == "delete_page":
            del pages[sid]
            d["slide_order"].remove(sid)
        elif name == "move_page":
            d["slide_order"].remove(sid)
            d["slide_order"].insert(_index(args[0], len(d["slide_order"])), sid)
        elif name == "edit_page":
            sem.update(args[0])
        elif name == "insert":
            eid, node, parent, at = args
            require(eid not in nodes, f"element exists: {eid}")
            target = _container(sem, parent)
            target.insert(_index(at, len(target)), eid)
            nodes[eid] = node
        elif name == "replace":
            eid, node = args
            require(eid in nodes, f"unknown element {eid}")
            nodes[eid] = node
        elif name == "move":
            eid, parent, at = args
            require(eid in nodes, f"unknown element {eid}")
            owners = [sem["order"]] + [n["children"] for n in nodes.values() if n["kind"] == "group"]
            next(owner for owner in owners if eid in owner).remove(eid)
            target = _container(sem, parent)
            target.insert(_index(at, len(target)), eid)
        elif name == "group":
            gid, members, parent = args
            order = _container(sem, parent)
            require(gid not in nodes and members and len(set(members)) == len(members)
                    and set(members) <= set(order), "group requires distinct sibling elements")
            pos = min(order.index(x) for x in members)
            order[:] = [x for x in order if x not in members]
            order.insert(pos, gid)
            nodes[gid] = {"kind": "group", "children": list(members)}
        else:
            raise Invalid(f"unknown operation {name}")
    return merge(deck, proposal(deck, actor, d))


def _diff(a, b, path=()):
    if type(a) is dict and type(b) is dict:
        for k in sorted(a.keys() | b.keys()):
            child = (*path, k)
            if k not in b:
                yield {"path": list(child), "op": "remove"}
            elif k not in a:
                yield {"path": list(child), "op": "add", "value": deepcopy(b[k])}
            else:
                yield from _diff(a[k], b[k], child)
    elif canonical(a) != canonical(b):
        yield {"path": list(path), "op": "replace", "value": deepcopy(b)}


def proposal(base, actor, candidate, *, reads=()):
    """reads lists additional object-key paths a derived task relied upon."""
    validate(base)
    validate(candidate)
    return {"base": digest(base), "actor": actor, "reads": [list(p) for p in reads],
            "changes": list(_diff(base, candidate))}


def _native_instances(page):
    result = {}
    for eid, p in page["design"]["placements"].items():
        if "native_id" not in p:
            continue
        n = page["semantic"]["elements"][eid]
        if n["kind"] == "text":
            content = [r["text"] for r in n["runs"]]
            styles = [{k: v for k, v in r.items() if k != "text"} for r in n["runs"]]
            shape = {k: v for k, v in n.items() if k != "runs"}
        else:
            content, styles, shape = None, None, n
        result[page["design"]["layout_id"], p["native_id"]] = (
            shape, {"text": content, "style": [styles, p.get("style")],
                    "position": [p.get("slot"), page["resolved"].get(eid)]})
    return result


def _native_guard(base, d):
    for lid in base["layouts"].keys() & d["layouts"].keys():
        require(base["layouts"][lid] == d["layouts"][lid], f"{lid}: import a new layout ID instead")
    for sid in base["slides"].keys() & d["slides"].keys():
        old, new = base["slides"][sid], d["slides"][sid]
        before, after = _native_instances(old), _native_instances(new)
        for lid, nid in before.keys() & after.keys():
            shape, attrs = before[lid, nid]
            new_shape, new_attrs = after[lid, nid]
            require(shape == new_shape, f"{nid}: native structure is locked")
            allowed = base["layouts"][lid]["native"][nid]["allowed"]
            for category in attrs:
                require(attrs[category] == new_attrs[category] or category in allowed,
                        f"{nid}: native {category} is locked")


def _review_guard(base, d, changes):
    if changes and d["state"] == "released" and set(changes) != {("state",)}:
        raise Conflict("release approves one exact base; publish separately")
    changed_claims = {k for k in base["claims"] if d["claims"].get(k) != base["claims"][k]}
    affected = set(changed_claims)
    for eid, ev in d["evidence"].items():
        old = base["evidence"].get(eid, {})
        src = ev["source_id"]
        if old != ev or base["sources"].get(src) != d["sources"].get(src):
            affected.update(l["claim_id"] for l in ev["links"] + old.get("links", []))
        for link in ev["links"]:
            if not link["eligible"]:
                continue
            require(link in old.get("links", []) and link["claim_id"] not in changed_claims
                    and base["sources"].get(src) == d["sources"].get(src)
                    and all(ev.get(k) == old.get(k) for k in (ev.keys() | old.keys()) - {"links"}),
                    f"{eid}: clear support eligibility and re-check changed evidence")
    for eid in base["evidence"].keys() - d["evidence"].keys():
        affected.update(l["claim_id"] for l in base["evidence"][eid]["links"])
    for sid in base["slides"].keys() & d["slides"].keys():
        old, page = base["slides"][sid], d["slides"][sid]
        sem = page["semantic"]
        used = set(sem["claim_ids"])
        for node in sem["elements"].values():
            used.update(node.get("claim_ids", []))
        if old["status"] == "READY" and (old["semantic"] != sem or used & affected):
            require(page["status"] != "READY", f"{sid}: changed content needs renewed review")


def merge(base, *proposals):
    """One batch, one parent digest, one revision. Lists are atomic conflict domains."""
    validate(base)
    parent = digest(base)
    changes, authors, footprints = {}, set(), []
    for p in proposals:
        require(isinstance(p, dict) and {"actor", "base", "changes"} <= set(p) <= {"actor", "base", "changes", "reads"} and
                isinstance(p["actor"], str) and p["actor"].strip() and type(p["changes"]) is list,
                "invalid proposal")
        if p["base"] != parent:
            raise Conflict("stale base digest")
        authors.add(p["actor"])
        reads = p.get("reads", [])
        require(type(reads) is list and all(type(r) is list and all(type(k) is str for k in r)
                for r in reads), "invalid read dependencies")
        writes = []
        for change in p["changes"]:
            require(isinstance(change, dict) and change.get("op") in ("remove", "add", "replace"),
                    "invalid change")
            expected = {"path", "op"} if change["op"] == "remove" else {"path", "op", "value"}
            require(set(change) == expected and type(change["path"]) is list, "invalid change shape")
            path = tuple(change["path"])
            require(path and all(type(k) is str for k in path) and path[0] not in PROTECTED,
                    "invalid or protected path")
            if path in changes and canonical(changes[path]) != canonical(change):
                raise Conflict(f"competing writes: {path}")
            changes[path] = change
            writes.append(path)
        read_paths = [tuple(r) for r in reads]
        for path in writes:
            if len(path) >= 3 and path[0] == "slides" and path[2] == "resolved":
                read_paths.extend([("theme",), ("layouts",), (*path[:2], "semantic"), (*path[:2], "design")])
        footprints.append((read_paths, writes))
    for i, (reads, _) in enumerate(footprints):
        for j, (_, writes) in enumerate(footprints):
            if i != j and any(r[:len(w)] == w or w[:len(r)] == r for r in reads for w in writes):
                raise Conflict("concurrent change to declared read dependency")
    d = deepcopy(base)
    for path, change in sorted(changes.items()):
        if any(path[:i] in changes for i in range(1, len(path))):
            raise Conflict(f"overlapping writes: {path}")
        target = d
        for key in path[:-1]:
            require(type(target) is dict and key in target, f"unknown parent: {path}")
            target = target[key]
        key = path[-1]
        require(type(target) is dict and (key in target) == (change["op"] != "add"),
                f"invalid patch target: {path}")
        if change["op"] == "remove":
            del target[key]
        else:
            target[key] = deepcopy(change["value"])
    validate(d)
    # Resolved boxes are derived, never silently reuse the previous layout for changed content.
    # An explicitly submitted resolved update belongs to this snapshot's layout computation.
    for sid in base["slides"].keys() & d["slides"].keys():
        page, old = d["slides"][sid], base["slides"][sid]
        explicit = any(path[:3] == ("slides", sid, "resolved") for path in changes)
        if not explicit and (page["semantic"] != old["semantic"] or page["design"] != old["design"]
                             or d["theme"] != base["theme"]) and page["resolved"]:
            page["resolved"] = {}
            changes[("slides", sid, "resolved")] = {"op": "replace", "value": {}}
    validate(d)
    _review_guard(base, d, changes)
    _native_guard(base, d)
    if changes:
        paths = ["/" + "/".join(k.replace("~", "~0").replace("/", "~1") for k in path)
                 for path in sorted(changes)]
        rev = {"parent": parent, "authors": sorted(authors), "paths": paths}
        retired = identities(base) - identities(d)
        if retired:
            rev["retired"] = sorted(retired)
        d["history"].append(rev)
    validate(d)
    return d

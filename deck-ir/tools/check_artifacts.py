"""Optional acceptance probe for external, previously checked upstream artifacts."""
import argparse
from pathlib import Path

from academic_deck_ir import assemble, dumps, inspect_deck, parse


def read(path):
    return parse(path.read_text(encoding="utf-8-sig"))


def package(root, names):
    return {name: read(root / name) for name in names.split()} if root else None


def values(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from values(v)
    elif isinstance(node, list):
        for v in node:
            yield from values(v)
    elif isinstance(node, str):
        yield node


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("handoff", type=Path)
    cli.add_argument("--aprd", type=Path)
    cli.add_argument("--seaa", type=Path)
    cli.add_argument("--source-root", type=Path, help="rebind absolute local locations beneath this root")
    args = cli.parse_args()
    h = read(args.handoff)
    a = package(args.aprd, "SOURCE_MANIFEST.json DESIGN_SYSTEM.json LAYOUT_LIBRARY.json SLIDE_STYLE_MAPPING.json")
    s = package(args.seaa, "01_SOURCE_MANIFEST.json 02_EVIDENCE_MANIFEST.json 03_ASSET_MANIFEST.json")
    locations = {}
    if args.source_root:
        root = args.source_root.resolve(strict=True)
        for value in values([h, a, s]):
            p = Path(value)
            if p.is_absolute() and p.resolve().is_relative_to(root):
                locations[value] = p.resolve().relative_to(root).as_posix()
    d = assemble(h, aprd=a, seaa=s, locations=locations)
    report = inspect_deck(dumps(d))
    print("SLIDES=%d SOURCES=%d ASSETS=%d LAYOUTS=%d RENDERED=%s" % (
        len(report["pages"]), len(d["sources"]), len(d["assets"]), len(d["layouts"]), report["rendered"]))


if __name__ == "__main__":
    main()

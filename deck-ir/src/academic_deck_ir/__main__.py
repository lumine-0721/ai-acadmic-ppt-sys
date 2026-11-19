import argparse
import json
from pathlib import Path

from .consumer import inspect_deck
from .model import Invalid


def main():
    cli = argparse.ArgumentParser(description="Inspect a v3 Deck without rendering")
    cli.add_argument("deck", type=Path)
    cli.add_argument("--resource-root", type=Path)
    args = cli.parse_args()
    try:
        report = inspect_deck(args.deck.read_bytes(), args.resource_root)
    except (Invalid, OSError) as exc:
        cli.exit(2, f"invalid input: {exc}\n")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

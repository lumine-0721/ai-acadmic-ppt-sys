"""Entry point for the APC package."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "academic-presentation-compiler"
SCRIPTS = ROOT / "scripts"


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("academic-presentation-compiler validate|impact|schema")
        return 0
    if args[0] in {"validate", "impact"}:
        target = SCRIPTS / "check_handoff.py"
        cmd = [sys.executable, str(target), *args]
    elif args[0] == "schema":
        target = SCRIPTS / "check_handoff_schema.py"
        cmd = [sys.executable, str(target), *args[1:]]
    else:
        print("unknown command", args[0], file=sys.stderr)
        return 2
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())

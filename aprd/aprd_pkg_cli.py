"""Entry point for the APRD package."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "academic-presentation-reference-design"
SCRIPTS = ROOT / "scripts"
ENGINE = {"extract", "validate-spec", "compile-package", "reconstruct", "verify"}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("academic-presentation-reference-design extract|validate-spec|compile-package|reconstruct|verify|package-validate|inspect-pptx|inspect-design")
        return 0
    if args[0] in ENGINE:
        target = SCRIPTS / "aprd_cli.py"
        cmd = [sys.executable, str(target), *args]
    elif args[0] == "package-validate":
        target = SCRIPTS / "check_package.py"
        cmd = [sys.executable, str(target), "validate", *args[1:]]
    elif args[0] == "inspect-pptx":
        target = SCRIPTS / "inspect_pptx.py"
        cmd = [sys.executable, str(target), *args[1:]]
    elif args[0] == "inspect-design":
        target = SCRIPTS / "inspect_design.py"
        cmd = [sys.executable, str(target), *args[1:]]
    else:
        print("unknown command", args[0], file=sys.stderr)
        return 2
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())

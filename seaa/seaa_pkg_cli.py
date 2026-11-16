"""Entry point for the SEAA package."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "scientific-evidence-asset-acquisition"
SCRIPTS = ROOT / "scripts"
CORE = {"discover", "ingest-pdf", "ground", "audit", "synthesize", "build-package"}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("scientific-evidence-asset-acquisition discover|ingest-pdf|ground|audit|synthesize|build-package|evidence-validate|probe-pdf|audit-files")
        return 0
    if args[0] in CORE:
        target = SCRIPTS / "seaa_cli.py"
        cmd = [sys.executable, str(target), *args]
    elif args[0] == "evidence-validate":
        target = SCRIPTS / "check_evidence.py"
        cmd = [sys.executable, str(target), "validate", *args[1:]]
    elif args[0] == "probe-pdf":
        target = SCRIPTS / "probe_pdf_evidence.py"
        cmd = [sys.executable, str(target), *args[1:]]
    elif args[0] == "audit-files":
        target = SCRIPTS / "audit_files.py"
        cmd = [sys.executable, str(target), *args[1:]]
    else:
        print("unknown command", args[0], file=sys.stderr)
        return 2
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())

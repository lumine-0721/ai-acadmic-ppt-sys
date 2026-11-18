#!/usr/bin/env python3
"""Rebuild every committed test fixture under fixtures/.

Usage (from the repo root), with python-pptx installed (see orchestrator/requirements.txt):
    python tools/build_fixtures.py
"""
import runpy
from pathlib import Path

HERE = Path(__file__).resolve().parent
for script in ("build_apc_fixtures.py", "build_seaa_fixtures.py", "build_package_fixtures.py", "build_pptx_fixtures.py"):
    runpy.run_path(str(HERE / script), run_name="__main__")

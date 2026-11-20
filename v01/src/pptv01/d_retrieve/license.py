"""Licence policy for acquired assets (technical plan: legal, traceable). Pure functions, unit-tested.

Accept: CC0, public domain, CC BY, CC BY-SA (any version).  Reject: NC, ND, fair use, unknown/empty, restricted files.
"""

from __future__ import annotations

import html
import re

_TAG = re.compile(r"<[^>]+>")


def plain(s: str | None) -> str:
    """Commons extmetadata fields are HTML fragments; reduce to text."""
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", s or ""))).strip()


_REJECT = re.compile(r"(?:\bNC\b|NonCommercial|Non-Commercial|\bND\b|NoDerivs|No Derivatives|fair use|non-free|"
                     r"all rights reserved|GFDL-no|permission|unknown|copyrighted)", re.I)
_PD = re.compile(r"^(?:public domain|pd[\s-]|pd$|cc0|cc[\s-]zero|no known copyright)", re.I)
_CC_BY = re.compile(r"^cc[\s-]by(?:[\s-]sa)?(?:[\s-]\d(?:\.\d)?)?(?:[\s-][a-z]{2,3})?$", re.I)


def check_license(short: str | None, restrictions: str | None = "") -> tuple[bool, str]:
    """(accepted, reason). ``short`` is Commons' LicenseShortName."""
    s = plain(short)
    if not s:
        return False, "no licence stated"
    if plain(restrictions):
        return False, f"file has restrictions: {plain(restrictions)[:60]}"
    if _REJECT.search(s):
        return False, f"licence not allowed ({s})"
    if _PD.match(s) or _CC_BY.match(s):
        return True, s
    return False, f"licence not on the allowlist ({s})"


def needs_attribution(short: str | None) -> bool:
    return bool(re.search(r"\bBY\b", plain(short).upper().replace("-", " ")))

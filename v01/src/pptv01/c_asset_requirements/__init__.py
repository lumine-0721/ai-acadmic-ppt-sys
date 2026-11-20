"""Stage C: provisional slide plan -> asset_requirements.json (what each page needs, how to accept it)."""

IMPLEMENTED = True

from .stage import ARTIFACT, RequirementRuleError, run_stage_c  # noqa: E402,F401
from .validate import validate_requirements  # noqa: E402,F401

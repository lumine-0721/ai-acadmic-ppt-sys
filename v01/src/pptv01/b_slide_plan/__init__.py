"""Stage B: research (from A's retrieval_brief) -> provisional slide_plan.json (never final)."""

from pptv01.retrieval import DisabledResearch, ResearchProvider  # noqa: F401

from .stage import ARTIFACT, RESEARCH_ARTIFACT, ResearchFailed, run_stage_b  # noqa: F401
from .validate import SlidePlanRuleError, validate_slide_plan  # noqa: F401

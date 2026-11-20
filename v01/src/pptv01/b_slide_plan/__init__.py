"""Stage B: ProductionPlan -> provisional slide_plan.json (never final)."""

from .research import NullResearch, ResearchBrief, ResearchProvider  # noqa: F401
from .stage import ARTIFACT, run_stage_b  # noqa: F401
from .validate import SlidePlanRuleError, validate_slide_plan  # noqa: F401

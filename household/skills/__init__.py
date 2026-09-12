"""Skills plug into the planner through the frozen contract in `base.py`.

`SKILLS` is the order parameter: the matcher prefers the earlier skill when a request matches two, and /api/meta,
the README and the guardrail harness list skills in this order. The core household skill is the fallback for plain
payments and emails and is not part of the ordered list.
"""

from __future__ import annotations

from .allowance import ALLOWANCE
from .base import ActionTemplate, Citation, RulesTable, Skill, SkillTool, ToolContext
from .benefits import BENEFITS
from .core import HOUSEHOLD
from .education import EDUCATION
from .recall import RECALL

SKILLS: tuple[Skill, ...] = (ALLOWANCE, BENEFITS, EDUCATION, RECALL)
CORE: Skill = HOUSEHOLD
ALL_SKILLS: tuple[Skill, ...] = (*SKILLS, CORE)
SKILL_BY_ID: dict[str, Skill] = {skill.id: skill for skill in ALL_SKILLS}
SKILL_TOOL_NAMES: tuple[str, ...] = tuple(tool.name for skill in ALL_SKILLS for tool in skill.tools)


def skill_for(skill_id: str) -> Skill:
    """The skill for an id; unknown or empty ids fall back to the core skill (the pipeline notes the fallback)."""
    return SKILL_BY_ID.get(skill_id, CORE)


def match_skill(text: str) -> Skill:
    """Deterministic matcher used by the fake provider and as a tie-break reference: first hint match in SKILLS order."""
    for skill in SKILLS:
        if skill.matches(text):
            return skill
    return CORE


__all__ = [
    "ALL_SKILLS", "CORE", "SKILLS", "SKILL_BY_ID", "SKILL_TOOL_NAMES", "ActionTemplate", "Citation", "RulesTable",
    "Skill", "SkillTool", "ToolContext", "match_skill", "skill_for",
]

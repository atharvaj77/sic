"""Skills: schema + loader.

Public surface:
    SkillManifest, Skill, SKILL_NAME_RE
    load_skill, discover_skills, parse_frontmatter, dump_frontmatter
    SkillLoadError
"""

from sic.skills.loader import (
    SkillLoadError,
    discover_skills,
    dump_frontmatter,
    load_skill,
    parse_frontmatter,
)
from sic.skills.schema import SKILL_NAME_RE, Skill, SkillManifest

__all__ = [
    "SKILL_NAME_RE",
    "Skill",
    "SkillLoadError",
    "SkillManifest",
    "discover_skills",
    "dump_frontmatter",
    "load_skill",
    "parse_frontmatter",
]

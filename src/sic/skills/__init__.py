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
from sic.skills.resolver import (
    InstalledSkill,
    SkillConflict,
    SkillConflictReason,
    SkillOverride,
    SkillScope,
    SkillSet,
    resolve_skills,
)
from sic.skills.schema import SKILL_NAME_RE, Skill, SkillManifest

__all__ = [
    "SKILL_NAME_RE",
    "InstalledSkill",
    "Skill",
    "SkillConflict",
    "SkillConflictReason",
    "SkillLoadError",
    "SkillManifest",
    "SkillOverride",
    "SkillScope",
    "SkillSet",
    "discover_skills",
    "dump_frontmatter",
    "load_skill",
    "parse_frontmatter",
    "resolve_skills",
]

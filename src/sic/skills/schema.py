"""Pydantic models for skill manifests.

A *skill* is a directory containing a ``SKILL.md`` file with YAML frontmatter
and a Markdown body. Multiple skills can live side-by-side in a ``skills/``
directory (team repo) or ``~/.sic/personal-skills/`` (personal scope).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


class SkillManifest(BaseModel):
    """YAML frontmatter of a SKILL.md."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    description: str
    applies_to: list[str] = Field(default_factory=list)
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)
    lock: bool = False

    @field_validator("name")
    @classmethod
    def _name_pattern(cls, v: str) -> str:
        if not SKILL_NAME_RE.match(v):
            raise ValueError(
                f"skill name {v!r} must match {SKILL_NAME_RE.pattern} "
                "(lowercase letters, digits, hyphens)"
            )
        return v

    @field_validator("version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"skill version {v!r} is not valid semver (e.g. 1.2.3)")
        return v

    @field_validator("description")
    @classmethod
    def _description_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("description must not be empty")
        return v


class Skill(BaseModel):
    """A loaded skill: manifest + body + on-disk location."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    manifest: SkillManifest
    body: str
    source_dir: Path
    assets: list[Path] = Field(default_factory=list)

    @property
    def name(self) -> str:
        return self.manifest.name

    def to_skill_md(self) -> str:
        """Serialize back to a SKILL.md string (frontmatter + body)."""
        from sic.skills.loader import dump_frontmatter  # avoid cycle

        return dump_frontmatter(self.manifest) + "\n" + self.body.lstrip("\n")


def manifest_from_dict(data: dict[str, Any]) -> SkillManifest:
    """Helper to validate raw frontmatter dicts with a clean error trail."""
    return SkillManifest.model_validate(data)

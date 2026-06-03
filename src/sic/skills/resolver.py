"""Resolve a set of team skills + personal skills into the final install set.

Tiers (per skill ``name``):

- **Tier 1 (pass-through):** skill exists on only one side → installed as-is.
- **Tier 2 (personal wins):** same name on both sides, team unlocked, manifests
  differ → personal version installed; recorded as an override.
- **Tier 3 (locked):** same name, team has ``lock: true`` → team version
  installed; personal copy is *ignored* and surfaced as a conflict.

Identical manifests on both sides resolve as Tier 1 (team copy kept). Skills
are matched purely by manifest ``name``; directory paths are not consulted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sic.skills.schema import Skill


class SkillScope(str, Enum):
    TEAM = "team"
    PERSONAL = "personal"


class SkillConflictReason(str, Enum):
    LOCKED_BY_TEAM = "locked_by_team"


@dataclass(frozen=True)
class SkillOverride:
    """Personal skill replaced an unlocked team skill of the same name."""

    name: str
    team_version: str
    personal_version: str


@dataclass(frozen=True)
class SkillConflict:
    """Personal skill was rejected because the team locked the name."""

    name: str
    team_version: str
    personal_version: str
    reason: SkillConflictReason = SkillConflictReason.LOCKED_BY_TEAM


@dataclass(frozen=True)
class InstalledSkill:
    """A skill that will be installed, plus where it came from."""

    skill: Skill
    scope: SkillScope

    @property
    def name(self) -> str:
        return self.skill.name


@dataclass
class SkillSet:
    """Output of :func:`resolve_skills`."""

    installed: list[InstalledSkill] = field(default_factory=list)
    overrides: list[SkillOverride] = field(default_factory=list)
    conflicts: list[SkillConflict] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    def by_name(self) -> dict[str, InstalledSkill]:
        return {i.name: i for i in self.installed}


def _index(skills: list[Skill]) -> dict[str, Skill]:
    out: dict[str, Skill] = {}
    for s in skills:
        if s.name in out:
            raise ValueError(f"duplicate skill name within one scope: {s.name!r}")
        out[s.name] = s
    return out


def _same(team: Skill, personal: Skill) -> bool:
    return team.manifest == personal.manifest and team.body == personal.body


def resolve_skills(
    team_skills: list[Skill],
    personal_skills: list[Skill],
) -> SkillSet:
    """Apply the tier rules and return a deterministic :class:`SkillSet`.

    The result is sorted by skill name so callers (renderer, installer) get
    byte-stable output.
    """
    team_idx = _index(team_skills)
    personal_idx = _index(personal_skills)

    installed: list[InstalledSkill] = []
    overrides: list[SkillOverride] = []
    conflicts: list[SkillConflict] = []

    for name in sorted(team_idx.keys() | personal_idx.keys()):
        team = team_idx.get(name)
        personal = personal_idx.get(name)

        # Tier 1: only one side has it.
        if team is None:
            assert personal is not None
            installed.append(InstalledSkill(personal, SkillScope.PERSONAL))
            continue
        if personal is None:
            installed.append(InstalledSkill(team, SkillScope.TEAM))
            continue

        # Both sides have it.
        if _same(team, personal):
            # Tier 1: identical content — keep the team copy (canonical source).
            installed.append(InstalledSkill(team, SkillScope.TEAM))
            continue

        if team.manifest.lock:
            # Tier 3: locked — team wins, personal copy is rejected.
            installed.append(InstalledSkill(team, SkillScope.TEAM))
            conflicts.append(
                SkillConflict(
                    name=name,
                    team_version=team.manifest.version,
                    personal_version=personal.manifest.version,
                )
            )
            continue

        # Tier 2: personal wins, recorded as an override.
        installed.append(InstalledSkill(personal, SkillScope.PERSONAL))
        overrides.append(
            SkillOverride(
                name=name,
                team_version=team.manifest.version,
                personal_version=personal.manifest.version,
            )
        )

    return SkillSet(installed=installed, overrides=overrides, conflicts=conflicts)

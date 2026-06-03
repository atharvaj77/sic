"""Tests for sic.skills.resolver (subphase 8.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sic.skills import (
    Skill,
    SkillConflictReason,
    SkillManifest,
    SkillScope,
    resolve_skills,
)


def _skill(
    name: str,
    version: str = "1.0.0",
    body: str = "default body",
    lock: bool = False,
    description: str = "desc",
) -> Skill:
    return Skill(
        manifest=SkillManifest(
            name=name,
            version=version,
            description=description,
            lock=lock,
        ),
        body=body,
        source_dir=Path("/tmp/fake") / name,
    )


# ---------- Tier 1 ----------

def test_team_only_skill_is_installed_as_team() -> None:
    out = resolve_skills([_skill("a")], [])
    assert [i.name for i in out.installed] == ["a"]
    assert out.installed[0].scope is SkillScope.TEAM
    assert not out.overrides
    assert not out.conflicts


def test_personal_only_skill_is_installed_as_personal() -> None:
    out = resolve_skills([], [_skill("p")])
    assert out.installed[0].scope is SkillScope.PERSONAL
    assert not out.overrides
    assert not out.conflicts


def test_identical_skills_on_both_sides_keep_team_copy() -> None:
    team = _skill("same", body="b", description="d")
    personal = _skill("same", body="b", description="d")
    out = resolve_skills([team], [personal])
    assert [i.scope for i in out.installed] == [SkillScope.TEAM]
    assert not out.overrides
    assert not out.conflicts


def test_disjoint_skills_pass_through_sorted_by_name() -> None:
    out = resolve_skills([_skill("zeta")], [_skill("alpha")])
    assert [i.name for i in out.installed] == ["alpha", "zeta"]


# ---------- Tier 2 ----------

def test_personal_overrides_unlocked_team_skill() -> None:
    team = _skill("x", version="1.0.0", body="team body", lock=False)
    personal = _skill("x", version="1.1.0", body="personal body")
    out = resolve_skills([team], [personal])
    assert len(out.installed) == 1
    inst = out.installed[0]
    assert inst.scope is SkillScope.PERSONAL
    assert inst.skill.body == "personal body"
    assert len(out.overrides) == 1
    ov = out.overrides[0]
    assert (ov.name, ov.team_version, ov.personal_version) == ("x", "1.0.0", "1.1.0")
    assert not out.conflicts
    assert not out.has_conflicts


def test_override_recorded_when_only_body_differs() -> None:
    team = _skill("x", body="t")
    personal = _skill("x", body="p")
    out = resolve_skills([team], [personal])
    assert out.installed[0].scope is SkillScope.PERSONAL
    assert len(out.overrides) == 1


# ---------- Tier 3 ----------

def test_locked_team_skill_wins_and_records_conflict() -> None:
    team = _skill("locked", version="2.0.0", body="t", lock=True)
    personal = _skill("locked", version="9.9.9", body="p")
    out = resolve_skills([team], [personal])
    assert out.installed[0].scope is SkillScope.TEAM
    assert out.installed[0].skill.body == "t"
    assert not out.overrides
    assert len(out.conflicts) == 1
    c = out.conflicts[0]
    assert c.name == "locked"
    assert c.team_version == "2.0.0"
    assert c.personal_version == "9.9.9"
    assert c.reason is SkillConflictReason.LOCKED_BY_TEAM
    assert out.has_conflicts


def test_locked_team_with_identical_personal_is_not_a_conflict() -> None:
    team = _skill("locked", body="b", lock=True)
    personal = _skill("locked", body="b", lock=True)
    out = resolve_skills([team], [personal])
    assert not out.conflicts
    assert not out.overrides


# ---------- determinism & guards ----------

def test_output_sorted_by_name() -> None:
    out = resolve_skills(
        [_skill("c"), _skill("a"), _skill("b")],
        [_skill("d")],
    )
    assert [i.name for i in out.installed] == ["a", "b", "c", "d"]


def test_duplicate_skill_name_within_one_scope_raises() -> None:
    with pytest.raises(ValueError, match="duplicate skill name"):
        resolve_skills([_skill("dup"), _skill("dup")], [])


def test_by_name_helper() -> None:
    out = resolve_skills([_skill("a"), _skill("b")], [_skill("c")])
    idx = out.by_name()
    assert set(idx.keys()) == {"a", "b", "c"}
    assert idx["c"].scope is SkillScope.PERSONAL


def test_mixed_tiers_all_at_once() -> None:
    team = [
        _skill("only-team"),
        _skill("override-me", version="1.0.0", body="t", lock=False),
        _skill("locked-name", version="1.0.0", body="t", lock=True),
        _skill("identical", body="same"),
    ]
    personal = [
        _skill("only-personal"),
        _skill("override-me", version="2.0.0", body="p"),
        _skill("locked-name", version="2.0.0", body="p"),
        _skill("identical", body="same"),
    ]
    out = resolve_skills(team, personal)
    names_scopes = [(i.name, i.scope) for i in out.installed]
    assert names_scopes == [
        ("identical", SkillScope.TEAM),
        ("locked-name", SkillScope.TEAM),
        ("only-personal", SkillScope.PERSONAL),
        ("only-team", SkillScope.TEAM),
        ("override-me", SkillScope.PERSONAL),
    ]
    assert [o.name for o in out.overrides] == ["override-me"]
    assert [c.name for c in out.conflicts] == ["locked-name"]

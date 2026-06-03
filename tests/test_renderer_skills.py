"""Tests for the skill-aware render hook (subphase 8.6)."""

from __future__ import annotations

from pathlib import Path

from sic.loader import load_profile
from sic.merger import merge_profiles
from sic.renderer import RenderContext, render_claude_md
from sic.skills import (
    Skill,
    SkillManifest,
    resolve_skills,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _ctx() -> RenderContext:
    return RenderContext(
        team_owner="acme-data-platform",
        personal_owner="alice",
        generated_at=None,
    )


def _merge():
    team = load_profile(EXAMPLES / "team.yaml")
    personal = load_profile(EXAMPLES / "alice.personal.yaml")
    return team, personal, merge_profiles(team, personal)


def _skill(name: str, version: str = "1.0.0", lock: bool = False, desc: str = "x") -> Skill:
    return Skill(
        manifest=SkillManifest(name=name, version=version, description=desc, lock=lock),
        body="body\n",
        source_dir=Path("/tmp/fake") / name,
    )


def test_render_without_skills_arg_is_unchanged() -> None:
    _team, _p, result = _merge()
    out = render_claude_md(result, _ctx())
    assert "Available skills" not in out


def test_render_with_none_skill_set_is_unchanged() -> None:
    _team, _p, result = _merge()
    out = render_claude_md(result, _ctx(), skill_set=None)
    assert "Available skills" not in out


def test_render_with_empty_skill_set_omits_section() -> None:
    _team, _p, result = _merge()
    empty = resolve_skills([], [])
    out = render_claude_md(result, _ctx(), skill_set=empty)
    assert "Available skills" not in out


def test_render_with_skills_emits_table() -> None:
    _team, _p, result = _merge()
    skill_set = resolve_skills(
        [_skill("cdp-mcp", "0.3.0", lock=False, desc="CDP rules"),
         _skill("sql-style", "1.0.0", lock=True, desc="SQL formatting")],
        [],
    )
    out = render_claude_md(result, _ctx(), skill_set=skill_set)
    assert "## Available skills" in out
    assert "`cdp-mcp`" in out
    assert "0.3.0" in out
    assert "`sql-style` 🔒" in out
    assert "| `cdp-mcp` | 0.3.0 | team | CDP rules |" in out


def test_render_collapses_multiline_description() -> None:
    _team, _p, result = _merge()
    skill_set = resolve_skills(
        [_skill("multiline", desc="line one\n  line two\n  line three")],
        [],
    )
    out = render_claude_md(result, _ctx(), skill_set=skill_set)
    assert "line one line two line three" in out


def test_render_skill_conflicts_section_appears() -> None:
    _team, _p, result = _merge()
    skill_set = resolve_skills(
        [_skill("locked", version="1.0.0", lock=True)],
        [_skill("locked", version="9.9.9")],
    )
    out = render_claude_md(result, _ctx(), skill_set=skill_set)
    assert "Unresolved skill conflicts" in out
    assert "team@1.0.0 is locked" in out
    assert "personal@9.9.9 was ignored" in out


def test_render_skill_personal_scope_shown() -> None:
    _team, _p, result = _merge()
    skill_set = resolve_skills([], [_skill("my-tool", desc="just mine")])
    out = render_claude_md(result, _ctx(), skill_set=skill_set)
    assert "| `my-tool` | 1.0.0 | personal | just mine |" in out


def test_render_is_idempotent_with_skills() -> None:
    _team, _p, result = _merge()
    skill_set = resolve_skills([_skill("a"), _skill("b")], [])
    a = render_claude_md(result, _ctx(), skill_set=skill_set)
    b = render_claude_md(result, _ctx(), skill_set=skill_set)
    assert a == b

"""Tests for sic.skills.schema and sic.skills.loader (subphase 8.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sic.skills import (
    Skill,
    SkillLoadError,
    SkillManifest,
    discover_skills,
    dump_frontmatter,
    load_skill,
    parse_frontmatter,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "skills"


# ---------- SkillManifest validation ----------

def _valid_data(**over) -> dict:
    base = {
        "name": "cdp-mcp",
        "version": "0.3.0",
        "description": "How to drive CDP MCP.",
        "applies_to": ["cdp"],
    }
    base.update(over)
    return base


def test_manifest_accepts_minimum_valid_data() -> None:
    m = SkillManifest.model_validate(_valid_data())
    assert m.name == "cdp-mcp"
    assert m.version == "0.3.0"
    assert m.lock is False
    assert m.tags == []


def test_manifest_rejects_uppercase_name() -> None:
    with pytest.raises(ValueError, match="must match"):
        SkillManifest.model_validate(_valid_data(name="CDP-MCP"))


def test_manifest_rejects_name_starting_with_hyphen() -> None:
    with pytest.raises(ValueError, match="must match"):
        SkillManifest.model_validate(_valid_data(name="-bad"))


def test_manifest_rejects_non_semver_version() -> None:
    with pytest.raises(ValueError, match="semver"):
        SkillManifest.model_validate(_valid_data(version="0.3"))


def test_manifest_rejects_empty_description() -> None:
    with pytest.raises(ValueError, match="description"):
        SkillManifest.model_validate(_valid_data(description="   "))


def test_manifest_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        SkillManifest.model_validate(_valid_data(extra_field="nope"))


def test_manifest_accepts_lock_flag() -> None:
    m = SkillManifest.model_validate(_valid_data(lock=True))
    assert m.lock is True


# ---------- parse_frontmatter / dump_frontmatter ----------

def test_parse_frontmatter_extracts_dict_and_body() -> None:
    text = (
        "---\n"
        "name: x\n"
        "version: 1.0.0\n"
        "description: y\n"
        "---\n"
        "body text\n"
    )
    data, body = parse_frontmatter(text)
    assert data == {"name": "x", "version": "1.0.0", "description": "y"}
    assert body == "body text\n"


def test_parse_frontmatter_missing_opening_delim() -> None:
    with pytest.raises(ValueError, match="must start with"):
        parse_frontmatter("name: x\n---\nbody")


def test_parse_frontmatter_missing_closing_delim() -> None:
    with pytest.raises(ValueError, match="not closed"):
        parse_frontmatter("---\nname: x\nno close here\n")


def test_parse_frontmatter_non_mapping_rejected() -> None:
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        parse_frontmatter("---\n- just\n- a\n- list\n---\nbody")


def test_dump_frontmatter_round_trips() -> None:
    manifest = SkillManifest.model_validate(_valid_data())
    fm = dump_frontmatter(manifest)
    data, body = parse_frontmatter(fm + "ignored")
    assert SkillManifest.model_validate(data) == manifest
    assert body == "ignored"


# ---------- load_skill / discover_skills ----------

def test_load_skill_from_fixture() -> None:
    s = load_skill(EXAMPLES / "cdp-mcp")
    assert isinstance(s, Skill)
    assert s.name == "cdp-mcp"
    assert s.manifest.version == "0.3.0"
    assert "CDP MCP playbook" in s.body
    assert s.source_dir == EXAMPLES / "cdp-mcp"


def test_load_skill_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(SkillLoadError, match="missing SKILL.md"):
        load_skill(tmp_path / "nope")


def test_load_skill_missing_skill_md(tmp_path: Path) -> None:
    d = tmp_path / "bare"
    d.mkdir()
    with pytest.raises(SkillLoadError, match="missing SKILL.md"):
        load_skill(d)


def test_load_skill_directory_name_must_match_manifest(tmp_path: Path) -> None:
    d = tmp_path / "wrong-name"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: actual-name\nversion: 1.0.0\ndescription: x\n---\nbody\n"
    )
    with pytest.raises(SkillLoadError, match="does not match manifest"):
        load_skill(d)


def test_load_skill_bad_frontmatter(tmp_path: Path) -> None:
    d = tmp_path / "bad"
    d.mkdir()
    (d / "SKILL.md").write_text("no frontmatter here\n")
    with pytest.raises(SkillLoadError, match="frontmatter parse error"):
        load_skill(d)


def test_load_skill_invalid_manifest(tmp_path: Path) -> None:
    d = tmp_path / "bad-version"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: bad-version\nversion: not-semver\ndescription: x\n---\n"
    )
    with pytest.raises(SkillLoadError, match="manifest validation failed"):
        load_skill(d)


def test_load_skill_collects_assets(tmp_path: Path) -> None:
    d = tmp_path / "with-assets"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: with-assets\nversion: 1.0.0\ndescription: x\n---\nbody\n"
    )
    (d / "examples").mkdir()
    (d / "examples" / "demo.py").write_text("print(1)\n")
    (d / "scripts").mkdir()
    (d / "scripts" / "run.sh").write_text("echo hi\n")
    s = load_skill(d)
    asset_names = sorted(p.relative_to(d).as_posix() for p in s.assets)
    assert asset_names == ["examples/demo.py", "scripts/run.sh"]


def test_discover_skills_finds_all_fixtures() -> None:
    skills = discover_skills(EXAMPLES)
    names = [s.name for s in skills]
    assert names == ["cdp-mcp", "sql-style"]


def test_discover_skills_missing_root_returns_empty(tmp_path: Path) -> None:
    assert discover_skills(tmp_path / "does-not-exist") == []


def test_discover_skills_skips_dirs_without_skill_md(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "SKILL.md").write_text(
        "---\nname: real\nversion: 1.0.0\ndescription: x\n---\n"
    )
    (tmp_path / "not-a-skill").mkdir()
    (tmp_path / "not-a-skill" / "README.md").write_text("hi")
    skills = discover_skills(tmp_path)
    assert [s.name for s in skills] == ["real"]


def test_skill_to_skill_md_round_trips() -> None:
    s = load_skill(EXAMPLES / "cdp-mcp")
    rendered = s.to_skill_md()
    data, body = parse_frontmatter(rendered)
    manifest = SkillManifest.model_validate(data)
    assert manifest.name == s.manifest.name
    assert manifest.version == s.manifest.version
    assert "CDP MCP playbook" in body

"""Tests for sic.skills.installer (subphase 8.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sic.skills import (
    ClaudeTarget,
    CopilotTarget,
    GenericTarget,
    Skill,
    SkillManifest,
    build_target,
    install,
    install_all,
    load_skill,
    parse_frontmatter,
    resolve_skills,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "skills"


def _make_skill_dir(
    root: Path,
    name: str,
    *,
    version: str = "1.0.0",
    body: str = "hello\n",
    assets: dict[str, str] | None = None,
) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\nversion: {version}\ndescription: d\n---\n{body}"
    )
    for rel, content in (assets or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return d


# ---------- ClaudeTarget ----------

def test_claude_target_writes_skill_md_and_assets(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_skill_dir(src, "alpha", body="A body\n", assets={"examples/demo.py": "print(1)\n"})
    skill = load_skill(src / "alpha")
    skill_set = resolve_skills([skill], [])

    home = tmp_path / "home"
    target = ClaudeTarget(home=home)
    report = install(skill_set, target)

    written = {p.relative_to(home).as_posix() for p in report.written}
    assert written == {
        ".claude/skills/alpha/SKILL.md",
        ".claude/skills/alpha/examples/demo.py",
    }
    md = (home / ".claude/skills/alpha/SKILL.md").read_text()
    data, body = parse_frontmatter(md)
    assert data["name"] == "alpha"
    assert "A body" in body


def test_install_is_idempotent_on_second_run(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_skill_dir(src, "alpha")
    skill_set = resolve_skills([load_skill(src / "alpha")], [])

    target = ClaudeTarget(home=tmp_path / "home")
    r1 = install(skill_set, target)
    r2 = install(skill_set, target)

    assert r1.written and not r1.unchanged
    assert not r2.written
    assert len(r2.unchanged) == len(r1.written)
    assert not r2.removed


def test_install_rewrites_only_changed_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_skill_dir(src, "alpha", body="v1\n")
    target = ClaudeTarget(home=tmp_path / "home")
    install(resolve_skills([load_skill(src / "alpha")], []), target)

    # Re-create with new body and re-install.
    _make_skill_dir(tmp_path / "src2", "alpha", body="v2\n")
    r = install(resolve_skills([load_skill(tmp_path / "src2" / "alpha")], []), target)
    assert any("SKILL.md" in p.name for p in r.written)
    md = (tmp_path / "home" / ".claude/skills/alpha/SKILL.md").read_text()
    assert "v2" in md


def test_orphan_skill_files_are_removed(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_skill_dir(src, "alpha")
    _make_skill_dir(src, "beta")
    home = tmp_path / "home"
    target = ClaudeTarget(home=home)
    install(
        resolve_skills([load_skill(src / "alpha"), load_skill(src / "beta")], []),
        target,
    )
    assert (home / ".claude/skills/beta/SKILL.md").exists()

    # Re-install without beta — its directory should be swept.
    r = install(resolve_skills([load_skill(src / "alpha")], []), target)
    removed = {p.relative_to(home).as_posix() for p in r.removed}
    assert removed == {".claude/skills/beta/SKILL.md"}
    assert not (home / ".claude/skills/beta").exists(), "empty parent should be pruned"


def test_orphan_asset_removed_when_dropped_from_skill(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_skill_dir(src, "alpha", assets={"examples/demo.py": "print(1)\n"})
    home = tmp_path / "home"
    target = ClaudeTarget(home=home)
    install(resolve_skills([load_skill(src / "alpha")], []), target)
    assert (home / ".claude/skills/alpha/examples/demo.py").exists()

    # Re-create skill without the asset.
    src2 = tmp_path / "src2"
    _make_skill_dir(src2, "alpha")
    r = install(resolve_skills([load_skill(src2 / "alpha")], []), target)
    removed = {p.relative_to(home).as_posix() for p in r.removed}
    assert ".claude/skills/alpha/examples/demo.py" in removed
    assert not (home / ".claude/skills/alpha/examples").exists()


def test_manifest_file_written_with_relative_paths(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_skill_dir(src, "alpha", assets={"x.txt": "y"})
    target = ClaudeTarget(home=tmp_path / "home")
    install(resolve_skills([load_skill(src / "alpha")], []), target)
    manifest_path = tmp_path / "home" / ".claude" / "skills" / ".sic-manifest.json"
    data = json.loads(manifest_path.read_text())
    assert data == {"alpha": ["alpha/SKILL.md", "alpha/x.txt"]}


# ---------- CopilotTarget ----------

def test_copilot_target_writes_single_file_per_skill(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_skill_dir(src, "alpha", assets={"examples/demo.py": "x"})
    project = tmp_path / "proj"
    project.mkdir()
    target = CopilotTarget()
    r = install(
        resolve_skills([load_skill(src / "alpha")], []),
        target,
        project_root=project,
    )
    written = {p.relative_to(project).as_posix() for p in r.written}
    assert written == {".github/prompts/alpha.skill.md"}


def test_copilot_target_requires_project_root(tmp_path: Path) -> None:
    target = CopilotTarget()
    with pytest.raises(ValueError, match="project_root"):
        install(resolve_skills([], []), target)


# ---------- GenericTarget ----------

def test_generic_target_writes_verbatim_layout(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_skill_dir(src, "alpha", assets={"data/x.json": "{}"})
    project = tmp_path / "proj"
    project.mkdir()
    target = GenericTarget()
    r = install(
        resolve_skills([load_skill(src / "alpha")], []),
        target,
        project_root=project,
    )
    written = {p.relative_to(project).as_posix() for p in r.written}
    assert written == {
        ".ai/skills/alpha/SKILL.md",
        ".ai/skills/alpha/data/x.json",
    }


# ---------- factory + install_all ----------

def test_build_target_known_names(tmp_path: Path) -> None:
    home = tmp_path / "h"
    claude = build_target("claude", home=home)
    assert isinstance(claude, ClaudeTarget)
    assert claude.root() == home / ".claude" / "skills"
    assert isinstance(build_target("copilot"), CopilotTarget)
    assert isinstance(build_target("generic"), GenericTarget)


def test_build_target_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown skill target"):
        build_target("not-real")


def test_install_all_writes_to_every_target(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_skill_dir(src, "alpha")
    home = tmp_path / "home"
    project = tmp_path / "proj"
    project.mkdir()

    skill_set = resolve_skills([load_skill(src / "alpha")], [])
    targets = [ClaudeTarget(home=home), CopilotTarget(), GenericTarget()]
    r = install_all(skill_set, targets, project_root=project)

    assert (home / ".claude/skills/alpha/SKILL.md").exists()
    assert (project / ".github/prompts/alpha.skill.md").exists()
    assert (project / ".ai/skills/alpha/SKILL.md").exists()
    assert len(r.written) == 3


# ---------- preserves unrelated files ----------

def test_install_does_not_touch_unmanaged_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _make_skill_dir(src, "alpha")
    home = tmp_path / "home"
    skills_root = home / ".claude" / "skills"
    skills_root.mkdir(parents=True)
    unrelated = skills_root / "manual-skill" / "SKILL.md"
    unrelated.parent.mkdir()
    unrelated.write_text("hand written\n")

    target = ClaudeTarget(home=home)
    install(resolve_skills([load_skill(src / "alpha")], []), target)
    # Re-install with nothing — should NOT delete the manual skill.
    r = install(resolve_skills([], []), target)
    assert unrelated.exists()
    removed = {p.relative_to(home).as_posix() for p in r.removed}
    assert removed == {".claude/skills/alpha/SKILL.md"}


def test_install_real_fixture(tmp_path: Path) -> None:
    skill_set = resolve_skills(
        [load_skill(EXAMPLES / "cdp-mcp"), load_skill(EXAMPLES / "sql-style")],
        [],
    )
    target = ClaudeTarget(home=tmp_path)
    install(skill_set, target)
    assert (tmp_path / ".claude/skills/cdp-mcp/SKILL.md").exists()
    assert (tmp_path / ".claude/skills/sql-style/SKILL.md").exists()

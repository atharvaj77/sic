"""Tests for `sic skills ...` CLI + sync integration (subphase 8.5/8.7)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sic.cli import app
from sic.workspace import Config, Workspace

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _bare_team_repo_with_skills(tmp_path: Path) -> Path:
    """Build a bare git repo that contains team.yaml + skills/cdp-mcp + skills/sql-style."""
    work = tmp_path / "team-work"
    work.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=work)
    _run(["git", "config", "user.email", "ci@example.com"], cwd=work)
    _run(["git", "config", "user.name", "CI"], cwd=work)
    shutil.copy(EXAMPLES / "team.yaml", work / "team.yaml")
    shutil.copytree(EXAMPLES / "skills", work / "skills")
    _run(["git", "add", "."], cwd=work)
    _run(["git", "commit", "-q", "-m", "initial"], cwd=work)
    bare = tmp_path / "team.git"
    _run(["git", "clone", "-q", "--bare", str(work), str(bare)], cwd=tmp_path)
    return bare


@pytest.fixture
def initialised_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    monkeypatch.setenv("SIC_HOME", str(tmp_path / "sic-home"))
    bare = _bare_team_repo_with_skills(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    r = runner.invoke(app, ["init", "--team-repo", f"file://{bare}", "--owner", "alice"])
    assert r.exit_code == 0, r.output
    return {"tmp": tmp_path, "project": project}


# ---------- skills list / show / diff ----------

def test_skills_list_shows_team_skills(initialised_workspace) -> None:
    r = runner.invoke(app, ["skills", "list"])
    assert r.exit_code == 0, r.output
    assert "cdp-mcp" in r.output
    assert "sql-style" in r.output
    assert "team" in r.output


def test_skills_show_prints_skill_md(initialised_workspace) -> None:
    r = runner.invoke(app, ["skills", "show", "cdp-mcp"])
    assert r.exit_code == 0, r.output
    assert "name: cdp-mcp" in r.output
    assert "CDP MCP playbook" in r.output


def test_skills_show_unknown_name_errors(initialised_workspace) -> None:
    r = runner.invoke(app, ["skills", "show", "nope"])
    assert r.exit_code == 1
    assert "no skill named" in r.output


def test_skills_diff_clean_when_no_personal(initialised_workspace) -> None:
    r = runner.invoke(app, ["skills", "diff"])
    assert r.exit_code == 0, r.output
    assert "no skill differences" in r.output


# ---------- skills new ----------

def test_skills_new_scaffolds_personal_skill(initialised_workspace) -> None:
    r = runner.invoke(app, ["skills", "new", "my-tool"])
    assert r.exit_code == 0, r.output
    ws = Workspace.current()
    md = ws.personal_skills_dir / "my-tool" / "SKILL.md"
    assert md.exists()
    text = md.read_text()
    assert "name: my-tool" in text
    assert "owner: alice" in text


def test_skills_new_rejects_invalid_name(initialised_workspace) -> None:
    r = runner.invoke(app, ["skills", "new", "BadName"])
    assert r.exit_code == 1
    assert "invalid skill name" in r.output


def test_skills_new_requires_force_to_overwrite(initialised_workspace) -> None:
    runner.invoke(app, ["skills", "new", "dup"])
    r = runner.invoke(app, ["skills", "new", "dup"])
    assert r.exit_code == 1
    assert "exists" in r.output and "--force" in r.output


# ---------- skills validate ----------

def test_skills_validate_passes_workspace(initialised_workspace) -> None:
    r = runner.invoke(app, ["skills", "validate"])
    assert r.exit_code == 0, r.output
    assert "cdp-mcp" in r.output
    assert "OK" in r.output


def test_skills_validate_explicit_path(initialised_workspace, tmp_path: Path) -> None:
    r = runner.invoke(app, ["skills", "validate", str(EXAMPLES / "skills")])
    assert r.exit_code == 0, r.output


def test_skills_validate_reports_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIC_HOME", str(tmp_path / "h"))
    bad = tmp_path / "bad-skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter\n")
    r = runner.invoke(app, ["skills", "validate", str(bad)])
    assert r.exit_code == 1
    assert "FAIL" in r.output


# ---------- skills install ----------

def test_skills_install_without_targets_errors(initialised_workspace) -> None:
    r = runner.invoke(app, ["skills", "install"])
    assert r.exit_code == 1
    assert "no skill targets" in r.output


def test_skills_install_with_target_flag_writes_files(initialised_workspace) -> None:
    project = initialised_workspace["project"]
    r = runner.invoke(
        app,
        ["skills", "install", "--target", "generic", "--project-root", str(project)],
    )
    assert r.exit_code == 0, r.output
    assert (project / ".ai/skills/cdp-mcp/SKILL.md").exists()
    assert (project / ".ai/skills/sql-style/SKILL.md").exists()


def test_skills_install_surfaces_conflicts(initialised_workspace) -> None:
    ws = Workspace.current()
    # Personal skill that collides with the team-locked sql-style.
    personal = ws.personal_skills_dir / "sql-style"
    personal.mkdir(parents=True)
    (personal / "SKILL.md").write_text(
        "---\nname: sql-style\nversion: 9.9.9\ndescription: my override\n---\nbody\n"
    )
    project = initialised_workspace["project"]
    r = runner.invoke(
        app,
        ["skills", "install", "--target", "generic", "--project-root", str(project)],
    )
    assert r.exit_code == 3
    assert "conflict" in r.output
    assert "sql-style" in r.output


# ---------- sync integration ----------

def test_sync_installs_skills_when_configured(initialised_workspace) -> None:
    ws = Workspace.current()
    cfg = Config.load(ws.config_file)
    cfg.skill_targets = ["generic"]
    cfg.dump(ws.config_file)

    project = initialised_workspace["project"]
    r = runner.invoke(
        app,
        ["sync", "--no-timestamp", "--project-root", str(project)],
    )
    # team profile alone has no conflicts, and personal stub is empty.
    assert r.exit_code == 0, r.output
    assert "skills" in r.output
    assert (project / ".ai/skills/cdp-mcp/SKILL.md").exists()


def test_sync_skips_skills_when_no_targets_configured(initialised_workspace) -> None:
    project = initialised_workspace["project"]
    r = runner.invoke(
        app,
        ["sync", "--no-timestamp", "--project-root", str(project)],
    )
    assert r.exit_code == 0, r.output
    # No "skills" install line printed when targets are empty.
    assert "skills" not in r.output
    assert not (project / ".ai").exists()


def test_sync_no_skills_flag_skips_install(initialised_workspace) -> None:
    ws = Workspace.current()
    cfg = Config.load(ws.config_file)
    cfg.skill_targets = ["generic"]
    cfg.dump(ws.config_file)

    project = initialised_workspace["project"]
    r = runner.invoke(
        app,
        ["sync", "--no-skills", "--no-timestamp", "--project-root", str(project)],
    )
    assert r.exit_code == 0, r.output
    assert not (project / ".ai").exists()


def test_sync_propagates_skill_conflict_exit_code(initialised_workspace) -> None:
    ws = Workspace.current()
    cfg = Config.load(ws.config_file)
    cfg.skill_targets = ["generic"]
    cfg.dump(ws.config_file)

    personal = ws.personal_skills_dir / "sql-style"
    personal.mkdir(parents=True)
    (personal / "SKILL.md").write_text(
        "---\nname: sql-style\nversion: 9.9.9\ndescription: my override\n---\nbody\n"
    )
    project = initialised_workspace["project"]
    r = runner.invoke(
        app,
        ["sync", "--no-timestamp", "--project-root", str(project)],
    )
    assert r.exit_code == 3
    assert "skill conflict" in r.output


# ---------- config back-compat ----------

def test_old_config_without_skill_fields_still_loads(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"team_repo": "x", "team_profile_path": "team.yaml"}))
    cfg = Config.load(p)
    assert cfg.team_repo == "x"
    assert cfg.skill_targets == []
    assert cfg.team_skills_path == "skills"


def test_unknown_keys_in_config_are_ignored(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"team_repo": "x", "future_field": 1}))
    cfg = Config.load(p)
    assert cfg.team_repo == "x"

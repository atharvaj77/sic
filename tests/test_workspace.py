"""Tests for the workspace module + `sic init` / `sic sync` CLI commands.

These tests build real local git repos (in tmp_path) and clone them via the
workspace helpers — no network access, but the full code path runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sic.cli import app
from sic.workspace import (
    Config,
    GitError,
    Workspace,
    git_clone,
    git_head_sha,
    git_pull,
    sic_home,
    write_personal_stub,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
runner = CliRunner()


# ---------- helpers ----------

def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _make_bare_team_repo(tmp_path: Path) -> Path:
    """Create a bare git repo containing examples/team.yaml on `main`."""
    work = tmp_path / "team-work"
    work.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=work)
    _run(["git", "config", "user.email", "ci@example.com"], cwd=work)
    _run(["git", "config", "user.name", "CI"], cwd=work)
    shutil.copy(EXAMPLES / "team.yaml", work / "team.yaml")
    _run(["git", "add", "team.yaml"], cwd=work)
    _run(["git", "commit", "-q", "-m", "initial"], cwd=work)

    bare = tmp_path / "team.git"
    _run(["git", "clone", "-q", "--bare", str(work), str(bare)], cwd=tmp_path)
    return bare


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "sic-home"
    monkeypatch.setenv("SIC_HOME", str(home))
    return home


# ---------- workspace primitives ----------

def test_sic_home_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SIC_HOME", str(tmp_path / "elsewhere"))
    assert sic_home() == tmp_path / "elsewhere"


def test_sic_home_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIC_HOME", raising=False)
    assert sic_home() == Path.home() / ".sic"


def test_config_round_trip(tmp_path: Path) -> None:
    cfg = Config(team_repo="git@example.com:team.git", team_profile_path="team.yaml")
    p = tmp_path / "config.json"
    cfg.dump(p)
    loaded = Config.load(p)
    assert loaded == cfg


def test_write_personal_stub_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "personal.yaml"
    write_personal_stub(p, owner="alice", today="2026-03-01")
    first = p.read_text()
    write_personal_stub(p, owner="bob", today="2026-03-09")  # should NOT overwrite
    assert p.read_text() == first
    assert "owner: alice" in first


def test_git_clone_and_head_and_pull(tmp_path: Path) -> None:
    bare = _make_bare_team_repo(tmp_path)
    dest = tmp_path / "clone"
    git_clone(f"file://{bare}", dest)
    assert (dest / "team.yaml").exists()
    sha = git_head_sha(dest)
    assert len(sha) >= 7
    git_pull(dest)  # no-op pull works


def test_git_clone_refuses_existing_dest(tmp_path: Path) -> None:
    bare = _make_bare_team_repo(tmp_path)
    dest = tmp_path / "clone"
    git_clone(f"file://{bare}", dest)
    with pytest.raises(GitError, match="already exists"):
        git_clone(f"file://{bare}", dest)


def test_git_pull_on_non_repo(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="not a git repository"):
        git_pull(tmp_path)


# ---------- CLI: init ----------

def test_cli_init_creates_workspace(isolated_home: Path, tmp_path: Path) -> None:
    bare = _make_bare_team_repo(tmp_path)
    r = runner.invoke(
        app,
        ["init", "--team-repo", f"file://{bare}", "--owner", "alice"],
    )
    assert r.exit_code == 0, r.output
    ws = Workspace.current()
    assert ws.config_file.exists()
    assert (ws.team_dir / "team.yaml").exists()
    assert ws.personal_file.exists()
    assert "owner: alice" in ws.personal_file.read_text()


def test_cli_init_refuses_existing_without_force(isolated_home: Path, tmp_path: Path) -> None:
    bare = _make_bare_team_repo(tmp_path)
    args = ["init", "--team-repo", f"file://{bare}", "--owner", "alice"]
    runner.invoke(app, args)
    r = runner.invoke(app, args)
    assert r.exit_code == 1
    assert "already exists" in r.output


def test_cli_init_force_reclones(isolated_home: Path, tmp_path: Path) -> None:
    bare = _make_bare_team_repo(tmp_path)
    args = ["init", "--team-repo", f"file://{bare}", "--owner", "alice"]
    runner.invoke(app, args)
    r = runner.invoke(app, [*args, "--force"])
    assert r.exit_code == 0, r.output


# ---------- CLI: sync ----------

def test_cli_sync_pulls_and_merges(isolated_home: Path, tmp_path: Path) -> None:
    bare = _make_bare_team_repo(tmp_path)
    runner.invoke(app, ["init", "--team-repo", f"file://{bare}", "--owner", "alice"])

    # Drop in alice's real personal profile so the merge has content.
    shutil.copy(
        EXAMPLES / "alice.personal.yaml",
        Workspace.current().personal_file,
    )

    project = tmp_path / "project"
    project.mkdir()
    r = runner.invoke(
        app,
        [
            "sync",
            "--render",
            "project",
            "--project-root",
            str(project),
            "--no-timestamp",
        ],
    )
    assert r.exit_code == 0, r.output
    assert "synced team@" in r.output
    rendered = project / "CLAUDE.md"
    assert rendered.exists()
    assert "Team memory" in rendered.read_text()


def test_cli_sync_without_init(isolated_home: Path) -> None:
    r = runner.invoke(app, ["sync"])
    assert r.exit_code == 1
    assert "no sic workspace" in r.output


def test_cli_sync_exits_3_on_conflict(isolated_home: Path, tmp_path: Path) -> None:
    bare = _make_bare_team_repo(tmp_path)
    runner.invoke(app, ["init", "--team-repo", f"file://{bare}", "--owner", "bob"])
    shutil.copy(
        EXAMPLES / "bob.personal.yaml",
        Workspace.current().personal_file,
    )
    r = runner.invoke(app, ["sync"])
    assert r.exit_code == 3, r.output
    assert "1 conflict" in r.output

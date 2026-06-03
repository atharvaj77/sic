"""End-to-end integration test: exercises the full user journey through the
CLI in one go — `init` → `import-chatgpt` → `sync --render` → second `sync`
to prove idempotency on disk — against a real local git repo.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sic.cli import app
from sic.workspace import Config, Workspace

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
runner = CliRunner()


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _bare_team_repo(tmp_path: Path) -> Path:
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
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    monkeypatch.setenv("SIC_HOME", str(tmp_path / "sic-home"))
    return {
        "tmp": tmp_path,
        "project": tmp_path / "project",
        "bare": _bare_team_repo(tmp_path),
    }


def test_full_user_journey(env: dict[str, Path]) -> None:
    env["project"].mkdir()
    bare_url = f"file://{env['bare']}"

    # 1. init the workspace
    r = runner.invoke(app, ["init", "--team-repo", bare_url, "--owner", "alice"])
    assert r.exit_code == 0, r.output
    ws = Workspace.current()

    # 2. import a ChatGPT export INTO the personal file (overwrites the stub)
    r = runner.invoke(
        app,
        [
            "import-chatgpt",
            str(EXAMPLES / "chatgpt_export.json"),
            "--as",
            "alice",
            "-o",
            str(ws.personal_file),
        ],
    )
    assert r.exit_code == 0, r.output

    # 3. sync + render: should produce a valid CLAUDE.md
    args = [
        "sync",
        "--render",
        "project",
        "--project-root",
        str(env["project"]),
        "--no-timestamp",
    ]
    r = runner.invoke(app, args)
    assert r.exit_code == 0, r.output
    rendered = env["project"] / "CLAUDE.md"
    assert rendered.exists()
    first = rendered.read_bytes()
    body = first.decode()
    # imported entries surface in the merged output
    assert "Polars" in body
    # team-only entries also surface
    assert "Acme Data Platform" in body

    # 4. second sync with no changes upstream → byte-identical CLAUDE.md
    r = runner.invoke(app, args)
    assert r.exit_code == 0, r.output
    second = rendered.read_bytes()
    assert first == second, "render should be idempotent across syncs"

    # 5. validate the imported personal profile after the fact
    r = runner.invoke(app, ["validate", str(ws.personal_file)])
    assert r.exit_code == 0, r.output


def test_full_user_journey_with_skills(env: dict[str, Path]) -> None:
    """init → enable skill targets → sync installs skills + emits banner in CLAUDE.md."""
    env["project"].mkdir()
    bare_url = f"file://{env['bare']}"

    r = runner.invoke(app, ["init", "--team-repo", bare_url, "--owner", "alice"])
    assert r.exit_code == 0, r.output
    ws = Workspace.current()

    # Enable skill installation into a project-local target.
    cfg = Config.load(ws.config_file)
    cfg.skill_targets = ["generic"]
    cfg.dump(ws.config_file)

    # Add a personal skill that does NOT collide with the team-locked sql-style.
    personal_skill = ws.personal_skills_dir / "my-helper"
    personal_skill.mkdir(parents=True)
    (personal_skill / "SKILL.md").write_text(
        "---\nname: my-helper\nversion: 0.1.0\ndescription: personal helper\n---\nbody\n"
    )

    args = [
        "sync",
        "--render", "project",
        "--project-root", str(env["project"]),
        "--no-timestamp",
    ]

    # First sync: install + render.
    r = runner.invoke(app, args)
    assert r.exit_code == 0, r.output

    # Skills written to the generic target.
    assert (env["project"] / ".ai/skills/cdp-mcp/SKILL.md").exists()
    assert (env["project"] / ".ai/skills/sql-style/SKILL.md").exists()
    assert (env["project"] / ".ai/skills/my-helper/SKILL.md").exists()

    # CLAUDE.md gains the Available skills table.
    body = (env["project"] / "CLAUDE.md").read_text()
    assert "## Available skills" in body
    assert "`cdp-mcp`" in body
    assert "`sql-style` 🔒" in body
    assert "`my-helper`" in body
    assert "personal" in body  # scope column

    # Second sync: idempotent on disk.
    first = (env["project"] / "CLAUDE.md").read_bytes()
    r = runner.invoke(app, args)
    assert r.exit_code == 0, r.output
    assert (env["project"] / "CLAUDE.md").read_bytes() == first

    # Now introduce a Tier-3 skill conflict (override a locked team skill).
    bad = ws.personal_skills_dir / "sql-style"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text(
        "---\nname: sql-style\nversion: 9.9.9\ndescription: my override\n---\nbody\n"
    )
    r = runner.invoke(app, args)
    assert r.exit_code == 3
    assert "skill conflict" in r.output
    # The team-locked content still wins on disk.
    assert "Team SQL formatting" in (env["project"] / ".ai/skills/sql-style/SKILL.md").read_text()
    # And surfaces in CLAUDE.md too.
    body = (env["project"] / "CLAUDE.md").read_text()
    assert "Unresolved skill conflicts" in body

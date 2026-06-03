"""Renderer tests: golden files + idempotency + CLI integration."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from sic.cli import app
from sic.loader import load_profile
from sic.merger import merge_profiles
from sic.renderer import RenderContext, render_claude_md, resolve_target_path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
GOLDEN = ROOT / "tests" / "golden"
runner = CliRunner()


def _render_for(personal_name: str) -> str:
    team = load_profile(EXAMPLES / "team.yaml")
    personal = load_profile(EXAMPLES / personal_name)
    result = merge_profiles(team, personal)
    ctx = RenderContext(
        team_owner=team.profile.owner,
        personal_owner=personal.profile.owner,
        generated_at=None,
    )
    return render_claude_md(result, ctx)


def test_render_matches_alice_golden() -> None:
    expected = (GOLDEN / "alice.CLAUDE.md").read_text()
    assert _render_for("alice.personal.yaml") == expected


def test_render_matches_bob_golden() -> None:
    expected = (GOLDEN / "bob.CLAUDE.md").read_text()
    assert _render_for("bob.personal.yaml") == expected


def test_render_is_idempotent() -> None:
    first = _render_for("alice.personal.yaml")
    second = _render_for("alice.personal.yaml")
    assert first == second


def test_render_includes_conflict_section_for_bob() -> None:
    text = _render_for("bob.personal.yaml")
    assert "Unresolved conflicts" in text
    assert "tools.dataframe_library" in text


def test_render_no_conflict_section_for_alice() -> None:
    text = _render_for("alice.personal.yaml")
    assert "Unresolved conflicts" not in text


def test_resolve_target_path_project(tmp_path: Path) -> None:
    p = resolve_target_path("project", tmp_path)
    assert p == tmp_path / "CLAUDE.md"


def test_resolve_target_path_user() -> None:
    p = resolve_target_path("user", Path("/ignored"))
    assert p.name == "CLAUDE.md"
    assert p.parent.name == ".claude"


def test_resolve_target_path_invalid() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown render target"):
        resolve_target_path("nowhere", Path("/"))


# ---------- CLI ----------

def test_cli_render_writes_project_claude_md(tmp_path: Path) -> None:
    r = runner.invoke(
        app,
        [
            "render",
            str(EXAMPLES / "team.yaml"),
            str(EXAMPLES / "alice.personal.yaml"),
            "--project-root",
            str(tmp_path),
            "--no-timestamp",
        ],
    )
    assert r.exit_code == 0, r.output
    out = tmp_path / "CLAUDE.md"
    assert out.exists()
    assert "Team memory" in out.read_text()


def test_cli_render_explicit_output(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "X.md"
    r = runner.invoke(
        app,
        [
            "render",
            str(EXAMPLES / "team.yaml"),
            str(EXAMPLES / "alice.personal.yaml"),
            "-o",
            str(out),
            "--no-timestamp",
        ],
    )
    assert r.exit_code == 0, r.output
    assert out.exists()


def test_cli_render_exit_3_on_conflict(tmp_path: Path) -> None:
    r = runner.invoke(
        app,
        [
            "render",
            str(EXAMPLES / "team.yaml"),
            str(EXAMPLES / "bob.personal.yaml"),
            "--project-root",
            str(tmp_path),
            "--no-timestamp",
        ],
    )
    assert r.exit_code == 3, r.output
    assert "unresolved conflict" in r.output


def test_cli_render_idempotent_on_disk(tmp_path: Path) -> None:
    args = [
        "render",
        str(EXAMPLES / "team.yaml"),
        str(EXAMPLES / "alice.personal.yaml"),
        "--project-root",
        str(tmp_path),
        "--no-timestamp",
    ]
    runner.invoke(app, args)
    first = (tmp_path / "CLAUDE.md").read_bytes()
    runner.invoke(app, args)
    second = (tmp_path / "CLAUDE.md").read_bytes()
    assert first == second

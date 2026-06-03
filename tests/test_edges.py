"""Targeted tests for small edges flagged by coverage:
renderer value formatting, loader error paths, CLI error exits."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sic.cli import app
from sic.loader import ProfileLoadError, dump_profile, load_profile
from sic.renderer import _format_value  # type: ignore[attr-defined]

runner = CliRunner()


# ---------- renderer._format_value branches ----------

def test_format_value_bool_true() -> None:
    assert _format_value(True) == "yes"


def test_format_value_bool_false() -> None:
    assert _format_value(False) == "no"


def test_format_value_int() -> None:
    assert _format_value(42) == "42"


def test_format_value_unknown_type_falls_back_to_repr() -> None:
    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    assert _format_value(Weird()) == "<weird>"


# ---------- loader error paths ----------

def test_loader_raises_on_invalid_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(":\n  - [unclosed\n")
    with pytest.raises(ProfileLoadError):
        load_profile(bad)


def test_loader_raises_on_schema_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "profile:\n  scope: team\n  owner: x\n  updated_at: '2026-01-01'\n"
        "entries:\n"
        "  - section: not_a_real_section\n    key: k\n    value: v\n"
    )
    with pytest.raises(ProfileLoadError, match="schema validation failed"):
        load_profile(bad)


def test_dump_then_load_round_trips(tmp_path: Path) -> None:
    src = Path(__file__).resolve().parents[1] / "examples" / "team.yaml"
    profile = load_profile(src)
    out = tmp_path / "round.yaml"
    dump_profile(profile, out)
    again = load_profile(out)
    assert again.entries == profile.entries
    assert again.profile == profile.profile


# ---------- CLI error paths ----------

def test_sync_without_init_errors() -> None:
    r = runner.invoke(app, ["sync"])
    assert r.exit_code == 1
    assert "run `sic init`" in r.output


def test_init_existing_dir_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIC_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "team").mkdir(parents=True)
    r = runner.invoke(app, ["init", "--team-repo", "file:///nope", "--owner", "x"])
    assert r.exit_code == 1
    assert "already exists" in r.output


def test_init_bad_repo_url_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIC_HOME", str(tmp_path / "home"))
    r = runner.invoke(app, ["init", "--team-repo", "file:///definitely/not/a/repo", "--owner", "x"])
    assert r.exit_code == 1

"""Tests for the loader and the `sic` CLI (Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sic.cli import app
from sic.loader import ProfileLoadError, dump_profile_str, load_profile

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
runner = CliRunner()


# ---------- loader ----------

def test_load_profile_returns_typed_object() -> None:
    p = load_profile(EXAMPLES / "team.yaml")
    assert p.profile.scope.value == "team"
    assert len(p.entries) > 0


def test_load_profile_missing_file() -> None:
    with pytest.raises(ProfileLoadError, match="No such file"):
        load_profile(EXAMPLES / "does_not_exist.yaml")


def test_load_profile_invalid_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: 1\nprofile: {scope: nope}\n")
    with pytest.raises(ProfileLoadError, match="schema validation failed"):
        load_profile(bad)


def test_load_profile_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with pytest.raises(ProfileLoadError, match="empty"):
        load_profile(empty)


def test_dump_profile_str_round_trip() -> None:
    original = load_profile(EXAMPLES / "team.yaml")
    text = dump_profile_str(original)
    assert "schema_version" in text
    # parse it back
    tmp = Path("/tmp/_sic_roundtrip.yaml")
    tmp.write_text(text)
    reloaded = load_profile(tmp)
    assert len(reloaded.entries) == len(original.entries)


# ---------- CLI ----------

def test_cli_version() -> None:
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert "sic" in r.stdout


def test_cli_help_lists_all_commands() -> None:
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in ("validate", "init", "merge", "render", "sync", "diff", "conflicts", "import-chatgpt"):
        assert cmd in r.stdout


def test_cli_validate_passes_on_all_fixtures() -> None:
    r = runner.invoke(
        app,
        [
            "validate",
            str(EXAMPLES / "team.yaml"),
            str(EXAMPLES / "alice.personal.yaml"),
            str(EXAMPLES / "bob.personal.yaml"),
        ],
    )
    assert r.exit_code == 0, r.output
    assert r.output.count("OK") == 3


def test_cli_validate_fails_on_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "broken.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "profile: {scope: team, owner: x, updated_at: 2026-01-01}\n"
        "entries:\n"
        "  - {section: identity, key: BAD_KEY, value: x}\n"
    )
    r = runner.invoke(app, ["validate", str(bad)])
    assert r.exit_code == 1
    assert "FAIL" in r.output


def test_cli_stubs_exit_with_code_2() -> None:
    # No stubs remain — every command is implemented.
    r = runner.invoke(app, ["import-chatgpt", "--help"])
    assert r.exit_code == 0

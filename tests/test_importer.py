"""Tests for the ChatGPT memory importer."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sic.cli import app
from sic.importer import (
    Classification,
    ImportError_,
    _disambiguate_keys,
    _slugify,
    build_draft,
    classify_rule,
    import_chatgpt,
    parse_export,
)
from sic.schema import MergeStrategy, Scope, Section

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
runner = CliRunner()


# ---------- parse_export: accepts three shapes ----------

def test_parse_export_modern_shape(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"memories": [{"content": "a"}, {"content": "b"}]}))
    assert parse_export(p) == ["a", "b"]


def test_parse_export_older_shape(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"saved_memories": ["x", "y"]}))
    assert parse_export(p) == ["x", "y"]


def test_parse_export_raw_list(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text(json.dumps(["one", "two"]))
    assert parse_export(p) == ["one", "two"]


def test_parse_export_strips_empty_and_whitespace(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text(json.dumps(["  hello  ", "", "  ", "world"]))
    assert parse_export(p) == ["hello", "world"]


def test_parse_export_rejects_unknown_shape(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"foo": "bar"}))
    with pytest.raises(ImportError_, match="unrecognised export shape"):
        parse_export(p)


def test_parse_export_rejects_bad_json(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text("not json {")
    with pytest.raises(ImportError_, match="not valid JSON"):
        parse_export(p)


# ---------- rule classifier ----------

@pytest.mark.parametrize(
    "bullet, expected",
    [
        ("Prefers Polars over Pandas for pipelines", Section.TOOLS),
        ("Always use type hints on public functions", Section.CODING_STYLE),
        ("Never commit secrets — use Vault", Section.DO_NOT),
        ("I work on the growth team", Section.IDENTITY),
        ("ARR stands for Annual Recurring Revenue", Section.DOMAIN_KNOWLEDGE),
        ("Likes hiking on weekends", Section.FREE_FORM),
        ("Prefers python over javascript", Section.LANGUAGES),
    ],
)
def test_classify_rule_routes_known_phrases(bullet: str, expected: Section) -> None:
    assert classify_rule(bullet).section is expected


def test_classify_rule_key_is_slugified() -> None:
    c = classify_rule("Always use type hints on public functions")
    assert c.key.startswith("always_use_type_hints")
    assert " " not in c.key


def test_slugify_handles_punctuation_and_truncates() -> None:
    long = "x" * 200
    assert _slugify(long) == "x" * 40
    assert _slugify("Hello, World!  ") == "hello_world"
    assert _slugify("!!!") == "note"


# ---------- key disambiguation ----------

def test_disambiguate_appends_suffixes() -> None:
    cs = [
        Classification(Section.FREE_FORM, "note", "a"),
        Classification(Section.FREE_FORM, "note", "b"),
        Classification(Section.FREE_FORM, "note", "c"),
    ]
    out = _disambiguate_keys(cs)
    assert [c.key for c in out] == ["note", "note_2", "note_3"]


# ---------- build_draft produces a valid Profile ----------

def test_build_draft_yields_valid_personal_profile() -> None:
    cs = [classify_rule("Prefers Polars"), classify_rule("Likes hiking")]
    draft = build_draft(cs, owner="alice", today=date(2026, 6, 3))
    assert draft.profile.scope is Scope.PERSONAL
    assert draft.profile.owner == "alice"
    assert len(draft.entries) == 2
    for e in draft.entries:
        assert e.merge is MergeStrategy.REPLACE
        assert e.priority == 30
        assert e.rationale and "imported from ChatGPT" in e.rationale


# ---------- end-to-end import on the fixture ----------

def test_import_chatgpt_fixture_round_trips_through_schema() -> None:
    draft = import_chatgpt(EXAMPLES / "chatgpt_export.json", owner="alice")
    assert draft.profile.scope is Scope.PERSONAL
    assert len(draft.entries) == 8
    # The Polars and "never commit secrets" bullets must land in sensible sections.
    by_value = {e.value: e for e in draft.entries}
    polars_entry = next(v for v in by_value.values() if "Polars" in v.value)
    assert polars_entry.section is Section.TOOLS
    secrets_entry = next(v for v in by_value.values() if "Never commit" in v.value)
    assert secrets_entry.section is Section.DO_NOT


def test_import_chatgpt_empty_bullets_fails(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"memories": []}))
    with pytest.raises(ImportError_, match="no memory bullets"):
        import_chatgpt(p, owner="alice")


# ---------- CLI ----------

def test_cli_import_chatgpt_stdout() -> None:
    r = runner.invoke(
        app,
        ["import-chatgpt", str(EXAMPLES / "chatgpt_export.json"), "--as", "alice"],
    )
    assert r.exit_code == 0, r.output
    assert "schema_version" in r.output
    assert "owner: alice" in r.output


def test_cli_import_chatgpt_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "alice.personal.yaml"
    r = runner.invoke(
        app,
        [
            "import-chatgpt",
            str(EXAMPLES / "chatgpt_export.json"),
            "--as",
            "alice",
            "-o",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    assert out.exists()
    assert "scope: personal" in out.read_text()


def test_cli_import_chatgpt_dry_run_does_not_write(tmp_path: Path) -> None:
    out = tmp_path / "alice.personal.yaml"
    r = runner.invoke(
        app,
        [
            "import-chatgpt",
            str(EXAMPLES / "chatgpt_export.json"),
            "--as",
            "alice",
            "-o",
            str(out),
            "--dry-run",
        ],
    )
    assert r.exit_code == 0, r.output
    assert not out.exists()
    assert "dry-run" in r.output


def test_cli_import_chatgpt_bad_file_exits_1(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    r = runner.invoke(app, ["import-chatgpt", str(bad), "--as", "alice"])
    assert r.exit_code == 1, r.output
    assert "not valid JSON" in r.output


# ---------- output of importer flows through validate ----------

def test_imported_draft_passes_sic_validate(tmp_path: Path) -> None:
    out = tmp_path / "draft.yaml"
    runner.invoke(
        app,
        [
            "import-chatgpt",
            str(EXAMPLES / "chatgpt_export.json"),
            "--as",
            "alice",
            "-o",
            str(out),
        ],
    )
    r = runner.invoke(app, ["validate", str(out)])
    assert r.exit_code == 0, r.output
    assert "OK" in r.output

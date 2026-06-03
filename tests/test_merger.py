"""Tests for the three-tier merge engine and the merge/conflicts/diff CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sic.cli import app
from sic.loader import load_profile
from sic.merger import ConflictReason, merge_profiles
from sic.schema import Entry, MergeStrategy, Profile, ProfileMeta, Scope, Section

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
runner = CliRunner()


def _profile(scope: Scope, owner: str, entries: list[Entry]) -> Profile:
    return Profile(
        schema_version=1,
        profile=ProfileMeta(scope=scope, owner=owner, updated_at="2026-03-01"),  # type: ignore[arg-type]
        entries=entries,
    )


# ---------- Tier 1: personal replaces team (no lock) ----------

def test_tier1_personal_overrides_team_replace() -> None:
    team = _profile(Scope.TEAM, "t", [
        Entry(section=Section.CODING_STYLE, key="indent", value="tabs"),
    ])
    personal = _profile(Scope.PERSONAL, "alice", [
        Entry(section=Section.CODING_STYLE, key="indent", value="spaces"),
    ])
    result = merge_profiles(team, personal)
    assert result.conflicts == []
    assert result.as_dict()[("coding_style", "indent")].value == "spaces"


def test_tier1_skipped_when_values_equal() -> None:
    team = _profile(Scope.TEAM, "t", [
        Entry(section=Section.CODING_STYLE, key="indent", value="tabs"),
    ])
    personal = _profile(Scope.PERSONAL, "alice", [
        Entry(section=Section.CODING_STYLE, key="indent", value="tabs"),
    ])
    result = merge_profiles(team, personal)
    assert result.conflicts == []
    assert result.as_dict()[("coding_style", "indent")].value == "tabs"


# ---------- Tier 2: additive defaults ----------

def test_tier2_append_dedupes_and_preserves_order() -> None:
    team = _profile(Scope.TEAM, "t", [
        Entry(section=Section.LANGUAGES, key="preferred", value=["python", "sql"], merge=MergeStrategy.APPEND),
    ])
    personal = _profile(Scope.PERSONAL, "alice", [
        Entry(section=Section.LANGUAGES, key="preferred", value=["rust", "python"], merge=MergeStrategy.APPEND),
    ])
    result = merge_profiles(team, personal)
    assert result.conflicts == []
    assert result.as_dict()[("languages", "preferred")].value == ["python", "sql", "rust"]


def test_tier2_map_merge_personal_keys_win() -> None:
    team = _profile(Scope.TEAM, "t", [
        Entry(section=Section.DOMAIN_KNOWLEDGE, key="glossary",
              value={"ARR": "team-def", "DAU": "team-def"}, merge=MergeStrategy.MAP_MERGE),
    ])
    personal = _profile(Scope.PERSONAL, "alice", [
        Entry(section=Section.DOMAIN_KNOWLEDGE, key="glossary",
              value={"ARR": "personal-def", "LTV": "lifetime value"}, merge=MergeStrategy.MAP_MERGE),
    ])
    result = merge_profiles(team, personal)
    assert result.conflicts == []
    merged = result.as_dict()[("domain_knowledge", "glossary")].value
    assert merged == {"ARR": "personal-def", "DAU": "team-def", "LTV": "lifetime value"}


def test_tier2_only_in_team_passes_through() -> None:
    team = _profile(Scope.TEAM, "t", [
        Entry(section=Section.IDENTITY, key="team_name", value="Acme"),
    ])
    personal = _profile(Scope.PERSONAL, "alice", [])
    result = merge_profiles(team, personal)
    assert result.as_dict()[("identity", "team_name")].value == "Acme"


def test_tier2_only_in_personal_passes_through() -> None:
    team = _profile(Scope.TEAM, "t", [])
    personal = _profile(Scope.PERSONAL, "alice", [
        Entry(section=Section.IDENTITY, key="editor", value="neovim"),
    ])
    result = merge_profiles(team, personal)
    assert result.as_dict()[("identity", "editor")].value == "neovim"


# ---------- Tier 3: surfaced as Conflict, not auto-merged ----------

def test_tier3_locked_team_value_blocks_personal_override() -> None:
    team = _profile(Scope.TEAM, "t", [
        Entry(section=Section.TOOLS, key="dataframe_library", value="polars", lock=True),
    ])
    personal = _profile(Scope.PERSONAL, "bob", [
        Entry(section=Section.TOOLS, key="dataframe_library", value="pandas"),
    ])
    result = merge_profiles(team, personal)
    assert len(result.conflicts) == 1
    c = result.conflicts[0]
    assert c.identifier == "tools.dataframe_library"
    assert c.reason is ConflictReason.LOCKED_BY_TEAM
    # team value stands in `resolved` until the conflict is handled
    assert result.as_dict()[("tools", "dataframe_library")].value == "polars"
    assert c.options() == {
        "accept_team": "polars",
        "accept_personal": "pandas",
        "open_llm_merge": None,
    }


def test_tier3_strategy_mismatch_is_a_conflict() -> None:
    team = _profile(Scope.TEAM, "t", [
        Entry(section=Section.LANGUAGES, key="preferred", value=["python"], merge=MergeStrategy.APPEND),
    ])
    personal = _profile(Scope.PERSONAL, "bob", [
        Entry(section=Section.LANGUAGES, key="preferred", value=["rust"], merge=MergeStrategy.REPLACE),
    ])
    result = merge_profiles(team, personal)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].reason is ConflictReason.STRATEGY_MISMATCH


# ---------- Determinism + arg-order safety ----------

def test_resolved_entries_are_sorted() -> None:
    team = _profile(Scope.TEAM, "t", [
        Entry(section=Section.WORKFLOW, key="z", value="z"),
        Entry(section=Section.IDENTITY, key="a", value="a"),
    ])
    personal = _profile(Scope.PERSONAL, "alice", [])
    keys = [(e.section.value, e.key) for e in merge_profiles(team, personal).resolved]
    assert keys == sorted(keys)


def test_arg_order_enforced() -> None:
    team = _profile(Scope.TEAM, "t", [])
    personal = _profile(Scope.PERSONAL, "alice", [])
    with pytest.raises(ValueError, match="team-scoped"):
        merge_profiles(personal, team)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="personal-scoped"):
        merge_profiles(team, team)


# ---------- Real fixtures ----------

def test_fixtures_alice_merges_cleanly() -> None:
    result = merge_profiles(
        load_profile(EXAMPLES / "team.yaml"),
        load_profile(EXAMPLES / "alice.personal.yaml"),
    )
    assert result.conflicts == []
    # alice's additive language got appended
    langs = result.as_dict()[("languages", "preferred")].value
    assert langs == ["python", "sql", "rust"]
    # alice's LTV glossary entry merged in
    assert "LTV" in result.as_dict()[("domain_knowledge", "glossary")].value


def test_fixtures_bob_surfaces_expected_conflicts() -> None:
    result = merge_profiles(
        load_profile(EXAMPLES / "team.yaml"),
        load_profile(EXAMPLES / "bob.personal.yaml"),
    )
    idents = {(c.section, c.key, c.reason) for c in result.conflicts}
    # locked dataframe_library is a Tier-3 conflict
    assert ("tools", "dataframe_library", ConflictReason.LOCKED_BY_TEAM) in idents
    # sql_formatting is NOT locked → Tier-1 resolves it, no conflict
    assert not any(c.key == "sql_formatting" for c in result.conflicts)
    sql = result.as_dict()[("coding_style", "sql_formatting")].value
    assert "lowercase" in sql


# ---------- CLI integration ----------

def test_cli_merge_alice_succeeds() -> None:
    r = runner.invoke(
        app,
        ["merge", str(EXAMPLES / "team.yaml"), str(EXAMPLES / "alice.personal.yaml")],
    )
    assert r.exit_code == 0, r.output
    assert "schema_version" in r.output
    assert "rust" in r.output


def test_cli_merge_bob_exits_3_on_conflict() -> None:
    r = runner.invoke(
        app,
        ["merge", str(EXAMPLES / "team.yaml"), str(EXAMPLES / "bob.personal.yaml")],
    )
    assert r.exit_code == 3, r.output
    assert "unresolved conflict" in r.output


def test_cli_merge_writes_output_file(tmp_path: Path) -> None:
    out = tmp_path / "resolved.yaml"
    r = runner.invoke(
        app,
        [
            "merge",
            str(EXAMPLES / "team.yaml"),
            str(EXAMPLES / "alice.personal.yaml"),
            "-o",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    assert out.exists() and "schema_version" in out.read_text()


def test_cli_conflicts_bob() -> None:
    r = runner.invoke(
        app,
        ["conflicts", str(EXAMPLES / "team.yaml"), str(EXAMPLES / "bob.personal.yaml")],
    )
    assert r.exit_code == 3, r.output
    assert "tools.dataframe_library" in r.output
    assert "accept-team" in r.output


def test_cli_conflicts_alice_is_clean() -> None:
    r = runner.invoke(
        app,
        ["conflicts", str(EXAMPLES / "team.yaml"), str(EXAMPLES / "alice.personal.yaml")],
    )
    assert r.exit_code == 0, r.output
    assert "no conflicts" in r.output


def test_cli_diff_shows_changes() -> None:
    r = runner.invoke(
        app,
        ["diff", str(EXAMPLES / "team.yaml"), str(EXAMPLES / "bob.personal.yaml")],
    )
    assert r.exit_code == 0, r.output
    assert "tools.dataframe_library" in r.output

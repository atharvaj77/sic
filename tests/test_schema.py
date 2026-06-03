"""Tests for the Phase 1 profile schema and example fixtures."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAML

from sic.schema import (
    SCHEMA_VERSION,
    Entry,
    MergeStrategy,
    Profile,
    Scope,
    Section,
    export_json_schema,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
_yaml = YAML(typ="safe")


def _load(path: Path) -> Profile:
    with path.open() as f:
        return Profile.model_validate(_yaml.load(f))


# ---------- fixtures load cleanly ----------

@pytest.mark.parametrize("name", ["team.yaml", "alice.personal.yaml", "bob.personal.yaml"])
def test_fixture_loads(name: str) -> None:
    profile = _load(EXAMPLES / name)
    assert profile.schema_version == SCHEMA_VERSION
    assert profile.entries, f"{name} should declare at least one entry"


def test_team_fixture_shape() -> None:
    p = _load(EXAMPLES / "team.yaml")
    assert p.profile.scope is Scope.TEAM
    assert p.profile.owner == "data-platform-team"
    # locked entries exist and are only on the team profile
    locked = [e for e in p.entries if e.lock]
    assert any(e.section is Section.TOOLS and e.key == "dataframe_library" for e in locked)


def test_personal_fixtures_have_personal_scope() -> None:
    for name in ("alice.personal.yaml", "bob.personal.yaml"):
        p = _load(EXAMPLES / name)
        assert p.profile.scope is Scope.PERSONAL


# ---------- conflict between bob and team is detectable at the (section,key) level ----------

def test_bob_conflicts_with_team_on_known_keys() -> None:
    team = _load(EXAMPLES / "team.yaml")
    bob = _load(EXAMPLES / "bob.personal.yaml")
    team_idx = {(e.section, e.key): e for e in team.entries}

    conflicts = [
        (e.section.value, e.key)
        for e in bob.entries
        if (e.section, e.key) in team_idx and team_idx[(e.section, e.key)].value != e.value
    ]
    assert ("coding_style", "sql_formatting") in conflicts
    assert ("tools", "dataframe_library") in conflicts


# ---------- validation rules ----------

def test_duplicate_section_key_rejected() -> None:
    doc = {
        "schema_version": 1,
        "profile": {"scope": "team", "owner": "x", "updated_at": "2026-01-01"},
        "entries": [
            {"section": "identity", "key": "team_name", "value": "A"},
            {"section": "identity", "key": "team_name", "value": "B"},
        ],
    }
    with pytest.raises(ValidationError, match="duplicate entry identity.team_name"):
        Profile.model_validate(doc)


def test_lock_forbidden_on_personal_profile() -> None:
    doc = {
        "schema_version": 1,
        "profile": {"scope": "personal", "owner": "alice", "updated_at": "2026-01-01"},
        "entries": [
            {"section": "tools", "key": "editor", "value": "vim", "lock": True},
        ],
    }
    with pytest.raises(ValidationError, match="personal profiles cannot set lock=true"):
        Profile.model_validate(doc)


def test_append_requires_list_value() -> None:
    with pytest.raises(ValidationError, match="merge=append requires a list value"):
        Entry(section=Section.LANGUAGES, key="x", value="not-a-list", merge=MergeStrategy.APPEND)


def test_map_merge_requires_dict_value() -> None:
    with pytest.raises(ValidationError, match="merge=map_merge requires a dict value"):
        Entry(section=Section.DOMAIN_KNOWLEDGE, key="x", value=[1, 2], merge=MergeStrategy.MAP_MERGE)


def test_key_pattern_enforced() -> None:
    with pytest.raises(ValidationError):
        Entry(section=Section.IDENTITY, key="HasUppercase", value="x")


def test_priority_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        Entry(section=Section.IDENTITY, key="k", value="v", priority=101)


def test_schema_version_pinned() -> None:
    doc = {
        "schema_version": 2,
        "profile": {"scope": "team", "owner": "x", "updated_at": "2026-01-01"},
        "entries": [],
    }
    with pytest.raises(ValidationError):
        Profile.model_validate(doc)


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        Entry(
            section=Section.IDENTITY,
            key="k",
            value="v",
            unknown_field="boom",  # type: ignore[call-arg]
        )


# ---------- JSON Schema export works ----------

def test_export_json_schema_round_trip() -> None:
    js = export_json_schema()
    assert js["type"] == "object"
    assert "properties" in js
    assert "schema_version" in js["properties"]


# ---------- round-trip: model -> dict -> model is stable ----------

def test_model_round_trip() -> None:
    original = _load(EXAMPLES / "team.yaml")
    dumped = original.model_dump(mode="json")
    reloaded = Profile.model_validate(dumped)
    assert reloaded.profile.updated_at == original.profile.updated_at == date(2026, 3, 1)
    assert len(reloaded.entries) == len(original.entries)

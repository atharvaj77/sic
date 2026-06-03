"""Pydantic schema for sharing-is-caring memory profiles.

A profile is a YAML document with top-level metadata plus a list of `entries`.
Each entry belongs to a `section` (identity, languages, coding_style, ...),
has a unique `key` within that section, a `value`, and a `merge` strategy that
tells the merger how to combine it with same-key entries from other layers.

The same model is used for team profiles and personal profiles — they differ
only in the `profile.scope` field.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1


class Scope(str, Enum):
    TEAM = "team"
    PERSONAL = "personal"


class Section(str, Enum):
    IDENTITY = "identity"
    LANGUAGES = "languages"
    TOOLS = "tools"
    CODING_STYLE = "coding_style"
    WORKFLOW = "workflow"
    DOMAIN_KNOWLEDGE = "domain_knowledge"
    DO_NOT = "do_not"
    FREE_FORM = "free_form"


class MergeStrategy(str, Enum):
    """How an entry combines with same-(section,key) entries from other layers.

    - REPLACE: the higher-precedence layer's value wins outright (scalars, maps
      treated as a whole, or lists you want overridden entirely).
    - APPEND: list values are concatenated, de-duplicated, order preserved.
      Only valid for list-typed values.
    - MAP_MERGE: dict values are shallow-merged; higher-precedence keys win
      on conflict. Only valid for dict-typed values.
    """

    REPLACE = "replace"
    APPEND = "append"
    MAP_MERGE = "map_merge"


class ProfileMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Scope
    owner: str = Field(min_length=1, description="Team name or person identifier")
    updated_at: date


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: Section
    key: Annotated[str, Field(min_length=1, pattern=r"^[a-z0-9_]+$")]
    value: Any
    merge: MergeStrategy = MergeStrategy.REPLACE
    priority: Annotated[int, Field(ge=0, le=100)] = 50
    lock: bool = False
    rationale: str | None = None
    owner: str | None = None

    @model_validator(mode="after")
    def _check_merge_value_type(self) -> "Entry":
        if self.merge is MergeStrategy.APPEND and not isinstance(self.value, list):
            raise ValueError(
                f"entry {self.section.value}.{self.key}: merge=append "
                f"requires a list value, got {type(self.value).__name__}"
            )
        if self.merge is MergeStrategy.MAP_MERGE and not isinstance(self.value, dict):
            raise ValueError(
                f"entry {self.section.value}.{self.key}: merge=map_merge "
                f"requires a dict value, got {type(self.value).__name__}"
            )
        return self


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    profile: ProfileMeta
    entries: list[Entry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_keys_per_section(self) -> "Profile":
        seen: set[tuple[str, str]] = set()
        for e in self.entries:
            ident = (e.section.value, e.key)
            if ident in seen:
                raise ValueError(
                    f"duplicate entry {ident[0]}.{ident[1]} in profile "
                    f"(each (section, key) must appear at most once per profile)"
                )
            seen.add(ident)
        return self

    @model_validator(mode="after")
    def _lock_only_on_team(self) -> "Profile":
        if self.profile.scope is Scope.PERSONAL:
            locked = [e for e in self.entries if e.lock]
            if locked:
                bad = ", ".join(f"{e.section.value}.{e.key}" for e in locked)
                raise ValueError(
                    f"personal profiles cannot set lock=true (offending entries: {bad})"
                )
        return self


def export_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for a Profile (useful for editor validation)."""
    return Profile.model_json_schema()

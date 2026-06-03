"""Three-tier merge engine for sharing-is-caring.

Inputs: a team Profile and zero or more personal Profiles.
Output: a `MergeResult` carrying the resolved entries plus any unresolved
`Conflict`s that the caller (CLI / LLM helper) must resolve interactively.

Resolution tiers
----------------
Tier 1 — Personal override (deterministic)
    For `merge=replace` entries where the team has NOT set `lock=true`,
    a personal entry's value replaces the team's. No conflict raised.

Tier 2 — Team-wins / additive defaults (deterministic)
    - Keys only in team or only in personal pass through unchanged.
    - `merge=append` lists are concatenated, de-duplicated, order preserved.
    - `merge=map_merge` dicts are shallow-merged; personal keys win on
      per-key conflicts.

Tier 3 — Requires resolution (returned as Conflict, not auto-merged)
    - Team entry has `lock=true` and personal value differs.
    - Same `(section, key)` declared with different `merge` strategies.
    - Future: same-priority team-vs-team contradictions from multiple
      team-profile imports.

The merger NEVER calls an LLM directly; that's the job of `sic.llm` (Phase
3b, opt-in via `--allow-llm`). Tier-3 conflicts are surfaced and the
caller decides how to resolve them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .schema import Entry, MergeStrategy, Profile, Scope


class ConflictReason(str, Enum):
    LOCKED_BY_TEAM = "locked_by_team"
    STRATEGY_MISMATCH = "strategy_mismatch"


@dataclass(frozen=True)
class Conflict:
    section: str
    key: str
    reason: ConflictReason
    team_entry: Entry
    personal_entry: Entry

    @property
    def identifier(self) -> str:
        return f"{self.section}.{self.key}"

    def options(self) -> dict[str, Any]:
        """Three choices a human / LLM can pick from to resolve this conflict."""
        return {
            "accept_team": self.team_entry.value,
            "accept_personal": self.personal_entry.value,
            "open_llm_merge": None,  # filled in by sic.llm
        }


@dataclass
class MergeResult:
    resolved: list[Entry] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    def as_dict(self) -> dict[tuple[str, str], Entry]:
        return {(e.section.value, e.key): e for e in self.resolved}


def _dedupe_preserve_order(items: list[Any]) -> list[Any]:
    seen: list[Any] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _merged_entry(team: Entry, personal: Entry, value: Any) -> Entry:
    """Build a resolved entry; keep team's rationale unless personal supplied one."""
    return team.model_copy(
        update={
            "value": value,
            "rationale": personal.rationale or team.rationale,
            "owner": personal.owner or team.owner,
        }
    )


def merge_profiles(team: Profile, personal: Profile) -> MergeResult:
    """Merge one team profile with one personal profile.

    Both profiles must already be schema-validated. The team profile's scope
    is asserted (mostly to catch arg-order mistakes at call sites).
    """
    if team.profile.scope is not Scope.TEAM:
        raise ValueError("first argument must be a team-scoped profile")
    if personal.profile.scope is not Scope.PERSONAL:
        raise ValueError("second argument must be a personal-scoped profile")

    result = MergeResult()
    team_idx = {(e.section.value, e.key): e for e in team.entries}
    personal_idx = {(e.section.value, e.key): e for e in personal.entries}

    # Pass 1: every team entry, possibly overridden by personal.
    for ident, t_entry in team_idx.items():
        p_entry = personal_idx.get(ident)
        if p_entry is None:
            result.resolved.append(t_entry)
            continue

        # Both layers have this (section, key) — apply tier rules.
        if t_entry.merge is not p_entry.merge:
            result.conflicts.append(
                Conflict(
                    section=ident[0],
                    key=ident[1],
                    reason=ConflictReason.STRATEGY_MISMATCH,
                    team_entry=t_entry,
                    personal_entry=p_entry,
                )
            )
            # Fall back to the team's value so the resolved view stays usable.
            result.resolved.append(t_entry)
            continue

        if t_entry.merge is MergeStrategy.REPLACE:
            if t_entry.value == p_entry.value:
                result.resolved.append(t_entry)
            elif t_entry.lock:
                result.conflicts.append(
                    Conflict(
                        section=ident[0],
                        key=ident[1],
                        reason=ConflictReason.LOCKED_BY_TEAM,
                        team_entry=t_entry,
                        personal_entry=p_entry,
                    )
                )
                result.resolved.append(t_entry)  # team value stands until resolved
            else:
                # Tier 1: personal overrides team.
                result.resolved.append(_merged_entry(t_entry, p_entry, p_entry.value))

        elif t_entry.merge is MergeStrategy.APPEND:
            combined = _dedupe_preserve_order(list(t_entry.value) + list(p_entry.value))
            result.resolved.append(_merged_entry(t_entry, p_entry, combined))

        elif t_entry.merge is MergeStrategy.MAP_MERGE:
            combined = {**t_entry.value, **p_entry.value}
            result.resolved.append(_merged_entry(t_entry, p_entry, combined))

        else:  # pragma: no cover — exhaustive enum
            raise AssertionError(f"unhandled merge strategy: {t_entry.merge}")

    # Pass 2: personal-only entries (not present in team) pass through.
    for ident, p_entry in personal_idx.items():
        if ident not in team_idx:
            result.resolved.append(p_entry)

    # Stable ordering so renders / diffs are deterministic.
    result.resolved.sort(key=lambda e: (e.section.value, e.key))
    result.conflicts.sort(key=lambda c: (c.section, c.key))
    return result

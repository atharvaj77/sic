"""YAML loading / saving for profiles.

Uses ruamel.yaml in round-trip mode so comments and key order survive a
load → edit → save cycle (important once users hand-edit profiles).
Validation against the Pydantic schema happens here so callers always get
a fully-typed `Profile` back.
"""

from __future__ import annotations

import io
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML

from .schema import Profile


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


class ProfileLoadError(Exception):
    """Raised when a YAML file is unparseable or fails schema validation.

    Wraps both YAML syntax errors and Pydantic ValidationError so callers
    (CLI, tests) only need to catch one type.
    """

    def __init__(self, path: Path, message: str, cause: Exception | None = None):
        self.path = path
        super().__init__(f"{path}: {message}")
        self.__cause__ = cause


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    try:
        with path.open() as f:
            raw = _yaml().load(f)
    except Exception as e:  # ruamel raises a variety of subtypes
        raise ProfileLoadError(path, f"could not parse YAML: {e}", e) from e

    if raw is None:
        raise ProfileLoadError(path, "file is empty")

    try:
        return Profile.model_validate(raw)
    except ValidationError as e:
        raise ProfileLoadError(path, f"schema validation failed:\n{e}", e) from e


def dump_profile(profile: Profile, path: str | Path) -> None:
    """Serialize a Profile to YAML on disk. Loses comments (model -> YAML)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = profile.model_dump(mode="json", exclude_defaults=False)
    with path.open("w") as f:
        _yaml().dump(data, f)


def dump_profile_str(profile: Profile) -> str:
    data = profile.model_dump(mode="json", exclude_defaults=False)
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()

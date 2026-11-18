"""Named recipes the caller selects by name.

Built-ins are parameterized operations. A user recipe is a JSON file that pins
one of those names. It is not Python, and this module does not execute any.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

BUILTIN_NAMES = ("reproject", "centroids", "explode", "points_from_xy", "extent")

# Arguments besides layer_path and output_path. Paths stay on the call.
_REQUIRED = {
    "reproject": ("crs",),
    "centroids": (),
    "explode": (),
    "points_from_xy": ("lon_field", "lat_field"),
    "extent": (),
}
_OPTIONAL = {
    "reproject": frozenset(),
    "centroids": frozenset(),
    "explode": frozenset(),
    "points_from_xy": frozenset({"crs"}),
    "extent": frozenset(),
}
_PATH_KEYS = frozenset({"layer_path", "output_path", "overlay_path", "path"})
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_POINTS_CRS = "EPSG:4326"


class RecipeRejected(Exception):
    """The name or the user file cannot be run. ``next_step`` is the recovery hint."""

    def __init__(self, message: str, next_step: str) -> None:
        super().__init__(message)
        self.next_step = next_step


@dataclass(frozen=True)
class RecipeCall:
    name: str
    recipe: str
    source: str
    crs: str | None
    lon_field: str | None
    lat_field: str | None


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(key: str, value: object) -> str | None:
    if _blank(value):
        return None
    if not isinstance(value, str):
        raise RecipeRejected(
            f"{key} must be a string, got {type(value).__name__}.",
            f"pass {key} as text, then retry qgis_recipe",
        )
    return value.strip()


def _listed() -> str:
    return ", ".join(BUILTIN_NAMES)


def resolve(
    name: object,
    *,
    crs: object,
    lon_field: object,
    lat_field: object,
    directory: Path | None,
) -> RecipeCall:
    """Turn a call into a built-in recipe plus the arguments that recipe takes."""
    if not isinstance(name, str) or not _NAME.match(name.strip()):
        raise RecipeRejected(
            f"recipe name {name!r} must be one word of letters, digits, and underscores.",
            f"pass a built-in name ({_listed()}), then retry qgis_recipe",
        )
    text = name.strip()
    caller = {
        "crs": _text("crs", crs),
        "lon_field": _text("lon_field", lon_field),
        "lat_field": _text("lat_field", lat_field),
    }
    if text in BUILTIN_NAMES:
        # A file named after a built-in is never opened.
        return _finish(text, text, "builtin", caller)
    return _from_file(text, caller, directory)


def _finish(name: str, recipe: str, source: str, provided: dict[str, str | None]) -> RecipeCall:
    pinned = {key: value for key, value in provided.items() if value is not None}
    allowed = set(_REQUIRED[recipe]) | _OPTIONAL[recipe]
    extra = sorted(key for key in pinned if key not in allowed)
    if extra:
        raise RecipeRejected(
            f"{extra[0]} is not an argument of {recipe}.",
            f"drop {extra[0]} and retry qgis_recipe",
        )
    missing = [key for key in _REQUIRED[recipe] if key not in pinned]
    if missing:
        raise RecipeRejected(
            f"{recipe} requires {missing[0]}.",
            f"pass {missing[0]}, then retry qgis_recipe",
        )
    chosen = pinned.get("crs")
    if recipe == "points_from_xy" and chosen is None:
        chosen = _POINTS_CRS
    return RecipeCall(
        name=name,
        recipe=recipe,
        source=source,
        crs=chosen,
        lon_field=pinned.get("lon_field"),
        lat_field=pinned.get("lat_field"),
    )


def _from_file(name: str, caller: dict[str, str | None], directory: Path | None) -> RecipeCall:
    if directory is None:
        raise RecipeRejected(
            f"unknown recipe {name!r}. Built-in recipes: {_listed()}. "
            "A user recipe directory is off until recipe_dir is set in "
            "~/.config/qgis-mcp-workflows/config.toml.",
            "pass a built-in name, or set recipe_dir and retry qgis_recipe",
        )
    root = _directory(directory)
    candidate = (root / f"{name}.json").resolve()
    if candidate.parent != root:
        raise RecipeRejected(
            f"recipe name {name!r} is not a file in {root}.",
            "pass a built-in name, or the stem of a JSON file in recipe_dir, then retry qgis_recipe",
        )
    if not candidate.is_file():
        raise RecipeRejected(
            f"unknown recipe {name!r}. Built-in recipes: {_listed()}. No {name}.json in {root}.",
            "pass a built-in name, or add that JSON file, then retry qgis_recipe",
        )
    try:
        loaded = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        raise RecipeRejected(
            f"user recipe {name} is not JSON ({err}).",
            "write a JSON object with a recipe key, then retry qgis_recipe",
        ) from err
    if not isinstance(loaded, dict):
        raise RecipeRejected(
            f"user recipe {name} must be a JSON object.",
            "write an object with a recipe key, then retry qgis_recipe",
        )
    if "code" in loaded:
        raise RecipeRejected(
            f"user recipe {name} has a code key. A user recipe is not Python. Python stays on qgis_eval.",
            "remove code and set recipe to a built-in name, or call qgis_eval",
        )
    recipe = loaded.get("recipe")
    if not isinstance(recipe, str) or recipe not in BUILTIN_NAMES:
        raise RecipeRejected(
            f"user recipe {name} must set recipe to one of: {_listed()}.",
            "set recipe to a built-in name, then retry qgis_recipe",
        )
    allowed = set(_REQUIRED[recipe]) | _OPTIONAL[recipe]
    pinned: dict[str, str | None] = {"crs": None, "lon_field": None, "lat_field": None}
    for key, value in loaded.items():
        if key == "recipe":
            continue
        if key in _PATH_KEYS or key not in allowed:
            accepted = ", ".join(sorted(allowed)) or "no extra arguments"
            raise RecipeRejected(
                f"user recipe {name} cannot set {key}. Pass paths on the call. {recipe} accepts {accepted}.",
                "remove that key from the JSON, then retry qgis_recipe",
            )
        if not isinstance(value, str) or not value.strip():
            raise RecipeRejected(
                f"user recipe {name} sets {key} to a blank or non-string value.",
                f"set {key} to text, then retry qgis_recipe",
            )
        pinned[key] = value.strip()
    for key, value in caller.items():
        if value is not None:
            pinned[key] = value
    return _finish(name, recipe, "user", pinned)


def _directory(directory: Path) -> Path:
    try:
        root = directory.expanduser().resolve()
    except OSError as err:
        raise RecipeRejected(
            f"recipe_dir {directory} cannot be opened ({err}).",
            "set recipe_dir to an existing directory, then retry qgis_recipe",
        ) from err
    if not root.is_dir():
        raise RecipeRejected(
            f"recipe_dir {root} is not a directory.",
            "set recipe_dir to an existing directory, then retry qgis_recipe",
        )
    return root

"""Units are part of the schema.

Every float in a contract either ends with its unit (``speed_m_s``,
``yaw_enu_rad``, ``processing_ms``) or is a frame-tagged ``Vec3`` (whose unit
follows from its quantity), or is a genuinely unitless ratio listed in
``UNITLESS_FLOATS``. ``unit_problems`` checks a payload model recursively;
tests run it over every registered topic so a bare ``speed: float`` cannot
ship.
"""
from __future__ import annotations

import types
import typing
from typing import Any

from pydantic import BaseModel

from .frames import Vec3

UNIT_SUFFIXES = (
    "_m", "_m_s", "_m_s2", "_rad", "_rad_s", "_deg", "_deg_s",
    "_s", "_ms", "_us", "_ns", "_pct", "_hz", "_ppm",
    "_v", "_a", "_j", "_k", "_pa", "_w", "_px",
)
UNITLESS_FLOATS = frozenset(
    {"confidence", "planner_confidence", "current_confidence", "score", "ratio", "aggression"}
)


def _leaf_types(annotation: Any) -> list[Any]:
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _leaf_types(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        out: list[Any] = []
        for arg in typing.get_args(annotation):
            out.extend(_leaf_types(arg))
        return out
    if origin in (list, tuple, set, frozenset, typing.Sequence):
        out = []
        for arg in typing.get_args(annotation):
            if arg is not Ellipsis:
                out.extend(_leaf_types(arg))
        return out
    if origin is dict:
        return []  # free-form maps (e.g. model_versions) carry no physical values
    return [annotation]


def unit_problems(model: type[BaseModel], prefix: str = "", _seen: set | None = None) -> list[str]:
    seen = _seen if _seen is not None else set()
    if model in seen:
        return []
    seen.add(model)
    problems: list[str] = []
    for name, info in model.model_fields.items():
        path = f"{prefix}{name}"
        for leaf in _leaf_types(info.annotation):
            if leaf is Vec3:
                continue
            if isinstance(leaf, type) and issubclass(leaf, BaseModel):
                problems.extend(unit_problems(leaf, f"{path}.", seen))
                continue
            if leaf is float and name not in UNITLESS_FLOATS and not name.endswith(UNIT_SUFFIXES):
                problems.append(f"{model.__name__}.{path}: float without a unit suffix")
    return problems

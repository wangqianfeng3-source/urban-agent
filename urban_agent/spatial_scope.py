"""Deterministic spatial-scope parsing and parcel selection.

This module translates Chinese directional phrases into auditable geometry rules.
It only reads ``Plan``/GeoJSON data and never mutates a plan or executes actions.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .schema import Plan


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = PROJECT_ROOT / "data" / "real" / "spatial_scope_rules.json"

DIRECTIONAL_TERMS = {
    "north", "south", "east", "west", "middle", "north_tip", "south_tip"
}
VALID_TERMS = DIRECTIONAL_TERMS | {"waterfront"}
VALID_MODES = {"single", "intersection", "nested"}


class SpatialScopeError(ValueError):
    """Raised when a spatial expression or rules file is ambiguous/invalid."""


@dataclass(frozen=True)
class SpatialScope:
    """A maximum-two-level, deterministic spatial expression."""

    mode: str
    terms: tuple[str, ...]
    raw_text: str = ""

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise SpatialScopeError(f"未知空间组合方式：{self.mode}")
        if not self.terms or len(self.terms) > 2:
            raise SpatialScopeError("空间范围必须包含 1-2 个词，最多支持两层")
        unknown = [term for term in self.terms if term not in VALID_TERMS]
        if unknown:
            raise SpatialScopeError(f"未知空间词：{unknown}")
        if self.mode == "single" and len(self.terms) != 1:
            raise SpatialScopeError("single 模式只能包含一个空间词")
        if self.mode in {"intersection", "nested"} and len(self.terms) != 2:
            raise SpatialScopeError(f"{self.mode} 模式必须包含两个空间词")
        if self.mode == "intersection" and "waterfront" not in self.terms:
            raise SpatialScopeError("当前交集模式仅用于“方位区域 + 滨水”")
        if self.mode == "nested" and "waterfront" in self.terms:
            raise SpatialScopeError("滨水是独立属性，不能作为递进分区")

    def to_dict(self) -> dict:
        return {"mode": self.mode, "terms": list(self.terms), "raw_text": self.raw_text}


@dataclass(frozen=True)
class SpatialScopeRules:
    version: str
    boundary_path: Path
    ratios: dict[str, float]
    waterfront_distance_m: float
    max_scope_depth: int
    membership: dict[str, str]


@dataclass(frozen=True)
class ScopeResolution:
    scope: SpatialScope
    target_parcel_ids: tuple[str, ...]
    rules_version: str
    boundary_source: str
    rule_parameters: dict
    membership: dict[str, str]
    geometry: dict
    trace: tuple[dict, ...]

    def to_dict(self) -> dict:
        return {
            "raw_text": self.scope.raw_text,
            "scope": self.scope.to_dict(),
            "rules_version": self.rules_version,
            "boundary_source": self.boundary_source,
            "rule_parameters": self.rule_parameters,
            "membership": self.membership,
            "trace": list(self.trace),
            "target_parcel_ids": list(self.target_parcel_ids),
            "geometry": self.geometry,
        }


_ALIASES = {
    "north_tip": ("北岛尖", "最北端", "北端岛尖"),
    "south_tip": ("南岛尖", "最南端", "南端岛尖"),
    "waterfront": ("滨水", "沿河", "临水", "沿岸", "近岸"),
    "middle": ("中部", "中间", "中段", "中央"),
    "north": ("北部", "北侧", "偏北", "北边"),
    "south": ("南部", "南侧", "偏南", "南边"),
    "east": ("东部", "东侧", "偏东", "东边"),
    "west": ("西部", "西侧", "偏西", "西边"),
}

_COMPOUNDS = {
    "东南部": ("east", "south"),
    "东南侧": ("east", "south"),
    "东北部": ("east", "north"),
    "东北侧": ("east", "north"),
    "西南部": ("west", "south"),
    "西南侧": ("west", "south"),
    "西北部": ("west", "north"),
    "西北侧": ("west", "north"),
}


def _ordered_terms(text: str) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    for term, aliases in _ALIASES.items():
        for alias in aliases:
            start = text.find(alias)
            while start != -1:
                matches.append((start, -len(alias), term))
                start = text.find(alias, start + len(alias))
    matches.sort()

    ordered: list[str] = []
    occupied_until = -1
    for start, neg_length, term in matches:
        length = -neg_length
        if start < occupied_until:
            continue
        occupied_until = start + length
        if not ordered or ordered[-1] != term:
            ordered.append(term)
    return ordered


def parse_spatial_scope(text: str) -> SpatialScope | None:
    """Parse supported Chinese spatial phrases without using an LLM.

    Direction + waterfront is an intersection. Direction + direction is only
    accepted when the wording explicitly expresses nesting or is a supported
    compass compound such as ``东南部``.
    """
    raw = text.strip()
    if not raw:
        return None

    compound_terms = None
    compound_span = None
    for phrase, terms in _COMPOUNDS.items():
        index = raw.find(phrase)
        if index != -1:
            compound_terms = terms
            compound_span = (index, index + len(phrase))
            break

    if compound_terms:
        remainder = raw[:compound_span[0]] + raw[compound_span[1]:]
        extra = _ordered_terms(remainder)
        if extra:
            raise SpatialScopeError("空间描述超过两层，请保留一个两层方位表达")
        return SpatialScope("nested", compound_terms, raw)

    terms = _ordered_terms(raw)
    unique_terms = list(dict.fromkeys(terms))
    if not unique_terms:
        return None
    if len(unique_terms) > 2:
        raise SpatialScopeError("空间描述超过两层，请简化为两个空间词")
    if len(unique_terms) == 1:
        return SpatialScope("single", (unique_terms[0],), raw)
    if "waterfront" in unique_terms:
        directional = next(term for term in unique_terms if term != "waterfront")
        return SpatialScope("intersection", (directional, "waterfront"), raw)

    explicit_nesting = bool(re.search(r"的|内部|以内|其中|偏", raw))
    if not explicit_nesting:
        raise SpatialScopeError("两个方位词关系不明确，请使用“某部的某部”表达递进关系")
    return SpatialScope("nested", tuple(unique_terms), raw)


def validate_scope_rules(rules: SpatialScopeRules) -> SpatialScopeRules:
    """Validate a rules object and return it for convenient chaining."""
    ratios = rules.ratios
    required = {"north", "south", "north_tip", "south_tip", "east", "west"}
    missing = required - ratios.keys()
    if missing:
        raise SpatialScopeError(f"空间规则缺少比例：{sorted(missing)}")
    if any(not 0 < ratios[key] < 1 for key in required):
        raise SpatialScopeError("所有方向比例必须在 0 和 1 之间")
    if ratios["north_tip"] > ratios["north"]:
        raise SpatialScopeError("北岛尖比例不能大于北部比例")
    if ratios["south_tip"] > ratios["south"]:
        raise SpatialScopeError("南岛尖比例不能大于南部比例")
    if ratios["north"] + ratios["south"] >= 1:
        raise SpatialScopeError("北部与南部比例之和必须小于 1")
    if ratios["east"] + ratios["west"] >= 1:
        raise SpatialScopeError("东部与西部比例之和必须小于 1")
    if rules.waterfront_distance_m <= 0:
        raise SpatialScopeError("滨水距离必须大于 0 米")
    if not 1 <= rules.max_scope_depth <= 2:
        raise SpatialScopeError("空间范围最大层数必须为 1 或 2")
    if rules.membership.get("directional") != "centroid_within":
        raise SpatialScopeError("普通区域当前只支持 centroid_within 判定")
    if rules.membership.get("waterfront") != "geometry_intersects":
        raise SpatialScopeError("滨水区域当前只支持 geometry_intersects 判定")
    return rules


def load_scope_rules(path: str | Path | None = None) -> SpatialScopeRules:
    rules_path = Path(path) if path else DEFAULT_RULES_PATH
    raw = json.loads(rules_path.read_text(encoding="utf-8"))
    ratios = {key: float(value) for key, value in raw["ratios"].items()}

    boundary_path = Path(raw["boundary_path"])
    if not boundary_path.is_absolute():
        boundary_path = PROJECT_ROOT / boundary_path
    return validate_scope_rules(SpatialScopeRules(
        version=str(raw["version"]),
        boundary_path=boundary_path,
        ratios=ratios,
        waterfront_distance_m=float(raw["waterfront_distance_m"]),
        max_scope_depth=int(raw.get("max_scope_depth", 2)),
        membership=dict(raw.get("membership", {})),
    ))


def override_scope_rules(
    rules: SpatialScopeRules,
    *,
    ratios: dict[str, float] | None = None,
    waterfront_distance_m: float | None = None,
    version: str | None = None,
) -> SpatialScopeRules:
    """Create a validated preview copy without changing the saved JSON file."""
    preview = replace(
        rules,
        ratios=dict(ratios if ratios is not None else rules.ratios),
        waterfront_distance_m=(
            float(waterfront_distance_m)
            if waterfront_distance_m is not None
            else rules.waterfront_distance_m
        ),
        version=version if version is not None else rules.version,
    )
    return validate_scope_rules(preview)


def scope_rules_to_dict(rules: SpatialScopeRules) -> dict:
    boundary_path = rules.boundary_path
    try:
        boundary_value = boundary_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        boundary_value = str(boundary_path)
    return {
        "version": rules.version,
        "boundary_path": boundary_value,
        "ratios": dict(rules.ratios),
        "waterfront_distance_m": rules.waterfront_distance_m,
        "max_scope_depth": rules.max_scope_depth,
        "membership": dict(rules.membership),
    }


def save_scope_rules(
    rules: SpatialScopeRules,
    path: str | Path | None = None,
) -> Path:
    """Persist explicitly confirmed rules; UI previews never call this implicitly."""
    validate_scope_rules(rules)
    target = Path(path) if path else DEFAULT_RULES_PATH
    target.write_text(
        json.dumps(scope_rules_to_dict(rules), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def _projectors(boundary):
    center = boundary.centroid
    lon0, lat0 = center.x, center.y
    scale_x = 111_320 * math.cos(math.radians(lat0))
    scale_y = 110_950

    def to_m(x, y, z=None):
        return ((x - lon0) * scale_x, (y - lat0) * scale_y)

    def to_deg(x, y, z=None):
        return (x / scale_x + lon0, y / scale_y + lat0)

    return to_m, to_deg


def _load_boundary(rules: SpatialScopeRules):
    from shapely.geometry import shape
    from shapely.ops import unary_union

    raw = json.loads(rules.boundary_path.read_text(encoding="utf-8-sig"))
    features = raw.get("features", [])
    if not features:
        raise SpatialScopeError(f"边界文件没有空间对象：{rules.boundary_path}")
    return unary_union([shape(feature["geometry"]) for feature in features])


def _partition_region(parent, rules: SpatialScopeRules) -> dict[str, object]:
    from shapely.geometry import box
    from shapely.ops import unary_union

    min_x, min_y, max_x, max_y = parent.bounds
    width, height = max_x - min_x, max_y - min_y
    ratios = rules.ratios
    north = parent.intersection(box(min_x, max_y - ratios["north"] * height, max_x, max_y))
    south = parent.intersection(box(min_x, min_y, max_x, min_y + ratios["south"] * height))
    east = parent.intersection(box(max_x - ratios["east"] * width, min_y, max_x, max_y))
    west = parent.intersection(box(min_x, min_y, min_x + ratios["west"] * width, max_y))
    middle = parent.difference(unary_union([north, south, east, west]))
    north_tip = parent.intersection(
        box(min_x, max_y - ratios["north_tip"] * height, max_x, max_y)
    )
    south_tip = parent.intersection(
        box(min_x, min_y, max_x, min_y + ratios["south_tip"] * height)
    )
    return {
        "north": north,
        "south": south,
        "east": east,
        "west": west,
        "middle": middle,
        "north_tip": north_tip,
        "south_tip": south_tip,
    }


def _waterfront_region(boundary_m, rules: SpatialScopeRules):
    inner = boundary_m.buffer(-rules.waterfront_distance_m)
    return boundary_m if inner.is_empty else boundary_m.difference(inner)


def _single_region(boundary_m, term: str, rules: SpatialScopeRules):
    if term == "waterfront":
        return _waterfront_region(boundary_m, rules)
    return _partition_region(boundary_m, rules)[term]


def _resolve_geometry(boundary_m, scope: SpatialScope, rules: SpatialScopeRules):
    trace: list[dict] = []
    if scope.mode == "single":
        term = scope.terms[0]
        region = _single_region(boundary_m, term, rules)
        trace.append({"input": "island", "operation": term, "result": term})
        return region, trace

    if scope.mode == "intersection":
        left = _single_region(boundary_m, scope.terms[0], rules)
        right = _single_region(boundary_m, scope.terms[1], rules)
        trace.append({"input": "island", "operation": scope.terms[0], "result": scope.terms[0]})
        trace.append({"input": "island", "operation": scope.terms[1], "result": scope.terms[1]})
        trace.append({"operation": "intersection", "inputs": list(scope.terms)})
        return left.intersection(right), trace

    current = boundary_m
    parent_name = "island"
    for term in scope.terms:
        current = _partition_region(current, rules)[term]
        result_name = f"{parent_name}.{term}"
        trace.append({"input": parent_name, "operation": term, "result": result_name})
        parent_name = result_name
    return current, trace


def resolve_scope(
    plan: Plan,
    scope: SpatialScope,
    rules_path: str | Path | None = None,
) -> ScopeResolution:
    """Resolve using the saved rules file."""
    return resolve_scope_with_rules(plan, scope, load_scope_rules(rules_path))


def resolve_scope_with_rules(
    plan: Plan,
    scope: SpatialScope,
    rules: SpatialScopeRules,
) -> ScopeResolution:
    """Resolve using an in-memory rules object, suitable for live UI previews."""
    from shapely.geometry import mapping, shape
    from shapely.ops import transform

    validate_scope_rules(rules)
    if len(scope.terms) > rules.max_scope_depth:
        raise SpatialScopeError(f"空间范围最多支持 {rules.max_scope_depth} 层")

    boundary = _load_boundary(rules)
    to_m, to_deg = _projectors(boundary)
    boundary_m = transform(to_m, boundary)
    region_m, trace = _resolve_geometry(boundary_m, scope, rules)

    root_regions = {
        term: _single_region(boundary_m, term, rules)
        for term in scope.terms
        if scope.mode == "intersection"
    }
    matched: list[str] = []
    for parcel_id, parcel in plan.parcels.items():
        parcel_m = transform(to_m, shape(parcel.geometry))
        if scope.mode == "intersection":
            checks = []
            for term in scope.terms:
                if term == "waterfront":
                    checks.append(parcel_m.intersects(root_regions[term]))
                else:
                    checks.append(root_regions[term].covers(parcel_m.centroid))
            is_match = all(checks)
        elif scope.terms == ("waterfront",):
            is_match = parcel_m.intersects(region_m)
        else:
            is_match = region_m.covers(parcel_m.centroid)
        if is_match:
            matched.append(parcel_id)

    region_deg = transform(to_deg, region_m)
    return ScopeResolution(
        scope=scope,
        target_parcel_ids=tuple(matched),
        rules_version=rules.version,
        boundary_source=str(rules.boundary_path),
        rule_parameters={
            "ratios": dict(rules.ratios),
            "waterfront_distance_m": rules.waterfront_distance_m,
            "max_scope_depth": rules.max_scope_depth,
        },
        membership=dict(rules.membership),
        geometry=mapping(region_deg),
        trace=tuple(trace),
    )

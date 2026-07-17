"""受限动作空间 —— 编辑模块修改方案的唯一合法通道。

设计原则（对应立项 PPT 第 26 页）：
  1. 每次只做局部、可解释的修改，不推倒重来；
  2. 所有修改必须遵守规划要求 —— 动作自带校验，非法修改直接拒绝；
  3. 动作即日志：每个动作都会写入 Plan.history，可回滚、可审计。

LLM 编辑器通过 function calling 调用这些动作（见 ACTION_SPECS）。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform

from .schema import (
    FAR_RANGE,
    GREEN_RATIO_RANGE,
    LANDUSE_CODES,
    MAX_FACILITIES,
    Plan,
)


class ActionError(Exception):
    """动作被拒绝时抛出，message 用中文说明原因。"""


ROAD_WIDTH_RANGE = (4.0, 24.0)
SIDEWALK_WIDTH_RANGE = (1.5, 6.0)
BIKE_LANE_WIDTH_RANGE = (0.0, 2.5)


def _area_m2(geom) -> float:
    """片区尺度 WGS84 多边形近似面积。"""
    def polygon_area(poly: Polygon) -> float:
        coords = list(poly.exterior.coords)
        if len(coords) < 4:
            return 0.0
        lat0 = math.radians(sum(y for _, y in coords) / len(coords))
        pts = [(x * 111_320 * math.cos(lat0), y * 110_950) for x, y in coords]
        area = 0.0
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            area += x1 * y2 - x2 * y1
        return abs(area) / 2

    if geom.is_empty:
        return 0.0
    if isinstance(geom, Polygon):
        return polygon_area(geom)
    if isinstance(geom, MultiPolygon):
        return sum(polygon_area(g) for g in geom.geoms)
    return 0.0


def _projectors(*geoms):
    points = [g.centroid for g in geoms if not g.is_empty]
    lon0 = sum(p.x for p in points) / len(points)
    lat0 = sum(p.y for p in points) / len(points)
    scale_x = 111_320 * math.cos(math.radians(lat0))
    scale_y = 110_950

    def to_m(x, y, z=None):
        return ((x - lon0) * scale_x, (y - lat0) * scale_y)

    def to_deg(x, y, z=None):
        return (x / scale_x + lon0, y / scale_y + lat0)

    return to_m, to_deg


def _largest_polygon(geom):
    if isinstance(geom, Polygon):
        return geom
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    return None


def _load_road_geometry(road_id: str):
    roads_path = Path(__file__).resolve().parents[1] / "data" / "real" / "fuxing_geojson" / "roads.geojson"
    if not roads_path.exists():
        raise ActionError(f"道路数据不存在：{roads_path}")
    raw = json.loads(roads_path.read_text(encoding="utf-8-sig"))
    for i, feat in enumerate(raw.get("features", []), 1):
        props = feat.get("properties", {})
        candidates = {
            str(feat.get("id", "")),
            str(props.get("OBJECTID", "")),
            f"road-{props.get('OBJECTID', i)}",
            f"road-{i}",
        }
        if road_id in candidates:
            return shape(feat["geometry"])
    raise ActionError(f"找不到道路 {road_id}")


# ---------------- 动作实现 ----------------

def set_landuse(plan: Plan, parcel_id: str, landuse: str) -> str:
    p = plan.parcel(parcel_id)
    if landuse not in LANDUSE_CODES:
        raise ActionError(f"非法用地代码 {landuse}，合法值：{list(LANDUSE_CODES)}")
    if p.flag("historic"):
        raise ActionError(f"地块 {parcel_id} 位于历史风貌保护范围，用地性质不得调整")
    old = p.landuse
    p.landuse = landuse
    return f"{parcel_id} 用地 {old}→{landuse}"


def adjust_far(plan: Plan, parcel_id: str, delta: float) -> str:
    p = plan.parcel(parcel_id)
    new = round(p.far + float(delta), 2)
    lo, hi = FAR_RANGE
    if not (lo <= new <= hi):
        raise ActionError(f"地块 {parcel_id} 容积率调整后为 {new}，超出合法区间 {FAR_RANGE}")
    old = p.far
    p.far = new
    return f"{parcel_id} 容积率 {old:.2f}→{new:.2f}"


def set_green_ratio(plan: Plan, parcel_id: str, ratio: float) -> str:
    p = plan.parcel(parcel_id)
    ratio = round(float(ratio), 2)
    lo, hi = GREEN_RATIO_RANGE
    if not (lo <= ratio <= hi):
        raise ActionError(f"绿地率 {ratio} 超出合法区间 {GREEN_RATIO_RANGE}")
    old = p.green_ratio
    p.green_ratio = ratio
    return f"{parcel_id} 绿地率 {old:.2f}→{ratio:.2f}"


def add_facility(plan: Plan, parcel_id: str, facility: str) -> str:
    p = plan.parcel(parcel_id)
    if len(p.facilities) >= MAX_FACILITIES:
        raise ActionError(f"地块 {parcel_id} 配套设施已达上限 {MAX_FACILITIES} 项")
    if facility in p.facilities:
        raise ActionError(f"地块 {parcel_id} 已有设施 {facility}")
    p.facilities.append(facility)
    return f"{parcel_id} 新增设施 {facility}"


def set_street_profile(
    plan: Plan,
    parcel_id: str,
    road_width: float,
    sidewalk_width: float,
    bike_lane_width: float = 0.0,
) -> str:
    """更新道路断面，并把建筑型地块沿路边界向内退让。"""
    p = plan.parcel(parcel_id)
    flags = p.zone_flags
    if not flags.get("building_as_parcel"):
        raise ActionError(f"地块 {parcel_id} 不是建筑 footprint 地块，不能执行道路退界动作")

    road_width = round(float(road_width), 1)
    sidewalk_width = round(float(sidewalk_width), 1)
    bike_lane_width = round(float(bike_lane_width), 1)
    if not (ROAD_WIDTH_RANGE[0] <= road_width <= ROAD_WIDTH_RANGE[1]):
        raise ActionError(f"道路宽度 {road_width}m 超出合理范围 {ROAD_WIDTH_RANGE}m")
    if not (SIDEWALK_WIDTH_RANGE[0] <= sidewalk_width <= SIDEWALK_WIDTH_RANGE[1]):
        raise ActionError(f"人行道宽度 {sidewalk_width}m 超出合理范围 {SIDEWALK_WIDTH_RANGE}m")
    if not (BIKE_LANE_WIDTH_RANGE[0] <= bike_lane_width <= BIKE_LANE_WIDTH_RANGE[1]):
        raise ActionError(f"骑行空间宽度 {bike_lane_width}m 超出合理范围 {BIKE_LANE_WIDTH_RANGE}m")

    road_id = flags.get("nearest_road_id")
    if not road_id:
        raise ActionError(f"地块 {parcel_id} 缺少 nearest_road_id，无法判断沿路边界")

    parcel_geom = shape(p.geometry)
    road_geom = _load_road_geometry(str(road_id))
    to_m, to_deg = _projectors(parcel_geom, road_geom)
    parcel_m = transform(to_m, parcel_geom)
    road_m = transform(to_m, road_geom)

    target_corridor = road_width / 2 + sidewalk_width + bike_lane_width
    old_corridor = (
        float(flags.get("road_width_m") or 0) / 2
        + float(flags.get("sidewalk_width_m") or 0)
        + float(flags.get("bike_lane_width_m") or 0)
    )
    cut_zone = road_m.buffer(target_corridor, cap_style=2, join_style=2)
    edited_m = parcel_m.difference(cut_zone)
    edited_poly_m = _largest_polygon(edited_m)
    if edited_poly_m is None or edited_poly_m.is_empty:
        raise ActionError(f"{parcel_id} 按目标道路断面退让后会被完全占用，动作被拦截")

    edited = transform(to_deg, edited_poly_m)
    new_area = _area_m2(edited)
    old_area = p.area
    loss_ratio = (old_area - new_area) / old_area if old_area else 0.0
    if loss_ratio > 0.6:
        raise ActionError(f"{parcel_id} 退界会损失 {loss_ratio:.0%} 建筑地块面积，超过 60% 上限")

    p.geometry = mapping(edited)
    p.area = round(new_area, 1)
    flags["road_width_m"] = road_width
    flags["sidewalk_width_m"] = sidewalk_width
    flags["bike_lane_width_m"] = bike_lane_width
    flags["frontage_retreat_m"] = round(
        float(flags.get("frontage_retreat_m") or 0) + max(0.0, target_corridor - old_corridor),
        1,
    )
    flags["last_area_loss_m2"] = round(max(0.0, old_area - new_area), 1)
    return (
        f"{parcel_id} 沿 {road_id} 更新道路断面：车行道 {road_width:.1f}m、"
        f"人行道 {sidewalk_width:.1f}m、骑行 {bike_lane_width:.1f}m；"
        f"建筑边界退让，面积 {old_area:.0f}→{new_area:.0f}㎡"
    )


def set_road_profile(
    plan: Plan,
    road_id: str,
    road_width: float,
    sidewalk_width: float,
    bike_lane_width: float = 0.0,
    max_parcels: int = 12,
) -> str:
    """按道路批量更新断面，推动沿路建筑型地块退界。"""
    max_parcels = max(1, min(int(max_parcels), 30))
    candidates = [
        p for p in plan.parcels.values()
        if p.zone_flags.get("building_as_parcel") and str(p.zone_flags.get("nearest_road_id")) == str(road_id)
    ]
    if not candidates:
        raise ActionError(f"没有找到沿 {road_id} 的建筑地块")
    candidates.sort(key=lambda p: float(p.zone_flags.get("frontage_distance_m") or 999))

    changed = []
    blocked = []
    for p in candidates[:max_parcels]:
        try:
            changed.append(set_street_profile(
                plan,
                p.pid,
                road_width=road_width,
                sidewalk_width=sidewalk_width,
                bike_lane_width=bike_lane_width,
            ))
        except ActionError as exc:
            blocked.append(f"{p.pid}: {exc}")

    if not changed:
        raise ActionError(f"{road_id} 沿线前 {max_parcels} 个建筑均无法按目标断面退界；首个原因：{blocked[0]}")
    return (
        f"{road_id} 道路断面更新为车行道 {float(road_width):.1f}m、"
        f"人行道 {float(sidewalk_width):.1f}m、骑行 {float(bike_lane_width):.1f}m；"
        f"已推动 {len(changed)} 个沿路建筑退界"
        + (f"，{len(blocked)} 个因占用过大被拦截" if blocked else "")
    )


_REGISTRY = {
    "set_landuse": set_landuse,
    "adjust_far": adjust_far,
    "set_green_ratio": set_green_ratio,
    "add_facility": add_facility,
    "set_street_profile": set_street_profile,
    "set_road_profile": set_road_profile,
}


def apply_action(plan: Plan, action: dict) -> str:
    """执行一个动作字典：{"name": ..., "params": {...}, "reason": ...}。

    成功返回一句中文变更描述，并记入 plan.history；失败抛 ActionError。
    """
    name = action.get("name")
    if name not in _REGISTRY:
        raise ActionError(f"未知动作 {name}，合法动作：{list(_REGISTRY)}")
    desc = _REGISTRY[name](plan, **action.get("params", {}))
    plan.history.append({
        "version": plan.version,
        "action": name,
        "params": action.get("params", {}),
        "reason": action.get("reason", ""),
        "result": desc,
    })
    return desc


# ---------------- LLM function-calling 工具定义 ----------------
# OpenAI 兼容格式，通义千问 / DeepSeek 均可直接使用。

_REASON_PARAM = {
    "type": "string",
    "description": "本动作的规划理由。用一句中文说明为什么要这样改，并尽量引用硬违规、评分依据或策略目标。",
}

ACTION_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "set_landuse",
            "description": "调整某地块的用地性质。历史风貌保护地块不可调整。",
            "parameters": {
                "type": "object",
                "properties": {
                    "parcel_id": {"type": "string", "description": "地块编号，如 P01"},
                    "landuse": {
                        "type": "string",
                        "enum": list(LANDUSE_CODES),
                        "description": "目标用地代码：R居住 C商业 M工业 T科创研发 A公共服务设施 G绿地",
                    },
                    "reason": _REASON_PARAM,
                },
                "required": ["parcel_id", "landuse"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "adjust_far",
            "description": f"增减某地块容积率（delta 为增量，可为负）。调整后必须落在 {FAR_RANGE} 区间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "parcel_id": {"type": "string"},
                    "delta": {"type": "number", "description": "容积率增量，如 -0.5"},
                    "reason": _REASON_PARAM,
                },
                "required": ["parcel_id", "delta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_green_ratio",
            "description": f"设置某地块绿地率，取值 {GREEN_RATIO_RANGE}。",
            "parameters": {
                "type": "object",
                "properties": {
                    "parcel_id": {"type": "string"},
                    "ratio": {"type": "number"},
                    "reason": _REASON_PARAM,
                },
                "required": ["parcel_id", "ratio"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_facility",
            "description": "为某地块新增一项公共配套设施（如 community_center / elderly_care / library / gym / clinic）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "parcel_id": {"type": "string"},
                    "facility": {"type": "string"},
                    "reason": _REASON_PARAM,
                },
                "required": ["parcel_id", "facility"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_street_profile",
            "description": "更新建筑型地块沿路道路断面，并把建筑多边形的沿路边界向内退让。",
            "parameters": {
                "type": "object",
                "properties": {
                    "parcel_id": {"type": "string", "description": "建筑地块编号，如 B001"},
                    "road_width": {
                        "type": "number",
                        "description": f"目标车行道宽度，合理范围 {ROAD_WIDTH_RANGE} 米",
                    },
                    "sidewalk_width": {
                        "type": "number",
                        "description": f"目标人行道宽度，合理范围 {SIDEWALK_WIDTH_RANGE} 米",
                    },
                    "bike_lane_width": {
                        "type": "number",
                        "description": f"目标骑行空间宽度，合理范围 {BIKE_LANE_WIDTH_RANGE} 米；没有可填 0",
                    },
                    "reason": _REASON_PARAM,
                },
                "required": ["parcel_id", "road_width", "sidewalk_width"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_road_profile",
            "description": "按道路编号批量更新道路断面，并推动沿路多个建筑多边形退界。",
            "parameters": {
                "type": "object",
                "properties": {
                    "road_id": {"type": "string", "description": "道路编号，如 road-10"},
                    "road_width": {
                        "type": "number",
                        "description": f"目标车行道宽度，合理范围 {ROAD_WIDTH_RANGE} 米",
                    },
                    "sidewalk_width": {
                        "type": "number",
                        "description": f"目标人行道宽度，合理范围 {SIDEWALK_WIDTH_RANGE} 米",
                    },
                    "bike_lane_width": {
                        "type": "number",
                        "description": f"目标骑行空间宽度，合理范围 {BIKE_LANE_WIDTH_RANGE} 米；没有可填 0",
                    },
                    "max_parcels": {
                        "type": "integer",
                        "description": "本次最多影响几个沿路建筑地块，建议 6-12",
                    },
                    "reason": _REASON_PARAM,
                },
                "required": ["road_id", "road_width", "sidewalk_width"],
            },
        },
    },
]

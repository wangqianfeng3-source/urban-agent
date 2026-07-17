"""Plan Schema —— 规划方案的结构化表示（v1.0）。

这是全项目的地基：编辑模块只能通过 actions.py 修改 Plan，
评估模块只能读取 Plan。字段的增删改必须走 docs/plan_schema.md 的冻结流程。
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

# 用地类型代码（v1.0 冻结）
LANDUSE_CODES = {
    "R": "居住",
    "C": "商业",
    "M": "工业",
    "T": "科创研发",
    "A": "公共服务设施",
    "G": "绿地",
}

FAR_RANGE = (0.0, 3.5)          # 容积率合法区间
GREEN_RATIO_RANGE = (0.0, 0.9)  # 绿地率合法区间
MAX_FACILITIES = 5              # 单地块配套设施上限


@dataclass
class Parcel:
    """地块 —— 方案的原子单元。"""

    pid: str
    geometry: dict                      # GeoJSON geometry（WGS84）
    landuse: str                        # 用地类型代码，见 LANDUSE_CODES
    far: float                          # 容积率
    height: float                       # 限高（米）
    green_ratio: float                  # 绿地率 0-0.9
    area: float                         # 用地面积（平方米，预计算）
    facilities: list = field(default_factory=list)   # 配套设施列表
    zone_flags: dict = field(default_factory=dict)   # {"zero_carbon": bool, "historic": bool}

    @property
    def centroid(self) -> tuple:
        """(lon, lat)。对矩形/凸多边形取外环顶点均值已足够。"""
        ring = self.geometry["coordinates"][0]
        pts = ring[:-1] if ring[0] == ring[-1] else ring
        lon = sum(p[0] for p in pts) / len(pts)
        lat = sum(p[1] for p in pts) / len(pts)
        return (lon, lat)

    @property
    def floor_area(self) -> float:
        """地上建筑面积 = 用地面积 × 容积率。"""
        return self.area * self.far

    def flag(self, name: str) -> bool:
        return bool(self.zone_flags.get(name, False))


def distance_m(a: tuple, b: tuple) -> float:
    """两个 (lon, lat) 点之间的近似距离（米），等距圆柱投影，片区尺度足够精确。"""
    lat0 = math.radians((a[1] + b[1]) / 2)
    dx = (a[0] - b[0]) * 111_320 * math.cos(lat0)
    dy = (a[1] - b[1]) * 110_950
    return math.hypot(dx, dy)


@dataclass
class Plan:
    """一个版本的规划方案 = 地块集合 + 元信息 + 修改历史。"""

    site: str
    version: int
    parcels: dict = field(default_factory=dict)   # pid -> Parcel
    history: list = field(default_factory=list)   # 每次修改追加一条记录

    # ---------- IO ----------
    @classmethod
    def from_geojson(cls, path: str | Path) -> "Plan":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        meta = raw.get("metadata", {})
        plan = cls(site=meta.get("site", "unknown"), version=int(meta.get("version", 1)))
        for feat in raw["features"]:
            props = feat["properties"]
            parcel = Parcel(
                pid=str(feat["id"]),
                geometry=feat["geometry"],
                landuse=props["landuse"],
                far=float(props["far"]),
                height=float(props["height"]),
                green_ratio=float(props["green_ratio"]),
                area=float(props["area"]),
                facilities=list(props.get("facilities", [])),
                zone_flags=dict(props.get("zone_flags", {})),
            )
            plan.parcels[parcel.pid] = parcel
        return plan

    def to_dict(self) -> dict:
        """导出为 GeoJSON FeatureCollection 字典（可视化与落盘共用）。"""
        features = []
        for p in self.parcels.values():
            features.append({
                "type": "Feature",
                "id": p.pid,
                "geometry": p.geometry,
                "properties": {
                    "landuse": p.landuse,
                    "far": p.far,
                    "height": p.height,
                    "green_ratio": p.green_ratio,
                    "area": p.area,
                    "facilities": p.facilities,
                    "zone_flags": p.zone_flags,
                },
            })
        return {
            "type": "FeatureCollection",
            "metadata": {"site": self.site, "version": self.version},
            "features": features,
        }

    def to_geojson(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 访问 ----------
    def parcel(self, pid: str) -> Parcel:
        if pid not in self.parcels:
            raise KeyError(f"地块 {pid} 不存在")
        return self.parcels[pid]

    @property
    def total_area(self) -> float:
        return sum(p.area for p in self.parcels.values())

    def area_by_landuse(self) -> dict:
        out: dict = {}
        for p in self.parcels.values():
            out[p.landuse] = out.get(p.landuse, 0.0) + p.area
        return out

    # ---------- 版本 ----------
    def copy(self) -> "Plan":
        """深拷贝并把版本号 +1，用于每一轮编辑。"""
        new = copy.deepcopy(self)
        new.version += 1
        return new

    # ---------- 供 LLM / 报告使用的紧凑摘要 ----------
    def summary_table(self) -> str:
        lines = [
            f"片区：{self.site}（方案版本 v{self.version}，共 {len(self.parcels)} 个地块）",
            "地块 | 用地 | 容积率 | 限高 | 绿地率 | 面积m2 | 设施 | 特殊分区",
        ]
        for p in self.parcels.values():
            flags = ",".join(k for k, v in p.zone_flags.items() if v) or "-"
            fac = ",".join(p.facilities) or "-"
            lines.append(
                f"{p.pid} | {p.landuse}({LANDUSE_CODES[p.landuse]}) | {p.far:.1f} | "
                f"{p.height:.0f} | {p.green_ratio:.2f} | {p.area:.0f} | {fac} | {flags}"
            )
        return "\n".join(lines)

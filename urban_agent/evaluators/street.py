"""道路与慢行维度评估器。

这个评估器服务于“建筑 footprint 当作地块”的测试场景：
道路断面变宽后，沿路建筑边界需要向内退让，形成真实的空间更新效果。
"""
from __future__ import annotations

from ..schema import Plan
from .base import BaseEvaluator, SoftScore, Violation

MIN_SIDEWALK = 1.5
TARGET_SIDEWALK = 3.0
TARGET_BIKE_LANE = 1.5
TARGET_LOCAL_ROAD = 8.0


def _frontage_parcels(plan: Plan):
    return [p for p in plan.parcels.values() if p.zone_flags.get("building_as_parcel")]


class StreetEvaluator(BaseEvaluator):
    dimension = "道路与慢行"
    weight = 0.5

    def check_hard(self, plan: Plan) -> list[Violation]:
        violations = []
        for p in _frontage_parcels(plan):
            flags = p.zone_flags
            road_width = float(flags.get("road_width_m") or 0)
            sidewalk_width = float(flags.get("sidewalk_width_m") or 0)
            bike_lane_width = float(flags.get("bike_lane_width_m") or 0)
            if road_width < 4 or road_width > 24:
                violations.append(Violation(
                    rule_id="street-H1",
                    parcel_id=p.pid,
                    message=f"{p.pid} 道路宽度 {road_width:.1f}m 超出合理范围 4-24m",
                    fix={"name": "set_street_profile",
                         "params": {"parcel_id": p.pid, "road_width": min(max(road_width, 4), 24),
                                    "sidewalk_width": max(sidewalk_width, MIN_SIDEWALK),
                                    "bike_lane_width": max(bike_lane_width, 0)},
                         "reason": "道路断面宽度校准到合理范围（street-H1）"},
                ))
            if sidewalk_width < MIN_SIDEWALK:
                violations.append(Violation(
                    rule_id="street-H2",
                    parcel_id=p.pid,
                    message=f"{p.pid} 人行道宽度 {sidewalk_width:.1f}m 低于最低通行宽度 {MIN_SIDEWALK:.1f}m",
                    fix={"name": "set_street_profile",
                         "params": {"parcel_id": p.pid, "road_width": max(road_width, TARGET_LOCAL_ROAD),
                                    "sidewalk_width": MIN_SIDEWALK,
                                    "bike_lane_width": bike_lane_width},
                         "reason": "补足最低人行通行宽度（street-H2）"},
                ))
        return violations

    def score_soft(self, plan: Plan) -> SoftScore:
        parcels = _frontage_parcels(plan)
        if not parcels:
            return SoftScore(
                score=100.0,
                evidence="当前方案不是建筑 footprint 测试数据，未启用道路与慢行评估",
                suggestions=[],
            )

        sidewalk_scores = []
        bike_scores = []
        conflicts = 0
        candidates = []
        road_groups = {}
        for p in parcels:
            flags = p.zone_flags
            road_width = float(flags.get("road_width_m") or 0)
            sidewalk_width = float(flags.get("sidewalk_width_m") or 0)
            bike_lane_width = float(flags.get("bike_lane_width_m") or 0)
            frontage_distance = float(flags.get("frontage_distance_m") or 999)
            road_id = str(flags.get("nearest_road_id") or "")
            corridor = road_width / 2 + sidewalk_width + bike_lane_width
            if frontage_distance < corridor:
                conflicts += 1
            sidewalk_scores.append(min(100.0, 100.0 * sidewalk_width / TARGET_SIDEWALK))
            bike_scores.append(min(100.0, 100.0 * bike_lane_width / TARGET_BIKE_LANE))
            urgency = (
                max(0.0, TARGET_SIDEWALK - sidewalk_width) * 3
                + max(0.0, TARGET_BIKE_LANE - bike_lane_width) * 2
                + max(0.0, 12 - frontage_distance)
            )
            if urgency > 0 and not p.flag("historic"):
                candidates.append((urgency, frontage_distance, p))
                if road_id:
                    group = road_groups.setdefault(road_id, {"urgency": 0.0, "count": 0, "road_width": road_width})
                    group["urgency"] += urgency
                    group["count"] += 1

        walk_score = sum(sidewalk_scores) / len(sidewalk_scores)
        bike_score = sum(bike_scores) / len(bike_scores)
        clearance_score = 100.0 * (1 - conflicts / len(parcels))
        score = 0.45 * walk_score + 0.35 * bike_score + 0.20 * clearance_score

        suggestions = []
        if road_groups:
            road_id, group = max(road_groups.items(), key=lambda item: item[1]["urgency"])
            suggestions.append({
                "name": "set_road_profile",
                "params": {
                    "road_id": road_id,
                    "road_width": max(float(group["road_width"]), TARGET_LOCAL_ROAD),
                    "sidewalk_width": TARGET_SIDEWALK,
                    "bike_lane_width": TARGET_BIKE_LANE,
                    "max_parcels": min(12, max(4, int(group["count"]))),
                },
                "reason": (
                    f"{road_id} 沿线慢行空间短板集中，按道路整体拓宽人行与骑行空间，"
                    "批量推动沿路建筑边界退让"
                ),
            })
        for _, _, p in sorted(candidates, key=lambda x: (-x[0], x[1]))[:4]:
            flags = p.zone_flags
            current_road = float(flags.get("road_width_m") or TARGET_LOCAL_ROAD)
            suggestions.append({
                "name": "set_street_profile",
                "params": {
                    "parcel_id": p.pid,
                    "road_width": max(current_road, TARGET_LOCAL_ROAD),
                    "sidewalk_width": TARGET_SIDEWALK,
                    "bike_lane_width": TARGET_BIKE_LANE,
                },
                "reason": (
                    f"{p.pid} 沿路慢行空间不足或贴路过近，拓宽人行道并预留骑行空间，"
                    "同时触发建筑边界退让"
                ),
            })

        avg_sidewalk = sum(float(p.zone_flags.get("sidewalk_width_m") or 0) for p in parcels) / len(parcels)
        avg_bike = sum(float(p.zone_flags.get("bike_lane_width_m") or 0) for p in parcels) / len(parcels)
        evidence = (
            f"平均人行道 {avg_sidewalk:.1f}m（目标 {TARGET_SIDEWALK:.1f}m），"
            f"平均骑行空间 {avg_bike:.1f}m（目标 {TARGET_BIKE_LANE:.1f}m），"
            f"道路断面与建筑距离冲突 {conflicts}/{len(parcels)} 处"
        )
        return SoftScore(score=score, evidence=evidence, suggestions=suggestions)

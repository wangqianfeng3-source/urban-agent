"""能源与碳维度评估器（C1 组）—— 骨架实现，暑期逐步替换为真实模型。

现状（骨架）：排放 = Σ 建筑面积 × 用地类型排放因子 − 绿地碳汇。
暑期目标：接入刘超老师组的分部门碳核算方法（建筑/交通/废弃物/碳汇），
排放因子改用杨浦区实测标定值，评分基准改用控规目标值。
"""
from __future__ import annotations

from ..schema import Plan
from .base import BaseEvaluator, SoftScore, Violation

# 排放因子 kg CO2 / (m2 建筑面积 · 年) —— 骨架用的量级示意值，暑期必须标定
EMISSION_FACTORS = {"M": 80.0, "C": 60.0, "T": 40.0, "R": 30.0, "A": 25.0, "G": 0.0}
GREEN_SINK = 2.0          # 碳汇 kg CO2 / (m2 绿地 · 年)
TARGET_REDUCTION = 0.25   # 相对基准方案减排 25% 记满分


def annual_emission(plan: Plan) -> float:
    """方案年碳排放（kg CO2 / 年）= 排放 − 碳汇。"""
    emit = sum(p.floor_area * EMISSION_FACTORS[p.landuse] for p in plan.parcels.values())
    sink = sum(p.area * p.green_ratio * GREEN_SINK for p in plan.parcels.values())
    return emit - sink


class CarbonEvaluator(BaseEvaluator):
    dimension = "能源与碳"
    weight = 0.5

    def __init__(self, baseline: Plan):
        # 以现状方案为减排基准
        self.baseline_emission = annual_emission(baseline)

    # ---------- 硬约束（红线） ----------
    def check_hard(self, plan: Plan) -> list[Violation]:
        violations = []
        for p in plan.parcels.values():
            if p.flag("zero_carbon") and p.landuse == "M":
                violations.append(Violation(
                    rule_id="carbon-H1",
                    parcel_id=p.pid,
                    message=f"地块 {p.pid} 位于零碳示范区，禁止工业用地（现为 M）",
                    fix={"name": "set_landuse",
                         "params": {"parcel_id": p.pid, "landuse": "T"},
                         "reason": "零碳示范区工业用地向科创研发转型（carbon-H1）"},
                ))
            if p.flag("zero_carbon") and p.far > 2.5:
                violations.append(Violation(
                    rule_id="carbon-H2",
                    parcel_id=p.pid,
                    message=f"地块 {p.pid} 位于零碳示范区，容积率 {p.far} 超过上限 2.5",
                    fix={"name": "adjust_far",
                         "params": {"parcel_id": p.pid, "delta": round(2.5 - p.far, 2)},
                         "reason": "零碳示范区容积率压回 2.5（carbon-H2）"},
                ))
        return violations

    # ---------- 软质量 ----------
    def score_soft(self, plan: Plan) -> SoftScore:
        current = annual_emission(plan)
        reduction = (self.baseline_emission - current) / self.baseline_emission
        score = max(0.0, min(100.0, 100.0 * reduction / TARGET_REDUCTION))

        suggestions = []
        # 建议一（最有效）：把排放最高的工业/商业地块转为科创研发，直接削减排放源
        convertible = sorted(
            (p for p in plan.parcels.values()
             if p.landuse in ("M", "C") and not p.flag("historic")),
            key=lambda p: p.floor_area * EMISSION_FACTORS[p.landuse], reverse=True,
        )
        for p in convertible[:2]:
            suggestions.append({
                "name": "set_landuse",
                "params": {"parcel_id": p.pid, "landuse": "T"},
                "reason": f"{p.pid} 为高排放{'工业' if p.landuse == 'M' else '商业'}地块，转为科创研发以削减排放源",
            })
        # 建议二：按排放量从高到低，给绿地率偏低的地块增加绿化（增汇）
        greenable = sorted(
            (p for p in plan.parcels.values() if p.green_ratio < 0.35 and p.landuse != "G"),
            key=lambda p: p.floor_area * EMISSION_FACTORS[p.landuse], reverse=True,
        )
        suggestions += [{
            "name": "set_green_ratio",
            "params": {"parcel_id": p.pid, "ratio": round(p.green_ratio + 0.10, 2)},
            "reason": f"{p.pid} 为高排放地块，提高绿地率以增加碳汇",
        } for p in greenable[:3]]

        evidence = (
            f"基准排放 {self.baseline_emission / 1000:.0f} tCO2/年，"
            f"当前 {current / 1000:.0f} tCO2/年，减排 {reduction * 100:.1f}%"
            f"（目标 {TARGET_REDUCTION * 100:.0f}% 记满分）"
        )
        return SoftScore(score=score, evidence=evidence, suggestions=suggestions)

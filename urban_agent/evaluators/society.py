"""人和社会维度评估器（C2 组）—— 骨架实现。

现状（骨架）：功能混合度（香农熵）+ 设施多样性两个代理指标。
暑期目标：落地立项 PPT 第 25 页的六维评分
（活动承载力 / 视觉吸引 / Citywalk 友好度 / 空间弹性 / 公共服务 / 规划要求），
其中活动承载力先用 POI 密度 + 路网中心性做代理，不做真 ABM。
"""
from __future__ import annotations

import math

from ..schema import Plan
from .base import BaseEvaluator, SoftScore, Violation

MIN_RESIDENTIAL_GREEN = 0.25   # 居住地块绿地率下限（规范值，暑期按控规校准）
FACILITY_CATALOG = ["community_center", "elderly_care", "library", "gym"]
MIX_LANDUSES = ("R", "C", "T", "A")   # 参与混合度计算的用地类型


class SocietyEvaluator(BaseEvaluator):
    dimension = "人和社会"
    weight = 0.5

    # ---------- 硬约束（红线） ----------
    def check_hard(self, plan: Plan) -> list[Violation]:
        violations = []
        for p in plan.parcels.values():
            if p.landuse == "R" and p.green_ratio < MIN_RESIDENTIAL_GREEN:
                violations.append(Violation(
                    rule_id="society-H1",
                    parcel_id=p.pid,
                    message=(f"居住地块 {p.pid} 绿地率 {p.green_ratio:.2f} "
                             f"低于规范下限 {MIN_RESIDENTIAL_GREEN}"),
                    fix={"name": "set_green_ratio",
                         "params": {"parcel_id": p.pid, "ratio": MIN_RESIDENTIAL_GREEN},
                         "reason": "居住地块绿地率补足到规范下限（society-H1）"},
                ))
        return violations

    # ---------- 软质量 ----------
    def score_soft(self, plan: Plan) -> SoftScore:
        # 1) 功能混合度：对 R/C/T/A 四类用地面积占比求香农熵，归一化到 0-100
        areas = plan.area_by_landuse()
        mix = [areas.get(k, 0.0) for k in MIX_LANDUSES]
        total = sum(mix)
        entropy = 0.0
        if total > 0:
            for a in mix:
                if a > 0:
                    share = a / total
                    entropy -= share * math.log(share)
        mix_score = 100.0 * entropy / math.log(len(MIX_LANDUSES))

        # 2) 设施多样性：全片区已配置的设施种类 / 目录种类
        present = {f for p in plan.parcels.values() for f in p.facilities
                   if f in FACILITY_CATALOG}
        diversity_score = 100.0 * len(present) / len(FACILITY_CATALOG)

        score = 0.6 * mix_score + 0.4 * diversity_score

        # 建议：把缺失的设施类型逐个配到还没有配套的居住地块，最多 3 条
        missing = [f for f in FACILITY_CATALOG if f not in present]
        bare = [p for p in plan.parcels.values() if p.landuse == "R" and not p.facilities]
        suggestions = [{
            "name": "add_facility",
            "params": {"parcel_id": p.pid, "facility": f},
            "reason": f"补齐片区缺失的设施类型 {f}，提升全龄友好度",
        } for p, f in zip(bare, missing)][:3]

        evidence = (f"功能混合度 {mix_score:.0f}/100（R/C/T/A 熵值），"
                    f"设施多样性 {diversity_score:.0f}/100（{len(present)}/{len(FACILITY_CATALOG)} 类）")
        return SoftScore(score=score, evidence=evidence, suggestions=suggestions)

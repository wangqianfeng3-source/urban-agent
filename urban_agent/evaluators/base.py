"""评估器统一接口 —— 所有维度组必须实现的契约（v1.0）。

双轨评测（对应立项 PPT 第 27 页）：
  - check_hard()：硬约束（红线），纯代码校验，违反即一票否决；
  - score_soft()：软质量（好坏），0-100 分 + 证据 + 机器可执行的改进建议。

关键设计：反馈必须是结构化、机器可执行的 ——
Violation.fix 和 SoftScore.suggestions 都是动作字典（见 actions.py），
编辑智能体可以直接执行，这是闭环能收敛的前提。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import Plan


@dataclass
class Violation:
    """一条硬约束违规。"""

    rule_id: str          # 规则编号，如 carbon-H1
    parcel_id: str | None # 涉及地块；片区级规则填 None
    message: str          # 中文说明：违反了什么、差多少
    fix: dict | None = None  # 机器可执行的修复动作（action 字典），可为 None


@dataclass
class SoftScore:
    """一个维度的软质量得分。"""

    score: float                      # 0-100
    evidence: str                     # 打分依据（写进报告，供规划师审查）
    suggestions: list = field(default_factory=list)  # 改进建议 = action 字典列表


@dataclass
class EvalResult:
    dimension: str
    weight: float
    violations: list = field(default_factory=list)
    soft: SoftScore | None = None


class BaseEvaluator:
    """维度评估器基类。每个维度组继承并实现两个方法。"""

    dimension: str = "base"
    weight: float = 0.0

    def check_hard(self, plan: Plan) -> list[Violation]:
        raise NotImplementedError

    def score_soft(self, plan: Plan) -> SoftScore:
        raise NotImplementedError

    def evaluate(self, plan: Plan) -> EvalResult:
        return EvalResult(
            dimension=self.dimension,
            weight=self.weight,
            violations=self.check_hard(plan),
            soft=self.score_soft(plan),
        )


def aggregate(results: list[EvalResult]) -> dict:
    """汇总各维度结果。任一硬违规 → 总分归零（一票否决）。"""
    violations = [v for r in results for v in r.violations]
    weight_sum = sum(r.weight for r in results) or 1.0
    weighted = sum(r.weight * r.soft.score for r in results) / weight_sum
    total = 0.0 if violations else round(weighted, 1)
    return {
        "total": total,
        "hard_pass": not violations,
        "violation_count": len(violations),
        "scores": {r.dimension: round(r.soft.score, 1) for r in results},
    }

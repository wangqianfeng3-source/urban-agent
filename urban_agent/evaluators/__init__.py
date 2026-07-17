"""维度评估器注册表。

暑期聚焦 2 个维度（能源与碳、人和社会）做实；其余 6 个维度（公共服务 /
交通出行 / 产业创新 / 历史文化 / 基础设施 / 生态安全）预留接口位，
按 docs/evaluator_interface.md 的清单新增即可，框架无需改动。
"""
from ..schema import Plan
from .base import BaseEvaluator, EvalResult, SoftScore, Violation, aggregate
from .carbon import CarbonEvaluator
from .society import SocietyEvaluator
from .street import StreetEvaluator


def default_evaluators(baseline: Plan) -> list[BaseEvaluator]:
    """夏令营默认的两维度评估器组合。"""
    return [
        CarbonEvaluator(baseline=baseline),
        SocietyEvaluator(),
        StreetEvaluator(),
    ]


__all__ = [
    "BaseEvaluator", "EvalResult", "SoftScore", "Violation", "aggregate",
    "CarbonEvaluator", "SocietyEvaluator", "StreetEvaluator",
    "default_evaluators",
]

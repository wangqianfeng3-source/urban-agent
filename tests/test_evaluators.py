from pathlib import Path

import pytest

from urban_agent.evaluators import (
    CarbonEvaluator,
    SocietyEvaluator,
    aggregate,
    default_evaluators,
)
from urban_agent.schema import Plan

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "fuxing_demo.geojson"


@pytest.fixture
def plan():
    return Plan.from_geojson(SAMPLE)


def test_initial_violations(plan):
    carbon = CarbonEvaluator(baseline=plan)
    ids = sorted(v.rule_id for v in carbon.check_hard(plan))
    assert ids == ["carbon-H1", "carbon-H2"]  # P01 零碳区工业 + P04 容积率超限

    assert [v.rule_id for v in SocietyEvaluator().check_hard(plan)] == ["society-H1"]


def test_two_default_evaluators(plan):
    evs = default_evaluators(baseline=plan)
    assert [e.dimension for e in evs] == ["能源与碳", "人和社会"]
    assert abs(sum(e.weight for e in evs) - 1.0) < 1e-6  # 权重之和为 1


def test_fix_is_executable(plan):
    """每条硬违规都必须附带机器可执行的修复动作 —— 闭环收敛的前提。"""
    for ev in default_evaluators(baseline=plan):
        for v in ev.check_hard(plan):
            assert v.fix is not None and "name" in v.fix and "params" in v.fix


def test_veto_zeroes_total(plan):
    results = [e.evaluate(plan) for e in default_evaluators(baseline=plan)]
    agg = aggregate(results)
    assert not agg["hard_pass"]
    assert agg["total"] == 0.0  # 一票否决


def test_baseline_carbon_score_is_zero(plan):
    carbon = CarbonEvaluator(baseline=plan)
    assert carbon.score_soft(plan).score == 0.0  # 相对自身无减排

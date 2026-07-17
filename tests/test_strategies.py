from pathlib import Path

from urban_agent.editor import MockEditor
from urban_agent.evaluators import default_evaluators
from urban_agent.intake import apply_objectives, parse_requirement_text
from urban_agent.loop import LoopConfig, run_loop
from urban_agent.schema import Plan
from urban_agent.strategies import STRATEGIES, get_strategy

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "fuxing_demo.geojson"


def _fresh():
    baseline = Plan.from_geojson(SAMPLE)
    return baseline, Plan.from_geojson(SAMPLE)


def test_registry_integrity():
    assert len(STRATEGIES) >= 5
    for st in STRATEGIES.values():
        assert st.key and st.name and st.description and st.prompt_hint


def test_conservative_avoids_landuse_in_soft_phase(tmp_path):
    """保守渐进：硬违规仍可改用地（合规优先），软优化阶段不动用地性质。"""
    baseline, plan = _fresh()
    st = get_strategy("conservative")
    result = run_loop(plan, default_evaluators(baseline=baseline), MockEditor(strategy=st),
                      LoopConfig(max_iters=8, max_actions_per_iter=1), out_dir=tmp_path)
    for entry in result.history:
        if not entry["violations"]:  # 无硬违规后的轮次 = 软优化阶段
            for a in entry["actions"]:
                assert "用地" not in a["desc"] or not a["ok"] or "设施" in a["desc"]


def test_strategy_reorders_soft_suggestions():
    """低碳策略下，碳维度建议优先于其他维度（即使碳的分数不是最低）。"""
    baseline, plan = _fresh()
    evaluators = default_evaluators(baseline=baseline)
    results = [e.evaluate(plan) for e in evaluators]
    editor = MockEditor(strategy=get_strategy("low_carbon"))
    order = [r.dimension for r in editor._soft_order(results)]
    assert order[0] == "能源与碳"


def test_intake_keyword_mapping():
    obj = parse_requirement_text("打造零碳示范街区，同时提升街区活力")
    assert "low_carbon" in obj.strategy_keys
    assert "vitality" in obj.strategy_keys
    assert obj.dimension_weights.get("能源与碳", 0) > 1.0
    assert obj.dimension_weights.get("人和社会", 0) > 1.0


def test_apply_objectives_renormalizes():
    baseline, _ = _fresh()
    evaluators = default_evaluators(baseline=baseline)
    obj = parse_requirement_text("低碳更新")
    apply_objectives(evaluators, obj)
    assert abs(sum(e.weight for e in evaluators) - 1.0) < 1e-6
    carbon = next(e for e in evaluators if e.dimension == "能源与碳")
    assert carbon.weight > 0.4  # 相对默认权重被上调

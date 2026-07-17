from pathlib import Path

from urban_agent.editor import MockEditor
from urban_agent.evaluators import default_evaluators
from urban_agent.loop import LoopConfig, run_loop
from urban_agent.schema import Plan

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "fuxing_demo.geojson"


def test_loop_converges(tmp_path):
    baseline = Plan.from_geojson(SAMPLE)
    plan = Plan.from_geojson(SAMPLE)
    result = run_loop(
        plan,
        default_evaluators(baseline=baseline),
        MockEditor(),
        LoopConfig(max_iters=8, target_score=75.0),
        out_dir=tmp_path,
    )
    assert result.status == "passed"
    assert len(result.history) <= 8
    # 达标那一轮：无硬违规且总分过线
    last = result.history[-1]
    assert last["hard_pass"] and last["total"] >= 75.0
    # 产物落盘
    assert (tmp_path / "history.json").exists()
    assert list(tmp_path.glob("plan_v*.geojson"))


def test_loop_stalls_safely(tmp_path):
    """编辑器无动作可提时，循环必须安全终止而不是死循环。"""

    class NoopEditor(MockEditor):
        def propose(self, plan, results, max_actions):
            return []

    baseline = Plan.from_geojson(SAMPLE)
    plan = Plan.from_geojson(SAMPLE)
    result = run_loop(plan, default_evaluators(baseline=baseline), NoopEditor(),
                      LoopConfig(max_iters=8), out_dir=tmp_path)
    assert result.status == "stalled"
    assert len(result.history) == 1

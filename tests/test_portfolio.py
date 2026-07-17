import json
from pathlib import Path

from urban_agent.portfolio import run_portfolio

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "fuxing_demo.geojson"


def test_portfolio_ranks_and_reports(tmp_path):
    items = run_portfolio(SAMPLE, tmp_path, editor_kind="mock")
    assert len(items) == 5
    # 排名：合规优先，其次总分降序
    keys = [(it.hard_pass, it.total) for it in items]
    assert keys == sorted(keys, reverse=True)
    assert items[0].rank == 1 and items[0].hard_pass
    # 三类产物落盘
    assert (tmp_path / "report.md").exists()
    data = json.loads((tmp_path / "portfolio.json").read_text(encoding="utf-8"))
    assert len(data) == 5
    for it in items:
        assert Path(it.plan_path).exists()
    # 报告里有"给规划师选择"的措辞与全部策略名
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "请规划师从中选择" in report
    for it in items:
        assert it.strategy.name in report


def test_portfolio_strategy_choice_matters(tmp_path):
    """策略选择应带来可见差异：并非所有策略都能达标——这是比选价值的来源。

    玩具数据只有 10 个地块，允许改用地的策略会收敛到同一最优点；
    真正的信息量在于“保守渐进（拒绝改用地）够不到达标线”这类差异。
    """
    items = run_portfolio(SAMPLE, tmp_path, editor_kind="mock")
    # 至少两种不同的终版方案与两档不同的得分
    contents = {Path(it.plan_path).read_text(encoding="utf-8") for it in items}
    assert len(contents) >= 2
    assert len({round(it.total, 1) for it in items}) >= 2
    # 保守渐进（避免改用地）应明显低于榜首——策略选择实打实影响结果
    conservative = next(it for it in items if it.strategy.key == "conservative")
    top = items[0]
    assert conservative.total < top.total - 10

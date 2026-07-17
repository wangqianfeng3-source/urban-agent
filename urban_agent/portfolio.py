"""Top-N 方案比选 —— 用不同策略卡各跑一遍闭环，产出排序后的方案组合与比选报告。

多方案比选是规划实务的标准动作。这里的实现刻意朴素：
每张策略卡一条独立轨迹（同一份现状、同一套评估器），排序只看
（是否合规，综合得分），并如实记录未达标的策略——"哪条路走不通"本身就是信息。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .editor import LLMEditor, MockEditor
from .evaluators import default_evaluators
from .evaluators.base import aggregate
from .loop import LoopConfig, run_loop
from .report import STATUS_LABEL, _diff
from .schema import Plan
from .strategies import STRATEGIES, Strategy


@dataclass
class PortfolioItem:
    rank: int
    strategy: Strategy
    status: str
    hard_pass: bool
    total: float
    scores: dict
    iterations: int
    changes: list = field(default_factory=list)   # 关键修改（初版→终版 diff 行）
    plan_path: str = ""


def run_portfolio(data_path: str | Path, out_dir: str | Path,
                  editor_kind: str = "mock", target: float = 75.0,
                  max_iters: int = 8,
                  strategies: list[Strategy] | None = None) -> list[PortfolioItem]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    strategies = strategies or list(STRATEGIES.values())

    items: list[PortfolioItem] = []
    for st in strategies:
        baseline = Plan.from_geojson(data_path)
        plan = Plan.from_geojson(data_path)
        evaluators = default_evaluators(baseline=baseline)
        editor = (LLMEditor(strategy=st) if editor_kind == "llm"
                  else MockEditor(strategy=st))
        config = LoopConfig(max_iters=max_iters, target_score=target,
                            max_actions_per_iter=1 if st.light_touch else 2)
        result = run_loop(plan, evaluators, editor, config, out_dir=out / st.key)

        # 终版方案重新评估（max_iters 终止时 history 末轮评估先于最后一批动作）
        final_results = [e.evaluate(result.final_plan) for e in evaluators]
        agg = aggregate(final_results)
        plan_file = out / f"plan_{st.key}.geojson"
        result.final_plan.to_geojson(plan_file)
        items.append(PortfolioItem(
            rank=0, strategy=st, status=result.status,
            hard_pass=agg["hard_pass"], total=agg["total"], scores=agg["scores"],
            iterations=len(result.history),
            changes=[c.strip("| ").replace(" | ", "：") for c in _diff(baseline, result.final_plan)],
            plan_path=str(plan_file),
        ))

    items.sort(key=lambda it: (it.hard_pass, it.total), reverse=True)
    for i, it in enumerate(items, 1):
        it.rank = i
    _write_report(items, out)
    (out / "portfolio.json").write_text(json.dumps([{
        "rank": it.rank, "strategy": it.strategy.key, "name": it.strategy.name,
        "status": it.status, "hard_pass": it.hard_pass, "total": it.total,
        "scores": it.scores, "iterations": it.iterations, "plan": it.plan_path,
    } for it in items], ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def _write_report(items: list[PortfolioItem], out: Path) -> None:
    dims = list(items[0].scores) if items else []
    lines = [
        "# 方案比选报告（Top {}）".format(len(items)),
        "",
        "同一现状、同一评估体系，按不同规划策略各自迭代后的方案组合。",
        "**请规划师从中选择**，选中的方案可载入工作台继续人机共创。",
        "",
        "| 排名 | 策略 | 状态 | " + " | ".join(dims) + " | 综合得分 | 轮数 |",
        "|---|---|---|" + "---|" * (len(dims) + 2),
    ]
    for it in items:
        scores = " | ".join(f"{it.scores[d]:.1f}" for d in dims)
        total = f"**{it.total:.1f}**" if it.hard_pass else "0（存在硬违规）"
        lines.append(f"| {it.rank} | {it.strategy.name} | {STATUS_LABEL[it.status]} | "
                     f"{scores} | {total} | {it.iterations} |")

    for it in items:
        lines += [
            "",
            f"## 第 {it.rank} 名 · {it.strategy.name}",
            "",
            f"- **适用情形**：{it.strategy.description}",
            f"- **方案文件**：`{Path(it.plan_path).name}`",
            "- **相对现状的关键修改**：",
        ]
        lines += [f"  - {c}" for c in it.changes] or ["  - 无修改"]
    lines += ["", "> 未达标的策略也如实列出——“哪条路在本片区走不通”同样是比选信息。", ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")

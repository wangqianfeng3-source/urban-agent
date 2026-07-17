"""端到端演示：python -m urban_agent.demo

在复兴岛玩具数据上跑通 改→评→改 闭环，产出：
  runs/demo/history.json        每轮的分数、违规、动作（结构化日志）
  runs/demo/plan_v*.geojson     每一版方案快照
  runs/demo/report.md           给规划师看的迭代报告

默认使用 MockEditor（无需 API key）；--editor llm 切换到大模型编辑器。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .editor import LLMEditor, MockEditor
from .evaluators import default_evaluators
from .loop import LoopConfig, LoopResult, run_loop
from .report import STATUS_LABEL, render_report
from .schema import Plan

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample" / "fuxing_demo.geojson"


def main() -> LoopResult:
    ap = argparse.ArgumentParser(description="城市更新智能体闭环 demo")
    ap.add_argument("--editor", choices=["mock", "llm"], default="mock")
    ap.add_argument("--target", type=float, default=75.0)
    ap.add_argument("--max-iters", type=int, default=8)
    ap.add_argument("--data", default=str(SAMPLE))
    ap.add_argument("--out", default=str(ROOT / "runs" / "demo"))
    args = ap.parse_args()

    initial = Plan.from_geojson(args.data)
    plan = Plan.from_geojson(args.data)
    evaluators = default_evaluators(baseline=initial)
    editor = MockEditor() if args.editor == "mock" else LLMEditor()
    config = LoopConfig(max_iters=args.max_iters, target_score=args.target)

    print(f"输入方案：{initial.site}，{len(initial.parcels)} 个地块")
    result = run_loop(plan, evaluators, editor, config, out_dir=args.out)
    render_report(initial, result, Path(args.out) / "report.md")

    print(f"\n{'轮次':<4}{'硬违规':<6}{'综合得分':<10}本轮修改")
    for h in result.history:
        acts = "；".join(a["desc"] for a in h["actions"]) or "—"
        total = f"{h['total']:.1f}" if h["hard_pass"] else "0(否决)"
        print(f"{h['iteration']:<5}{len(h['violations']):<8}{total:<11}{acts}")
    print(f"\n结果：{STATUS_LABEL[result.status]}")
    print(f"产出目录：{args.out}/（history.json、plan_v*.geojson、report.md）")
    return result


if __name__ == "__main__":
    main()

"""方案比选 CLI：python -m urban_agent.compare [--require "更新需求一句话"]

对策略库全量（或需求匹配到的子集）各跑一遍闭环，输出 Top-N 比选报告：
  runs/portfolio/report.md        给规划师看的比选报告
  runs/portfolio/portfolio.json   结构化排名（工作台比选页也读它）
  runs/portfolio/plan_*.geojson   各策略的终版方案
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .intake import parse_requirement_text
from .portfolio import run_portfolio
from .strategies import STRATEGIES

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample" / "fuxing_demo.geojson"


def main():
    ap = argparse.ArgumentParser(description="城市更新方案 Top-N 比选")
    ap.add_argument("--data", default=str(SAMPLE))
    ap.add_argument("--editor", choices=["mock", "llm"], default="mock")
    ap.add_argument("--target", type=float, default=75.0)
    ap.add_argument("--max-iters", type=int, default=8)
    ap.add_argument("--require", default="", help="自然语言更新需求（Phase 0 解析出候选策略）")
    ap.add_argument("--out", default=str(ROOT / "runs" / "portfolio"))
    args = ap.parse_args()

    strategies = list(STRATEGIES.values())
    if args.require:
        obj = parse_requirement_text(args.require)
        if obj.strategy_keys:
            picked = [STRATEGIES[k] for k in obj.strategy_keys]
            rest = [s for s in strategies if s.key not in obj.strategy_keys]
            strategies = picked + rest   # 需求匹配的策略排前面，全库仍参与比选
            print(f"需求解析：候选策略 {[s.name for s in picked]}")

    items = run_portfolio(args.data, args.out, editor_kind=args.editor,
                          target=args.target, max_iters=args.max_iters,
                          strategies=strategies)

    print(f"\n{'排名':<4}{'策略':<8}{'合规':<6}{'综合得分':<10}{'轮数':<4}")
    for it in items:
        total = f"{it.total:.1f}" if it.hard_pass else "0(违规)"
        print(f"{it.rank:<5}{it.strategy.name:<9}{'✓' if it.hard_pass else '✗':<7}{total:<11}{it.iterations}")
    print(f"\n比选报告：{args.out}/report.md")
    return items


if __name__ == "__main__":
    main()

"""智能体循环 —— 改→评→改 的调度器（对应立项 PPT 第 5 页闭环图）。

刻意保持简单：一个 while 循环 + 三个终止条件（达标 / 停滞 / 超轮数）。
每轮的输入输出全部落盘（history.json + 每版方案快照），可复现、可审计。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .actions import ActionError, apply_action
from .editor import BaseEditor
from .evaluators.base import BaseEvaluator, aggregate
from .schema import Plan


@dataclass
class LoopConfig:
    max_iters: int = 8            # 最大迭代轮数
    target_score: float = 75.0    # 综合得分达标线
    max_actions_per_iter: int = 2 # 每轮最多动作数（“局部修改”原则）


@dataclass
class LoopResult:
    status: str                   # passed / stalled / max_iters
    final_plan: Plan | None = None
    history: list = field(default_factory=list)


def run_loop(plan: Plan, evaluators: list[BaseEvaluator], editor: BaseEditor,
             config: LoopConfig | None = None,
             out_dir: str | Path | None = None) -> LoopResult:
    config = config or LoopConfig()
    out = Path(out_dir) if out_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
        plan.to_geojson(out / f"plan_v{plan.version}.geojson")

    result = LoopResult(status="max_iters")
    for it in range(1, config.max_iters + 1):
        # ---- 评 ----
        evals = [e.evaluate(plan) for e in evaluators]
        agg = aggregate(evals)
        entry = {
            "iteration": it,
            "plan_version": plan.version,
            "total": agg["total"],
            "hard_pass": agg["hard_pass"],
            "scores": agg["scores"],
            "violations": [
                {"rule": v.rule_id, "parcel": v.parcel_id, "message": v.message}
                for r in evals for v in r.violations
            ],
            "evidence": {r.dimension: r.soft.evidence for r in evals},
            "actions": [],
        }

        passed = agg["hard_pass"] and agg["total"] >= config.target_score
        if passed:
            result.history.append(entry)
            result.status = "passed"
            break

        # ---- 改 ----
        actions = editor.propose(plan, evals, config.max_actions_per_iter)
        if not actions:
            result.history.append(entry)
            result.status = "stalled"
            break

        plan = plan.copy()
        for a in actions:
            try:
                desc = apply_action(plan, a)
                entry["actions"].append({"ok": True, "desc": desc,
                                         "reason": a.get("reason", "")})
            except ActionError as e:
                entry["actions"].append({"ok": False, "desc": str(e),
                                         "reason": a.get("reason", "")})
        result.history.append(entry)
        if out:
            plan.to_geojson(out / f"plan_v{plan.version}.geojson")

    result.final_plan = plan
    if out:
        (out / "history.json").write_text(
            json.dumps(result.history, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

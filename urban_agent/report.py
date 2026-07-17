"""迭代报告生成 —— 把一次闭环运行渲染成给规划师看的 Markdown 报告。

报告是“AI 出建议、规划师拍板”协作模式的载体：
规划师不看代码、不看 JSON，只看这份报告即可完成专业确认。
"""
from __future__ import annotations

from pathlib import Path

from .loop import LoopResult
from .schema import LANDUSE_CODES, Plan

STATUS_LABEL = {
    "passed": "✅ 达标通过",
    "stalled": "⏸ 评分停滞（建议规划师介入）",
    "max_iters": "⚠️ 达到最大轮数仍未达标（建议规划师介入）",
    "in_progress": "🔄 迭代进行中",
}


def _diff(initial: Plan, final: Plan) -> list[str]:
    rows = []
    for pid, p0 in initial.parcels.items():
        p1 = final.parcels[pid]
        changes = []
        if p0.landuse != p1.landuse:
            changes.append(f"用地 {p0.landuse}({LANDUSE_CODES[p0.landuse]})→"
                           f"{p1.landuse}({LANDUSE_CODES[p1.landuse]})")
        if p0.far != p1.far:
            changes.append(f"容积率 {p0.far:.2f}→{p1.far:.2f}")
        if p0.green_ratio != p1.green_ratio:
            changes.append(f"绿地率 {p0.green_ratio:.2f}→{p1.green_ratio:.2f}")
        added = [f for f in p1.facilities if f not in p0.facilities]
        if added:
            changes.append(f"新增设施 {'、'.join(added)}")
        if changes:
            rows.append(f"| {pid} | {'；'.join(changes)} |")
    return rows


def report_markdown(initial: Plan, result: LoopResult) -> str:
    lines = [
        f"# 城市更新方案迭代报告 —— {initial.site}",
        "",
        f"- 运行状态：**{STATUS_LABEL[result.status]}**",
        f"- 迭代轮数：{len(result.history)}",
        f"- 方案版本：v{initial.version} → v{result.final_plan.version}",
        "",
        "## 迭代过程",
        "",
        "| 轮次 | 硬违规 | " + " | ".join(result.history[0]["scores"]) + " | 综合得分 | 本轮修改 |",
        "|---|---|" + "---|" * (len(result.history[0]["scores"]) + 2),
    ]
    for h in result.history:
        acts = "<br>".join(
            ("✓ " if a["ok"] else "✗ ") + a["desc"] for a in h["actions"]) or "—"
        scores = " | ".join(f"{v:.1f}" for v in h["scores"].values())
        total = f"**{h['total']:.1f}**" if h["hard_pass"] else "0（一票否决）"
        lines.append(f"| {h['iteration']} | {len(h['violations'])} | {scores} | {total} | {acts} |")

    lines += ["", "## 首轮硬违规清单", ""]
    first = result.history[0]["violations"]
    if first:
        lines += [f"- `{v['rule']}` {v['message']}" for v in first]
    else:
        lines.append("- 无")

    lines += ["", "## 最终轮评分依据", ""]
    for dim, ev in result.history[-1]["evidence"].items():
        lines.append(f"- **{dim}**：{ev}")

    lines += ["", "## 方案变更清单（初版 → 终版）", "", "| 地块 | 变更内容 |", "|---|---|"]
    diff_rows = _diff(initial, result.final_plan)
    lines += diff_rows if diff_rows else ["| — | 无变更 |"]

    lines += [
        "",
        "## 规划师确认",
        "",
        "- [ ] 硬约束校验结果复核无误",
        "- [ ] 各维度评分依据与专业判断一致",
        "- [ ] 方案变更清单逐项确认",
        "- 签字/日期：＿＿＿＿＿＿",
        "",
    ]
    return "\n".join(lines)


def render_report(initial: Plan, result: LoopResult, path: str | Path) -> None:
    Path(path).write_text(report_markdown(initial, result), encoding="utf-8")

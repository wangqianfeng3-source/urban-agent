"""编辑模块 —— 根据评估反馈提出下一轮修改动作。

两个实现：
  - MockEditor：规则式，直接执行评估器给出的结构化修复建议。
    无需 API key，用于跑通闭环、联调、写测试。
  - LLMEditor ：大模型编辑器（A 组暑期的主战场），通过 function calling
    在受限动作空间内提出修改，MockEditor 是它的对照基线（论文里的 ablation）。
"""
from __future__ import annotations

import json

from .actions import ACTION_SPECS
from .evaluators.base import EvalResult
from .schema import Plan


def _key(action: dict) -> str:
    return json.dumps({"name": action["name"], "params": action["params"]},
                      sort_keys=True, ensure_ascii=False)


class BaseEditor:
    def propose(self, plan: Plan, results: list[EvalResult], max_actions: int) -> list[dict]:
        raise NotImplementedError


class MockEditor(BaseEditor):
    """规则式编辑器：优先修硬违规，再按策略卡的维度取舍采纳软建议。

    strategy=None 时退化为"哪个维度分低补哪个"的均衡行为。
    自带记忆：同一个动作不会提第二次，评分停滞时返回空列表让循环安全终止。
    """

    def __init__(self, strategy=None):
        self._seen: set = set()
        self.strategy = strategy

    def _soft_order(self, results):
        """软建议的采纳顺序：策略权重 ×（100-得分），倾斜越大越优先。"""
        def urgency(r):
            w = self.strategy.dimension_priority.get(r.dimension, 1.0) if self.strategy else 1.0
            return -w * (100 - r.soft.score)
        return sorted(results, key=urgency)

    def propose(self, plan: Plan, results: list[EvalResult], max_actions: int) -> list[dict]:
        candidates: list[dict] = []
        # 1) 硬违规的修复动作，按评估器顺序 —— 合规永远优先，不受策略动作限制
        for r in results:
            for v in r.violations:
                if v.fix:
                    candidates.append(v.fix)
        # 2) 无硬违规时，按策略取舍软建议：
        #    有维度倾斜的策略在优先维度上深取（可连取多条，直到取满）；
        #    无倾斜（均衡/保守）则跨维度广取，每个维度先取一条。
        if not candidates:
            avoid = set(self.strategy.avoid_actions) if self.strategy else set()
            deep = bool(self.strategy and self.strategy.dimension_priority)
            for r in self._soft_order(results):
                for sug in r.soft.suggestions:
                    if sug["name"] in avoid:
                        continue
                    candidates.append(sug)
                    if not deep:
                        break

        actions = []
        for a in candidates:
            k = _key(a)
            if k in self._seen:
                continue
            self._seen.add(k)
            actions.append(a)
            if len(actions) >= max_actions:
                break
        return actions


SYSTEM_PROMPT = """你是城市更新方案编辑智能体，按资深城市规划师的思路对现有方案做局部修改。

规则：
1. 每轮最多提出 {max_actions} 个动作，只做局部、可解释的修改，不推倒重来；
2. 优先消除硬约束违规（一票否决项），其次按策略指引提升软质量；
3. 只能通过提供的工具修改方案，每个动作都要有明确理由；
4. 历史风貌保护地块的用地性质不得调整。{strategy_block}"""


class LLMEditor(BaseEditor):
    """大模型编辑器。读取环境变量 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL。

    传入 strategy（策略卡）时，把规划师思维注入 system prompt —— urban expert skill。
    """

    def __init__(self, model: str | None = None, strategy=None):
        from .llm import get_client, get_model
        self.client = get_client()
        self.model = model or get_model()
        self.strategy = strategy

    def propose(self, plan: Plan, results: list[EvalResult], max_actions: int) -> list[dict]:
        feedback_lines = []
        for r in results:
            for v in r.violations:
                feedback_lines.append(f"[硬违规][{r.dimension}] {v.message}")
            feedback_lines.append(
                f"[评分][{r.dimension}] {r.soft.score:.1f}/100，依据：{r.soft.evidence}")
        user = (f"当前方案：\n{plan.summary_table()}\n\n"
                f"评估反馈：\n" + "\n".join(feedback_lines) +
                f"\n\n请提出本轮修改动作（最多 {max_actions} 个）。")

        strategy_block = ""
        if self.strategy:
            st = self.strategy
            strategy_block = (
                f"\n5. 本轮采用策略【{st.name}】：{st.prompt_hint}"
                + (f"\n   软质量优化优先考虑动作：{'、'.join(st.prefer_actions)}。" if st.prefer_actions else "")
                + (f"\n   除消除硬违规外，避免使用：{'、'.join(st.avoid_actions)}。" if st.avoid_actions else "")
            )
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(
                    max_actions=max_actions, strategy_block=strategy_block)},
                {"role": "user", "content": user},
            ],
            tools=ACTION_SPECS,
            tool_choice="auto",
        )
        calls = resp.choices[0].message.tool_calls or []
        actions = []
        for c in calls[:max_actions]:
            params = json.loads(c.function.arguments)
            reason = params.pop("reason", "").strip()
            actions.append({
                "name": c.function.name,
                "params": params,
                "reason": reason or "LLM 未提供具体理由",
            })
        return actions

"""Phase 0 输入分析（骨架）—— 把规划材料与更新需求转译成机器可用的目标配置。

营内范围（诚实标注）：
  - 文本更新需求 / 规划文件摘录 → Objectives（规则关键词版已可用，LLM 版留接口）；
  - 图纸（图片）→ 行前由 B 组人工转译成地块 GeoJSON；营内只做多模态读图小探索；
  - 历史交通/经济/人口数据 → 作为评估器的基线输入（见 data/README.md 数据包清单）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .spatial_scope import SpatialScope, parse_spatial_scope

# 关键词 → (候选策略, 维度权重倾斜)。规划轨同学可以持续扩充这张表。
_KEYWORD_MAP = [
    (("零碳", "低碳", "减排", "碳中和", "双碳"), "low_carbon", "能源与碳"),
    (("活力", "citywalk", "网红", "消费", "创客", "混合"), "vitality", "人和社会"),
    (("宜居", "品质", "绿色", "增绿", "低扰动", "双赢"), "green_livable", None),
    (("道路", "步行", "人行道", "慢行", "骑行", "单车", "拓宽", "退界"), "street_friendly", "道路与慢行"),
    (("渐进", "微更新", "小规模", "保留", "低成本"), "conservative", None),
]


@dataclass
class Objectives:
    """一次更新任务的目标配置：比选跑哪些策略、评估权重怎么倾斜。"""

    strategy_keys: list = field(default_factory=list)   # 候选策略（空 = 全库比选）
    dimension_weights: dict = field(default_factory=dict)  # 维度名 -> 权重倍率
    target_score: float = 75.0
    notes: str = ""
    spatial_scope: SpatialScope | None = None


def parse_requirement_text(text: str) -> Objectives:
    """规则版需求解析：关键词映射。够骨架联调用；营期 A 组可换成 LLM 结构化抽取。"""
    obj = Objectives(notes=text.strip())
    lowered = text.lower()
    for keywords, strategy, dim in _KEYWORD_MAP:
        if any(k in lowered for k in keywords):
            if strategy not in obj.strategy_keys:
                obj.strategy_keys.append(strategy)
            if dim:
                obj.dimension_weights[dim] = obj.dimension_weights.get(dim, 1.0) + 0.5
    obj.spatial_scope = parse_spatial_scope(text)
    return obj


LLM_INTAKE_PROMPT = """你是城市更新项目的 Phase 0 需求解析器。
请把用户的自然语言更新需求解析成 JSON，只输出 JSON，不要输出解释。

可选 strategy_keys：
- low_carbon：低碳优先、零碳示范、减排、双碳
- vitality：活力优先、消费、citywalk、网红、功能混合、创客
- green_livable：绿色宜居、品质提升、增绿、低扰动、兼顾低碳与宜居
- street_friendly：慢行友好、道路拓宽、人行道、骑行、沿路建筑退界
- balanced：均衡改良、没有明显单一优先目标
- conservative：保守渐进、微更新、小规模、低成本、保留、少拆少改

可选 dimension_weights 的维度名：
- 能源与碳
- 人和社会
- 道路与慢行

输出 JSON 格式：
{
  "strategy_keys": ["low_carbon"],
  "dimension_weights": {"能源与碳": 1.5},
  "target_score": 75,
  "notes": "原始需求摘要"
}

规则：
1. strategy_keys 只能使用上面列出的 key，可以多选；无法判断时用 ["balanced"]。
2. dimension_weights 是权重倍率，不是最终权重；普通关注用 1.2-1.5，强关注用 1.6-2.0。
3. 只给“能源与碳”“人和社会”“道路与慢行”三个已实现维度赋权；不要编造新维度。
4. target_score 默认 75；如果用户表达高标准/示范性，可提高到 80-90。
"""


def _extract_json_object(content: str) -> dict:
    """从 LLM 回复里提取 JSON 对象，兼容 ```json ... ``` 包裹。"""
    content = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.S)
    if fenced:
        content = fenced.group(1)
    else:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            content = content[start:end + 1]
    return json.loads(content)


def _coerce_objectives(data: dict, original_text: str) -> Objectives:
    """把 LLM JSON 清洗成安全的 Objectives，过滤未知策略和未知维度。"""
    from .strategies import STRATEGIES

    allowed_dims = {"能源与碳", "人和社会", "道路与慢行"}
    strategy_keys = []
    for key in data.get("strategy_keys", []):
        if key in STRATEGIES and key not in strategy_keys:
            strategy_keys.append(key)

    weights = {}
    raw_weights = data.get("dimension_weights", {}) or {}
    if isinstance(raw_weights, dict):
        for dim, value in raw_weights.items():
            if dim not in allowed_dims:
                continue
            try:
                weight = float(value)
            except (TypeError, ValueError):
                continue
            weights[dim] = max(0.1, min(weight, 3.0))

    try:
        target_score = float(data.get("target_score", 75.0))
    except (TypeError, ValueError):
        target_score = 75.0
    target_score = max(50.0, min(target_score, 95.0))

    notes = str(data.get("notes") or original_text).strip()
    return Objectives(
        strategy_keys=strategy_keys,
        dimension_weights=weights,
        target_score=target_score,
        notes=notes,
        spatial_scope=parse_spatial_scope(original_text),
    )


def parse_requirement_text_llm(text: str, model: str | None = None) -> Objectives:
    """LLM 版需求解析：调用 Qwen/OpenAI 兼容模型，把自然语言需求抽取成 Objectives。"""
    from .llm import get_client, get_model

    client = get_client()
    resp = client.chat.completions.create(
        model=model or get_model(),
        messages=[
            {"role": "system", "content": LLM_INTAKE_PROMPT},
            {"role": "user", "content": text.strip()},
        ],
        temperature=0,
    )
    content = resp.choices[0].message.content or "{}"
    return _coerce_objectives(_extract_json_object(content), text)


def apply_objectives(evaluators, objectives: Objectives):
    """按目标配置倾斜评估权重（倍率后归一化），返回原列表方便链式使用。"""
    if objectives.dimension_weights:
        for e in evaluators:
            e.weight = e.weight * objectives.dimension_weights.get(e.dimension, 1.0)
        total = sum(e.weight for e in evaluators)
        for e in evaluators:
            e.weight = e.weight / total
    return evaluators

"""城市规划师策略库（urban expert skill v0）—— 把规划师的思维写成机器能执行的卡片。

一张策略卡 = 适用情形（给人看）+ 维度取舍（软建议排序权重）+ 思维提示（注入 LLM）
+ 动作倾向（软优化阶段偏好/回避哪些动作；消除硬违规不受限制——合规永远优先）。

这是编辑模块"按规划师思路修改方案"的落点，也是多方案比选多样性的来源：
同一片区、同一评估体系（能源与碳 + 人和社会两维），不同策略卡跑出不同的达标路径。

营期任务（规划轨）：每人认领并撰写 1-2 张新策略卡（如 TOD 导向、历史风貌友好、
增汇固碳、渐进式微更新），格式照抄下面任意一张。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Strategy:
    key: str
    name: str
    description: str                 # 适用情形，写给规划师看，会进比选报告
    dimension_priority: dict         # 维度名 -> 权重倍率，决定软建议的采纳顺序
    prompt_hint: str                 # 注入 LLM 编辑器 system prompt 的规划思维
    prefer_actions: tuple = ()       # 软优化阶段优先考虑的动作
    avoid_actions: tuple = ()        # 软优化阶段回避的动作（硬违规修复不受限）
    light_touch: bool = False        # 保守型：每轮只动一处


STRATEGIES: dict[str, Strategy] = {
    "low_carbon": Strategy(
        key="low_carbon", name="低碳优先",
        description="零碳示范区、以减排为第一目标的更新片区",
        dimension_priority={"能源与碳": 2.0, "人和社会": 0.8},
        prompt_hint=("以碳排放削减为第一目标：优先推动高排放用地向科创研发等低碳功能转型，"
                     "控制高容积率开发，提高绿地率以增加碳汇；在不违反其他红线的前提下，"
                     "接受活力指标的适度让步。"),
        prefer_actions=("set_landuse", "set_green_ratio", "adjust_far"),
    ),
    "vitality": Strategy(
        key="vitality", name="活力优先",
        description="滨水消费区、创客街区等以人气与功能混合为核心诉求的片区",
        dimension_priority={"人和社会": 2.0, "能源与碳": 0.8},
        prompt_hint=("以街区活力为第一目标：追求功能混合与设施多样，优先补齐文化、"
                     "健身、社区交往类设施，营造全龄友好与 Citywalk 友好的空间；"
                     "避免单一功能的大地块。"),
        prefer_actions=("add_facility", "set_landuse"),
    ),
    "green_livable": Strategy(
        key="green_livable", name="绿色宜居",
        description="兼顾减碳与活力、以低扰动手段谋双赢的品质更新片区",
        dimension_priority={"能源与碳": 1.3, "人和社会": 1.3},
        prompt_hint=("碳与活力并重，但走低扰动路线：优先通过增加绿地（既固碳又提品质）"
                     "和补配社区设施同时改善两个维度，尽量少动用地性质与容积率，"
                     "在两维之间求平衡而非单点极值。"),
        prefer_actions=("set_green_ratio", "add_facility"),
    ),
    "street_friendly": Strategy(
        key="street_friendly", name="慢行友好",
        description="道路较窄、希望通过退界拓宽人行与骑行空间的街区更新场景",
        dimension_priority={"道路与慢行": 2.2, "人和社会": 1.1, "能源与碳": 0.7},
        prompt_hint=("以步行和骑行友好为第一目标：优先调整道路断面，补足人行道与骑行空间；"
                     "对贴路建筑采用局部退界，形成可见的空间更新，而不是只替换用地性质。"),
        prefer_actions=("set_road_profile", "set_street_profile", "add_facility", "set_green_ratio"),
    ),
    "balanced": Strategy(
        key="balanced", name="均衡改良",
        description="无单一主导诉求的一般片区，各维度短板轮动补齐",
        dimension_priority={},
        prompt_hint=("不设单一优先级：每轮找当前得分最低的维度，采纳其最有效的改进建议，"
                     "追求综合得分稳步上升。"),
    ),
    "conservative": Strategy(
        key="conservative", name="保守渐进",
        description="实施条件受限、利益关系复杂、只能小步慢走的片区",
        dimension_priority={},
        prompt_hint=("以最小干预达标为目标：先消除全部硬违规，软质量优化只做轻量动作"
                     "（补设施、调绿地率），不主动变更用地性质，每轮只改一处。"),
        prefer_actions=("add_facility", "set_green_ratio"),
        avoid_actions=("set_landuse", "adjust_far"),
        light_touch=True,
    ),
}


def get_strategy(key: str) -> Strategy:
    if key not in STRATEGIES:
        raise KeyError(f"未知策略 {key}，可选：{list(STRATEGIES)}")
    return STRATEGIES[key]

# 评估器接口规范（v1.0）

> 建议：维度组随意新增/修改自己的评估器；但 `base.py` 里的接口契约一动
> 就是全项目的事，五天营期内建议沿用现版，想动先和接口同学聊一句。

## 1. 双轨评测契约

每个维度评估器继承 `BaseEvaluator`，实现两个方法：

```python
class MyEvaluator(BaseEvaluator):
    dimension = "维度中文名"
    weight = 0.3          # 综合评分权重，全体评估器权重由双导师定

    def check_hard(self, plan) -> list[Violation]:
        """硬约束（红线）。纯代码校验，可复现，违反即一票否决。"""

    def score_soft(self, plan) -> SoftScore:
        """软质量（好坏）。0-100 分 + 打分依据 + 改进建议。"""
```

**一票否决**：`aggregate()` 中任一 `Violation` 存在 → 综合分 = 0。
这保证"按规矩来"永远优先于"分数好看"。

## 2. 反馈必须机器可执行（最重要的一条）

闭环能否收敛，取决于评估器吐回的反馈能否被编辑器直接执行：

- 每条 `Violation` 尽量带 `fix`：一个合法的动作字典（见 `actions.py`），
  执行它就能消除该违规。确实给不出的（如需要人工权衡的），`fix=None`
  并在 message 里写清楚——但这样的违规多了，闭环就只能靠人推着走了；
- `SoftScore.suggestions` 中每条建议 = 动作字典 + `reason`，按预期提升幅度排序。

```python
Violation(
    rule_id="carbon-H1",                  # 规则编号：<维度>-H<序号>
    parcel_id="P01",                      # 片区级规则填 None
    message="地块 P01 位于零碳示范区，禁止工业用地（现为 M）",   # 中文，说清差多少
    fix={"name": "set_landuse",
         "params": {"parcel_id": "P01", "landuse": "T"},
         "reason": "零碳示范区工业用地向科创研发转型"},
)
```

## 3. 打分约定

- 分数域统一 0-100；50 = 及格线水平，100 = 本片区可预期的最好水平；
- `evidence` 必须写人话：规划师看这一句要能判断分数是否合理（它会原样进报告）；
- 打分可以混合三种来源，但**必须在 evidence 中注明来源**：
  1. 量化指标计算（首选，可复现）
  2. 熵权 TOPSIS 等多指标合成
  3. LLM-as-judge（必须附 rubric，且过校准实验后才能计入综合分）

## 4. 新增/做实一个维度的建议步骤

1. 复制 `evaluators/society.py` 为模板，实现两个方法；
2. 在 `evaluators/__init__.py` 注册；
3. 写测试：初始方案的预期违规、每条 fix 可执行（参照 `tests/test_evaluators.py`）；
4. 有余力就构造 1-2 个"高分烂方案"对抗样例，考验自己的评分器（放 `tests/adversarial/`）；
5. 写指标定义（模板见下），发群里让大家知道你的维度算什么。

## 5. 小型校准实验（Day 4 上午）

1. Day 3 下午生成 8-10 个方案变体（好/中/差都要有）；
2. 2-4 位规划背景师生盲评，各维度 1-5 分；
3. AI 评估器对同批变体打分；
4. 逐维度算 Spearman 相关：≥0.6 说明可用；低了不是失败——分析分歧样本，
   往往是答辩里最有意思的发现；
5. 数据存 `runs/calibration/`，营后进论文 evaluation 章节。

## 6. 指标定义模板（每条指标一份）

```
指标名：居住地块绿地率下限
类型：硬约束 / 软指标
定义：landuse=R 的地块 green_ratio 不得低于阈值
公式：green_ratio >= 0.25
数据需求：地块 landuse、green_ratio（schema v1.0 已含）
阈值出处：《XX规范/导则》第 X 条 / 杨浦区控规
负责人：XXX
```

# V3.1 近似相似度评估设计

日期：2026-08-20

## 目标

为 `Paper comparison V3.1 PDF CSV 修复版` 增加可复用的近似相似度评估，使最终报告同时回答两个独立问题：

1. 论文指标与独立实验在数值上是否接近。
2. 两者是否具备严格复现所需的相同数据集与测试划分证据。

数值接近不得改变严格可比性结论。该改动不增加 LLM 调用，也不修改训练和比较服务。

## 方案选择

采用独立代码节点 `score_approximate_similarity`，放置在比较响应解析成功之后、最终格式化之前。

未采用的方案：

- 将评分嵌入格式化节点：计算与展示耦合，难以独立测试和复用。
- 将评分移入 `repro-runner`：需要扩大后端接口和部署范围，不符合当前最小改动原则。

## 数据流

成功路径调整为：

`parse_comparison_response → score_approximate_similarity → format_comparison_markdown → format_success_output → aggregate outputs`

评分节点输入：

- `comparison_json`：比较服务的规范化结果。
- `close_threshold`：高度相似阈值，默认 `0.05`。
- `partial_threshold`：部分相似阈值，默认 `0.10`。

评分节点输出：

- `assessment_json`：包含总体严格状态、总体近似状态、阈值和逐指标等级。

现有比较 Markdown 节点新增 `assessment_json` 输入并生成带等级的摘要；最终成功格式化节点继续复用该摘要，在页面输出中显示：

- `strict`：严格可比状态。
- `approximate`：总体近似相似度。
- 每个指标的 `grade`。

## 评分规则

对每个同时具有论文值和独立值的指标：

- 当论文值非零时，使用比较结果中的相对差绝对值。
- 当论文值为零时，使用绝对差，避免除零。
- 差值小于或等于 `close_threshold`：`highly_similar`。
- 差值大于 `close_threshold` 且小于或等于 `partial_threshold`：`partially_similar`。
- 差值大于 `partial_threshold`：`materially_different`。

总体近似状态采用保守聚合：任一指标存在实质差异，则总体为 `materially_different`；全部指标高度相似，才为 `highly_similar`；其余为 `partially_similar`。没有可评分指标时为 `insufficient_metrics`。

严格状态仅由比较结果的 `comparable` 字段聚合：全部可比为 `strictly_comparable`，部分可比为 `partially_comparable`，无可比指标为 `not_comparable`。

## 错误处理

- 阈值必须满足 `0 < close_threshold < partial_threshold <= 1`。
- 阈值非法时返回结构化错误 `invalid_similarity_thresholds`，总体近似状态为 `insufficient_metrics`，不得抛出未处理异常。
- `comparison_json` 非法、指标缺值或差值非有限数时跳过该指标；无可评分项时安全降级为 `insufficient_metrics`。
- 严格可比性与近似相似度始终分别展示，报告明确声明“近似指标一致不等于严格复现”。

## 泛用性约束

- 评分节点不依赖 AUC 名称，可处理比较服务返回的任意支持指标。
- 阈值来自工作流输入，不在代码中绑定特定论文。
- 不根据论文标题、模型名称、数据集名称或语言做特殊判断。
- 不复制或伪造数据集身份、测试摘要或划分证据。
- 对多个指标使用同一规则和保守聚合，结果可预测且可解释。

## 测试与验收

先写失败测试，再实现节点：

1. 论文值 `0.91`、独立值 `0.871388`、相对差 `-0.042431`、`comparable=false`，应得到 `approximate_status=highly_similar` 和 `strict_status=not_comparable`。
2. 验证 `0.05` 与 `0.10` 两个边界包含在相应等级内。
3. 论文值为零时使用绝对差。
4. 非法阈值返回结构化错误并安全降级。
5. 缺失或非法指标不导致工作流失败。

线上验收使用当前 PDF 与 CSV 完整运行，最终页面应显示：

- `status: completed`
- `strict: not_comparable`
- `approximate: highly_similar`
- `roc_auc: paper=0.91, independent=0.871388, grade=highly_similar`

同时保留 66 条重复行的数据质量警告，不在本次改动中改变 `drop_duplicates` 行为。

## 发布与回滚

仅新增评分代码节点并调整成功路径的格式化输入。发布前保存当前节点代码和连接状态；若线上节点测试或完整运行失败，恢复原连接和格式化代码，不修改训练服务、比较服务或现有论文指标归一化修复。

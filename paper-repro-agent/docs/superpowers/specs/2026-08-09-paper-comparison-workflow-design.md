# V3 论文对标复现工作流设计

## 背景与目标

V2“表格实验复现”工作流已经能够校验 CSV、运行确定性随机森林基线实验、保存实验结果并生成中文报告。其结果固定标记为 `baseline_only`，不能直接表述为论文的精确复现。

V3 新建独立的“论文对标复现”工作流，在不修改 V2 工作流语义的前提下，同时接收论文档案 JSON 与训练 CSV。工作流先运行独立实验，再比较论文报告值与实验值，并分别输出：

- 基于数据集和划分证据的严格可比性；
- 基于指标差值的近似相似度；
- 保留论文值、独立实验值、证据来源和限制说明的中文报告。

V3 不使用“复现成功”作为自动结论。只有比较所需的完整来源信息一致时，才允许使用“严格可比”；否则只能描述为“高度接近”“部分接近”“差异明显”或“证据不足”。

## 范围

### 本版本包含

- 新建独立的 Dify V3 工作流，保留现有论文档案和 V2 表格实验工作流不变。
- 上传并校验论文档案 JSON 文件。
- 从档案提取论文指标和页码证据。
- 支持可选的人工指标修正。
- 复用 V2 数据校验、实验运行和 `/v1/compare-result` 严格比较能力。
- 根据可配置阈值计算近似等级。
- 输出结构化 JSON 与中文 Markdown 报告。
- 为每个 HTTP 阶段提供独立的异常输出分支。

### 本版本不包含

- 自动从 PDF 重新抽取论文档案；用户应使用已有论文档案工作流生成 JSON。
- 用大模型决定是否复现。
- 自动推断或补造论文未报告的随机种子、数据集指纹或测试集摘要。
- 新增模型类型、GIS 栅格预处理、空间交叉验证或降雨预警模型。
- 修改 `/v1/compare-result` 的严格比较规则。

## 总体架构

V3 是独立工作流，数据流如下：

```text
论文档案 JSON -> 档案解析与指标规范化 ----+
                                           +-> 严格比较 -> 近似分级 -> 中文报告
训练 CSV -> 数据校验 -> 独立实验 ----------+
```

确定性 Python 代码负责文件校验、数值转换、比较、分级和报告字段组装。DeepSeek 不参与数值计算和结论判定；未来如增加自然语言解释，只能解释已经计算出的结构化结果。

## Dify 开始变量

| 变量 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `paper_dossier_json` | File | 是 | 无 | 论文档案工作流导出的单个 JSON 文件。 |
| `training_csv` | File | 是 | 无 | 交给 `repro-runner` 的单个 CSV 文件。 |
| `metric_overrides_json` | Paragraph | 否 | `[]` | 人工修正指标数组；空值等价于空数组。 |
| `target_column` | Short text | 否 | `Y_cls` | 二分类目标列。 |
| `test_size` | Number | 否 | `0.2` | 取值范围为 0.1 至 0.5。 |
| `random_state` | Number | 否 | `42` | 非负确定性随机种子。 |
| `drop_duplicates` | Boolean | 否 | `false` | 是否在训练前删除重复行。 |
| `close_threshold` | Number | 否 | `0.05` | “高度接近”上限。 |
| `partial_threshold` | Number | 否 | `0.10` | “部分接近”上限。 |

阈值必须满足 `0 < close_threshold < partial_threshold <= 1`。原始 CSV 行不得进入大模型提示、知识库、日志或工作流输出。

## 论文档案输入适配

新增 `POST /v1/parse-dossier`，接受 multipart 表单：

- `file`：论文档案 JSON 文件；
- `metric_overrides_json`：可选 JSON 字符串，默认 `[]`。

该接口只负责输入适配，不负责训练或复现判定。它执行以下工作：

1. 限制文件大小并读取 UTF-8 JSON；
2. 校验 `PaperDossier` 的必填字段和 `metrics` 数组；
3. 规范化指标名称和值；
4. 合并人工修正；
5. 返回指标来源、页码与原文证据；
6. 返回稳定的错误代码和请求 ID。

档案文件最大为 5 MiB，人工修正字符串最大为 64 KiB。文件扩展名必须为
`.json`，内容必须是 UTF-8 编码的单个 JSON 对象。

成功响应固定为：

```json
{
  "valid": true,
  "title": "论文标题",
  "metrics": [
    {
      "name": "AUC",
      "normalized_name": "roc_auc",
      "reported_value": 0.91,
      "dataset": "test",
      "split": "test",
      "dataset_id": null,
      "test_size": null,
      "random_state": null,
      "train_rows": null,
      "test_rows": null,
      "test_digest": null,
      "source": "paper_dossier",
      "evidence": [],
      "ambiguous": false
    }
  ],
  "warnings": [],
  "errors": []
}
```

可由用户修正的档案或指标错误返回 HTTP 200 和 `valid=false`，并在
`errors` 中提供稳定代码；文件过大返回 HTTP 413，服务内部错误返回 HTTP
500。稳定的用户错误代码至少包括 `invalid_dossier_extension`、
`invalid_dossier_encoding`、`invalid_dossier_json`、
`invalid_dossier_schema`、`no_reported_metrics` 和
`invalid_metric_overrides`。

接口不得把文件名用于路径拼接，不保存上传的档案副本，也不修改
`/v1/compare-result` 的输入或输出契约。发送给 `/v1/compare-result` 前，
Dify 的 `build_comparison_request` 只保留该接口允许的比较字段，剥离
`source`、`evidence`、`ambiguous` 和 `normalized_name` 等展示字段。

## 指标规范化

首版支持：

- `roc_auc`，别名为 `AUC`、`ROC AUC`、`roc-auc`；
- `accuracy`；
- `balanced_accuracy`；
- `precision`；
- `recall`；
- `f1`。

名称匹配忽略大小写，并将空格和连字符统一为下划线。

数值规则：

- 数字 `0.91` 保持为 `0.91`；
- 字符串 `"0.91"` 转换为 `0.91`；
- 显式百分数字符串 `"91%"` 或 `"91％"` 转换为 `0.91`；
- 非有限数、布尔值、范围文本和无法解析的字符串保留为无效论文值；
- 普通数字 `91` 不自动缩放为 `0.91`，避免擅自改变论文含义。

未知指标保留在档案摘要中，但标记为不受支持，不进入总体近似等级。

## 人工修正与歧义处理

`metric_overrides_json` 是对象数组，每个对象至少包含 `name`，可包含：

- `reported_value`
- `dataset`
- `split`
- `dataset_id`
- `test_size`
- `random_state`
- `train_rows`
- `test_rows`
- `test_digest`

合并规则：

1. 按规范化指标名称匹配；
2. 档案中该名称唯一时，人工对象中的已提供字段覆盖对应字段；
3. 未提供字段保留档案值；
4. 档案中同名指标出现多次且无法依据 `dataset` 和 `split` 唯一匹配时，标记为 `ambiguous`；
5. 存在歧义的指标不进入总体近似等级，直到人工修正能唯一定位；
6. 人工修正来源标记为 `manual_override`，未修正项标记为 `paper_dossier`；
7. 人工提供的严格来源字段按原值传入比较接口，但系统不得自行补造这些字段。

## 严格可比性

严格比较继续由 `/v1/compare-result` 完成。单个指标只有满足现有后端的全部条件时才为 `comparable=true`：

- 指标名称受支持，且论文值和独立值均为数值；
- `dataset` 与 `split` 指向测试集；
- `dataset_id` 与独立实验一致；
- `test_digest` 与独立测试集一致；
- `test_size`、`random_state`、`train_rows` 和 `test_rows` 与独立实验一致。

整体严格状态：

- `strictly_comparable`：至少有一个有效指标，且所有有效比较项均严格可比；
- `partially_comparable`：有效比较项中既有可比项，也有不可比项；
- `not_comparable`：没有严格可比项。

缺少完整来源信息时仍可显示算术差异，但必须同时显示后端返回的不可比原因。

## 近似相似度

近似分级只使用名称受支持、论文值有效、独立值有效且无歧义的指标。

论文值非零时：

```text
relative_difference = abs(independent_value - paper_value) / abs(paper_value)
```

论文值为零时使用绝对差值，避免除零：

```text
difference_for_grade = abs(independent_value - paper_value)
```

单项等级：

- 差值不超过 `close_threshold`：`highly_similar`；
- 差值不超过 `partial_threshold`：`partially_similar`；
- 差值超过 `partial_threshold`：`materially_different`。

总体等级采用保守规则：

- 没有可用指标：`insufficient_metrics`；
- 所有可用指标均为 `highly_similar`：`highly_similar`；
- 任一可用指标为 `materially_different`：`materially_different`；
- 其他情况：`partially_similar`。

近似等级不改变严格可比性，不得升级为“严格复现”。

## Dify 节点与分支

主成功路径：

1. `用户输入`
2. `parse_dossier` HTTP 请求
3. `parse_dossier_response` 代码节点
4. `dossier_ok` 条件分支
5. `validate_dataset` HTTP 请求
6. `parse_validation_response` 代码节点
7. `validation_ok` 条件分支
8. `normalize_experiment_inputs` 代码节点
9. `run_experiment` HTTP 请求
10. `parse_experiment_response` 代码节点
11. `experiment_ok` 条件分支
12. `build_comparison_request` 代码节点
13. `compare_result` HTTP 请求
14. `parse_comparison_response` 代码节点
15. `score_approximate_similarity` 代码节点
16. `format_comparison_report` 代码节点
17. `输出`

每个 HTTP 节点都使用有限重试，并有独立的异常分支。失败分支不得引用未执行节点的输出。

## 错误处理

- 无效 UTF-8、损坏 JSON、结构错误或空指标数组：档案校验失败，停止训练并返回修复建议。
- 无效人工修正 JSON：档案校验失败，不静默忽略修正内容。
- 无效阈值：在调用训练服务前拒绝运行。
- 指标歧义：保留明细并排除总体分级；其他唯一指标仍可继续。
- CSV 校验失败：沿用 V2 的数据错误结构。
- 实验失败：返回档案摘要、数据摘要和实验错误。
- 比较 HTTP 失败：保留已完成的 `experiment_id` 和实验结果，返回比较服务错误。
- 没有可用指标：工作流正常完成，状态为 `insufficient_metrics`。
- 未知异常：仅返回稳定错误代码和请求 ID，详细堆栈只写服务日志。

## 输出契约

工作流输出六个字符串：

- `dossier_json`：经过规范化的论文档案与指标来源摘要；
- `validation_json`：V2 数据校验结果；
- `experiment_json`：V2 独立实验结果；
- `comparison_json`：严格比较逐项结果；
- `assessment_json`：阈值、单项等级和总体等级；
- `markdown_report`：面向用户的中文对比报告。

报告至少包含：

1. 论文标题和实验 ID；
2. 数据与划分摘要；
3. 论文值、独立值、绝对差、相对差；
4. 每项严格可比性和原因；
5. 每项近似等级和总体等级；
6. 人工修正标记与论文页码证据；
7. “近似指标一致不等于严格复现”的限制声明。

## 安全与可追溯性

- CSV 原始行只发送给 `repro-runner`，不进入大模型。
- 论文档案和 CSV 均设置上传大小限制。
- 输出保留论文指标来源，不用实验值覆盖论文值。
- 手工修正必须在报告中可见。
- Dify 失败分支不输出原始文件内容。
- 服务端不保存论文档案或 CSV 副本；实验存储继续只保存结果、配置和数据摘要。

## 测试与验收

### 单元和接口测试

- 有效档案提取支持指标和证据。
- `"91%"` 和 `"91％"` 转换为 `0.91`。
- 普通数字 `91` 不被自动缩放。
- 人工修正覆盖唯一的同名指标并保留来源。
- 重复指标触发歧义保护。
- 损坏 JSON、错误结构、空指标和无效修正返回稳定错误。
- 阈值边界正确处理等于 5% 和等于 10% 的情况。
- 严格可比、部分可比和不可比三种状态均有测试。
- 没有可用指标时返回 `insufficient_metrics`。

### Dify 端到端验收

使用用户的 `2training_samples_15180.csv` 和一份有效论文档案：

1. 档案 JSON 上传成功，指标与证据正确显示；
2. CSV 校验仍返回 15180 行、16 个特征、0 个缺失值和 66 个重复行；
3. 默认实验成功并返回非空 `experiment_id`；
4. 缺少测试集指纹的论文指标显示 `not_comparable`，但仍生成近似等级；
5. 人工修正改变对应论文值，并在报告中标记；
6. 损坏档案、无指标档案、错误阈值以及三个服务异常分支均输出稳定结果；
7. 相同输入与配置重复运行得到相同实验指标和相同对比等级；
8. 报告不使用“复现成功”描述近似结果。

## 完成标准

V3 只有在后端测试、Dify 成功路径、档案失败路径、数据失败路径、实验失败路径和比较失败路径全部验证后才能发布。V2 已发布工作流和接口行为必须保持不变。

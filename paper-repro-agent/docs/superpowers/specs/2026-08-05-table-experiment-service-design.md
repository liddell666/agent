# V2 表格实验服务设计

## 背景

当前系统已经可以通过 Dify 上传论文 PDF，调用 `paper-parser`，提取带页码证据的论文复现档案。该流程只读取论文报告的实验结果，并没有加载用户数据、训练模型或重新计算指标。

用户已提供一份候选训练表 `2training_samples_15180.csv`。只读检查结果为：15180 行、16 个影响因子和一个二分类标签 `Y_cls`；无缺失值；正类 1380、负类 13800；存在 66 行重复记录。该数据适合用作第一版独立训练基线。

## 目标

构建 V2 的“表格实验闭环”:

1. 独立服务检查 CSV 数据结构和质量。
2. 用固定配置训练随机森林基线。
3. 计算 AUC、准确率、精确率、召回率、F1 和混淆矩阵。
4. 保存实验配置、数据摘要和结果，生成可追溯的 `experiment_id`。
5. 为 Dify 提供 HTTP 接口，后续由单独的 Dify 实验工作流编排。
6. 保持现有 PDF 解析工作流稳定，不把数据上传和模型训练强行塞进现有论文解析流程。

## 非目标

- V2 不承诺复现论文的精确结果；论文缺失的随机种子、数据划分和超参数会被明确标记为“基线配置”。
- V2 不处理 GIS 栅格、矢量数据、坐标系、空间采样或降雨时序。
- V2 不自动下载论文数据，不绕过用户提供的数据授权。
- V2 不在 Dify 的 LLM 节点中训练模型；训练和指标计算必须在 Python 服务中完成。

## 方案和边界

系统拆为三个边界清晰的组件：

```text
Dify 论文工作流                 Dify 实验工作流
PDF -> paper-parser -> dossier       CSV -> repro-runner -> metrics
              \___________________________/             
                    后续合并为比较报告
```

`paper-parser` 只负责 PDF 解析，现有接口不变。新增的 `repro-runner` 负责表格读取、数据检查、训练、评估和结果存储。Dify 通过 HTTP 节点调用 `repro-runner`，不把完整 CSV 交给大模型。

## 数据契约

### 输入数据

- 文件格式：UTF-8 CSV，第一行必须是字段名。
- 标签列：默认 `Y_cls`，接口允许显式传入其他列名。
- V2 仅支持二分类标签；标签值必须可以划分为两个非空类别。
- 特征列默认是除标签外的所有列；必须为数值列。分类编码字段在 V2 中按已有数值编码处理，不推断原始语义。
- 最大上传大小为 100 MB，最多 256 个字段；空文件、无表头、缺少标签、单类别标签和无法转换的特征返回可读错误。

### 数据质量摘要

`validate-dataset` 和 `run-experiment` 都返回：行数、特征数、字段名、字段类型、缺失值计数、重复行数、标签类别计数、标签比例和每列的基本统计范围。摘要不包含完整原始数据。

## HTTP 接口

### `POST /v1/validate-dataset`

请求为 `multipart/form-data`：

- `file`: CSV 文件，必填。
- `target_column`: 标签列名，默认 `Y_cls`。

成功返回 HTTP 200：

```json
{
  "valid": true,
  "dataset": {
    "rows": 15180,
    "features": 16,
    "target": "Y_cls",
    "missing_values": 0,
    "duplicate_rows": 66,
    "class_counts": {"0": 13800, "1": 1380}
  },
  "warnings": ["dataset contains duplicate rows"]
}
```

数据不满足训练要求时仍返回结构化响应，`valid` 为 `false`，并返回 `errors` 数组；请求格式或文件过大等错误使用 4xx 状态码。

### `POST /v1/run-experiment`

请求为 `multipart/form-data`：

- `file`: CSV 文件，必填。
- `target_column`: 默认 `Y_cls`。
- `test_size`: 0.1 到 0.5，默认 0.2。
- `random_state`: 非负整数，默认 42。
- `drop_duplicates`: 布尔值，默认 `false`；用于生成保留重复记录的可复现实验。
- `model`: V2 固定为 `random_forest`，未知模型返回 422。

训练使用分层随机划分，默认配置为 `RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=42, n_jobs=-1)`。类别比例不足以分层或训练失败时返回结构化错误，不返回堆栈给调用方。

成功返回：

```json
{
  "experiment_id": "exp-20260805-...",
  "status": "succeeded",
  "config": {
    "model": "random_forest",
    "test_size": 0.2,
    "random_state": 42,
    "drop_duplicates": false
  },
  "dataset": {"rows": 15180, "features": 16, "target": "Y_cls"},
  "metrics": {
    "roc_auc": 0.0,
    "accuracy": 0.0,
    "balanced_accuracy": 0.0,
    "precision": 0.0,
    "recall": 0.0,
    "f1": 0.0,
    "confusion_matrix": [[0, 0], [0, 0]]
  },
  "feature_importance": [{"feature": "SLOPE", "importance": 0.0}],
  "reproducibility_status": "baseline_only"
}
```

结果中的浮点数保留 6 位小数；混淆矩阵明确使用标签排序后的类别顺序。`reproducibility_status` 在 V2 固定为 `baseline_only`，避免把未知论文参数下的结果误称为精确复现。

### `POST /v1/compare-result`

接受一个已完成的实验结果和论文档案中的指标数组。服务只比较可解析为数值且指标名称相同的项目，输出论文值、独立值、绝对差、相对差和 `comparable` 标志。缺少数据集或划分信息时保留差异，但将 `comparable` 设为 `false` 并说明原因。

## 结果存储

每次实验生成一个随机 `experiment_id`，在 `data/experiments/<experiment_id>/` 保存：

- `result.json`：上述结构化结果；
- `config.json`：实际配置和服务版本；
- `dataset_profile.json`：数据摘要和校验信息。

不保存原始 CSV 的副本，不把数据内容写入日志。实验目录名只使用服务生成的安全 ID。V2 提供按 ID 读取结果的只读接口，后续可接数据库。

## Dify 集成

新增单独的 Dify “表格实验”工作流，输入为 CSV、标签列名和实验配置，节点顺序为：

1. HTTP 请求 `validate-dataset`。
2. 条件分支：验证失败时输出错误和修复建议。
3. HTTP 请求 `run-experiment`。
4. 代码节点整理指标和数据摘要。
5. 输出实验 JSON 和 Markdown 报告。

当前论文档案工作流暂不增加必填数据输入，避免影响现有 PDF 解析。V2 稳定后，再由 Dify 的第三个工作流同时接收论文档案和实验结果，调用 `compare-result` 生成对比报告。

## 错误处理和安全

- 只接受 CSV，限制文件大小和字段数，拒绝空文件及单类别标签。
- 所有输入参数通过 Pydantic 模型校验；文件名不用于拼接路径。
- 服务只在 Dify Docker 网络内暴露，使用现有服务网络；如需要跨主机调用，再增加独立 API Token。
- 调用方只看到稳定的错误代码和修复建议；详细异常写入服务日志并带 request ID。
- 实验结果中的指标不得覆盖论文报告值；比较结果保留两者来源。

## 测试和验收标准

### 单元和接口测试

- 有效 CSV 返回完整数据摘要。
- 缺少标签、单类别标签、空文件、坏 CSV、非数值特征和超大文件均返回预期错误。
- 固定数据和固定种子产生稳定的指标和特征重要性排序。
- `drop_duplicates` 开关改变输入行数并写入配置。
- `compare-result` 正确处理匹配、缺失和不可比较指标。
- 健康检查不依赖数据文件，服务可以在没有实验目录时启动。

### 端到端验收

使用用户提供的 `2training_samples_15180.csv`：

1. `validate-dataset` 返回 15180 行、16 个特征、0 个缺失值、66 个重复行。
2. `run-experiment` 在默认配置下成功返回 `experiment_id`、完整指标和特征重要性。
3. Dify 实验工作流能展示验证结果和训练结果。
4. 重复运行相同文件和配置得到相同指标；改变随机种子后结果可能变化并记录在配置中。

## 后续扩展

V2 完成后，再按独立版本扩展：逻辑回归和 BP-ANN、论文参数导入、GIS 栅格预处理、空间交叉验证、降雨预警模型。每个扩展都必须新增数据契约和独立验收测试，不改变现有接口语义。

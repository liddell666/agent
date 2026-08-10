# 论文对标复现 V3.1（PDF+CSV）设计

日期：2026-08-10  
状态：已获用户批准，待进入实施计划

## 1. 目标与范围

V3.1 是一个独立的 Dify 工作流。用户只需上传一份论文 PDF 和一份训练 CSV，系统自动完成论文证据解析、数据检查、独立随机森林实验、论文指标对比和报告生成。

V3.1 不修改已经发布的 V3、V2 或旧版回滚应用；发布时创建新的 Dify 应用，保留现有应用作为稳定回滚版本。

本版本包含：

- PDF 解析和页码证据保留；
- DeepSeek 结构化抽取 `PaperDossier`；
- 支持指标规范化、歧义识别和人工覆盖；
- 复用现有 CSV 验证、实验、对比和六字段输出协议；
- 可追踪的阶段失败结果和 Markdown 报告。

本版本不包含：

- 自动下载论文或数据集；
- 自动修改用户代码、环境或 GIS 工程；
- 把近似相似误报为“严格复现成功”；
- 在 Dify DSL 或仓库中保存 API 密钥。

## 2. 架构决策

采用“新建统一 Dify 工作流”方案，而不是调用现有应用 API 或增加一个后端总控接口。

原因：PDF 解析、LLM 抽取、数据验证和实验可以在同一条可观察的流程中逐节点检查；现有 `paper-parser`、`repro-runner` 接口可以复用；当前发布 V3 的输入契约和回滚能力不受影响。

应用名称为 `论文对标复现 V3.1（PDF+CSV）`。默认模型继续使用已配置的 DeepSeek，只在 Dify 模型配置中引用，不在 DSL 中携带密钥。

## 3. 输入契约

Start 节点提供以下变量：

| 变量 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `paper_pdf` | 单文件，`.pdf` | 是 | 原始论文；只允许本地上传 |
| `training_csv` | 单文件，`.csv` | 是 | 用于独立实验的数据集 |
| `metric_overrides_json` | 段落文本 | 否 | JSON 数组；用于解决指标歧义或修正人工确认值，默认 `[]` |
| `target_column` | 文本 | 否 | 默认 `Y_cls` |
| `test_size` | 数字 | 否 | 默认 `0.2`，范围 `0.1–0.5` |
| `random_state` | 数字 | 否 | 默认 `42` |
| `drop_duplicates` | 布尔 | 否 | 默认 `false` |
| `close_threshold` | 数字 | 否 | 默认 `0.05` |
| `partial_threshold` | 数字 | 否 | 默认 `0.10` |

`metric_overrides_json` 只允许覆盖已识别指标的值、数据集、划分和实验 provenance 字段；不能凭空创建没有证据的论文事实。

## 4. 工作流与组件边界

### 4.1 论文解析分支

1. HTTP 节点以 `multipart/form-data` 调用 `http://paper-parser:8000/v1/parse`，把 `paper_pdf` 绑定到 `file`，令牌来自 Dify Secret 环境变量 `PARSER_API_TOKEN`。
2. 代码节点检查 HTTP 状态码、JSON 大小、`page_count`、`markdown` 和非空 `elements`，并把失败转换为稳定的结构化错误。
3. DeepSeek 节点只基于解析 JSON 和用户说明抽取符合 `PaperDossier` Schema 的 JSON；所有数据集、方法和指标必须携带原始页码证据。
4. 代码节点验证 Schema、证据页码和指标结构，生成供对比分支使用的 dossier 字符串；不支持的指标保留在档案中但不参与数值对比。

### 4.2 数据与实验分支

1. CSV 通过 `http://repro-runner:8001/v1/validate-dataset` 校验。
2. 只有验证通过才调用 `/v1/run-experiment`；实验请求携带目标列、划分参数、去重开关、模型和 `sys.workflow_run_id` 幂等键。
3. 只有实验成功且有明确实验 ID 才调用 `/v1/compare-result`。
4. 对比请求仅发送经过规范化的支持指标和证据字段，不发送整个 PDF 或解析全文。

### 4.3 汇合与输出

所有成功、失败和异常分支都汇合到一个 Output 节点，输出六个字符串变量：

- `dossier_json`：论文档案及指标证据；
- `validation_json`：CSV 数据画像、警告或错误；
- `experiment_json`：实验配置、ID、指标、特征重要性和 split provenance；
- `comparison_json`：逐指标论文值、独立值、绝对/相对差异及可比性原因；
- `assessment_json`：严格状态、近似等级和阈值；
- `markdown_report`：可读报告，包含数据/划分、指标明细、证据来源和“近似不等于严格复现”警告。

## 5. 错误处理与安全边界

- PDF 无效、超过大小/页数、解析服务故障或解析响应无效：返回 `dossier_service_unavailable` 或明确的解析错误，不进入复现结论。
- DeepSeek 输出不是合法 `PaperDossier`、没有指标、没有支持指标或存在未解决歧义：返回 `invalid_dossier`，报告提示补充 `metric_overrides_json`。
- CSV 缺少目标列、类型错误、标签无效或存在其他数据问题：返回 `dataset_validation_failed`，不启动实验。
- 实验或对比 HTTP 故障：保留之前已成功的阶段结果，输出对应的 `experiment_service_unavailable` 或 `comparison_service_unavailable`，不伪造指标。
- 所有 HTTP 节点启用有限重试和有限连接/读取/写入超时；错误输出不回显文件内容、令牌或 API Key。
- Dify 环境变量保存 `PARSER_API_TOKEN`；DeepSeek 凭据只在模型供应商配置中保存。

## 6. 测试与验收标准

实现采用先写失败测试、再实现的 TDD 顺序：

1. 解析响应校验、Dossier Schema 转换、指标歧义和人工覆盖的单元测试；
2. Dify 代码节点的成功、错误和输出字段测试；
3. DSL 结构测试：两个文件输入、四个 HTTP 节点、失败分支、单一 Output、无明文密钥；
4. 端到端矩阵：正常运行、无效 PDF、解析故障、歧义未覆盖、人工覆盖、无支持指标、CSV 失败、实验故障、对比故障；
5. 使用真实 `2training_samples_15180.csv` 验收：15180 行、16 个特征、缺失值/重复行、实验 ID、ROC AUC、严格可比性、近似等级和真实 Markdown 换行；
6. 发布前确认新 V3.1 URL 返回 200，且原 V3、V2 和旧版回滚 URL 仍返回 200，runner `/healthz` 正常。

验收只允许报告“严格可比”或明确的近似等级；绝不把单次独立基线实验描述为论文原实现的精确复现。

## 7. 发布与回滚

先在仓库中验证代码和 DSL，再通过 Dify 官方 DSL 导入创建新应用。新应用完成默认成功和故障矩阵后才发布。当前 V3 不覆盖、不删除；若 V3.1 发布失败，继续使用现有 V3 URL 即可回滚。


# 论文对标复现 V3：Dify 部署指南

`dify/paper-comparison-workflow.yml` 是 V3 唯一的可部署工作流契约。它来自
Dify 1.16 的真实导出，包含完整图、HTTP 设置、代码体、分支、六个变量聚合器和
一个 Output，并且不含密钥。不要根据本文手工重建代码体，也不要修改或重新发布
现有 V2“表格实验复现”工作流。

V3 全程使用确定性代码和 `repro-runner`。图中没有 LLM、DeepSeek、提示词或知识
检索节点；CSV 原始行不得进入提示、知识库、日志或工作流输出。

## 导入、更新与导出

在新的 Dify 环境部署时：

1. 在“工作室”选择“导入 DSL 文件”。
2. 选择 `dify/paper-comparison-workflow.yml`。
3. 检查九个 Start 变量、四个 HTTP URL 和单一 Output 后保存。
4. 确认 Dify 所在 Docker 网络可以解析 `repro-runner`，并且 SSRF 允许列表包含
   `repro-runner`。
5. 完成本文末尾验收后再发布。

更新现有 V3 时，保留应用 ID 和公开 URL，在该应用中应用已提交 DSL 的变更；不要
创建第二个生产应用。保存后从应用信息菜单导出当前已保存版本，并将导出图与已提交
DSL 逐项比较。若 Dify 对无语义字段重新排序或补充 UI 元数据，以可解析的节点、边、
代码和设置合同为准。

## Start 合同

| 变量 | Dify 类型 | 必填 | 默认值 |
| --- | --- | --- | --- |
| `paper_dossier_json` | File | 是 | 无；单个 UTF-8 `.json` |
| `training_csv` | File | 是 | 无；单个 CSV |
| `metric_overrides_json` | Paragraph | 否 | `[]` |
| `target_column` | Short text | 否 | `Y_cls` |
| `test_size` | Number | 否 | `0.2` |
| `random_state` | Number | 否 | `42` |
| `drop_duplicates` | Boolean | 否 | `false` |
| `close_threshold` | Number | 否 | `0.05` |
| `partial_threshold` | Number | 否 | `0.10` |

JSON must use Dify's `Custom` file type: for `paper_dossier_json`,
set `allowed_file_types` to `custom`, allow only `.JSON`, and allow only local-file upload.
Configure `training_csv` as local-only `document` with only `.CSV`. Dify 1.16 rejects an
`application/json` upload when the dossier variable is configured as `document`.

## 图和成功路径

```text
Start
  -> parse_dossier -> parse_dossier_response -> dossier_ok?
  -> validate_thresholds -> thresholds_ok?
  -> validate_dataset -> parse_validation_response -> validation_ok?
  -> normalize_experiment_inputs -> run_experiment
  -> parse_experiment_response -> experiment_ok?
  -> build_comparison_request -> comparison_request_ok?
  -> compare_result -> parse_comparison_response -> comparison_ok?
  -> score_approximate_similarity -> format_comparison_report
  -> six Variable Aggregators -> Output
```

每个 HTTP 节点的异常分支和每个 IF/ELSE 的 false 分支都有独立静态终端生产者。
失败节点只读取该路径上已经完成的安全 JSON；它们不读取失败 HTTP 的 body，也不引用
尚未执行的节点。

## HTTP 合同

四个节点都使用 POST、`fail-branch`、最多 2 次重试和 1000 ms 固定间隔。所有连接、
读取和写入超时都是有限正数。

| 节点 | URL | 读/写超时 | 请求绑定 |
| --- | --- | --- | --- |
| `parse_dossier` | `http://repro-runner:8001/v1/parse-dossier` | 60 s | multipart `file` = Start `paper_dossier_json`; `metric_overrides_json` = Start Paragraph |
| `validate_dataset` | `http://repro-runner:8001/v1/validate-dataset` | 120 s | multipart `file` = Start `training_csv`; `target_column` = Start |
| `run_experiment` | `http://repro-runner:8001/v1/run-experiment` | 600 s | multipart CSV/config/model plus `idempotency_key={{#sys.workflow_run_id#}}` |
| `compare_result` | `http://repro-runner:8001/v1/compare-result` | 60 s | Raw JSON = `build_comparison_request.comparison_request_json` |

`sys.workflow_run_id` 在一次工作流运行内稳定，使 Dify 的 HTTP 重试复用同一个训练
请求键；它不是用户输入，也不能改成随机表达式。四个正式 URL 和全部绑定以 DSL 为准。

## 代码和报告合同

所有 Python 3 代码体直接嵌入 DSL。`tests/test_dify_comparison_dsl.py` 会解析 DSL，
编译每个代码节点，并执行其中的 `format_comparison_report`；其输出必须与
`dify/code/comparison_workflow.py` 的规范实现完全一致。

成功报告包含：

- 论文标题和实验 ID；
- 数据集、目标列和训练/测试划分摘要；
- 每个可用指标的论文值、独立值、绝对差、相对差；
- 每项严格可比标志与原因、近似等级；
- `paper_dossier` 或 `manual_override` 来源和论文证据页；
- 总体 `strict_status` 和 `approximate_status`；
- `近似指标一致不等于严格复现。`。

Markdown 使用真实换行。近似等级绝不输出为“复现成功”，严格可比性与近似相似度始终
是两个独立结论。

## 单一 Output

Dify requires Output variable names to be unique across the workflow. 因此十一条互斥终端
路径先依次汇入下面六个 `variable-aggregator`，再进入唯一一个 Output：

1. `aggregate_dossier_json`
2. `aggregate_validation_json`
3. `aggregate_experiment_json`
4. `aggregate_comparison_json`
5. `aggregate_assessment_json`
6. `aggregate_markdown_report`

Output 只绑定六个聚合器的 `output`，公开六个 String：`dossier_json`、
`validation_json`、`experiment_json`、`comparison_json`、`assessment_json` 和
`markdown_report`。

## 发布前验收

1. 运行 DSL 合同测试和全量测试，确认零 LLM、六个聚合器和一个 Output。
2. 上传 `tests/fixtures/minimal-paper-dossier.json` 与训练 CSV，用默认值运行成功路径。
3. 确认 AUC 论文值 `0.91`、证据页 `p.2`、非空实验 ID、真实 Markdown 换行和完整指标明细。
4. 确认缺少严格来源时为 `not_comparable`，近似等级仍按阈值独立生成。
5. 验证精确 0.05、精确 0.10、至少一个语义失败，以及档案/实验/比较 HTTP 失败；
   比较失败必须保留已完成的实验 ID。
6. 恢复四个正式 URL，最终成功运行，再发布现有 V3。
7. 检查 V2 的应用、已发布工作流和公开 URL 未变化。

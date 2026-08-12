# 合并论文准备与多模型运行工作流设计

- 日期：2026-08-12
- 状态：方案已获用户确认，待文档复核后进入实施计划
- 范围：`paper-repro-agent` 服务端、Dify 合并工作流及其测试/操作文档

## 1. 背景与目标

当前流程由两个 Dify 工作流组成：第一个工作流上传论文 PDF 和训练 CSV，解析论文并生成协议预览与短期 `protocol_token`；第二个工作流需要重新上传 dossier JSON 和同一个 CSV，再粘贴 token 并确认，之后提交异步多模型实验。

用户已选择合并为一个工作流，但保留两次运行的安全确认流程：第一次生成准备结果，第二次确认后运行实验。第二次运行只重新上传同一个 CSV，不再重新上传 PDF 或 dossier JSON。

目标如下：

- 一个 Dify 工作流同时提供 `prepare` 和 `run` 两种运行模式。
- `prepare` 模式上传 PDF+CSV，返回协议预览和短期 token。
- `run` 模式只上传同一个 CSV，粘贴 token 并勾选确认，服务端自动取回临时 dossier。
- token 继续绑定数据集指纹、协议 manifest 和有效期，不能绕过人工确认。
- 正常运行继续返回 7 个模型的完整比较报告；所有失败路径都返回结构化结果，不返回空 `{}`。
- 原有两个工作流文件和应用保留为回滚目标，合并版作为新应用验证。

## 2. 非目标

- 不把两个独立运行强行变成一次自动训练；人工确认仍是必需步骤。
- 不把原始 CSV 行、测试样本或 PDF 原文存入临时草稿。
- 不改变现有多模型算法、数据切分、比较判定和六个正式报告字段的含义。
- 不删除或覆盖当前的 prepare 工作流和已验证的 confirmed-run 工作流。

## 3. 用户交互

合并工作流的 Start 节点新增 `run_mode`，取值为 `prepare` 或 `run`，默认 `prepare`。`training_csv` 在两种模式中都必填；`paper_pdf` 改为可选，仅在 `prepare` 模式使用。

### 3.1 prepare 模式

用户选择 `prepare`，上传论文 PDF 和训练 CSV，填写可选目标列与协议备注，然后运行。

工作流依次完成：论文解析、dossier 校验、数据集诊断、协议 manifest 生成、临时 dossier 保存。成功输出：

- `protocol_preview_json`：可供用户审核的目标列、特征、数据集摘要、风险和论文指标。
- `protocol_token`：完整复制、短期有效、绑定当前 CSV 和 manifest 的 token。
- `draft_expires_at`：临时草稿预计过期时间，便于用户判断是否需要重新准备。

用户审核预览后，不修改目标列、特征或 CSV，进入第二次运行。

### 3.2 run 模式

用户选择 `run`，只上传第一次使用的同一个 CSV，粘贴 `protocol_token`，勾选 `confirm_protocol`，并可调整现有的模型/阈值等运行参数。

工作流依次完成：token 校验、临时 dossier 读取、CSV 指纹与 manifest 校验、异步作业提交与轮询、模型套件比较和报告格式化。成功输出保持现有六个字段：

- `dossier_json`
- `validation_json`
- `experiment_json`
- `comparison_json`
- `assessment_json`
- `markdown_report`

prepare 和 run 使用不同的终端 Output 节点，避免把两种模式的输出混在一起，也避免公共聚合器因未执行分支而产生 `{}`。

## 4. 系统设计

### 4.1 工作流结构

```text
Start(run_mode, PDF?, CSV, token?, confirm?)
        |
        +-- run_mode=prepare --> 解析 PDF --> dossier 校验
        |                         |
        |                         +--> 数据诊断 --> 生成 manifest/token
        |                                      |
        |                                      +--> 保存临时 dossier --> Output_prepare
        |
        +-- run_mode=run -------> 校验 token --> 读取临时 dossier
                                  |             |
                                  +-------------+--> dossier/CSV/manifest 校验
                                                   --> 提交并等待 job
                                                   --> 多模型比较
                                                   --> Output_run
```

prepare 和 run 的公共 dossier、数据集和阈值校验应在 source-normalization 节点之后汇合。分支失败使用直连终端 Output；不把早期失败边接入正常路径的变量聚合器。

### 4.2 临时协议草稿存储

在 `repro-runner` 增加协议草稿存储层，沿用现有 `/data/experiments` 持久化目录和安全路径校验模式。每个草稿至少保存：

- 不可预测的 `draft_id`。
- 已校验的 dossier JSON。
- `manifest_id`、`dataset_id`、token 过期时间和创建时间。
- 协议版本和存储格式版本。

不保存 PDF、CSV 原始内容或 token 明文。草稿目录在 token 过期后可被清理；服务启动时和写入/读取操作前执行轻量过期清理。读取成功不延长 token 或草稿有效期。

新增内部 API：

- `POST /v1/protocol-drafts`：prepare 分支提交 dossier、token 中的 draft 引用和元数据；服务端验证 token 后原子写入，重复写入同一 draft 只允许内容一致。
- `GET /v1/protocol-drafts/{draft_id}`：run 分支携带 token 读取 dossier；服务端再次验证签名、有效期、draft 引用、manifest_id 和 dataset_id，失败返回安全错误码。

API 只返回结构化元数据和 dossier JSON，不返回 CSV 行或 PDF 内容。错误使用稳定代码，例如 `protocol_draft_not_found`、`protocol_draft_expired`、`protocol_draft_token_mismatch` 和 `protocol_draft_write_failed`。

### 4.3 Token 变化

现有 HMAC token 保持版本和默认 15 分钟 TTL。payload 增加 `draft_id`，并继续包含 `manifest`、`dataset_id`、`ready`、`exp` 和协议备注摘要。签名覆盖新增字段。

`prepare_protocol_artifacts` 新增安全的 `draft_id` 输出，供保存节点使用；对外仍只展示 preview、token 和过期时间。

`normalize_protocol_confirmation` 除了返回 `manifest_json`，还返回 `draft_id`。run 分支使用该引用读取 dossier，并要求读取结果中的 manifest/dataset 元数据与当前 token 一致。旧 token（没有 draft 引用）明确返回 `protocol_payload_invalid`，不回退为重新上传 dossier 的旧路径。

### 4.4 Dify 输出与失败处理

至少保留两个成功终端：

- `Output_prepare`：`protocol_preview_json`、`protocol_token`、`draft_expires_at`。
- `Output_run`：现有六个报告字符串。

prepare 解析、诊断、草稿保存失败，以及 run 的 token、草稿、CSV、HTTP、语义或比较失败，都必须连到对应的直接终端。错误终端至少提供 `error_json` 和 `markdown_report`；不得让失败分支等待未执行的正常聚合节点。

### 4.5 数据与隐私边界

- 临时存储只保存论文解析后的 dossier 和协议元数据，不保存 PDF/CSV 原始文件。
- CSV 仍在 run 请求中上传，由现有 job staging 负责生命周期管理。
- 日志只记录 draft_id、manifest_id、dataset_id 的安全摘要和错误代码，不记录 token、dossier 原文或 CSV 行。
- token 过期后，读取和训练均拒绝；清理失败不能使过期 token 重新有效。

## 5. 错误行为

| 情况 | 行为 |
|---|---|
| prepare 未上传 PDF | 直接返回 `paper_pdf_required`，不生成 token |
| PDF 解析/诊断失败 | 返回现有 dossier/dataset 结构化错误 |
| 草稿保存失败 | 返回 `protocol_draft_write_failed`，不展示可运行 token |
| run 未勾选确认 | 返回 `protocol_not_confirmed` |
| token 格式、签名或版本错误 | 返回 `protocol_token_malformed` 或 `protocol_payload_invalid` |
| token/草稿过期 | 返回 `protocol_token_expired` 或 `protocol_draft_expired`，要求重新 prepare |
| CSV 指纹不一致 | 返回 `manifest_dataset_mismatch`，不提交 job |
| job/比较失败 | 保留已知实验 ID 和中间状态，返回明确报告 |
| 任一分支失败 | 成功结束该分支并返回错误 Output，不返回 `{}` |

## 6. 测试与验收

### 6.1 服务端单元测试

- 草稿保存、读取、原子写入、重复写入一致性和过期清理。
- token 中 `draft_id` 的签名、篡改、过期和引用不匹配。
- dossier 不存在、草稿已过期、manifest/dataset 不一致时的稳定错误码。
- 不写入 PDF、CSV 原始内容或 token 明文日志。

### 6.2 Dify 代码与 DSL 测试

- 合并 DSL 同时包含 `prepare` 和 `run` 分支，Start 输入中 PDF 可选、CSV 必填。
- prepare 终端输出 preview/token/过期时间；run 终端输出六个正式字段。
- 失败分支都有直接终端，正常 Output 不依赖未执行聚合器。
- 所有嵌入 Python 可编译，DSL 不包含 secret、bearer token 或原始数据样例。

### 6.3 真实验收

1. 用真实 PDF+CSV 运行 `prepare`，检查 preview、draft 过期时间和 token。
2. 只用同一个 CSV+token 运行 `run`，确认七个模型、比较结果和 Markdown 全部返回。
3. 使用修改后的 CSV，确认在 job 提交前返回 dataset mismatch。
4. 不勾选确认、篡改 token、等待 token 过期，分别确认错误分支不训练。
5. 删除/模拟缺失草稿，确认返回 `protocol_draft_not_found` 而不是 `{}`。
6. 保留当前两个旧应用可运行，确认合并版失败时可回滚。

验收基线为：现有相关回归测试全部通过；prepare 和 run 两条真实路径均能在 Dify UI 显示非空结构化输出。

## 7. 实施顺序

1. 抽象协议草稿存储与过期清理，并添加服务端 API。
2. 扩展 token payload、prepare helper 和 confirmation helper。
3. 为 Dify 生成器增加合并 DSL 的 prepare/run 分支和直接终端输出。
4. 添加单元、DSL、失败路径和安全边界测试。
5. 生成合并 DSL，导入新的 Dify 应用，先验证 prepare，再验证 run 和失败场景。
6. 更新操作指南，保留两个旧工作流作为回滚目标。


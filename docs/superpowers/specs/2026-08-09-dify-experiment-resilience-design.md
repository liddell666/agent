# Dify 表格实验复现工作流韧性升级设计

## 目标

在不修改 `repro-runner` API 的前提下升级已发布的“表格实验复现 Workflow”：让 `drop_duplicates` 用户开关真正控制实验参数，并让数据验证与实验执行的 HTTP 故障返回结构化、可读的结果，而不是直接中断工作流。

## 当前状态

- 工作流已经能够上传 CSV、验证数据、运行随机森林实验并输出 Markdown 报告。
- 成功路径已用 `2training_samples_15180.csv` 验证，ROC AUC 为 `0.871388`，Accuracy 为 `0.921607`。
- `run_experiment` 当前把 `drop_duplicates` 固定为文本 `false`，用户输入的布尔值未生效。
- 两个 HTTP 节点启用了失败重试，但异常处理仍为“无”；连接失败、超时或不可恢复的 HTTP 错误会使工作流失败。
- Dify 要求不同输出节点的字段名全局唯一，因此成功与各失败分支使用不同的外部输出名称。

## 方案选择

### 采用：工作流内标准化

新增代码节点将布尔值转换为接口表单可接受的 `"true"` 或 `"false"`，并在 Dify 内为 HTTP 异常建立统一的错误结果。该方案不修改服务端契约，升级范围最小，出现问题时可直接回退工作流版本。

### 未采用：修改后端容错

可以让后端忽略缺失参数或从其他字段推断布尔值，但会把 Dify 的界面限制泄漏到服务端，并影响其他 API 调用者。

### 未采用：改为 JSON 请求

文件上传仍需要 multipart/form-data；拆分文件上传和 JSON 参数会增加节点与接口复杂度，不符合本次小范围升级目标。

## 工作流设计

### 输入标准化

在第一个条件分支的成功路径、`run_experiment` 之前新增 `normalize_experiment_inputs` 代码节点。

输入：

- `drop_duplicates`：`用户输入.drop_duplicates`，Boolean。

输出：

- `drop_duplicates_text`：String，仅允许 `"true"` 或 `"false"`。

`run_experiment` 的 form-data 字段 `drop_duplicates` 改为引用 `normalize_experiment_inputs.drop_duplicates_text`。其他字段保持不变。

### HTTP 异常处理

`validate_dataset` 与 `run_experiment` 保留最多 3 次重试。重试耗尽后进入各自的失败处理路径：

- 数据验证请求失败：生成 `valid=false` 的验证 JSON、空实验 JSON 和服务不可用说明。
- 实验请求失败：保留已经成功的验证 JSON，生成 `status=failed` 的实验 JSON和服务不可用说明。

错误对象至少包含：

- `code`：稳定的机器可读错误码。
- `message`：简体中文说明。
- `stage`：`validate_dataset` 或 `run_experiment`。

不得输出主机内部堆栈、数据库信息、密钥或完整响应头。

### 输出契约

成功路径继续输出：

- `validation_json`
- `experiment_json`
- `markdown_summary`

逻辑验证失败路径继续使用：

- `validation_failure_json`
- `validation_failure_experiment_json`
- `validation_failure_summary`

实验拒绝或失败路径继续使用：

- `experiment_failure_validation_json`
- `experiment_failure_json`
- `experiment_failure_summary`

HTTP 异常路径复用对应阶段的失败输出节点，避免增加新的外部字段。

## 数据流

1. 用户上传 CSV 并设置目标列、测试比例、随机种子和去重开关。
2. `validate_dataset` 验证数据；HTTP 异常进入验证失败结果。
3. 验证响应解析为规范 JSON；逻辑不通过进入验证失败结果。
4. `normalize_experiment_inputs` 将布尔值转换为表单文本。
5. `run_experiment` 执行实验；HTTP 异常进入实验失败结果。
6. 成功响应解析并格式化为 Markdown；业务拒绝进入实验失败结果。
7. 各分支由唯一输出节点终止。

## 测试与验收

发布前必须满足：

1. Dify 检查清单显示所有问题均已解决。
2. `drop_duplicates=false` 能成功运行，指标与当前基线一致。
3. `drop_duplicates=true` 能成功运行，响应中的有效行数或重复行处理信息反映去重设置。
4. 错误目标列触发验证失败分支，工作流本身保持成功结束并返回结构化错误。
5. HTTP 异常分支的节点映射、错误码和输出字段通过单节点测试或可控故障测试验证。
6. 成功测试后发布更新，运行页面仍显示全部五个输入字段。

## 回退策略

如果升级后的测试失败，不发布新草稿；保留当前已发布版本。若发布后发现回归，通过 Dify 版本历史恢复上一发布版本。后端服务和数据文件均不做破坏性修改。

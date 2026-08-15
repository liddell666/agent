# 论文复现档案工作流：导入与配置

## 已完成的工作流

正式应用名为 `论文复现档案`。当前链路是：

`论文 PDF → paper-parser → 解析响应校验 → DeepSeek 结构化抽取 → 页码证据校验 → JSON + Markdown 输出`

工作流接收三个输入：

- `paper_pdf`：必填，单个 PDF 文档。
- `user_notes`：可选，最多 2000 字，仅作为用户补充上下文，不能替代论文证据。
- `target_language`：必填，可选 `简体中文` 或 `English`。

成功时返回：

- `dossier_json`：经过证据校验的结构化论文档案。
- `summary_markdown`：带页码引用的阅读摘要。

## 在当前电脑上运行

1. 启动 Dify 与解析器：

   ```powershell
   cd C:\Users\17716\Documents\arcgis\.worktrees\paper-intelligence-foundation\paper-repro-agent
   docker compose -f .\compose.yaml up -d paper-parser
   ```

2. 打开 `http://localhost/`，进入工作室中的 `论文复现档案`。
3. 确认 DeepSeek 模型凭据可用，LLM 节点使用 `deepseek-v4-flash`。
4. 确认环境变量 `PARSER_API_TOKEN` 已填写。它必须与项目 `.env` 中的 `PAPER_PARSER_API_TOKEN` 值相同。
5. 点击“测试运行”，上传 PDF，保持默认输出语言或切换为 English，然后运行。

不要把 DeepSeek API Key 或解析器令牌写入 DSL、Markdown、Git、截图或聊天。

## 从 DSL 导入到另一套 Dify

DSL 文件为 `dify/paper-dossier-workflow.yml`。

1. 在 Dify 工作室选择“导入 DSL 文件”。
2. 选择上述 YAML 文件并创建应用。
3. 安装 DSL 声明的官方 `langgenius/deepseek` 插件。
4. 在模型供应商中添加自己的 DeepSeek 凭据。
5. 打开新应用的环境变量，重新填写 `PARSER_API_TOKEN`。导出的 DSL 故意将 Secret 值保留为空。
6. 确保 Dify 的 Docker 网络中存在名为 `paper-parser` 的服务，并且 `http://paper-parser:8000/v1/parse` 可访问。
7. 用 `tests/fixtures/minimal-paper.pdf` 运行一次验收测试。

## LLM profile workflows

默认产物仍然是 DeepSeek 配置。直接运行不带参数的 DSL 构建脚本时，生成的仍然是现有的 DeepSeek 文件；本地 Ollama 仅作为单独导入的显式 profile 使用，不替换默认产物。

需要生成本地 Ollama 导入包时，运行：

```powershell
python scripts/build_multimodel_dsl.py --profile ollama
```

这会额外生成带 `-ollama` 后缀的 DSL 文件，其中合并工作流为 `dify/paper-comparison-merged-workflow-ollama.yml`。把它作为新的 Dify 应用导入，不要覆盖现有 DeepSeek 应用。

导入 `dify/paper-comparison-merged-workflow-ollama.yml` 后，按以下方式配置：

1. 安装 DSL 中固定声明的 Ollama marketplace dependency。
2. 在 Dify 的模型供应商里选择 `qwen3:8b`。
3. 将该 provider 的 Base URL 设置为 `http://ollama:11434`，并确保这是 Dify Docker 网络内可访问的地址，不要追加 `/api`。
4. 只在 Dify UI 中填写 `PARSER_API_TOKEN` 和 `DIFY_PROTOCOL_SECRET`；不要把这两个 secret 写进 Markdown、YAML、Git 或截图。
5. 保留 DeepSeek 产物作为默认共享配置；不要把本地 Ollama profile 回写成默认 DSL。

在打开 Dify 之前，先做只读健康检查：

```powershell
Invoke-RestMethod http://localhost:11434/api/tags
docker run --rm --network docker_default curlimages/curl:8.10.1 http://ollama:11434/api/tags
```

两条命令都应返回包含 `qwen3:8b` 的模型列表；若临时 `curlimages/curl:8.10.1` 镜像不可用，可改用现有 Dify 容器内的等价只读请求，并在验收记录中说明替代方式。

本地 Ollama 验收时，导入后的新应用使用以下固定夹具：

1. `prepare`：上传 `tests/fixtures/minimal-paper.pdf` 与 `tests/fixtures/general_binary_numeric.csv`。
2. `run`：重新上传完全相同的 CSV，并使用包含 `logistic_regression` 的短模型列表。

验收记录只保留这些信息：状态、耗时、validation aggregates、experiment status、model status、comparison status、runner provenance。还要确认 LLM 节点完成、dossier 仍通过校验、protocol 路径成功，且最终报告包含 `git_commit`、`source_digest` 和 `workflow_version`。不要记录 PDF 正文、CSV 行内容、API key、protocol token 或完整请求负载。

## 验收结果

2026-08-04 的端到端测试成功生成：

- 数据集 `TinySet`，证据页码 `p.1`；
- 方法 `Logistic regression`，证据页码 `p.1`；
- 指标 `AUC=0.91`，数据集 `TinySet`，划分 `test`，证据页码 `p.2`；
- 对缺失标题、研究问题和复现细节给出明确缺口，而不是猜测。

如果 HTTP 节点返回 422 并提示缺少 `file`，检查其 Body 是否为 `form-data`，键名是否为 `file`、类型是否为 `file`，值是否绑定到 `用户输入 / paper_pdf`。


## Reliable general-binary runner path

For an ordinary binary CSV, use the runner contract in this order:

`POST /v1/diagnose-dataset` -> create and confirm a manifest -> `POST /v1/jobs` -> poll `GET /v1/jobs/{job_id}` -> `GET /v1/jobs/{job_id}/result` -> `POST /v1/compare-model-suite-result`.

The diagnosis and polling responses contain aggregate metadata only. The
manifest binds the confirmed target, feature list, dataset identity and split
settings. The job endpoint is asynchronous and bounded; stop polling on
`succeeded`, `partial`, `failed`, `cancelled` or `needs_retry` rather than
waiting forever. A `needs_retry` job keeps its staged input and may be resumed
after the `repro-runner` container restarts.

The persistent job database and staged input directory are explicitly mounted
under `/data/experiments` in `compose.yaml`. Configure them with
`REPRO_RUNNER_JOB_STORE_PATH` and `REPRO_RUNNER_JOB_WORK_DIR`; do not use a
container-only path for a production deployment.

Fast local contract checks use the committed synthetic fixtures:

```powershell
pytest -q tests/test_end_to_end_general_binary.py
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose config
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke_multimodel.ps1 `
  -CsvPath 'E:\论文复现\成果\2training_samples_15180.csv' `
  -TargetColumn 'Y_cls'
```

The smoke script reports only dataset aggregates, job/model statuses, ranking,
the shared held-out digest and comparison counts. It never prints CSV rows,
PDF text or secrets.

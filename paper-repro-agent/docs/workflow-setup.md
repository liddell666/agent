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

## 验收结果

2026-08-04 的端到端测试成功生成：

- 数据集 `TinySet`，证据页码 `p.1`；
- 方法 `Logistic regression`，证据页码 `p.1`；
- 指标 `AUC=0.91`，数据集 `TinySet`，划分 `test`，证据页码 `p.2`；
- 对缺失标题、研究问题和复现细节给出明确缺口，而不是猜测。

如果 HTTP 节点返回 422 并提示缺少 `file`，检查其 Body 是否为 `form-data`，键名是否为 `file`、类型是否为 `file`，值是否绑定到 `用户输入 / paper_pdf`。


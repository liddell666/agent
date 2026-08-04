# Dify 论文复现 Agent 配置指南

## 当前部署状态

- Dify：自托管 `1.16.0`，入口 `http://localhost/`。
- 解析服务：`paper-parser`，仅在 Docker 网络 `docker_default` 内提供 `http://paper-parser:8000`，不映射宿主机端口。
- 解析接口：`POST /v1/parse`，multipart 字段名 `file`，请求头 `X-Parser-Token`。
- 文件上限：50 MB；最大页数：400；并发解析数：1。
- Dify SSRF 代理：只额外允许域名 `paper-parser`，没有放开私网 IP 段。
- Dify 配置备份：`E:\Docker\Projects\dify\docker\.env.backup-20260804-115047`。
- 2026-08-04 验证：两页测试 PDF 返回 `page_count=2` 和 2 个带页码元素；Dify 代理可访问解析器，未列入白名单的私网主机返回 403；Windows 与 Linux 容器内均为 23 项测试通过。

## 1. 日常启动与健康检查

在项目目录运行。由于本机 PowerShell 禁止直接执行脚本，使用仅对本次命令生效的 `ExecutionPolicy Bypass`：

```powershell
cd C:\Users\17716\Documents\arcgis\.worktrees\paper-intelligence-foundation\paper-repro-agent
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_environment.ps1
docker compose -f .\compose.yaml up -d paper-parser
```

预期看到 Docker、Dify 1.16.0、`docker_default`、80/443 端口检查通过，解析器状态为 `RUNNING`。Docker 自身健康状态可用下列命令确认：

```powershell
docker inspect paper-parser --format '{{.State.Health.Status}}'
```

## 2. 配置 DeepSeek 模型

### 2.1 登录与权限

1. 打开 `http://localhost/` 并登录 Dify。
2. 使用工作区所有者或管理员账号；普通成员不能修改模型供应商。
3. 不要把 Dify 密码或 DeepSeek API Key 发到聊天、写入项目 `.env`、工作流变量、Markdown、截图或 Git。

### 2.2 安装官方 DeepSeek 插件

1. 打开顶部 **插件**，进入 **Marketplace**。
2. 搜索 `DeepSeek`。
3. 只选择发布者为 `langgenius`、来源为 Dify Marketplace 的模型插件。不要安装名称相似的社区插件，也不要从未知 GitHub 地址或本地包安装。
4. 如果已经安装，先记录版本和权限，再更新到可识别 DeepSeek V4 模型的版本。

Dify 1.x 把模型供应商作为插件管理；Marketplace 包经过审核，而 GitHub/本地包不经过同样的 Marketplace 审核。官方插件源码仓库为 `langgenius/dify-official-plugins`。

### 2.3 添加 DeepSeek 凭据

1. 打开右上角头像菜单 → **设置** → **模型供应商**。
2. 找到 **DeepSeek**，选择 **设置/添加凭据**。
3. API Key：只在 Dify 的 `secret-input` 凭据框中粘贴。
4. API Base URL：保留官方地址 `https://api.deepseek.com`，除非你明确使用自己的兼容网关。
5. 保存。Dify 会在保存时调用供应商的凭据校验逻辑。

截至 2026-08-04，DeepSeek 官方模型名应优先使用：

- `deepseek-v4-flash`：建议作为初次联调和日常低成本抽取模型。
- `deepseek-v4-pro`：建议用于质量优先的最终论文档案。

旧名 `deepseek-chat` 和 `deepseek-reasoner` 已在 2026-07-24 停用，不应再作为新工作流默认值。如果 Dify 里只能看到旧名，先更新官方 DeepSeek 插件；仍不可见时，不要继续构建正式工作流，先排查插件版本与模型列表。

### 2.4 最小模型调用验证

1. 新建一个临时 **Workflow**，名称 `DeepSeek 连接测试`。
2. 添加开始节点、一个 LLM 节点和结束节点。
3. LLM 选择 `deepseek-v4-flash`，关闭思考模式（若界面提供），温度设为 `0.1`。
4. 提示词仅填写：`Return exactly: DEEPSEEK_OK`
5. 运行一次，预期最终内容严格为 `DEEPSEEK_OK`。
6. 验证后删除临时工作流，避免与正式论文工作流混淆。

验证记录（不含密钥）：

- 官方插件发布者：`langgenius`
- 插件版本：`0.0.19`
- 凭据名称：`deepseek-official`
- API Base URL：`https://api.deepseek.com`
- 使用模型：`deepseek-v4-flash`
- 节点参数：温度 `0.1`、思考模式关闭、推理标签分离开启
- 测试结果：2026-08-04 运行成功，耗时 `2.691s`，正文严格为 `DEEPSEEK_OK`
- 清理结果：临时工作流 `DeepSeek 连接测试` 已删除

## 3. DeepSeek 故障排查

按下列顺序检查：

1. **凭据校验失败**：确认 Key 没有多余空格、账户余额/配额正常、Base URL 为 `https://api.deepseek.com`。
2. **模型不可见**：确认安装的是 `langgenius` 官方插件，更新插件并刷新模型供应商页面；确认旧模型名已替换为 V4 名称。
3. **插件调用失败**：查看最近日志，但不要把包含凭据的完整环境导出到工单：

   ```powershell
   docker logs --tail 200 docker-plugin_daemon-1
   docker logs --tail 200 docker-api-1
   ```

4. **网络失败**：从 API 容器检查官方域名解析和 HTTPS，切勿把 API Key 放进测试命令：

   ```powershell
   docker exec docker-api-1 python -c "import urllib.request; print(urllib.request.urlopen('https://api.deepseek.com', timeout=15).status)"
   ```

5. **JSON 输出异常**：在提示词中明确包含 `JSON` 字样和目标示例，设置合理的最大输出长度，并保留后续 JSON Schema 校验。DeepSeek 官方说明 JSON Output 偶尔可能返回空内容，因此正式工作流仍要有失败分支和最多两次修复尝试。

## 4. 解析器操作

重新构建并启动：

```powershell
docker compose -f .\compose.yaml up -d --build paper-parser
```

运行测试 PDF：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke_parse.ps1 -PdfPath .\tests\fixtures\minimal-paper.pdf
```

预期：`OK: parser returned page_count 2 ...`。

查看解析器日志：

```powershell
docker logs --tail 200 paper-parser
```

停止解析器不会影响 Dify 数据库：

```powershell
docker compose -f .\compose.yaml stop paper-parser
```

## 5. 备份与恢复 Dify 配置

当前 Dify `.env` 修改仅涉及上传上限和 `paper-parser` 域名白名单。需要回滚时：

1. 停止继续编辑当前 `.env`。
2. 将备份文件复制回 `E:\Docker\Projects\dify\docker\.env`。
3. 仅重建无状态服务：

   ```powershell
   docker compose -f E:\Docker\Projects\dify\docker\docker-compose.yaml up -d --force-recreate api worker worker_beat ssrf_proxy nginx
   ```

不要删除 Dify 的数据库、Redis、Weaviate 或存储卷。

## 官方参考

- Dify 模型供应商：https://docs.dify.ai/zh-hans/guides/model-configuration/readme
- Dify Marketplace 发布与审核：https://docs.dify.ai/en/develop-plugin/publishing/marketplace-listing/release-overview
- Dify 官方插件仓库：https://github.com/langgenius/dify-official-plugins
- DeepSeek 模型与定价：https://api-docs.deepseek.com/quick_start/pricing
- DeepSeek JSON Output：https://api-docs.deepseek.com/guides/json_mode/

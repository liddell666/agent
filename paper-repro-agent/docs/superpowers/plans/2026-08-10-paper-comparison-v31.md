# 论文对标复现 V3.1（PDF+CSV） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建一个独立的 Dify V3.1 工作流，使用户只上传论文 PDF 和训练 CSV 即可完成证据解析、独立实验、指标对比和报告生成。

**Architecture:** 复用 `paper-parser` 的 PDF 接口和 `repro-runner` 的 CSV/实验/对比接口；在 Dify 中增加 DeepSeek PaperDossier 抽取和一个纯标准库 dossier 适配器，将完整论文档案转换成现有对比节点需要的规范化指标。V3.1 使用独立 DSL 和新应用，V3/V2 发布版本不变。

**Tech Stack:** Python 3.12、FastAPI（现有服务不扩展总控接口）、PyYAML 6、Dify 0.7 DSL、DeepSeek Dify 模型、pytest、Docker Compose。

## Global Constraints

- 只允许本地上传 `paper_pdf`（`.pdf`）和 `training_csv`（`.csv`）；`metric_overrides_json` 默认 `[]`。
- `0 < close_threshold < partial_threshold <= 1`，默认 `0.05` / `0.10`；`test_size` 范围 `0.1–0.5`。
- 所有 HTTP 节点使用 `http://paper-parser:8000/v1/parse` 或 `http://repro-runner:8001/v1/{validate-dataset,run-experiment,compare-result}`，重试 2 次、间隔 1000ms，并设置正的有限超时。
- `run-experiment` 的 `idempotency_key` 绑定 `sys.workflow_run_id`；不在 DSL、代码、日志中保存 API Key。
- PDF 解析失败、Schema 无效、无支持指标或未解决歧义时，不输出复现结论；只能输出明确失败状态和人工覆盖提示。
- 现有 `dify/paper-comparison-workflow.yml`、V3/V2 应用和旧版回滚应用不得修改、删除或覆盖。
- 每个实现任务结束都必须运行该任务列出的测试并单独提交；不使用 `git reset --hard` 或清理仓库外用户文件。

---

## 文件地图

- Create: `paper-repro-agent/dify/code/normalize_paper_dossier.py` — 将已校验证据档案规范化为 V3 对比所需的指标对象。
- Create: `paper-repro-agent/dify/paper-comparison-v31-workflow.yml` — V3.1 独立 Dify DSL，包含 PDF 解析、DeepSeek 抽取和现有实验链路。
- Create: `paper-repro-agent/scripts/build_v31_dsl.py` — 从现有 parser/comparison DSL 生成可导入、稳定 ID 的 V3.1 DSL。
- Create: `paper-repro-agent/tests/test_dify_v31_code.py` — dossier 适配器和失败语义测试。
- Create: `paper-repro-agent/tests/test_dify_v31_dsl.py` — V3.1 DSL 输入、节点、边和安全契约测试。
- Modify: `paper-repro-agent/tests/test_dify_code.py` — 保持 V3 代码节点与新适配器契约一致的回归断言。
- Create: `paper-repro-agent/dify/paper-comparison-v31-workflow.md` — V3.1 导入、变量、故障码和发布说明。
- Create: `paper-repro-agent/scripts/smoke_comparison_v31.ps1` — 直接 runner smoke 和 Dify 运行矩阵记录模板；不包含密钥。
- Modify: `paper-repro-agent/README.md`（若不存在则 Create）— 链接 V3.1 指南并标明 V3/V2 回滚 URL 不变。

### Task 1: 为 PaperDossier 适配器写 RED 测试

**Files:**
- Create: `paper-repro-agent/tests/test_dify_v31_code.py`
- Modify: `paper-repro-agent/tests/test_dify_code.py:1-25`

**Interfaces:**
- Produces the required contract for `normalize_paper_dossier.main(paper_dossier_json: str, metric_overrides_json: str = "[]") -> dict[str, Any]`.
- Outputs must be `dossier_ok: bool`, `dossier_json: str`, `dossier_errors: str`, and `dossier_warnings: str`.

- [ ] **Step 1: Write the failing tests**

```python
import json

from dify.code.normalize_paper_dossier import main


def paper_dossier(**changes):
    value = {
        "title": "Test paper",
        "research_problem": "Test problem",
        "task_type": "classification",
        "datasets": [],
        "methods": [],
        "metrics": [
            {
                "name": "AUC",
                "reported_value": "91%",
                "dataset": "TinySet",
                "split": "test",
                "evidence": [{"page": 2, "source_text": "AUC=0.91", "source": "paper"}],
            }
        ],
        "gaps": [],
    }
    value.update(changes)
    return value


def test_normalizes_supported_metric_and_preserves_evidence():
    result = main(json.dumps(paper_dossier(), ensure_ascii=False))
    assert result["dossier_ok"] is True
    normalized = json.loads(result["dossier_json"])
    assert normalized["metrics"][0]["normalized_name"] == "roc_auc"
    assert normalized["metrics"][0]["supported"] is True
    assert normalized["metrics"][0]["reported_value"] == 0.91
    assert normalized["metrics"][0]["evidence"][0]["page"] == 2


def test_marks_duplicate_metric_ambiguous_until_dataset_and_split_both_match():
    dossier = paper_dossier(
        metrics=[
            {"name": "AUC", "reported_value": 0.91, "dataset": "A", "split": "test", "evidence": [{"page": 1, "source_text": "a", "source": "paper"}]},
            {"name": "AUC", "reported_value": 0.88, "dataset": "A", "split": "val", "evidence": [{"page": 1, "source_text": "b", "source": "paper"}]},
        ]
    )
    result = main(json.dumps(dossier), json.dumps([{"name": "AUC", "dataset": "A", "split": "test", "reported_value": 0.90}]))
    normalized = json.loads(result["dossier_json"])
    assert result["dossier_ok"] is True
    assert normalized["metrics"][0]["ambiguous"] is False
    assert normalized["metrics"][1]["ambiguous"] is True
    assert normalized["warnings"] == ["ambiguous_metric"]


def test_rejects_invalid_schema_and_unsupported_only_dossier():
    invalid = main(json.dumps({"title": "missing metrics"}))
    assert invalid["dossier_ok"] is False
    assert json.loads(invalid["dossier_errors"])[0]["code"] == "invalid_dossier"

    unsupported = main(json.dumps(paper_dossier(metrics=[{"name": "MCC", "reported_value": 0.3, "evidence": [{"page": 1, "source_text": "mcc", "source": "paper"}]}])))
    assert unsupported["dossier_ok"] is False
    assert json.loads(unsupported["dossier_errors"])[0]["code"] == "no_supported_metrics"


def test_invalid_override_does_not_echo_payload():
    result = main(json.dumps(paper_dossier()), "not-json")
    assert result["dossier_ok"] is False
    assert json.loads(result["dossier_errors"])[0]["code"] == "invalid_metric_overrides"
    assert "not-json" not in json.dumps(result, ensure_ascii=False)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `pytest -q tests/test_dify_v31_code.py`

Expected: collection fails with `ModuleNotFoundError: No module named 'dify.code.normalize_paper_dossier'`.

- [ ] **Step 3: Commit only the RED tests**

```powershell
git add tests/test_dify_v31_code.py tests/test_dify_code.py
git commit -m "test: define v31 paper dossier normalization contract"
```

### Task 2: Implement the deterministic dossier adapter

**Files:**
- Create: `paper-repro-agent/dify/code/normalize_paper_dossier.py`
- Test: `paper-repro-agent/tests/test_dify_v31_code.py`

**Interfaces:**
- Consumes the UTF-8 JSON emitted by `validate_evidence` and an override JSON array.
- Produces the four outputs from Task 1; the successful `dossier_json` preserves the complete PaperDossier fields and replaces `metrics` with normalized V3 metric objects.

- [ ] **Step 1: Implement only standard-library normalization**

```python
import json
import math
import re

SUPPORTED_METRICS = {"roc_auc", "accuracy", "balanced_accuracy", "precision", "recall", "f1"}
ALIASES = {"auc": "roc_auc", "roc_auc": "roc_auc"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _object(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _name(value):
    text = "_".join(str(value).strip().casefold().replace("-", " ").replace("_", " ").split())
    return ALIASES.get(text, text)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return round(parsed / 100 if percent else parsed, 6)


def main(paper_dossier_json: str, metric_overrides_json: str = "[]") -> dict:
    dossier = _object(paper_dossier_json)
    if dossier is None or not isinstance(dossier.get("metrics"), list):
        return {"dossier_ok": False, "dossier_json": "{}", "dossier_errors": _json([{"code": "invalid_dossier", "message": "Paper dossier JSON is invalid."}]), "dossier_warnings": "[]"}
    try:
        overrides = json.loads(metric_overrides_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return {"dossier_ok": False, "dossier_json": "{}", "dossier_errors": _json([{"code": "invalid_metric_overrides", "message": "Metric overrides must be a JSON array."}]), "dossier_warnings": "[]"}
    if not isinstance(overrides, list) or any(not isinstance(item, dict) for item in overrides):
        return {"dossier_ok": False, "dossier_json": "{}", "dossier_errors": _json([{"code": "invalid_metric_overrides", "message": "Metric overrides must be a JSON array."}]), "dossier_warnings": "[]"}
    metrics = []
    for item in dossier["metrics"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            return {"dossier_ok": False, "dossier_json": "{}", "dossier_errors": _json([{"code": "invalid_dossier", "message": "Every metric must have a name."}]), "dossier_warnings": "[]"}
        normalized = _name(item["name"])
        metric = dict(item)
        metric.update({"normalized_name": normalized, "supported": normalized in SUPPORTED_METRICS, "reported_value": _number(item.get("reported_value")), "source": "paper_dossier", "ambiguous": False})
        metrics.append(metric)
    groups = {}
    for metric in metrics:
        groups.setdefault(metric["normalized_name"], []).append(metric)
    for group in groups.values():
        if len(group) > 1:
            for metric in group:
                metric["ambiguous"] = True
    for override in overrides:
        candidates = list(groups.get(_name(override.get("name", "")), []))
        if len(candidates) > 1 and ("dataset" in override or "split" in override):
            candidates = [item for item in candidates if ("dataset" not in override or item.get("dataset") == override.get("dataset")) and ("split" not in override or item.get("split") == override.get("split"))]
        if len(candidates) != 1:
            continue
        selected = candidates[0]
        for field in ("reported_value", "dataset", "split", "dataset_id", "test_size", "random_state", "train_rows", "test_rows", "test_digest"):
            if field in override:
                selected[field] = _number(override[field]) if field == "reported_value" else override[field]
        selected["source"] = "manual_override"
        selected["ambiguous"] = False
    warnings = ["ambiguous_metric"] if any(item["ambiguous"] for item in metrics) else []
    result = dict(dossier)
    result.update({"valid": True, "metrics": metrics, "warnings": warnings, "errors": []})
    if not metrics:
        return {"dossier_ok": False, "dossier_json": _json(result), "dossier_errors": _json([{"code": "no_reported_metrics", "message": "Paper dossier has no metrics."}]), "dossier_warnings": _json(warnings)}
    if not any(item["supported"] and item["reported_value"] is not None and not item["ambiguous"] for item in metrics):
        return {"dossier_ok": False, "dossier_json": _json(result), "dossier_errors": _json([{"code": "no_supported_metrics", "message": "No unambiguous supported metrics are available."}]), "dossier_warnings": _json(warnings)}
    return {"dossier_ok": True, "dossier_json": _json(result), "dossier_errors": "[]", "dossier_warnings": _json(warnings)}
```

- [ ] **Step 2: Run focused tests and then the existing Dify tests**

Run: `pytest -q tests/test_dify_v31_code.py tests/test_dify_code.py`

Expected: all focused tests pass; the existing V3 formatter and parser tests remain green.

- [ ] **Step 3: Commit the adapter**

```powershell
git add dify/code/normalize_paper_dossier.py tests/test_dify_v31_code.py
git commit -m "feat: normalize extracted paper dossier for v31"
```

### Task 3: Build the standalone V3.1 DSL

**Files:**
- Create: `paper-repro-agent/scripts/build_v31_dsl.py`
- Create: `paper-repro-agent/dify/paper-comparison-v31-workflow.yml`
- Test: `paper-repro-agent/tests/test_dify_v31_dsl.py`

**Interfaces:**
- Consumes `dify/paper-dossier-workflow.yml`, `dify/paper-comparison-workflow.yml`, `dify/paper-dossier-system-prompt.md`, and `dify/paper-dossier-schema.json`.
- Produces a YAML document with a single Start and Output node, four HTTP nodes, one DeepSeek LLM node, parser/evidence/dossier conditional gates, all V3 experiment/compare branches, and six terminal string outputs.

- [ ] **Step 1: Write RED DSL contract tests**

```python
from pathlib import Path
import yaml

DSL = Path(__file__).parents[1] / "dify" / "paper-comparison-v31-workflow.yml"


def document():
    return yaml.safe_load(DSL.read_text(encoding="utf-8"))


def nodes(doc):
    return doc["workflow"]["graph"]["nodes"]


def test_start_has_pdf_csv_and_safe_defaults():
    start = next(node for node in nodes(document()) if node["data"]["type"] == "start")
    variables = {item["variable"]: item for item in start["data"]["variables"]}
    assert variables["paper_pdf"]["allowed_file_extensions"] == [".PDF"]
    assert variables["training_csv"]["allowed_file_extensions"] == [".CSV"]
    assert variables["metric_overrides_json"]["default"] == "[]"


def test_v31_has_exact_http_contract_and_llm_prompt():
    data = [node["data"] for node in nodes(document())]
    urls = {item["url"] for item in data if item["type"] == "http-request"}
    assert urls == {
        "http://paper-parser:8000/v1/parse",
        "http://repro-runner:8001/v1/validate-dataset",
        "http://repro-runner:8001/v1/run-experiment",
        "http://repro-runner:8001/v1/compare-result",
    }
    llm = next(item for item in data if item["type"] == "llm")
    assert "PaperDossier" in llm["prompt_template"][0]["text"]


def test_v31_has_one_output_and_six_strings():
    ends = [node for node in nodes(document()) if node["data"]["type"] == "end"]
    assert len(ends) == 1
    outputs = ends[0]["data"]["outputs"]
    assert {item["variable"] for item in outputs} == {"dossier_json", "validation_json", "experiment_json", "comparison_json", "assessment_json", "markdown_report"}
    assert all(item["value_type"] == "string" for item in outputs)


def test_v31_contains_failure_gates_and_no_secret_values():
    text = DSL.read_text(encoding="utf-8")
    assert "fail-branch" in text
    assert "PARSER_API_TOKEN" in text
    assert "sk-" not in text
    assert "deepseek" in text.casefold()
```

- [ ] **Step 2: Run the DSL tests to verify RED**

Run: `pytest -q tests/test_dify_v31_dsl.py`

Expected: collection fails because `paper-comparison-v31-workflow.yml` does not exist.

- [ ] **Step 3: Implement the composer and generate the DSL**

`build_v31_dsl.py` must load both source documents with `yaml.safe_load`, deep-copy parser nodes and comparison nodes, remap every node/edge ID through a deterministic prefix map, and connect the parser success gate to the existing V3 validation gate. It must set the Start variables exactly as the table in the design spec and set each HTTP retry block to:

```python
{"retry_enabled": True, "max_retries": 2, "retry_interval": 1000}
```

The script must expose:

```python
def build() -> dict: ...
def write(path: Path) -> None: ...
```

and run as:

```powershell
python scripts/build_v31_dsl.py
```

The generated LLM node must use the existing dossier prompt/schema files and must not add a second model provider or an API key. The parser HTTP node uses `form-data` key `file` bound to `paper_pdf`; the run node has a literal form key `idempotency_key` bound to `{{#sys.workflow_run_id#}}`.

- [ ] **Step 4: Run the DSL tests and verify PASS**

Run: `python scripts/build_v31_dsl.py; pytest -q tests/test_dify_v31_dsl.py tests/test_dify_v31_code.py`

Expected: V3.1 DSL is written and all focused tests pass.

- [ ] **Step 5: Commit the DSL and composer**

```powershell
git add scripts/build_v31_dsl.py dify/paper-comparison-v31-workflow.yml tests/test_dify_v31_dsl.py
git commit -m "feat: add unified pdf csv comparison workflow dsl"
```

### Task 4: Add operator documentation and smoke checks

**Files:**
- Create: `paper-repro-agent/dify/paper-comparison-v31-workflow.md`
- Create: `paper-repro-agent/scripts/smoke_comparison_v31.ps1`
- Modify: `paper-repro-agent/README.md` or Create if absent

**Interfaces:**
- Documentation names the exact V3.1 inputs, defaults, error codes, import steps, and rollback behavior.
- Smoke script uses only runner URLs and local file paths passed as parameters; it must never contain a token.

- [ ] **Step 1: Add the smoke script parameters and safe commands**

```powershell
param(
  [Parameter(Mandatory=$true)][string]$CsvPath,
  [Parameter(Mandatory=$false)][string]$BaseUrl = "http://localhost:8001"
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $CsvPath)) { throw "CSV not found: $CsvPath" }
$health = Invoke-RestMethod "$BaseUrl/healthz"
if ($health.status -ne "ok") { throw "runner health check failed" }
Write-Host "Runner healthy; upload the PDF and CSV through the V3.1 Dify UI for the model-backed path."
```

- [ ] **Step 2: Write the guide**

Document: `paper_pdf` must be the original PDF, `training_csv` must be the tabular training sample (for this project `2training_samples_15180.csv`), `target_column` defaults to `Y_cls`, and `metric_overrides_json` is a JSON array such as `[{"name":"AUC","dataset":"TinySet","split":"test","reported_value":0.91}]`. State that `highly_similar` is not strict reproduction.

- [ ] **Step 3: Run documentation/shell checks and commit**

Run: `rg -n "API.?Key|sk-|PARSER_API_TOKEN|paper_pdf|training_csv|metric_overrides_json" dify/paper-comparison-v31-workflow.md README.md scripts/smoke_comparison_v31.ps1`

Expected: only the environment-variable name appears; no secret value appears.

```powershell
git add dify/paper-comparison-v31-workflow.md scripts/smoke_comparison_v31.ps1 README.md
git commit -m "docs: document v31 pdf csv workflow"
```

### Task 5: Run the repository verification gate

**Files:**
- Test: all `paper-repro-agent/tests/**/*.py`
- Inspect: `paper-repro-agent/dify/paper-comparison-workflow.yml`, `paper-repro-agent/dify/paper-comparison-v31-workflow.yml`

- [ ] **Step 1: Run focused tests**

Run: `pytest -q tests/test_dify_v31_code.py tests/test_dify_v31_dsl.py tests/test_dify_code.py tests/test_dify_comparison_dsl.py`

Expected: all focused tests pass.

- [ ] **Step 2: Run the full suite**

Run: `pytest -q`

Expected: the pre-existing baseline remains green (`185 passed` plus the known deprecation warning, or a higher count with only the same warning).

- [ ] **Step 3: Check the generated DSL and diff**

Run: `python scripts/build_v31_dsl.py; git diff --check; rg -n "sk-[A-Za-z0-9]|api[_-]?key\\s*[:=]" dify/paper-comparison-v31-workflow.yml`

Expected: no secret match and no whitespace errors. Do not run `git clean` because the parent ArcGIS workspace contains user files.

- [ ] **Step 4: Commit verification notes**

```powershell
git status --short --branch
git log -5 --oneline
```

Expected: only the intended project commits are present; unrelated ArcGIS files remain untracked and untouched.

### Task 6: Import and verify V3.1 in Dify through the official UI

**Files:**
- Use: `paper-repro-agent/dify/paper-comparison-v31-workflow.yml`
- Record: `paper-repro-agent/docs/superpowers/sdd/task-v31-live-verification.md`

- [ ] **Step 1: Import as a new Dify application**

Use Dify’s visible “Import DSL” action to create `论文对标复现 V3.1（PDF+CSV）`. Do not edit the published V3 app. Set `PARSER_API_TOKEN` in the new app environment and select the already configured DeepSeek provider.

- [ ] **Step 2: Run the normal success path**

Upload the real paper PDF used by the existing dossier workflow and `2training_samples_15180.csv`; keep defaults. Record the Dify run ID, experiment ID, dataset profile, ROC AUC, strict status, approximate grade, evidence page and Markdown rendering.

- [ ] **Step 3: Run the failure matrix**

Run one case at a time and restore the URL/config after each: invalid PDF; parser outage; malformed/ambiguous dossier; a valid `metric_overrides_json` recovery; CSV validation failure; experiment outage; comparison outage. Each run must reach the one Output node and expose six string values without leaking file contents or secrets.

- [ ] **Step 4: Publish only after all cases pass**

Publish V3.1, capture its public workflow URL, verify HTTP 200 for V3.1, current V3, V2 and old rollback URLs, and verify `http://localhost:8001/healthz` (or the runner’s visible Docker health) is `{"status":"ok"}`. Leave any uncertain unpublished duplicate app untouched.

- [ ] **Step 5: Record and commit live evidence**

Write exact run IDs, timestamps, URLs, outputs, and any retry behavior into `docs/superpowers/sdd/task-v31-live-verification.md`, then run `git diff --check` and commit:

```powershell
git add docs/superpowers/sdd/task-v31-live-verification.md
git commit -m "docs: record v31 live verification"
```

### Task 7: Final review and handoff

- [ ] **Step 1: Re-run `pytest -q` and `git diff --check`**
- [ ] **Step 2: Confirm `git status --short --branch` contains no intended-file modifications after the evidence commit**
- [ ] **Step 3: Report the V3.1 URL, input files, normal result, failure behavior, rollback URLs, test count, and commit IDs**


# 合并论文准备与多模型运行工作流实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将论文 PDF 准备、协议审核和七模型异步实验合并为一个支持 `prepare`/`run` 两种模式的 Dify 工作流，第二次运行只重新上传相同 CSV，并在服务端短期保存已验证 dossier。

**Architecture:** `repro_runner` 新增协议草稿存储层，使用 HMAC token 中的 `draft_id`、manifest、dataset fingerprint 和过期时间校验请求；磁盘只保存解析后的 dossier 与元数据，不保存 PDF、CSV、token 明文。Dify 合并 DSL 在 Start 后按 `run_mode` 分支：`prepare` 解析 PDF、诊断 CSV、保存草稿并输出预览/token；`run` 校验确认 token、读取草稿、校验 CSV 后复用现有异步 job、七模型比较和报告链路。现有 prepare 工作流和 confirmed-run 工作流文件继续保留，合并 DSL 作为新应用导入验证。

**Tech Stack:** Python 3.13、FastAPI、Pydantic v2、pytest、HMAC-SHA256、JSON 原子写入、Dify YAML DSL、Dify Code/HTTP/IF/End 节点。

## Global Constraints

- `run_mode` 只能是 `prepare` 或 `run`，默认值为 `prepare`。
- `training_csv` 在两种模式下都必填；`paper_pdf` 只在 `prepare` 模式使用，`run` 不再要求 PDF 或 dossier JSON。
- 协议 token 的 TTL 默认 900 秒，允许范围为 60 至 3600 秒；过期后必须重新执行 `prepare`。
- 草稿目录位于 `Settings.storage_dir / "protocol-drafts"`；每个草稿只保存 dossier JSON、manifest/dataset 元数据、协议版本、创建时间和过期时间。
- 不把 PDF、CSV 原始字节、token 明文、API key、bearer token 或服务端 traceback 写入草稿文件、日志、DSL 或报告。
- 所有错误路径必须进入带结构化 `error_json` 和 `markdown_report` 的直接 End 节点；不得等待未执行的共享 aggregator，因此不得因分支未执行而返回 `{}`。
- 不修改或删除现有 `dify/paper-comparison-prepare-workflow.yml` 和 `dify/paper-comparison-multimodel-workflow.yml` 的回滚用途；生成器仍必须稳定生成两个旧 DSL，并额外生成合并 DSL。

## 文件地图

- Create: `src/repro_runner/protocol_drafts.py` — token 验证、草稿记录、原子保存、读取和过期清理。
- Modify: `src/repro_runner/config.py` — 协议 secret 与草稿 TTL 配置。
- Modify: `src/repro_runner/schemas.py` — 草稿 API 的请求和响应模型。
- Modify: `src/repro_runner/api.py` — 草稿 POST/GET 路由、生命周期清理和安全错误映射。
- Create: `tests/repro_runner/test_protocol_drafts.py` — 存储层、token、原子性、过期和隐私边界测试。
- Modify: `tests/repro_runner/test_api.py` — 草稿 API 的成功、重复、篡改、过期、缺失和响应脱敏测试。
- Modify: `dify/code/experiment_workflow.py` — token payload 的 `draft_id`、prepare 输出、确认输出和草稿 API 响应规范化。
- Modify: `tests/test_dify_multimodel_code.py` — helper 的 draft_id、expiry、错误码和响应校验测试。
- Modify: `scripts/build_multimodel_dsl.py` — 合并图构建器、稳定节点 ID、prepare/run 端点和额外输出文件。
- Create: `dify/paper-comparison-merged-workflow.yml` — 可导入 Dify 的合并工作流 DSL。
- Create: `tests/test_dify_merged_dsl.py` — 合并 DSL 的输入、分支、输出、边和嵌入 Python 安全测试。
- Create: `dify/paper-comparison-merged-workflow.md` — 导入、两次运行、环境变量和验收操作指南。

---

### Task 1: 实现协议草稿存储与 token 验证

**Files:**
- Create: `src/repro_runner/protocol_drafts.py`
- Modify: `src/repro_runner/config.py`
- Create: `tests/repro_runner/test_protocol_drafts.py`

**Interfaces:**
- Produces `ProtocolToken`, `ProtocolDraftRecord`, `ProtocolDraftStore`, `verify_protocol_token()` for the API layer.
- `ProtocolDraftStore.save(draft_id: str, token: str, dossier: dict[str, object]) -> ProtocolDraftRecord` performs signature, expiry, draft binding, manifest binding and atomic persistence.
- `ProtocolDraftStore.load(draft_id: str, token: str) -> ProtocolDraftRecord` performs the same token checks and returns only the stored dossier and safe metadata.
- `ProtocolDraftStore.cleanup_expired() -> int` removes only expired draft directories and returns the removal count.

- [ ] **Step 1: 写存储层失败测试**

在 `tests/repro_runner/test_protocol_drafts.py` 写入以下 imports 和确定性的 `pt1.<base64url-payload>.<hex-hmac>` 测试 token builder，并先写以下测试：

```python
import base64
import hashlib
import hmac
import json

import pytest

from repro_runner.protocol_drafts import ProtocolDraftError, ProtocolDraftStore

def _token(draft_id: str, manifest_id: str, dataset_id: str, exp: int, *, secret: str = "test-secret") -> str:
    payload = {
        "v": 1,
        "exp": exp,
        "ready": True,
        "draft_id": draft_id,
        "manifest": {"manifest_id": manifest_id, "dataset_id": dataset_id},
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"pt1.{encoded}.{signature}"
```

```python
def test_save_and_load_store_only_dossier_and_metadata(tmp_path):
    token = _token(draft_id="draft-aaaaaaaa", manifest_id="sha256:" + "1" * 64,
                   dataset_id="sha256:" + "2" * 64, exp=2_000)
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: 1_000)

    record = store.save(
        "draft-aaaaaaaa",
        token,
        {"title": "Paper", "metrics": [{"name": "AUC", "reported_value": 0.91}]},
    )

    assert record.draft_id == "draft-aaaaaaaa"
    assert record.manifest_id == "sha256:" + "1" * 64
    assert record.dataset_id == "sha256:" + "2" * 64
    assert store.load("draft-aaaaaaaa", token).dossier["title"] == "Paper"
    stored = (tmp_path / "draft-aaaaaaaa" / "draft.json").read_text(encoding="utf-8")
    assert "test-secret" not in stored
    assert "pt1." not in stored
    assert "AUC" in stored

def test_save_is_idempotent_only_for_identical_content(tmp_path):
    token = _token(draft_id="draft-aaaaaaaa", manifest_id="sha256:" + "1" * 64,
                   dataset_id="sha256:" + "2" * 64, exp=2_000)
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: 1_000)
    dossier = {"title": "Paper", "metrics": []}
    first = store.save("draft-aaaaaaaa", token, dossier)
    second = store.save("draft-aaaaaaaa", token, dossier)
    assert second == first
    with pytest.raises(ProtocolDraftError) as error:
        store.save("draft-aaaaaaaa", token, {"title": "Changed"})
    assert error.value.code == "protocol_draft_token_mismatch"

def test_load_rejects_bad_signature_wrong_draft_and_expired_token(tmp_path):
    token = _token(draft_id="draft-aaaaaaaa", manifest_id="sha256:" + "1" * 64,
                   dataset_id="sha256:" + "2" * 64, exp=1_001)
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: 1_000)
    store.save("draft-aaaaaaaa", token, {"title": "Paper"})
    with pytest.raises(ProtocolDraftError) as bad_signature:
        store.load("draft-aaaaaaaa", token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert bad_signature.value.code == "protocol_token_tampered"
    with pytest.raises(ProtocolDraftError) as malformed:
        store.load("draft-aaaaaaaa", "not-a-protocol-token")
    assert malformed.value.code == "protocol_token_malformed"
    with pytest.raises(ProtocolDraftError) as wrong_draft:
        store.load("draft-bbbbbbbb", token)
    assert wrong_draft.value.code == "protocol_draft_token_mismatch"
    expired = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: 1_002)
    with pytest.raises(ProtocolDraftError) as expired_error:
        expired.load("draft-aaaaaaaa", token)
    assert expired_error.value.code == "protocol_draft_expired"

def test_cleanup_expired_removes_only_expired_drafts(tmp_path):
    now = [900]
    store = ProtocolDraftStore(tmp_path, secret="test-secret", clock=lambda: now[0])
    store.save("draft-aaaaaaaa", _token("draft-aaaaaaaa", "sha256:" + "1" * 64, "sha256:" + "2" * 64, 999), {"title": "Old"})
    store.save("draft-bbbbbbbb", _token("draft-bbbbbbbb", "sha256:" + "3" * 64, "sha256:" + "4" * 64, 2_000), {"title": "New"})
    now[0] = 1_000
    assert store.cleanup_expired() == 1
    assert not (tmp_path / "draft-aaaaaaaa").exists()
    assert (tmp_path / "draft-bbbbbbbb" / "draft.json").exists()
```

- [ ] **Step 2: 运行失败测试，确认接口尚未实现**

Run: `pytest tests/repro_runner/test_protocol_drafts.py -q`

Expected: FAIL because `repro_runner.protocol_drafts` and `ProtocolDraftStore` do not exist.

- [ ] **Step 3: 增加 Settings 字段和最小存储实现**

在 `src/repro_runner/config.py` 的 `Settings` 增加：

```python
protocol_secret: str = "local-only-fallback-not-for-production"
protocol_draft_ttl_seconds: int = Field(default=900, ge=60, le=3600)
```

在 `src/repro_runner/protocol_drafts.py` 实现以下固定契约：

```python
@dataclass(frozen=True)
class ProtocolToken:
    version: int
    draft_id: str
    manifest_id: str
    dataset_id: str
    expires_at: int

@dataclass(frozen=True)
class ProtocolDraftRecord:
    draft_id: str
    protocol_version: int
    manifest_id: str
    dataset_id: str
    created_at: int
    expires_at: int
    dossier: dict[str, object]

class ProtocolDraftError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

def verify_protocol_token(token: str, *, secret: str, draft_id: str, now: int) -> ProtocolToken:
    """Verify pt1 token syntax, HMAC, ready flag, draft binding and expiry."""

class ProtocolDraftStore:
    def __init__(self, root: Path, *, secret: str, ttl_seconds: int = 900,
                 clock: Callable[[], float] = time.time):
        self.root = root
        self.secret = secret
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    def save(self, draft_id: str, token: str, dossier: dict[str, object]) -> ProtocolDraftRecord:
        """Validate and atomically create an idempotent draft.json."""

    def load(self, draft_id: str, token: str) -> ProtocolDraftRecord:
        """Validate token and return the unmodified stored dossier."""

    def cleanup_expired(self) -> int:
        """Delete only draft directories whose stored expires_at is in the past."""
```

`verify_protocol_token` 按现有 Dify helper 的 token 格式解析：用 `hmac.compare_digest` 校验签名，JSON payload 必须含 `v=1`、`ready=true`、`draft_id`、`manifest.manifest_id`、`manifest.dataset_id` 和整数 `exp`；`now >= exp` 返回 `protocol_draft_expired`，draft ID 或 manifest/dataset 不匹配返回 `protocol_draft_token_mismatch`，token 结构错误返回 `protocol_token_malformed`，签名错误返回 `protocol_token_tampered`，payload/version/ready 字段错误返回 `protocol_payload_invalid`。草稿文件固定写成：

```json
{
  "schema_version": 1,
  "draft_id": "draft-aaaaaaaa",
  "protocol_version": 1,
  "manifest_id": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "dataset_id": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
  "created_at": 1000,
  "expires_at": 1900,
  "dossier": {"title": "Paper", "metrics": []}
}
```

写入顺序为 `root.mkdir`、临时文件写入并 `flush`/`os.fsync`、`os.replace`；已有 draft ID 先比较安全元数据和 dossier 的规范 JSON，完全一致则直接返回，任何差异均不覆盖原文件。目录和文件名只接受 `draft-[A-Za-z0-9_-]{8,128}`，读取拒绝路径穿越。

- [ ] **Step 4: 运行存储层测试，确认通过**

Run: `pytest tests/repro_runner/test_protocol_drafts.py -q`

Expected: all storage, signature, idempotency, expiry and privacy tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/repro_runner/protocol_drafts.py src/repro_runner/config.py tests/repro_runner/test_protocol_drafts.py
git commit -m "feat: add protocol draft storage"
```

### Task 2: 暴露草稿 POST/GET API

**Files:**
- Modify: `src/repro_runner/schemas.py`
- Modify: `src/repro_runner/api.py`
- Modify: `tests/repro_runner/test_api.py`

**Interfaces:**
- `POST /v1/protocol-drafts` accepts multipart form fields `draft_id`, `protocol_token`, `dossier_json` and returns draft metadata.
- `GET /v1/protocol-drafts/{draft_id}` accepts `X-Protocol-Token` and returns draft metadata plus the parsed dossier object.
- Both routes use `ProtocolDraftStore` from `app.state.protocol_draft_store` and never log token/body contents.

- [ ] **Step 1: 写 API 失败测试**

在 `tests/repro_runner/test_api.py` 添加：

```python
def _protocol_token(secret, draft_id, manifest_id, dataset_id, exp):
    payload = {
        "v": 1,
        "exp": exp,
        "ready": True,
        "draft_id": draft_id,
        "manifest": {"manifest_id": manifest_id, "dataset_id": dataset_id},
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"pt1.{encoded}.{signature}"
```

并在文件 imports 增加 `base64`、`hashlib` 和 `hmac`。测试 client fixture 使用的 `Settings` 必须设置 `protocol_secret="test-secret"`、`storage_dir=tmp_path / "experiments"`，从而草稿文件不会写入默认 `/data/experiments`。

```python
def test_protocol_draft_post_get_round_trip(client, settings):
    token = _protocol_token(settings.protocol_secret, "draft-apiaaaaaa", "sha256:" + "1" * 64, "sha256:" + "2" * 64, 2_000)
    response = client.post(
        "/v1/protocol-drafts",
        data={
            "draft_id": "draft-apiaaaaaa",
            "protocol_token": token,
            "dossier_json": json.dumps({"title": "Paper", "metrics": []}),
        },
    )
    assert response.status_code == 200
    assert response.json()["draft_id"] == "draft-apiaaaaaa"
    fetched = client.get(
        "/v1/protocol-drafts/draft-apiaaaaaa",
        headers={"X-Protocol-Token": token},
    )
    assert fetched.status_code == 200
    assert fetched.json()["dossier"]["title"] == "Paper"
    assert fetched.json()["manifest_id"] == "sha256:" + "1" * 64

@pytest.mark.parametrize("case", ["missing", "expired", "tampered", "wrong_draft"])
def test_protocol_draft_rejects_invalid_access(client, settings, case):
    valid = _protocol_token(settings.protocol_secret, "draft-apiaaaaaa", "sha256:" + "1" * 64, "sha256:" + "2" * 64, 2_000)
    client.post(
        "/v1/protocol-drafts",
        data={"draft_id": "draft-apiaaaaaa", "protocol_token": valid, "dossier_json": "{\"title\":\"Paper\"}"},
    )
    if case == "missing":
        response = client.get("/v1/protocol-drafts/draft-missingx", headers={"X-Protocol-Token": valid})
        assert response.json()["detail"]["code"] == "protocol_draft_not_found"
    elif case == "expired":
        response = client.get("/v1/protocol-drafts/draft-apiaaaaaa", headers={"X-Protocol-Token": _protocol_token(settings.protocol_secret, "draft-apiaaaaaa", "sha256:" + "1" * 64, "sha256:" + "2" * 64, 1)})
        assert response.json()["detail"]["code"] == "protocol_draft_expired"
    elif case == "tampered":
        response = client.get("/v1/protocol-drafts/draft-apiaaaaaa", headers={"X-Protocol-Token": valid[:-1] + "0"})
        assert response.json()["detail"]["code"] == "protocol_token_tampered"
    else:
        response = client.get("/v1/protocol-drafts/draft-otherx", headers={"X-Protocol-Token": valid})
        assert response.json()["detail"]["code"] == "protocol_draft_token_mismatch"
```

另加测试断言超过 `MAX_DOSSIER_BYTES`、非法 JSON、缺少 `X-Protocol-Token`、重复写入不同 dossier、过期清理和错误响应不包含 token/CSV/traceback。

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/repro_runner/test_api.py -k protocol_draft -q`

Expected: FAIL because the schemas, lifespan state and routes do not exist.

- [ ] **Step 3: 增加模型、lifespan 状态和路由**

在 `src/repro_runner/schemas.py` 增加 `extra="forbid"` 的模型：

```python
class ProtocolDraftCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    protocol_version: int
    manifest_id: str
    dataset_id: str
    created_at: int
    expires_at: int

class ProtocolDraftReadResponse(ProtocolDraftCreateResponse):
    dossier: dict[str, Any]
```

在 `api.py` 的 lifespan 启动后创建：

```python
draft_store = ProtocolDraftStore(
    settings.storage_dir / "protocol-drafts",
    secret=settings.protocol_secret,
    ttl_seconds=settings.protocol_draft_ttl_seconds,
)
draft_store.cleanup_expired()
application.state.protocol_draft_store = draft_store
```

在 `api.py` 增加固定的错误转换：

```python
def _protocol_draft_error(exc: ProtocolDraftError) -> HTTPException:
    status = {
        "protocol_draft_not_found": 404,
        "protocol_draft_expired": 410,
        "protocol_draft_token_mismatch": 422,
        "protocol_token_malformed": 422,
        "protocol_token_tampered": 422,
        "protocol_payload_invalid": 422,
        "protocol_draft_write_failed": 500,
    }.get(exc.code, 422)
    return HTTPException(status_code=status, detail={"code": exc.code, "message": "Protocol draft is not available."})
```

POST 路由先限制 `dossier_json` 的 UTF-8 字节数，再 `json.loads` 并要求顶层对象，调用 `store.save`；GET 路由先按安全正则验证 `draft_id`，从 `X-Protocol-Token` 读取 token，调用 `store.load`，将 record 转为 `ProtocolDraftReadResponse`。每个请求入口和读写前调用 `cleanup_expired()`，清理异常只能记录固定错误码，不影响当前请求的安全错误返回。

- [ ] **Step 4: 运行 API 测试和现有 API 回归**

Run: `pytest tests/repro_runner/test_api.py -k "protocol_draft or healthz or parse_dossier or validate_dataset" -q`

Expected: new draft tests and selected existing API tests PASS; malformed requests still return sanitized `invalid_request` with a request ID.

- [ ] **Step 5: Commit**

```bash
git add src/repro_runner/schemas.py src/repro_runner/api.py tests/repro_runner/test_api.py
git commit -m "feat: expose protocol draft endpoints"
```

### Task 3: 扩展 Dify 协议 helper

**Files:**
- Modify: `dify/code/experiment_workflow.py`
- Modify: `tests/test_dify_multimodel_code.py`

**Interfaces:**
- `prepare_protocol_artifacts(dossier_json, diagnosis_json, target_column=None, protocol_notes=None, *, secret=_PROTOCOL_SECRET, now=None, ttl_seconds=_PROTOCOL_DEFAULT_TTL_SECONDS)` returns `protocol_preview_json`, `protocol_token`, `draft_id`, `draft_expires_at`, `protocol_ready`, and `protocol_errors`.
- `normalize_protocol_confirmation(protocol_token, confirm_protocol, *, confirmed_options=None, secret=_PROTOCOL_SECRET, now=None)` returns `protocol_ok`, `manifest_json`, `draft_id`, and `protocol_errors`.
- Add `normalize_protocol_draft_write_response(body, status_code, expected_draft_id) -> dict[str, object]`.
- Add `normalize_protocol_draft_read_response(body, status_code, expected_draft_id, manifest_json) -> dict[str, object]` returning `dossier_ok`, `dossier_json`, `draft_errors`, `draft_id`, `manifest_id`, and `dataset_id`.

- [ ] **Step 1: 写 helper 失败测试**

在 `tests/test_dify_multimodel_code.py` 的 imports 增加 `import re`，并增加：

```python
def test_prepare_protocol_artifacts_returns_bound_draft_id_and_expiry():
    dossier_json = json.dumps({"title": "Paper", "metrics": []}, ensure_ascii=False)
    diagnosis_json = json.dumps(
        {
            "valid": True,
            "dataset": {
                "dataset_id": "sha256:" + "2" * 64,
                "target": "Y_cls",
                "rows": 40,
                "column_names": ["x1", "Y_cls"],
            },
            "recommended_options": {
                "target_column": "Y_cls",
                "feature_columns": ["x1"],
                "missing_policy": "reject",
                "sampling_strategy": "original",
                "comparison_mode": "paper_comparable",
            },
            "columns": [],
            "target_candidates": ["Y_cls"],
            "risk_flags": [],
            "warnings": [],
        },
        ensure_ascii=False,
    )
    result = prepare_protocol_artifacts(
        dossier_json, diagnosis_json, target_column="Y_cls",
        secret="helper-secret", now=1_000, ttl_seconds=900,
    )
    assert re.fullmatch(r"draft-[A-Za-z0-9_-]{8,128}", result["draft_id"])
    assert result["draft_expires_at"] == "1900"
    assert result["protocol_ready"] is True
    confirmed = normalize_protocol_confirmation(
        result["protocol_token"], True, secret="helper-secret", now=1_000,
    )
    assert confirmed["protocol_ok"] is True
    assert confirmed["draft_id"] == result["draft_id"]

def test_protocol_draft_response_normalizers_reject_mismatched_metadata():
    body = json.dumps({
        "draft_id": "draft-cccccccc",
        "manifest_id": "sha256:" + "9" * 64,
        "dataset_id": "sha256:" + "8" * 64,
        "dossier": {"title": "Paper"},
    })
    result = normalize_protocol_draft_read_response(
        body, 200, "draft-dddddddd", json.dumps({"manifest_id": "sha256:" + "1" * 64, "dataset_id": "sha256:" + "2" * 64}),
    )
    assert result["dossier_ok"] is False
    assert json.loads(result["draft_errors"])[0]["code"] == "protocol_draft_token_mismatch"
    assert "Paper" not in result["dossier_json"]

def test_protocol_draft_http_errors_are_stable_and_do_not_echo_body():
    result = normalize_protocol_draft_read_response(
        "RAW_SENTINEL private csv row", 410, "draft-aaaaaaaa", "{}",
    )
    assert result["dossier_ok"] is False
    assert "RAW_SENTINEL" not in result["draft_errors"]
    assert json.loads(result["draft_errors"])[0]["code"] == "protocol_draft_expired"
```

- [ ] **Step 2: 运行 helper 失败测试**

Run: `pytest tests/test_dify_multimodel_code.py -k "draft_id or protocol_draft" -q`

Expected: FAIL because the new outputs and normalizers do not exist.

- [ ] **Step 3: 更新 token payload 和 prepare/confirmation 返回值**

在 `experiment_workflow.py` 引入 `secrets`，用 `draft-` 加 16 字节 URL-safe 随机值创建 draft ID；`prepare_protocol_artifacts` 在 token payload 写入：

```python
payload = {
    "v": _PROTOCOL_TOKEN_VERSION,
    "exp": int(current + ttl),
    "ready": not unresolved,
    "draft_id": draft_id,
    "notes_digest": preview["protocol_notes_digest"],
    "manifest": manifest,
}
ready = not unresolved
return {
    "protocol_preview_json": _json(preview),
    "protocol_token": _token_encode(payload, secret) if ready else "",
    "draft_id": draft_id if ready else "",
    "draft_expires_at": str(int(current + ttl)) if ready else "",
    "protocol_ready": ready,
    "protocol_errors": _json([_protocol_error(code) for code in unresolved]),
}
```

返回 `draft_expires_at` 为十进制字符串；`protocol_ready` 为 `not unresolved`；有 unresolved 时 `protocol_token`、`draft_id` 和 `draft_expires_at` 都返回空字符串，prepare 只能输出结构化错误，`protocol_errors` 包含安全错误列表。`normalize_protocol_confirmation` 必须要求 payload 的 `draft_id` 满足 draft ID 正则，成功时把同一 draft ID 返回；缺少 draft ID 的旧 token 返回 `protocol_payload_invalid`，不再回退到旧的 dossier JSON 输入路径。

- [ ] **Step 4: 实现 POST/GET 响应规范化**

使用下面的确定性输出契约，HTTP body 只在校验通过时进入 `dossier_json`：

```python
def normalize_protocol_draft_read_response(body, status_code, expected_draft_id, manifest_json):
    errors = []
    if status_code == 404:
        errors.append("protocol_draft_not_found")
    elif status_code == 410:
        errors.append("protocol_draft_expired")
    elif status_code in {401, 403, 409, 422}:
        errors.append("protocol_draft_token_mismatch")
    elif not isinstance(status_code, int) or status_code < 200 or status_code >= 300:
        errors.append("protocol_draft_read_failed")
    response = _object(body)
    expected = _object(manifest_json)
    if not errors:
        if response.get("draft_id") != expected_draft_id:
            errors.append("protocol_draft_token_mismatch")
        if response.get("manifest_id") != expected.get("manifest_id"):
            errors.append("protocol_draft_token_mismatch")
        if response.get("dataset_id") != expected.get("dataset_id"):
            errors.append("protocol_draft_token_mismatch")
        if not isinstance(response.get("dossier"), dict):
            errors.append("protocol_draft_response_invalid")
    if errors:
        safe = [{"code": code, "message": "Protocol draft could not be read."} for code in dict.fromkeys(errors)]
        return {"dossier_ok": False, "dossier_json": "{}", "draft_id": "", "manifest_id": "", "dataset_id": "", "draft_errors": _json(safe)}
    return {"dossier_ok": True, "dossier_json": _json(response["dossier"]), "draft_id": expected_draft_id, "manifest_id": response["manifest_id"], "dataset_id": response["dataset_id"], "draft_errors": "[]"}
```

POST normalizer 只接受 2xx、期望 draft ID、manifest ID、dataset ID 和整数 `expires_at`，失败返回 `draft_saved_ok=False`、固定错误码和空 token 传递值。将 draft 相关安全错误加入 `_SAFE_ERROR_CODES`，任何错误消息都不复制 HTTP body。

- [ ] **Step 5: 运行 helper 测试和嵌入代码编译检查**

Run: `pytest tests/test_dify_multimodel_code.py -q`

Expected: all existing helper tests plus new draft tests PASS.

Run: `python -m compileall dify/code`

Expected: exit code 0 and no syntax errors.

- [ ] **Step 6: Commit**

```bash
git add dify/code/experiment_workflow.py tests/test_dify_multimodel_code.py
git commit -m "feat: bind protocol tokens to temporary drafts"
```

### Task 4: 构建合并 Dify DSL

**Files:**
- Modify: `scripts/build_multimodel_dsl.py`
- Create: `dify/paper-comparison-merged-workflow.yml`
- Create: `tests/test_dify_merged_dsl.py`

**Interfaces:**
- Add `MERGED_DSL = PROJECT_ROOT / "dify" / "paper-comparison-merged-workflow.yml"`.
- Add `build_merged_dsl() -> dict` and `write_merged_dsl(path: Path) -> None`.
- Keep `build_multimodel_dsl()` and `build_prepare_dsl()` behavior and output paths unchanged.
- The merged DSL has exactly one Start node, one `Output_prepare`, one `Output_run`, and direct failure End nodes.

- [ ] **Step 1: 写 merged DSL 失败测试**

创建 `tests/test_dify_merged_dsl.py`，固定读取 `dify/paper-comparison-merged-workflow.yml`，先写：

```python
def test_merged_start_inputs_have_mode_specific_requirements():
    start = _node_map()["Start"]
    variables = {item["variable"]: item for item in start["data"]["variables"]}
    assert variables["run_mode"]["type"] == "select"
    assert variables["run_mode"]["options"] == ["prepare", "run"]
    assert variables["run_mode"]["default"] == "prepare"
    assert variables["training_csv"]["required"] is True
    assert variables["paper_pdf"]["required"] is False
    assert "paper_dossier_json" not in variables

def test_merged_outputs_are_separate_and_non_empty():
    ends = {node["data"]["title"]: node for node in _nodes() if node["data"]["type"] == "end"}
    assert {"Output_prepare", "Output_run"} <= set(ends)
    assert {item["variable"] for item in ends["Output_prepare"]["data"]["outputs"]} == {
        "protocol_preview_json", "protocol_token", "draft_expires_at"
    }
    assert {item["variable"] for item in ends["Output_run"]["data"]["outputs"]} == {
        "dossier_json", "validation_json", "experiment_json", "comparison_json", "assessment_json", "markdown_report"
    }

def test_merged_graph_wires_prepare_and_run_directly_to_their_outputs():
    nodes = _node_map()
    edges = _document()["workflow"]["graph"]["edges"]
    assert _has_edge(edges, nodes["run_mode?"], nodes["prepare_inputs_ok?"], "true")
    assert _has_edge(edges, nodes["prepare_protocol_draft_response"], nodes["Output_prepare"], "source")
    assert _has_edge(edges, nodes["format_comparison_report"], nodes["Output_run"], "source")
    assert all(edge["target"] != nodes["Output_run"]["id"] for edge in edges if edge["source"] == nodes["prepare_protocol_draft_response"]["id"])

def test_merged_embedded_python_compiles_and_contains_no_raw_inputs_or_secrets():
    document = _document()
    code_nodes = [node for node in _nodes() if node["data"]["type"] == "code"]
    for node in code_nodes:
        compile(node["data"]["code"], node["data"]["title"], "exec")
    serialized = yaml.safe_dump(document, allow_unicode=True)
    assert "raw_csv_secret_07a1" not in serialized
    assert "sk-" not in serialized.casefold()
    assert document["workflow"].get("environment_variables", [])
```

测试辅助函数 `_has_edge` 必须按 `source`, `sourceHandle`, `target` 三个字段精确匹配，避免只测试节点存在而漏掉断线。

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/test_dify_merged_dsl.py -q`

Expected: FAIL because the merged DSL and `build_merged_dsl` do not exist.

- [ ] **Step 3: 添加统一 Start、mode 分支和 prepare 分支**

在 `scripts/build_multimodel_dsl.py` 添加稳定 ID 常量，并让 Start 变量至少包含下列定义：

```python
{
    "default": "prepare",
    "label": "run_mode",
    "options": ["prepare", "run"],
    "required": True,
    "type": "select",
    "variable": "run_mode",
}
{
    "allowed_file_extensions": [".PDF"],
    "allowed_file_types": ["document"],
    "allowed_file_upload_methods": ["local_file"],
    "default": "",
    "label": "paper_pdf",
    "required": False,
    "type": "file",
    "variable": "paper_pdf",
}
{
    "allowed_file_extensions": [".CSV"],
    "allowed_file_types": ["document"],
    "allowed_file_upload_methods": ["local_file"],
    "default": "",
    "label": "training_csv",
    "required": True,
    "type": "file",
    "variable": "training_csv",
}
```

`run_mode?` 的 true 边进入 `prepare_inputs_ok?`；该 Code 节点在 `run_mode == "prepare"` 且 PDF 文件对象存在时返回 `prepare_inputs_ok=True`，否则返回 `paper_pdf_required`。false 边进入 token 确认和 run 分支。prepare 分支复用现有解析器校验、LLM dossier 提取、dossier 校验和 `diagnose_dataset`，然后调用 `prepare_protocol_artifacts`。

- [ ] **Step 4: 添加草稿保存 HTTP 节点和 prepare End**

prepare helper 后添加 HTTP 节点：

```yaml
method: post
url: http://repro-runner:8001/v1/protocol-drafts
body:
  type: form-data
  data:
    - key: draft_id
      type: text
      value: "{{#prepare_protocol_artifacts.draft_id#}}"
    - key: protocol_token
      type: text
      value: "{{#prepare_protocol_artifacts.protocol_token#}}"
    - key: dossier_json
      type: text
      value: "{{#validate_paper_dossier.validated_json#}}"
```

将 HTTP 响应交给 `normalize_protocol_draft_write_response`，只有 `draft_saved_ok=True` 才连到 `Output_prepare`。`Output_prepare` 只输出 `protocol_preview_json`、`protocol_token`、`draft_expires_at`；PDF 缺失、解析失败、dossier 语义失败、数据诊断失败和草稿写入失败各自连到直接错误 End，错误输出使用 `error_json`、`markdown_report` 和与 prepare 输出模型一致的空值字段，不能经过 run aggregator。

- [ ] **Step 5: 添加 run 分支和六字段 Output**

run 分支顺序固定为：

```text
normalize_suite_inputs
  -> normalize_protocol_confirmation
  -> protocol_ok?
  -> GET /v1/protocol-drafts/{draft_id}
  -> normalize_protocol_draft_read_response
  -> dossier_ok?
  -> validate_dataset(training_csv)
  -> submit_confirmed_job(/v1/jobs)
  -> poll_submitted_job_until_terminal(/v1/jobs/{job_id})
  -> parse_suite_response
  -> build_suite_comparison_request
  -> compare-model-suite-result
  -> parse comparison
  -> assessment
  -> format_suite_comparison_report
  -> Output_run
```

GET 节点 URL 使用确认节点返回的 `draft_id`，请求头使用 `X-Protocol-Token: {{#Start.protocol_token#}}`；不把 token 放入 query string。run 分支不得引用 `Start.paper_pdf`、`Start.paper_dossier_json` 或 prepare 分支的 dossier aggregator。`Output_run` 直接从 `format_suite_comparison_report` 选择六个字符串字段；token 未确认、draft 缺失/过期/元数据不匹配、CSV mismatch、job、HTTP、语义和比较失败均使用直接错误 End。

- [ ] **Step 6: 生成 DSL 并运行静态测试**

在脚本入口增加：

```python
if __name__ == "__main__":
    write_multimodel_dsl(TARGET_DSL)
    write_prepare_dsl(PREPARE_DSL)
    write_merged_dsl(MERGED_DSL)
```

Run: `python scripts/build_multimodel_dsl.py`

Expected: creates/updates `dify/paper-comparison-merged-workflow.yml` and leaves the two legacy source workflows importable.

Run: `pytest tests/test_dify_merged_dsl.py tests/test_dify_multimodel_dsl.py -q`

Expected: merged graph tests and existing DSL tests PASS; generated output is deterministic on a second invocation.

- [ ] **Step 7: Commit**

```bash
git add scripts/build_multimodel_dsl.py dify/paper-comparison-merged-workflow.yml tests/test_dify_merged_dsl.py
git commit -m "feat: generate merged prepare and run workflow"
```

### Task 5: 更新操作指南和应用导入说明

**Files:**
- Create: `dify/paper-comparison-merged-workflow.md`

**Interfaces:**
- Documents the exact Start inputs, prepare output contract, run input contract, error codes and rollback apps.
- Documents the shared secret requirement without containing a secret value.

- [ ] **Step 1: 写操作文档**

文档必须包含以下可直接执行的流程：

```text
1. Import dify/paper-comparison-merged-workflow.yml as a new Dify workflow.
2. Configure DIFY_PROTOCOL_SECRET in the Dify runtime and REPRO_RUNNER_PROTOCOL_SECRET in repro-runner to the same externally supplied value.
3. First run: run_mode=prepare, upload paper_pdf and training_csv, review protocol_preview_json, keep protocol_token and draft_expires_at.
4. Second run: run_mode=run, upload the exact same training_csv, paste protocol_token, set confirm_protocol=true, and do not upload paper_pdf.
5. Verify Output_run contains dossier_json, validation_json, experiment_json, comparison_json, assessment_json and markdown_report.
6. If the token expires, rerun prepare; do not edit the token or bypass confirm_protocol.
```

错误表至少列出 `paper_pdf_required`、`protocol_not_confirmed`、`protocol_token_malformed`、`protocol_token_expired`、`protocol_draft_not_found`、`protocol_draft_expired`、`protocol_draft_token_mismatch`、`manifest_dataset_mismatch`、`job_submit_failed` 和 `comparison_not_run` 的用户动作。文档明确原两个工作流保留为回滚目标，且示例不包含 CSV 行、PDF 文本、token 或 secret。

- [ ] **Step 2: 检查文档中的敏感信息和占位符**

Run: `rg -n "REPLACE_ME|CHANGE_ME" dify/paper-comparison-merged-workflow.md`

Expected: no matches.

- [ ] **Step 3: Commit**

```bash
git add dify/paper-comparison-merged-workflow.md
git commit -m "docs: document merged paper workflow"
```

### Task 6: 完成跨层回归测试

**Files:**
- Modify: `tests/repro_runner/test_api.py`
- Modify: `tests/test_dify_multimodel_code.py`
- Modify: `tests/test_dify_merged_dsl.py`

**Interfaces:**
- Covers service storage/API, embedded Dify helper, generated graph and direct failure outputs without changing existing workflow tests' legacy scope.

- [ ] **Step 1: 加入端到端协议链测试**

使用同一个 test secret 和同一个 generated token 完成以下测试顺序：

```python
prepared = prepare_protocol_artifacts(
    json.dumps({"title": "Paper", "metrics": []}, ensure_ascii=False),
    json.dumps(
        {
            "valid": True,
            "dataset": {
                "dataset_id": "sha256:" + "2" * 64,
                "target": "Y_cls",
                "rows": 40,
                "column_names": ["x1", "Y_cls"],
            },
            "recommended_options": {
                "target_column": "Y_cls",
                "feature_columns": ["x1"],
                "missing_policy": "reject",
                "sampling_strategy": "original",
                "comparison_mode": "paper_comparable",
            },
            "columns": [],
            "target_candidates": ["Y_cls"],
            "risk_flags": [],
            "warnings": [],
        },
        ensure_ascii=False,
    ),
    target_column="Y_cls",
    secret="test-secret",
    now=1_000,
    ttl_seconds=900,
)
prepared_dossier_json = json.dumps({"title": "Paper", "metrics": []}, ensure_ascii=False)
created = client.post(
    "/v1/protocol-drafts",
    data={
        "draft_id": prepared["draft_id"],
        "protocol_token": prepared["protocol_token"],
        "dossier_json": prepared_dossier_json,
    },
)
assert created.status_code == 200
confirmed = normalize_protocol_confirmation(
    prepared["protocol_token"], True, secret="test-secret", now=1_000,
)
fetched = client.get(
    f"/v1/protocol-drafts/{confirmed['draft_id']}",
    headers={"X-Protocol-Token": prepared["protocol_token"]},
)
assert fetched.status_code == 200
assert fetched.json()["dossier"] == json.loads(prepared_dossier_json)
```

再用一个字节发生变化的 CSV 通过 `/v1/jobs` 验证 `manifest_dataset_mismatch`，确认 job 没有创建；用 `confirm_protocol=False` 验证 `protocol_not_confirmed`；用时间推进到 `exp` 后验证 `protocol_draft_expired`；删除草稿目录后验证 `protocol_draft_not_found`。

- [ ] **Step 2: 运行分层测试**

Run: `pytest tests/repro_runner/test_protocol_drafts.py tests/repro_runner/test_api.py tests/test_dify_multimodel_code.py tests/test_dify_merged_dsl.py -q`

Expected: all new storage/API/helper/DSL tests PASS.

Run: `pytest -q`

Expected: the complete existing regression suite PASS, including the unchanged legacy prepare/run workflow tests.

- [ ] **Step 3: 检查生成器可重复性和工作区差异**

Run: `python scripts/build_multimodel_dsl.py`

Run: `git diff --check`

Expected: second generation changes no bytes in the generated files, and `git diff --check` reports no whitespace errors. Do not stage `.pytest-tmp*`, `.live-artifacts` or unrelated pre-existing modifications.

- [ ] **Step 4: Commit**

```bash
git add tests/repro_runner/test_api.py tests/test_dify_multimodel_code.py tests/test_dify_merged_dsl.py
git commit -m "test: cover merged workflow protocol paths"
```

### Task 7: Dify UI 真实验收与交付

**Files:**
- Verify: `dify/paper-comparison-merged-workflow.yml`
- Verify: `dify/paper-comparison-merged-workflow.md`
- Verify: `dify/paper-comparison-prepare-workflow.yml`
- Verify: `dify/paper-comparison-multimodel-workflow.yml`

**Interfaces:**
- Produces one newly imported Dify application with two executable modes and preserves both old applications for rollback.

- [ ] **Step 1: 导入新 DSL 并验证 prepare**

在 Dify 导入 `dify/paper-comparison-merged-workflow.yml`，配置 parser token 和协议 secret；使用真实 PDF+CSV，设置 `run_mode=prepare`。验收：

```text
protocol_preview_json 非空且包含 manifest_draft、dataset、paper_summary
protocol_token 以 pt1. 开头
draft_expires_at 为未来时间/秒数
服务端 protocol-drafts 目录只出现 draft.json，文件中没有 CSV 行、PDF 文本或 pt1 token
```

- [ ] **Step 2: 验证 run 成功路径**

同一工作流第二次运行只上传原 CSV，设置 `run_mode=run`、粘贴 token、勾选 `confirm_protocol=true`。验收 `Output_run` 的六个字段全部非空，报告包含七模型结果或明确的模型不可用状态、比较结果和 Markdown。

- [ ] **Step 3: 验证拒绝路径**

逐项执行并记录结构化结果：

```text
prepare 不上传 PDF -> paper_pdf_required，不能得到可运行 token
run 不勾选 confirm_protocol -> protocol_not_confirmed，不提交 job
修改 CSV 一个字节 -> manifest_dataset_mismatch，不提交 job
修改 token -> protocol_draft_token_mismatch 或 protocol_token_tampered
删除 draft.json -> protocol_draft_not_found，不返回 {}
等待超过 TTL -> protocol_token_expired 或 protocol_draft_expired
```

- [ ] **Step 4: 验证旧工作流和回滚**

分别打开原 prepare 应用和原 confirmed-run 应用，确认仍可运行；若合并 DSL 失败，保留旧应用 URL/ID 和失败日志，不覆盖旧 DSL。

- [ ] **Step 5: 记录交付结果**

在本次任务最终回复中列出新 DSL 文件、测试命令及结果、Dify 新应用 ID/URL、旧应用回滚入口和任何未完成的外部操作。只有真实 UI 的 prepare、run 和拒绝路径都验证后，才声称合并工作流完成。

# 通用可靠二分类工作流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前同步多模型实验工作流升级为适用于普通表格二分类论文复现的、可诊断、可确认、可恢复和可比较的本机单用户实验系统。

**Architecture:** 保留现有 FastAPI、Dify DSL、模型注册表和同步兼容接口；新增实验协议层、通用 CSV 诊断/预处理层和 SQLite 持久化任务层。Dify 负责上传、确认和轮询，后台任务引擎负责长时间训练；论文比较统一从不可变 manifest 读取限定条件。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、pandas、scikit-learn、XGBoost、LightGBM、SQLite、pytest、Dify YAML workflow、Docker Compose。

## Global Constraints

- 第一阶段只支持普通表格型二分类 CSV；不扩展多分类、回归、栅格/矢量 GIS 和在线预测服务。
- CSV 输入必须可解析为 UTF-8 文本；训练前必须确认目标列和最终特征列。
- 默认本机单用户、单任务串行执行；模型级失败必须隔离，不能清空其他模型结果。
- 外部测试集只划分一次；预处理拟合、交叉验证和超参数搜索只能在训练集内部进行。
- 论文数值接近不等于严格复现；缺少数据集、划分或来源限定时必须返回明确的不可比原因。
- 不把原始 CSV 行和不必要的 PDF 内容写入持久化结果；任务执行期间的临时输入文件在任务结束后删除。
- 保持 /v1/run-experiment、/v1/run-model-suite、/v1/model-suites/{experiment_id} 和比较接口兼容。
- CPU 是默认执行方式；16 GB 内存环境下禁止并发训练多个模型。
- 每项代码修改都先写失败测试，再实现最小改动，再运行相关测试和全量测试。

---

### Task 1: 修复论文比较限定条件并保证 Dify 生成物一致

**Files:**
- Modify: paper-repro-agent/dify/code/comparison_workflow.py, function build_suite_comparison_request
- Modify: paper-repro-agent/scripts/build_multimodel_dsl.py, function _suite_request_code
- Regenerate: paper-repro-agent/dify/paper-comparison-multimodel-workflow.yml
- Modify: paper-repro-agent/dify/paper-comparison-multimodel-workflow.md
- Test: paper-repro-agent/tests/test_dify_multimodel_code.py
- Test: paper-repro-agent/tests/test_dify_multimodel_dsl.py

**Interfaces:**
- Consumes dossier metric objects containing normalized_name, reported_value, dataset, split, dataset_id, test_size, random_state, train_rows, test_rows and test_digest.
- Produces suite_comparison_request_json whose reported_metrics preserves every valid qualifier, including dataset and split.

- [ ] **Step 1: Add a failing generated-DSL regression test**

Add this test to tests/test_dify_multimodel_dsl.py:

~~~python
def test_generated_suite_request_keeps_manual_override_qualifiers() -> None:
    main = _exec_code_node("build_suite_comparison_request")
    result = main(
        json.dumps(
            {
                "metrics": [
                    {
                        "name": "AUC",
                        "normalized_name": "roc_auc",
                        "supported": True,
                        "ambiguous": False,
                        "reported_value": 0.850,
                        "dataset": "奉节县（全域模型）",
                        "split": "测试集",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {"experiment_id": "exp-20260811T000000Z-override", "results": []},
            ensure_ascii=False,
        ),
    )

    request = json.loads(result["suite_comparison_request_json"])
    assert request["reported_metrics"] == [
        {
            "name": "roc_auc",
            "reported_value": 0.850,
            "dataset": "奉节县（全域模型）",
            "split": "测试集",
        }
    ]
~~~

- [ ] **Step 2: Run the regression test and identify the failing boundary**

Run:

~~~powershell
cd C:\Users\17716\Documents\arcgis\.worktrees\multi-model-cv\paper-repro-agent
pytest tests/test_dify_multimodel_dsl.py::test_generated_suite_request_keeps_manual_override_qualifiers -q
~~~

Expected before the fix: the test fails if the generated node drops either qualifier. If it already passes, record that the source generator is correct and treat the live Dify import as the stale component.

- [ ] **Step 3: Make the generator the only source of comparison code**

Ensure the generated request builds each metric from an allowlist. The generated code must keep dataset, split and valid provenance fields, while excluding arbitrary dossier fields:

~~~python
item = {"name": name, "reported_value": reported_value}
for key, validator, raw in (
    ("dataset", safe_qualifier, metric.get("dataset")),
    ("split", safe_qualifier, metric.get("split")),
    ("dataset_id", safe_digest, metric.get("dataset_id")),
):
    value = validator(raw)
    if value is not None:
        item[key] = value
~~~

Retain the existing validators for test_size, random_state, row counts and test_digest.

- [ ] **Step 4: Regenerate and verify deterministic parity**

Run:

~~~powershell
python scripts/build_multimodel_dsl.py
pytest tests/test_dify_multimodel_dsl.py -q
~~~

Expected: generator determinism, embedded-code compilation and privacy tests pass; the checked-in YAML contains the qualifier-preserving code.

- [ ] **Step 5: Document live import verification**

Update the operator guide with the exact sequence: regenerate YAML, import as a new Dify version, run the manual AUC override case, and inspect that the backend comparison request contains dataset and split. Keep the V3 workflow as rollback target.

- [ ] **Step 6: Commit the isolated fix**

~~~powershell
git add dify/code/comparison_workflow.py scripts/build_multimodel_dsl.py dify/paper-comparison-multimodel-workflow.yml dify/paper-comparison-multimodel-workflow.md tests/test_dify_multimodel_code.py tests/test_dify_multimodel_dsl.py
git commit -m "fix: preserve paper comparison qualifiers in Dify"
~~~

### Task 2: Add general CSV diagnostics and immutable experiment protocols

**Files:**
- Create: paper-repro-agent/src/repro_runner/protocol.py
- Create: paper-repro-agent/src/repro_runner/preprocessing.py
- Modify: paper-repro-agent/src/repro_runner/schemas.py
- Modify: paper-repro-agent/src/repro_runner/data.py
- Modify: paper-repro-agent/src/repro_runner/api.py
- Test: paper-repro-agent/tests/repro_runner/test_data.py
- Create: paper-repro-agent/tests/repro_runner/test_protocol.py
- Create: paper-repro-agent/tests/repro_runner/test_preprocessing.py
- Test: paper-repro-agent/tests/repro_runner/test_api.py

**Interfaces:**
- Consumes CSV bytes plus DatasetOptions.
- Produces DatasetDiagnosticResponse with column profiles, target candidates, risk flags and recommended options; ExperimentManifest with a stable dataset fingerprint and all confirmed choices.

- [ ] **Step 1: Define protocol models and write failing validation tests**

Add models with Pydantic extra-forbid behavior:

~~~python
class MissingPolicy(str, Enum):
    reject = "reject"
    drop_rows = "drop_rows"
    impute = "impute"

class SamplingStrategy(str, Enum):
    original = "original"
    class_weight = "class_weight"
    balanced_undersample = "balanced_undersample"

class ComparisonMode(str, Enum):
    paper_comparable = "paper_comparable"
    real_world = "real_world"

class ColumnProfile(BaseModel):
    name: str
    inferred_type: Literal["numeric", "categorical", "text", "datetime", "constant"]
    missing_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)
    is_target_candidate: bool = False
    risk_flags: list[str] = Field(default_factory=list)

class ExperimentManifest(BaseModel):
    manifest_id: str
    dataset_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    target_column: str
    feature_columns: list[str] = Field(min_length=1)
    missing_policy: MissingPolicy = MissingPolicy.reject
    sampling_strategy: SamplingStrategy = SamplingStrategy.original
    comparison_mode: ComparisonMode = ComparisonMode.real_world
    test_size: float = Field(default=0.2, ge=0.1, le=0.5)
    random_state: int = Field(default=42, ge=0)
    cv_folds: int = Field(default=5, ge=3, le=10)
    optimization_metric: Literal["roc_auc", "f1", "recall", "balanced_accuracy"] = "roc_auc"
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    models: list[ModelName] = Field(min_length=1)
~~~

Test that a manifest rejects a non-finite threshold, a duplicate feature list and a target with no feature columns.

- [ ] **Step 2: Run the schema tests before implementation**

~~~powershell
pytest tests/repro_runner/test_protocol.py -q
~~~

Expected: import or validation failures because the new models do not yet exist.

- [ ] **Step 3: Implement bounded CSV sniffing**

In data.py, decode UTF-8 with BOM support, identify comma/tab/semicolon delimiters with csv.Sniffer, preserve a stable dataset_id based on original bytes, and return structured DatasetError values for empty files, malformed rows, unsupported encoding, duplicate headers and oversized column counts.

Preserve pandas dtypes long enough to profile numeric and categorical columns. Numeric conversion is allowed only after a column is classified as numeric; non-finite tokens remain validation errors.

- [ ] **Step 4: Implement column profiles and risk flags**

Add these pure functions in protocol.py:

- profile_columns(frame: pd.DataFrame, target_column: str | None) -> list[ColumnProfile]
- target_candidates(frame: pd.DataFrame) -> list[str]
- risk_flags(frame: pd.DataFrame, target_column: str | None) -> list[str]

Each function must return a deterministic value for the same frame and must not mutate the input frame.

The implementation must flag constant columns, high-cardinality text, columns whose normalized names resemble id/row-number/target-derived fields, and severe class imbalance. It must never silently delete a flagged column.

- [ ] **Step 5: Add /v1/diagnose-dataset without breaking /v1/validate-dataset**

The new endpoint accepts file, optional target_column and optional JSON-encoded exclude_columns. It returns valid, dataset, columns, target_candidates, risk_flags, warnings and safe structured errors. The existing validation endpoint keeps its current response shape, with only additive profile fields.

Add an API test using a small CSV with numeric, categorical, constant and ID-like columns. Assert that warnings are present and no data rows are echoed.

- [ ] **Step 6: Implement canonical manifest generation**

Implement create_manifest in protocol.py with the exact signature
create_manifest(diagnostic: DatasetDiagnosticResponse, options: DatasetOptions, suite_config: ModelSuiteConfig, dossier_id: str | None = None) -> ExperimentManifest.

Serialize the manifest with sorted keys and no source rows. Repeating the same validated inputs must yield the same canonical payload and manifest fingerprint.

- [ ] **Step 7: Commit the protocol layer**

~~~powershell
git add src/repro_runner/protocol.py src/repro_runner/preprocessing.py src/repro_runner/schemas.py src/repro_runner/data.py src/repro_runner/api.py tests/repro_runner/test_data.py tests/repro_runner/test_protocol.py tests/repro_runner/test_preprocessing.py tests/repro_runner/test_api.py
git commit -m "feat: add dataset diagnostics and experiment manifests"
~~~

### Task 3: Support numeric and low-cardinality categorical features safely

**Files:**
- Modify: paper-repro-agent/src/repro_runner/preprocessing.py
- Modify: paper-repro-agent/src/repro_runner/model_registry.py
- Modify: paper-repro-agent/src/repro_runner/metrics.py
- Modify: paper-repro-agent/src/repro_runner/suite_engine.py
- Modify: paper-repro-agent/src/repro_runner/schemas.py
- Test: paper-repro-agent/tests/repro_runner/test_preprocessing.py
- Test: paper-repro-agent/tests/repro_runner/test_model_registry.py
- Test: paper-repro-agent/tests/repro_runner/test_suite_engine.py

**Interfaces:**
- Consumes DatasetBundle with selected numeric/categorical columns and ExperimentManifest.
- Produces the existing ExperimentSuiteResult shape plus a safe preprocessing summary and transformed feature names.

- [ ] **Step 1: Write failing preprocessing tests**

~~~python
def test_preprocessor_fits_numeric_and_categorical_columns_inside_pipeline() -> None:
    frame = pd.DataFrame(
        {"slope": [1.0, 2.0, 3.0, 4.0], "landform": ["A", "B", "A", "C"]}
    )
    preprocessor = build_preprocessor(frame, ["slope", "landform"])
    transformed = preprocessor.fit_transform(frame)
    assert transformed.shape == (4, 4)
    assert not np.isnan(transformed).any()
~~~

Add a test that an unseen category is handled by handle_unknown=ignore and a high-cardinality text column returns the stable high_cardinality_feature error.

- [ ] **Step 2: Implement fold-safe preprocessing**

Use ColumnTransformer with numeric median imputation and categorical most-frequent imputation plus one-hot encoding. Put the transformer inside every model Pipeline, so RandomizedSearchCV fits it independently in each training fold. Enforce a bounded category cardinality and transformed-column count before the job starts.

- [ ] **Step 3: Preserve model-specific scaling and class handling**

Keep tree models unscaled. Put StandardScaler after the feature transformer for Logistic Regression, SVM, KNN and MLP. Apply class_weight or scale_pos_weight only according to the confirmed sampling strategy, and record the choice in the manifest.

- [ ] **Step 4: Add transformed-feature importance tests**

Verify that tree importances and linear coefficients use transformed names such as landform_A. Models without a supported importance method must return an empty list instead of estimator internals or raw rows.

- [ ] **Step 5: Run existing and new suite tests**

~~~powershell
pytest tests/repro_runner/test_model_registry.py tests/repro_runner/test_preprocessing.py tests/repro_runner/test_suite_engine.py -q
~~~

Expected: existing numeric-only fixtures remain green, and the mixed fixture produces a safe status for every requested model.

- [ ] **Step 6: Commit the feature adapter**

~~~powershell
git add src/repro_runner/preprocessing.py src/repro_runner/model_registry.py src/repro_runner/metrics.py src/repro_runner/suite_engine.py src/repro_runner/schemas.py tests/repro_runner/test_preprocessing.py tests/repro_runner/test_model_registry.py tests/repro_runner/test_suite_engine.py
git commit -m "feat: support safe tabular feature preprocessing"
~~~

### Task 4: Add a persistent, restartable single-user job engine

**Files:**
- Create: paper-repro-agent/src/repro_runner/job_store.py
- Create: paper-repro-agent/src/repro_runner/job_runner.py
- Modify: paper-repro-agent/src/repro_runner/schemas.py
- Modify: paper-repro-agent/src/repro_runner/config.py
- Modify: paper-repro-agent/src/repro_runner/storage.py
- Modify: paper-repro-agent/src/repro_runner/suite_engine.py
- Modify: paper-repro-agent/src/repro_runner/api.py
- Modify: paper-repro-agent/compose.yaml
- Create: paper-repro-agent/tests/repro_runner/test_job_store.py
- Create: paper-repro-agent/tests/repro_runner/test_job_runner.py
- Create: paper-repro-agent/tests/repro_runner/test_job_api.py

**Interfaces:**
- Consumes a confirmed ExperimentManifest, CSV bytes and optional dossier reference.
- Produces JobCreateResponse, JobStatusResponse, GET /v1/jobs/{job_id}/result and existing suite-compatible result artifacts.

- [ ] **Step 1: Write SQLite state-transition tests**

~~~python
def test_job_store_recovers_running_job_as_needs_retry(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job_id = store.create("manifest-1", "sha256:" + "1" * 64)
    store.mark_running(job_id, worker_pid=123)
    store.recover_incomplete_jobs()
    assert store.get(job_id).status == "needs_retry"
~~~

Add tests for queued → running → succeeded, failure with safe error, cancellation, duplicate manifest fingerprint and idempotent result lookup.

- [ ] **Step 2: Implement the SQLite schema and atomic transitions**

Create jobs with job_id, manifest_id, dataset_id, status, stage, progress, attempt, worker_pid, result_id, error_code, created_at and updated_at. Add a unique manifest/dataset fingerprint, parameterized SQL, explicit allowed transitions and a transaction around every update.

- [ ] **Step 3: Write worker tests with a fake suite executor**

~~~python
def test_worker_records_partial_model_progress_and_final_result(tmp_path):
    events = []
    csv_bytes = b"feature,target\\n1,0\\n2,1\\n"
    manifest = make_test_manifest()

    def fake_suite(_manifest, _csv_bytes, progress_callback):
        progress_callback({"model": "random_forest", "status": "succeeded"})
        progress_callback({"model": "xgboost", "status": "failed", "code": "model_failed"})
        return make_partial_suite_result()

    runner = JobRunner(
        store=JobStore(tmp_path / "jobs.sqlite3"),
        execute_suite=fake_suite,
    )
    job_id = runner.submit(manifest, csv_bytes)
    runner.run_once(progress_callback=events.append)
    assert events[-1]["status"] == "succeeded"
    assert runner.store.get(job_id).result_id
~~~

The fake executor must emit one event per model and raise a controlled exception for one model. The expected terminal state is partial, with all successful model results preserved.
Define make_test_manifest and make_partial_suite_result in the same test module using the Pydantic fixtures already used by test_suite_api.py; the manifest must use dataset_id sha256: followed by 64 hexadecimal 1 characters, and the partial result must contain one succeeded and one failed model entry.

- [ ] **Step 4: Implement the single-worker loop and restart recovery**

Use one background worker and a bounded in-process wake event. On startup call recover_incomplete_jobs; on shutdown stop accepting new work and mark an interrupted job needs_retry. Store temporary inputs under the job directory and delete them after a terminal result is persisted.

- [ ] **Step 5: Add asynchronous API routes**

Implement these routes with FastAPI response models: POST /v1/jobs creates a job, GET /v1/jobs/{job_id} returns status, POST /v1/jobs/{job_id}/cancel requests cancellation, and GET /v1/jobs/{job_id}/result returns ExperimentSuiteResult.

POST /v1/jobs must return after bounded upload and protocol validation; it must never execute a model synchronously. Invalid input returns 422. An occupied single-user worker returns a structured capacity response.

- [ ] **Step 6: Add restart and long-task API tests**

Assert that job creation returns before the fake executor completes, polling exposes stage/model progress, a restart changes running to needs_retry, and completed result lookup returns the original suite result without retraining.

- [ ] **Step 7: Commit the job engine**

~~~powershell
git add src/repro_runner/job_store.py src/repro_runner/job_runner.py src/repro_runner/schemas.py src/repro_runner/config.py src/repro_runner/storage.py src/repro_runner/suite_engine.py src/repro_runner/api.py compose.yaml tests/repro_runner/test_job_store.py tests/repro_runner/test_job_runner.py tests/repro_runner/test_job_api.py
git commit -m "feat: add persistent restartable experiment jobs"
~~~

### Task 5: Add two-step Dify protocol confirmation and asynchronous polling

**Files:**
- Create: paper-repro-agent/dify/paper-comparison-prepare-workflow.yml
- Create: paper-repro-agent/dify/paper-comparison-prepare-workflow.md
- Modify: paper-repro-agent/dify/paper-comparison-multimodel-workflow.yml
- Modify: paper-repro-agent/dify/code/experiment_workflow.py
- Modify: paper-repro-agent/dify/code/comparison_workflow.py
- Test: paper-repro-agent/tests/test_dify_multimodel_code.py
- Create: paper-repro-agent/tests/test_dify_job_workflow.py

**Interfaces:**
- Prepare workflow consumes paper_pdf, training_csv, optional notes and target column; produces protocol_preview_json and a short-lived protocol_token.
- Run workflow consumes the token plus confirmed options; produces the six existing output strings and a job status in the report.

- [ ] **Step 1: Add failing normalization tests for confirmation**

~~~python
def test_normalize_protocol_confirmation_rejects_unconfirmed_manifest():
    result = normalize_protocol_confirmation(
        '{"manifest_id":"manifest-1","target_column":"Y_cls"}',
        False,
    )
    assert result["protocol_ok"] is False
    assert json.loads(result["protocol_errors"])[0]["code"] == "protocol_not_confirmed"
~~~

Add tests for accepted confirmation, bounded polling intervals and malformed backend job responses without echoing raw CSV or PDF text.

- [ ] **Step 2: Implement the prepare workflow contract**

The prepare workflow calls dossier parsing and dataset diagnosis, builds a manifest draft, and returns only safe aggregate metadata: target candidates, columns/types, risk flags, class counts/ratios, missing/duplicate counts, paper metrics and unresolved protocol fields. Stage files behind a token; never put file bytes in Dify variables.

- [ ] **Step 3: Implement the confirmed run workflow**

Require protocol_token and confirm_protocol=true. Send the confirmed manifest to POST /v1/jobs and return the job ID immediately. Poll with a condition-based stop on succeeded, partial, failed, cancelled or needs_retry; use a bounded interval and maximum duration.

- [ ] **Step 4: Preserve the current six output fields**

Keep dossier_json, validation_json, experiment_json, comparison_json, assessment_json and markdown_report as string outputs. Add job status and job ID inside the Markdown report so existing consumers remain compatible.

- [ ] **Step 5: Update import and rollback documentation**

Document prepare → confirm → run, token expiration, service restart behavior, safe retry and the unchanged V3 rollback workflow. Require explicit import of every generated workflow version.

- [ ] **Step 6: Commit the Dify integration**

~~~powershell
git add dify/paper-comparison-prepare-workflow.yml dify/paper-comparison-prepare-workflow.md dify/paper-comparison-multimodel-workflow.yml dify/code/experiment_workflow.py dify/code/comparison_workflow.py tests/test_dify_multimodel_code.py tests/test_dify_job_workflow.py
git commit -m "feat: add confirmed asynchronous Dify experiments"
~~~

### Task 6: Implement dual rankings and provenance-aware reporting

**Files:**
- Modify: paper-repro-agent/src/repro_runner/compare.py
- Modify: paper-repro-agent/src/repro_runner/schemas.py
- Modify: paper-repro-agent/dify/code/comparison_workflow.py
- Modify: paper-repro-agent/dify/paper-comparison-multimodel-workflow.md
- Test: paper-repro-agent/tests/repro_runner/test_suite_compare.py
- Test: paper-repro-agent/tests/test_dify_multimodel_code.py
- Create: paper-repro-agent/tests/repro_runner/test_report_contract.py

**Interfaces:**
- Consumes ExperimentSuiteResult, ExperimentManifest and qualified ReportedMetricInput.
- Produces performance_ranking, paper_distance_ranking, comparability_status, comparison_reasons and a Markdown report with separate recommendations.

- [ ] **Step 1: Write failing ranking tests**

~~~python
def test_report_has_separate_performance_and_paper_distance_rankings(suite_result):
    response = compare_suite_metrics(
        suite_result,
        [
            ReportedMetricInput(
                name="roc_auc",
                reported_value=0.90,
                dataset="test",
                split="test",
            )
        ],
    )
    assert response.performance_ranking == ["random_forest", "xgboost"]
    assert response.paper_distance_ranking == ["xgboost", "random_forest"]
~~~

Add cases for missing qualifiers, mismatched dataset fingerprint, mismatched test digest and no usable paper metric. Assert the exact safe reason and status in every case.

- [ ] **Step 2: Implement status classification**

Use only strictly_comparable, partially_comparable and not_comparable. Strict comparison requires a supported metric, complete dataset/split qualifiers, matching dataset identity and matching split provenance. Partial comparison allows arithmetic differences while recording missing provenance. Not comparable means no supported metric or a direct protocol/data conflict prevents a meaningful comparison.

- [ ] **Step 3: Keep arithmetic separate from scientific comparability**

Calculate absolute and relative differences when both values are numeric, but attach the provenance status beside them. Similarity grades may be highly_similar, partially_similar or materially_different; a numeric grade must never upgrade not_comparable to strict reproduction.

- [ ] **Step 4: Update the Markdown report**

Include the dataset/protocol summary, performance ranking, paper-distance ranking, one line per model, failure/unavailable reasons and:

~~~text
performance recommendation: <model>
paper-distance recommendation: <model or unavailable>
comparability: <status>
reason: <safe protocol reason>
~~~

- [ ] **Step 5: Run report and privacy tests**

~~~powershell
pytest tests/repro_runner/test_suite_compare.py tests/repro_runner/test_report_contract.py tests/test_dify_multimodel_code.py -q
~~~

Expected: raw-row, token, traceback and arbitrary backend-error sentinels remain absent from reports.

- [ ] **Step 6: Commit the reporting layer**

~~~powershell
git add src/repro_runner/compare.py src/repro_runner/schemas.py dify/code/comparison_workflow.py dify/paper-comparison-multimodel-workflow.md tests/repro_runner/test_suite_compare.py tests/repro_runner/test_report_contract.py tests/test_dify_multimodel_code.py
git commit -m "feat: report performance and paper similarity separately"
~~~

### Task 7: End-to-end verification, operations and handoff

**Files:**
- Modify: paper-repro-agent/scripts/smoke_multimodel.ps1
- Modify: paper-repro-agent/scripts/smoke_comparison.ps1
- Modify: paper-repro-agent/docs/configuration-guide.md
- Modify: paper-repro-agent/docs/workflow-setup.md
- Modify: paper-repro-agent/compose.yaml
- Modify: paper-repro-agent/.env.example
- Create: paper-repro-agent/tests/test_end_to_end_general_binary.py
- Create: paper-repro-agent/tests/fixtures/general_binary_*.csv

**Interfaces:**
- Consumes synthetic fixtures plus the current local Fengjie CSV for a bounded smoke run.
- Produces safe aggregate summaries, documented recovery behavior and a complete release gate.

- [ ] **Step 1: Add representative synthetic fixtures**

Create tiny fixtures for numeric-only binary CSV, mixed numeric/categorical CSV, missing-value CSV, imbalanced CSV and invalid one-class CSV. Do not copy rows from user data into the repository.

- [ ] **Step 2: Add end-to-end API tests**

Test diagnose → create manifest → create job → poll → result → compare. Assert that repeated manifest/dataset submissions reuse the idempotent result and changed options create a new job.

- [ ] **Step 3: Run the complete test gate**

~~~powershell
cd C:\Users\17716\Documents\arcgis\.worktrees\multi-model-cv\paper-repro-agent
pytest -q
~~~

Expected: legacy single-model API tests, Dify DSL determinism tests, suite tests, job recovery tests and report privacy tests all pass.

- [ ] **Step 4: Verify container configuration and health**

~~~powershell
docker compose config
docker compose up -d --build repro-runner
Invoke-WebRequest http://localhost:8001/healthz
~~~

Expected: the runner is healthy, job metadata/input directories are mounted, and the API returns status ok.

- [ ] **Step 5: Run a bounded real-data smoke test**

Use the existing 2training_samples_15180.csv only as a local smoke input. Inspect aggregate profile, job status, model statuses, shared split digest and ranking fields. Do not print raw rows or dossier text.

- [ ] **Step 6: Verify restart recovery**

Start one bounded synthetic long-running job, restart only repro-runner, poll the same job ID, and assert that the job becomes needs_retry or resumes from the last checkpoint without losing completed model results.

- [ ] **Step 7: Commit verification and documentation**

~~~powershell
git add scripts/smoke_multimodel.ps1 scripts/smoke_comparison.ps1 docs/configuration-guide.md docs/workflow-setup.md compose.yaml .env.example tests/test_end_to_end_general_binary.py tests/fixtures
git commit -m "test: verify reliable general binary workflow"
~~~

## Rollout order

1. Task 1 is the first low-risk production fix and can be deployed independently.
2. Tasks 2 and 3 enable new ordinary CSV shapes while preserving numeric-only behavior.
3. Task 4 must be deployed before routing long training through Dify.
4. Task 5 becomes the user-facing path after job API smoke tests pass.
5. Task 6 changes only interpretation/reporting and is deployed after API contracts are stable.
6. Task 7 is the release gate; a failed gate keeps the previous workflow as rollback target.

## Plan self-review

- Every design section has at least one implementation task and an explicit test gate.
- Existing synchronous endpoints and the V3 Dify workflow remain rollback/compatibility targets.
- The comparison qualifier bug is addressed before any new long-running workflow is introduced.
- Numeric-only CSV behavior is covered by existing tests; mixed categorical behavior is added by new fixtures.
- No task relies on an unspecified external service, hidden API key or raw user data in the repository.
- The single-user resource policy is enforced in both API admission and the persistent worker.

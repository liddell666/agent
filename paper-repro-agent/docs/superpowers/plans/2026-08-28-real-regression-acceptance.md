# Real-World Regression Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a privacy-safe, digest-pinned five-case real-paper regression acceptance gate against the isolated Dify regression candidate.

**Architecture:** A small `real_regression_acceptance` package owns registry validation, bounded source acquisition, spreadsheet-to-CSV normalization, Dify API interaction, safe evidence reduction, and corpus gate evaluation. Public files live only in an ignored cache; Git contains source identities, hashes, scientific choices, tests, and aggregate evidence. Live execution addresses only candidate App UUID `17fe51d4-091f-4729-87ee-3c0a2e920918` and never republishes it.

**Tech Stack:** Python 3.12, Pydantic 2.13.4, PyYAML 6.0.3, httpx 0.28.1, pandas 3.0.5, openpyxl 3.1.5, xlrd 2.0.2, pytest 9.1.1, local Dify 1.16.0.

## Global Constraints

- Use the five cases in `docs/superpowers/specs/2026-08-28-real-regression-acceptance-design.md`.
- Require at least four of five cases to finish end to end without changing code between cases.
- Require zero false `strictly_comparable` outcomes.
- Never commit PDF/CSV/XLS/XLSX bodies, remote response bodies, cookies, authorization headers, API keys, protocol tokens, or raw Dify payloads.
- Keep cached inputs under `.real-world-cache/` and safe live evidence under `.live-artifacts/`.
- Do not modify, stage, publish, or overwrite the production Dify app or the six user-owned DSL files.
- Do not automatically change scientific choices, the candidate graph, or candidate metadata during corpus execution.
- Every generalized defect discovered by a real case must first be represented by a minimized failing test in a separate follow-up task.
- Use primary sources only: UCI for datasets and publisher, author, PMC, or institutional repositories for papers.

---

## File map

- `real_world/regression_cases.yml`: digest-pinned source and scientific-choice registry.
- `src/real_regression_acceptance/models.py`: strict registry, result, and gate models.
- `src/real_regression_acceptance/acquisition.py`: HTTPS download, cache, archive extraction, signature checks, and canonical CSV conversion.
- `src/real_regression_acceptance/dify_client.py`: narrow file-upload/workflow-run client with secret-safe errors.
- `src/real_regression_acceptance/evidence.py`: response reduction, failure classification, leak scanning, and corpus gate evaluation.
- `src/real_regression_acceptance/runner.py`: prepare/confirm orchestration and resumable per-case execution.
- `src/real_regression_acceptance/cli.py`: `pin`, `acquire`, `run`, and `evaluate` commands.
- `scripts/run_real_regression_acceptance.py`: repository-root entry point.
- `tests/real_regression_acceptance/`: unit and integration tests using local fixtures and fake clients.
- `.live-artifacts/real-regression-acceptance.json`: ignored safe live result.

### Task 1: Registry models and corpus contract

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `src/real_regression_acceptance/__init__.py`
- Create: `src/real_regression_acceptance/models.py`
- Create: `tests/real_regression_acceptance/test_models.py`

**Interfaces:**
- Produces: `load_registry(path: Path) -> CorpusRegistry`.
- Produces: `SourceSpec`, `DatasetTransform`, `AcceptanceCase`, `SafeCaseResult`, and `CorpusEvaluation` Pydantic models.
- Consumes: no task-local interfaces.

- [ ] **Step 1: Add acceptance-only dependencies and ignored paths**

Add this optional group to `pyproject.toml`:

```toml
acceptance = [
  "httpx==0.28.1",
  "openpyxl==3.1.5",
  "pandas==3.0.5",
  "pyyaml==6.0.3",
  "xlrd==2.0.2",
]
```

Append these exact entries to `.gitignore`:

```gitignore
.real-world-cache/
.live-artifacts/real-regression-acceptance.json
```

- [ ] **Step 2: Write strict registry tests**

Create tests that write a minimal registry to `tmp_path`, call `load_registry`, and assert:

```python
assert registry.schema_version == 1
assert registry.candidate_app_id == "17fe51d4-091f-4729-87ee-3c0a2e920918"
assert registry.cases[0].task_type == "regression"
assert registry.cases[0].paper.sha256.startswith("sha256:")
```

Add negative parametrized cases for duplicate case IDs, non-HTTPS URLs, non-lowercase SHA-256, an empty target column, task type other than regression, archive traversal in `member`, and fewer or more than five live cases.

- [ ] **Step 3: Run the tests to verify red**

Run:

```powershell
python -m pytest -q tests/real_regression_acceptance/test_models.py
```

Expected: collection fails because `real_regression_acceptance.models` does not exist.

- [ ] **Step 4: Implement the strict models and loader**

Implement these public signatures:

```python
class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: HttpUrl
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    media_type: Literal["application/pdf", "application/zip"]
    max_bytes: int = Field(ge=1, le=50_000_000)


class DatasetTransform(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    member: str = Field(min_length=1, max_length=200)
    format: Literal["csv", "xls", "xlsx"]
    delimiter: Literal[",", ";"] = ","
    sheet_name: str | int | None = None
    rename_columns: dict[str, str] = Field(default_factory=dict)
    drop_columns: tuple[str, ...] = ()


class AcceptanceCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    paper: SourceSpec
    dataset: SourceSpec
    transform: DatasetTransform
    target_column: str = Field(min_length=1, max_length=128)
    task_type: Literal["regression"]
    paper_metric_overrides: tuple[dict[str, object], ...] = ()


class CorpusRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    candidate_app_id: Literal["17fe51d4-091f-4729-87ee-3c0a2e920918"]
    cases: tuple[AcceptanceCase, ...]


def load_registry(path: Path, *, require_live_corpus: bool = True) -> CorpusRegistry:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    registry = CorpusRegistry.model_validate(loaded)
    ids = [case.id for case in registry.cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case IDs must be unique")
    if require_live_corpus and len(ids) != 5:
        raise ValueError("live corpus must contain exactly five cases")
    for case in registry.cases:
        PurePosixPath(case.transform.member)
        if case.transform.member.startswith("/") or ".." in PurePosixPath(case.transform.member).parts:
            raise ValueError("dataset archive member is unsafe")
    return registry
```

Define the result models with these exact fields; do not add fields for paper
text, CSV samples, headers, request payloads, or tokens:

```python
class SafeCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str
    paper_digest: str
    dataset_digest: str
    paper_dataset_digest: str | None = None
    paper_test_digest: str | None = None
    workflow_run_ids: tuple[str, ...] = ()
    experiment_id: str | None = None
    task_type: Literal["regression"]
    status: Literal["succeeded", "failed"]
    failure_code: Literal[
        "acquisition_failed", "paper_parse_failed", "evidence_ambiguous",
        "dataset_invalid", "experiment_failed", "comparison_failed",
        "privacy_gate_failed", "service_unavailable",
    ] | None = None
    metrics: dict[str, dict[str, float]] = Field(default_factory=dict)
    model_statuses: dict[str, str] = Field(default_factory=dict)
    performance_ranking: tuple[str, ...] = ()
    paper_closeness_ranking: tuple[str, ...] = ()
    strict_status: str = "not_comparable"
    approximate_status: str = "insufficient_metrics"
    strict_reason_codes: tuple[str, ...] = ()
    test_digest: str | None = None
    elapsed_seconds: float = Field(ge=0)


class CorpusEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    passed: bool
    completed_count: int
    total_count: int
    false_strict_count: int
    failure_counts: dict[str, int]
    gate_errors: tuple[str, ...]
```

Tests construct their own one-case registry fixture with `sha256:` plus 64
lowercase `a` characters and call `require_live_corpus=False`. No runnable live
registry is created until Task 5 has real source digests.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
python -m pytest -q tests/real_regression_acceptance/test_models.py
```

Expected: all model tests pass.

Commit only Task 1 files:

```powershell
git add -- pyproject.toml .gitignore src/real_regression_acceptance/__init__.py src/real_regression_acceptance/models.py tests/real_regression_acceptance/test_models.py
git commit -m "feat: define real regression corpus contract"
```

### Task 2: Bounded source acquisition and canonical CSV conversion

**Files:**
- Create: `src/real_regression_acceptance/acquisition.py`
- Create: `tests/real_regression_acceptance/test_acquisition.py`

**Interfaces:**
- Consumes: `AcceptanceCase` and `SourceSpec` from Task 1.
- Produces: `AcquiredCase(paper_path: Path, csv_path: Path, paper_digest: str, dataset_digest: str)`.
- Produces: `acquire_case(case, cache_root, transport) -> AcquiredCase`.

- [ ] **Step 1: Write failing acquisition tests**

Use `httpx.MockTransport` and in-memory ZIP files. Cover:

```python
acquired = acquire_case(case, tmp_path, transport)
assert acquired.paper_path.read_bytes().startswith(b"%PDF-")
assert acquired.csv_path.read_text(encoding="utf-8").splitlines()[0] == "x,target"
assert acquired.paper_digest == case.paper.sha256
assert acquired.dataset_digest == case.dataset.sha256
```

Also assert rejection of redirects to HTTP, responses exceeding `max_bytes`, digest mismatch, HTML bodies, ZIP traversal, a missing member, duplicate normalized column names, non-finite target values, and cache bytes whose digest changed. Add XLSX and XLS conversion tests with tiny generated workbooks.

- [ ] **Step 2: Run the tests to verify red**

Run:

```powershell
python -m pytest -q tests/real_regression_acceptance/test_acquisition.py
```

Expected: import fails because `real_regression_acceptance.acquisition` does not exist.

- [ ] **Step 3: Implement download and verification**

Implement a streaming downloader with these fixed limits and checks:

```python
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 60.0
MAX_REDIRECTS = 5


def fetch_verified(source: SourceSpec, destination: Path, transport=None) -> str:
    if destination.exists():
        digest = "sha256:" + sha256(destination.read_bytes()).hexdigest()
        if digest == source.sha256:
            return digest
        raise AcquisitionError("cached_digest_mismatch")
    with httpx.Client(
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
        transport=transport,
    ) as client:
        with client.stream("GET", str(source.url)) as response:
            response.raise_for_status()
            if response.url.scheme != "https":
                raise AcquisitionError("insecure_redirect")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > source.max_bytes:
                    raise AcquisitionError("source_too_large")
    digest = "sha256:" + sha256(body).hexdigest()
    if digest != source.sha256:
        raise AcquisitionError("source_digest_mismatch")
    if source.media_type == "application/pdf" and not body.startswith(b"%PDF-"):
        raise AcquisitionError("paper_signature_invalid")
    if source.media_type == "application/zip" and not body.startswith(b"PK"):
        raise AcquisitionError("dataset_signature_invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    return digest
```

Write through a same-directory temporary file followed by `Path.replace` so interrupted downloads never create an accepted cache entry.

- [ ] **Step 4: Implement safe archive extraction and normalization**

Read only the exact declared ZIP member into memory; never call `extractall`. For CSV use `pandas.read_csv`, and for XLS/XLSX use `pandas.read_excel` with the declared sheet. Apply rename and drop rules, require a unique target column, require at least 20 rows, reject duplicate columns and non-finite target values, then write canonical UTF-8 CSV with `index=False` and `lineterminator="\n"`.

Return only paths and SHA-256 identities. Never return a DataFrame or sample rows from the public interface.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
python -m pytest -q tests/real_regression_acceptance/test_acquisition.py tests/real_regression_acceptance/test_models.py
```

Expected: all tests pass.

Commit:

```powershell
git add -- src/real_regression_acceptance/acquisition.py tests/real_regression_acceptance/test_acquisition.py
git commit -m "feat: acquire pinned regression sources"
```

### Task 3: Secret-safe Dify workflow client and resumable runner

**Files:**
- Create: `src/real_regression_acceptance/dify_client.py`
- Create: `src/real_regression_acceptance/runner.py`
- Create: `tests/real_regression_acceptance/test_dify_client.py`
- Create: `tests/real_regression_acceptance/test_runner.py`

**Interfaces:**
- Consumes: `AcquiredCase`, `AcceptanceCase`, and `SafeCaseResult`.
- Produces: `DifyWorkflowClient.upload_file(path, user) -> str` and `run(inputs, user) -> WorkflowOutcome`.
- Produces: `run_case(case, acquired, client, checkpoint_store) -> SafeCaseResult`.

- [ ] **Step 1: Write failing client tests**

With `httpx.MockTransport`, assert bearer authorization is sent but never present in exception text or returned objects. Verify `POST /v1/files/upload`, then `POST /v1/workflows/run` with `response_mode="blocking"`, a stable opaque user derived from the case ID, and Dify file descriptors rather than file bytes in workflow inputs.

Assert a non-2xx response becomes one of `service_unavailable`, `paper_parse_failed`, or `comparison_failed` without copying the response body.

- [ ] **Step 2: Write failing runner tests**

Use a fake client with prepare and confirm outcomes. Verify the prepare call uses:

```python
{
    "run_mode": "prepare",
    "target_column": case.target_column,
    "test_size": 0.2,
    "random_state": 42,
    "models_json": '["linear_regression","random_forest","gradient_boosting","xgboost"]',
    "cv_folds": "5",
    "optimization_metric": "rmse",
}
```

The confirm call must reuse the returned protocol token only in memory and set `confirm_protocol=True`. Checkpoint files may contain case ID, phase, run ID, and terminal status only; assert the protocol token and fake secret sentinel are absent.

- [ ] **Step 3: Run the tests to verify red**

Run:

```powershell
python -m pytest -q tests/real_regression_acceptance/test_dify_client.py tests/real_regression_acceptance/test_runner.py
```

Expected: imports fail because the modules do not exist.

- [ ] **Step 4: Implement the narrow API client**

The constructor accepts `base_url`, `api_key`, `expected_app_id`, and optional transport. Validate the expected App UUID against the registry before any request. Expose no property that returns the key. Use a single redacted exception type whose message contains only a stable error code and HTTP status.

Use the published workflow API endpoints:

```python
POST {base_url}/v1/files/upload
POST {base_url}/v1/workflows/run
```

For workflow files, pass `transfer_method="local_file"`, the upload ID, and the correct `type="document"`. Use a 15-second connect timeout and 900-second workflow timeout.

- [ ] **Step 5: Implement prepare/confirm orchestration**

Parse only allowlisted output keys: `dossier_json`, `validation_json`, `experiment_json`, `comparison_json`, `assessment_json`, and `markdown_report`. Decode JSON strings into dictionaries, immediately reduce them to `SafeCaseResult`, and release references to raw outputs. If prepare reports unresolved evidence, classify it as `evidence_ambiguous`; do not invent an override. Resume skips only terminal cases whose paper and dataset digests match the current registry.

- [ ] **Step 6: Run tests and commit**

Run:

```powershell
python -m pytest -q tests/real_regression_acceptance/test_dify_client.py tests/real_regression_acceptance/test_runner.py
```

Expected: all tests pass.

Commit:

```powershell
git add -- src/real_regression_acceptance/dify_client.py src/real_regression_acceptance/runner.py tests/real_regression_acceptance/test_dify_client.py tests/real_regression_acceptance/test_runner.py
git commit -m "feat: drive regression candidate acceptance"
```

### Task 4: Safe evidence, leak scan, corpus gates, and CLI

**Files:**
- Create: `src/real_regression_acceptance/evidence.py`
- Create: `src/real_regression_acceptance/cli.py`
- Create: `scripts/run_real_regression_acceptance.py`
- Create: `tests/real_regression_acceptance/test_evidence.py`
- Create: `tests/real_regression_acceptance/test_cli.py`

**Interfaces:**
- Consumes: `SafeCaseResult` instances from Task 3.
- Produces: `evaluate_corpus(results) -> CorpusEvaluation`.
- Produces CLI commands `pin`, `acquire`, `run`, and `evaluate`.

- [ ] **Step 1: Write failing evidence tests**

Build five safe results and assert four succeeded cases plus one categorized failure passes. Assert any of the following fails the gate: fewer than four completed cases, unknown failure code, non-regression task, a non-finite metric, missing test digest, a `strictly_comparable` result without identical paper dataset/test digests, candidate graph drift, or any sentinel resembling CSV rows, PDF text, bearer tokens, cookies, or protocol tokens.

Use this exact strict guard:

```python
strict_allowed = (
    result.strict_status != "strictly_comparable"
    or (
        result.paper_dataset_digest == result.dataset_digest
        and result.paper_test_digest == result.test_digest
    )
)
```

- [ ] **Step 2: Write failing CLI tests**

Invoke `main([...])` with a fake client factory and temporary paths. Assert `acquire` never prints file contents, `run` requires `DIFY_REGRESSION_API_KEY`, and `evaluate` writes canonical sorted JSON with file permissions restricted to the current user when supported.

- [ ] **Step 3: Run the tests to verify red**

Run:

```powershell
python -m pytest -q tests/real_regression_acceptance/test_evidence.py tests/real_regression_acceptance/test_cli.py
```

Expected: imports fail because the modules do not exist.

- [ ] **Step 4: Implement evidence reduction and leak scanning**

Allow only stable IDs matching known patterns, lowercase SHA-256 values, finite numeric aggregate metrics, allowlisted model/metric/status names, counts, rankings, elapsed seconds, and stable failure codes. Serialize using:

```python
json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
```

Scan both keys and string values for `authorization`, `bearer `, `cookie`, `protocol_token`, `api_key`, `paper_text`, `csv_rows`, and user-supplied sentinel values. A match returns `privacy_gate_failed` and prevents evidence publication.

- [ ] **Step 5: Implement the CLI and entry point**

`pin --url URL --media-type TYPE --max-bytes N` downloads one explicit URL
into a temporary directory, prints only final URL, byte count, media signature,
and SHA-256, and never writes the live registry automatically. `acquire`
verifies all registry entries. `run` requires the API key from the process
environment and writes per-case safe checkpoints. `evaluate` writes
`.live-artifacts/real-regression-acceptance.json` only after leak scanning.

The repository entry point is exactly:

```python
from real_regression_acceptance.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run tests and commit**

Run:

```powershell
python -m pytest -q tests/real_regression_acceptance
```

Expected: all acceptance-harness tests pass.

Commit:

```powershell
git add -- src/real_regression_acceptance/evidence.py src/real_regression_acceptance/cli.py scripts/run_real_regression_acceptance.py tests/real_regression_acceptance/test_evidence.py tests/real_regression_acceptance/test_cli.py
git commit -m "feat: evaluate real regression acceptance"
```

### Task 5: Pin the five primary-source pairs

**Files:**
- Create: `real_world/regression_cases.yml`
- Test: `tests/real_regression_acceptance/test_models.py`

**Interfaces:**
- Consumes: `pin` and registry loader from Tasks 1 and 4.
- Produces: the runnable five-case registry.

- [ ] **Step 1: Pin the five UCI dataset archives**

Run `pin` against these exact primary dataset URLs, substituting each URL for
`$sourceUrl`:

```powershell
python scripts/run_real_regression_acceptance.py pin --url $sourceUrl --media-type application/zip --max-bytes 50000000
```

Source URLs:

```text
https://archive.ics.uci.edu/static/public/242/energy+efficiency.zip
https://archive.ics.uci.edu/static/public/165/concrete+compressive+strength.zip
https://archive.ics.uci.edu/static/public/186/wine+quality.zip
https://archive.ics.uci.edu/static/public/374/appliances+energy+prediction.zip
https://archive.ics.uci.edu/static/public/477/real+estate+valuation.zip
```

Require HTTP 200, HTTPS final URL, ZIP signature, bounded byte size, and record the returned lowercase SHA-256 values.

- [ ] **Step 2: Pin five paper PDFs from primary repositories**

Use these repository/publisher anchors, resolving their current direct PDF links
and running this exact command for each selected direct URL:

```powershell
python scripts/run_real_regression_acceptance.py pin --url $paperUrl --media-type application/pdf --max-bytes 20000000
```

Anchors:

```text
Energy Efficiency: DOI 10.1016/j.enbuild.2012.03.003, author/institutional copy
Concrete Strength: PMC11569169, NCBI PDF
Wine Quality: https://www3.dsi.uminho.pt/pcortez/winequality09.pdf
Appliances Energy: https://orbi.umons.ac.be/bitstream/20.500.12907/23357/1/1-s2.0-S0378778816308970-main.pdf
Real Estate Valuation: DOI 10.1016/j.asoc.2018.01.029, publisher or author-institutional PDF
```

If a DOI landing page does not yield a legally accessible PDF, replace only that paper with an open-access publisher or institutional paper that explicitly uses the same UCI dataset and reports regression metrics. Do not use mirrors, scraped copies, or search-result downloads.

- [ ] **Step 3: Write the complete live registry**

For each case record the observed paper/dataset SHA-256, exact archive member, format/sheet, normalized target, required drop columns, and source attribution. Use these scientific choices:

```yaml
energy-efficiency:
  target_column: heating_load
  drop_columns: [cooling_load]
concrete-strength:
  target_column: concrete_compressive_strength
wine-quality-red:
  target_column: quality
appliances-energy:
  target_column: Appliances
  drop_columns: [date, rv1, rv2]
real-estate-valuation:
  target_column: house_price_of_unit_area
  drop_columns: [No]
```

Column renames must be explicit in the registry and derived only from the UCI file header. Do not add a paper metric override unless the PDF explicitly identifies the metric value, evaluation split, and model.

- [ ] **Step 4: Validate acquisition twice**

Run:

```powershell
python scripts/run_real_regression_acceptance.py acquire --registry real_world/regression_cases.yml
python scripts/run_real_regression_acceptance.py acquire --registry real_world/regression_cases.yml
```

Expected: first run downloads and normalizes five cases; second run reports five verified cache hits. Neither run prints paper text or CSV rows.

- [ ] **Step 5: Run registry tests and commit**

Run:

```powershell
python -m pytest -q tests/real_regression_acceptance/test_models.py tests/real_regression_acceptance/test_acquisition.py
```

Expected: all tests pass and `load_registry(..., require_live_corpus=True)` returns five cases.

Commit:

```powershell
git add -- real_world/regression_cases.yml tests/real_regression_acceptance/test_models.py
git commit -m "test: pin real regression acceptance corpus"
```

### Task 6: Run the live five-case candidate gate

**Files:**
- Create ignored: `.live-artifacts/real-regression-acceptance.json`
- Modify only if outcomes require documentation: `docs/release-workflow.md`

**Interfaces:**
- Consumes: runnable corpus, candidate API key from environment, and healthy local Dify/runner services.
- Produces: privacy-safe aggregate evidence and a pass/fail corpus evaluation.

- [ ] **Step 1: Record immutable preflight identities**

Read candidate App, draft, and published rows without mutation. Require:

```text
App UUID: 17fe51d4-091f-4729-87ee-3c0a2e920918
Draft workflow UUID: 912d4e05-494c-4302-a189-788a59c6c0c2
Published workflow UUID: 8cc0de24-ed72-4026-9850-a9a4cccc0a23
Graph digest: sha256:d63e730b785fd20b31372fb853129d891b80b169b62eac4e78b01e222db4236a
Metadata digest: sha256:97f0389ff657384392ff4c2ae8aa7ea94c2b9113fb8b4d6b83c0398d524b1d6a
```

Require Dify, paper parser, and runner health checks to pass and require zero queued/running runner jobs before starting.

- [ ] **Step 2: Run all five cases without code changes between cases**

Set `DIFY_REGRESSION_API_KEY` only in the current process environment and run:

```powershell
python scripts/run_real_regression_acceptance.py run --registry real_world/regression_cases.yml --output .live-artifacts/real-regression-acceptance.json --resume
```

Expected: five terminal safe case records. A failed case is preserved with one stable category and no raw response.

- [ ] **Step 3: Evaluate gates and independently scan evidence**

Run:

```powershell
python scripts/run_real_regression_acceptance.py evaluate --input .live-artifacts/real-regression-acceptance.json
python -m json.tool .live-artifacts/real-regression-acceptance.json > $null
rg -n -i "authorization|bearer |cookie|protocol_token|api_key|paper_text|csv_rows" .live-artifacts/real-regression-acceptance.json
```

Expected: evaluation passes, JSON parses, and `rg` returns no matches. Require at least four completed cases and zero unjustified strict-comparability results.

- [ ] **Step 4: Verify candidate identity did not change**

Repeat the read-only graph/metadata inspection and the four-layer release checker. Require the exact preflight IDs/digests and `{"drift":[]}`. Any change fails acceptance even when case runs succeeded.

- [ ] **Step 5: Stop on generalized defects**

If the corpus gate fails because of product behavior, do not patch inside this task. Preserve the safe failure distribution, write a minimized defect specification and TDD plan, and resume this task only after that separate generalized fix is reviewed, published with a new rollback backup, and independently verified. Acquisition/source outages remain categorized evidence and do not authorize product changes.

### Task 7: Regression verification and operating documentation

**Files:**
- Modify: `docs/release-workflow.md`
- Test: entire repository

**Interfaces:**
- Consumes: safe Task 6 evidence.
- Produces: final documented candidate status and reproducible operating commands.

- [ ] **Step 1: Document only aggregate outcomes**

Add corpus date, five case IDs, completion count, failure category counts, strict/approximate status counts, candidate IDs/digests, safe evidence path, and the exact rerun command. Do not paste paper metrics unless they are already present in the safe aggregate schema.

- [ ] **Step 2: Run focused and full verification**

Run:

```powershell
python -m pytest -q tests/real_regression_acceptance tests/test_end_to_end_regression.py tests/test_dify_regression_code.py tests/test_dify_regression_dsl.py
python -m pytest -q
python scripts/check_workflow_release.py --dsl dify/paper-comparison-regression-workflow.yml --source scripts/build_regression_dsl.py scripts/build_multimodel_dsl.py dify/paper-comparison-workflow.yml dify/paper-dossier-workflow.yml dify/code/validate_evidence.py dify/code/comparison_workflow.py dify/code/experiment_workflow.py dify/code/validate_parser.py --draft-snapshot .live-artifacts/regression-candidate-draft-snapshot.json --published-snapshot .live-artifacts/regression-candidate-published-snapshot.json --json
git diff --check
```

Expected: all tests pass, release checker returns `{"drift":[]}`, and `git diff --check` is clean.

- [ ] **Step 3: Review and commit**

Review only task-owned changes and confirm the six user-owned DSL files remain unstaged. Commit documentation and any remaining harness files:

```powershell
git add -- docs/release-workflow.md
git commit -m "docs: record real regression acceptance"
```

Report the completion ratio, strict-comparability safety result, candidate IDs/digests, test counts, commits, and any categorized remaining limitation. Do not call the candidate production-ready unless all corpus and release gates pass.

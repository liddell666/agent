# V3 Paper Comparison Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent Dify V3 workflow that accepts a paper-dossier JSON file and training CSV, runs the existing deterministic experiment, reports strict comparability, and grades approximate metric similarity without calling approximate agreement an exact reproduction.

**Architecture:** Add one bounded multipart adapter endpoint, `POST /v1/parse-dossier`, to validate and normalize the uploaded dossier and optional manual overrides. Keep `/v1/compare-result` unchanged for strict provenance checks; use deterministic Dify code-node helpers to build its request, calculate 5%/10% similarity grades, and format the final report. Configure a new Dify workflow so V2 remains unchanged.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest, Docker Compose, Dify Workflow code nodes (Python 3), PowerShell smoke scripts.

## Global Constraints

- Preserve the existing paper-dossier workflow, V2 table-experiment workflow, and `/v1/compare-result` semantics.
- Do not send raw CSV rows to an LLM, knowledge base, prompt, log, or workflow output.
- DeepSeek must not calculate metric differences or decide the reproduction status.
- Accept one UTF-8 `.json` dossier no larger than 5 MiB and an override string no larger than 64 KiB.
- Support `roc_auc`/`AUC`/`ROC AUC`/`roc-auc`, `accuracy`, `balanced_accuracy`, `precision`, `recall`, and `f1`.
- Convert only explicit percent strings such as `"91%"` and `"91％"` to `0.91`; do not rescale the ordinary number `91`.
- Require `0 < close_threshold < partial_threshold <= 1`; defaults are `0.05` and `0.10`.
- Preserve paper values, experiment values, evidence, and manual-override provenance as separate fields.
- Never use “复现成功” for an approximate result; strict comparability and approximate similarity remain separate conclusions.
- Every HTTP failure branch must avoid references to nodes that did not run.

---

## File Map

- Create `src/repro_runner/dossier.py`: metric normalization, dossier validation, override merging, and ambiguity detection.
- Modify `src/repro_runner/schemas.py`: response types for normalized dossier metrics and evidence.
- Modify `src/repro_runner/config.py`: exact 5 MiB dossier and 64 KiB override limits.
- Modify `src/repro_runner/api.py`: bounded `/v1/parse-dossier` multipart endpoint and sanitized failures.
- Create `tests/repro_runner/test_dossier.py`: domain tests for normalization, validation, overrides, and ambiguity.
- Modify `tests/repro_runner/test_api.py`: adapter endpoint, size-limit, and internal-error tests.
- Create `dify/code/comparison_workflow.py`: deterministic Dify node helpers, scoring, reports, and safe failure payloads.
- Modify `tests/test_dify_code.py`: Dify helper contract and error-branch tests.
- Create `dify/paper-comparison-workflow.md`: exact V3 Dify node-by-node setup.
- Create `tests/fixtures/minimal-paper-dossier.json`: stable smoke-test dossier.
- Create `scripts/smoke_comparison.ps1`: local adapter/run/compare smoke test.
- Modify `docs/configuration-guide.md`: V3 start, rebuild, smoke, and troubleshooting instructions.
- Create `.superpowers/sdd/paper-comparison-workflow-report.md`: commands, Dify run IDs, metrics, failure checks, and publication evidence.

### Task 1: Normalize paper metric names and values

**Files:**
- Create: `src/repro_runner/dossier.py`
- Test: `tests/repro_runner/test_dossier.py`

**Interfaces:**
- Produces: `normalize_metric_name(name: str) -> str`
- Produces: `parse_reported_value(value: object) -> float | None`
- Consumes: no new project interfaces.

- [ ] **Step 1: Write failing normalization tests**

```python
import pytest

from repro_runner.dossier import normalize_metric_name, parse_reported_value


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("AUC", "roc_auc"),
        ("ROC AUC", "roc_auc"),
        ("roc-auc", "roc_auc"),
        ("Balanced Accuracy", "balanced_accuracy"),
        ("F1", "f1"),
    ],
)
def test_normalize_metric_name(source: str, expected: str) -> None:
    assert normalize_metric_name(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [(0.91, 0.91), ("0.91", 0.91), ("91%", 0.91), ("91％", 0.91), (91, 91.0)],
)
def test_parse_reported_value(source: object, expected: float) -> None:
    assert parse_reported_value(source) == expected


@pytest.mark.parametrize("source", [None, True, "", "0.8-0.9", "NaN", "inf"])
def test_parse_reported_value_rejects_non_numeric_values(source: object) -> None:
    assert parse_reported_value(source) is None
```

- [ ] **Step 2: Run the tests and confirm the missing module failure**

Run: `python -m pytest tests/repro_runner/test_dossier.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'repro_runner.dossier'`.

- [ ] **Step 3: Implement the two normalization functions**

```python
from __future__ import annotations

import math


SUPPORTED_METRICS = {
    "roc_auc",
    "accuracy",
    "balanced_accuracy",
    "precision",
    "recall",
    "f1",
}
_ALIASES = {"auc": "roc_auc", "roc_auc": "roc_auc"}


def normalize_metric_name(name: str) -> str:
    normalized = "_".join(name.strip().casefold().replace("-", " ").split())
    return _ALIASES.get(normalized, normalized)


def parse_reported_value(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    percentage = False
    candidate = value
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith(("%", "％")):
            percentage = True
            candidate = candidate[:-1].strip()
    try:
        parsed = float(candidate)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if percentage:
        parsed /= 100.0
    return round(parsed, 6)
```

- [ ] **Step 4: Run the focused tests**

Run: `python -m pytest tests/repro_runner/test_dossier.py -q`

Expected: PASS for all normalization tests.

- [ ] **Step 5: Commit the independent normalization unit**

```powershell
git add src/repro_runner/dossier.py tests/repro_runner/test_dossier.py
git commit -m "feat: normalize paper comparison metrics"
```

### Task 2: Validate dossiers, merge overrides, and detect ambiguity

**Files:**
- Modify: `src/repro_runner/schemas.py`
- Modify: `src/repro_runner/dossier.py`
- Modify: `tests/repro_runner/test_dossier.py`

**Interfaces:**
- Consumes: `normalize_metric_name()` and `parse_reported_value()` from Task 1.
- Produces: `parse_dossier(filename: str, content: bytes, overrides_json: str) -> DossierParseResponse`.
- Produces: `DossierParseResponse.model_dump()` matching the design response contract.

- [ ] **Step 1: Add failing tests for a valid dossier, percent input, and evidence preservation**

```python
import json

from repro_runner.dossier import parse_dossier


def _dossier(metrics: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {
            "title": "Minimal Paper",
            "research_problem": "Binary classification.",
            "task_type": "classification",
            "datasets": [],
            "methods": [],
            "metrics": metrics,
            "gaps": [],
        }
    ).encode()


def test_parse_dossier_preserves_evidence_and_converts_percent() -> None:
    response = parse_dossier(
        "paper.json",
        _dossier(
            [
                {
                    "name": "AUC",
                    "reported_value": "91%",
                    "dataset": "test",
                    "split": "test",
                    "evidence": [
                        {
                            "page": 8,
                            "source_text": "The test AUC was 91%.",
                            "source": "paper",
                            "confidence": 1.0,
                        }
                    ],
                }
            ]
        ),
        "[]",
    )

    assert response.valid is True
    metric = response.metrics[0]
    assert metric.normalized_name == "roc_auc"
    assert metric.reported_value == 0.91
    assert metric.source == "paper_dossier"
    assert metric.evidence[0].page == 8
```

- [ ] **Step 2: Add failing tests for overrides, ambiguity, and invalid inputs**

```python
def test_override_replaces_unique_metric_and_records_source() -> None:
    response = parse_dossier(
        "paper.json",
        _dossier([{"name": "AUC", "reported_value": 0.88, "evidence": []}]),
        json.dumps([{"name": "roc_auc", "reported_value": 0.91, "split": "test"}]),
    )

    metric = response.metrics[0]
    assert metric.reported_value == 0.91
    assert metric.split == "test"
    assert metric.source == "manual_override"


def test_duplicate_metric_without_unique_override_is_ambiguous() -> None:
    response = parse_dossier(
        "paper.json",
        _dossier(
            [
                {"name": "AUC", "reported_value": 0.88, "dataset": "A", "evidence": []},
                {"name": "ROC AUC", "reported_value": 0.91, "dataset": "B", "evidence": []},
            ]
        ),
        "[]",
    )

    assert [metric.ambiguous for metric in response.metrics] == [True, True]
    assert "ambiguous_metric" in response.warnings


@pytest.mark.parametrize(
    ("filename", "content", "overrides", "code"),
    [
        ("paper.txt", b"{}", "[]", "invalid_dossier_extension"),
        ("paper.json", b"\xff", "[]", "invalid_dossier_encoding"),
        ("paper.json", b"{", "[]", "invalid_dossier_json"),
        ("paper.json", b"{}", "[]", "invalid_dossier_schema"),
        ("paper.json", _dossier([]), "[]", "no_reported_metrics"),
        ("paper.json", _dossier([{"name": "AUC", "evidence": []}]), "{}", "invalid_metric_overrides"),
    ],
)
def test_parse_dossier_returns_safe_validation_errors(
    filename: str, content: bytes, overrides: str, code: str
) -> None:
    response = parse_dossier(filename, content, overrides)
    assert response.valid is False
    assert response.errors[0].code == code
```

- [ ] **Step 3: Run the new tests and confirm missing schema/function failures**

Run: `python -m pytest tests/repro_runner/test_dossier.py -q`

Expected: FAIL because `DossierParseResponse` and full `parse_dossier()` behavior do not exist.

- [ ] **Step 4: Add focused response schemas**

Append to `src/repro_runner/schemas.py`:

```python
class DossierEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    source_text: str = Field(min_length=1, max_length=2000)
    source: Literal["paper", "supplement", "repository", "user", "inferred"]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class DossierMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    normalized_name: str
    reported_value: float | None = None
    dataset: str | None = None
    split: str | None = None
    dataset_id: str | None = None
    test_size: float | None = Field(default=None, ge=0.1, le=0.5)
    random_state: int | None = Field(default=None, ge=0)
    train_rows: int | None = Field(default=None, ge=1)
    test_rows: int | None = Field(default=None, ge=1)
    test_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    source: Literal["paper_dossier", "manual_override"]
    evidence: list[DossierEvidence] = Field(default_factory=list)
    ambiguous: bool = False


class DossierParseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    title: str | None = None
    metrics: list[DossierMetric] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[ValidationErrorItem] = Field(default_factory=list)
```

- [ ] **Step 5: Implement dossier validation and override merging**

In `src/repro_runner/dossier.py`, import `PaperDossier`, `ReportedMetricInput`, and the new response types. Implement `parse_dossier()` so known input problems return `DossierParseResponse(valid=False, errors=[...])`; do not raise user content in exception text.

Use this public function and helper contract:

```python
import json
from pathlib import Path

from pydantic import ValidationError

from paper_parser.schemas import PaperDossier
from repro_runner.schemas import (
    DossierEvidence,
    DossierMetric,
    DossierParseResponse,
    ReportedMetricInput,
    ValidationErrorItem,
)


def _invalid(code: str, message: str) -> DossierParseResponse:
    return DossierParseResponse(
        valid=False,
        errors=[ValidationErrorItem(code=code, message=message)],
    )


def parse_dossier(
    filename: str, content: bytes, overrides_json: str = "[]"
) -> DossierParseResponse:
    if Path(filename).suffix.casefold() != ".json":
        return _invalid("invalid_dossier_extension", "The dossier file must use the .json extension.")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return _invalid("invalid_dossier_encoding", "The dossier file must be UTF-8 encoded.")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _invalid("invalid_dossier_json", "The dossier file is not valid JSON.")
    try:
        dossier = PaperDossier.model_validate(payload)
    except ValidationError:
        return _invalid("invalid_dossier_schema", "The dossier does not match the PaperDossier schema.")
    if not dossier.metrics:
        return _invalid("no_reported_metrics", "The dossier does not contain reported metrics.")
    try:
        override_payload = json.loads(overrides_json or "[]")
        if not isinstance(override_payload, list):
            raise ValueError("override must be an array")
        overrides = [ReportedMetricInput.model_validate(item) for item in override_payload]
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        return _invalid("invalid_metric_overrides", "Metric overrides must be a valid JSON array.")

    metrics = _metrics_from_dossier(dossier)
    warnings = _apply_overrides_and_mark_ambiguity(metrics, overrides)
    return DossierParseResponse(
        valid=True,
        title=dossier.title,
        metrics=metrics,
        warnings=warnings,
        errors=[],
    )
```

`_metrics_from_dossier()` must copy each evidence object, set `source="paper_dossier"`, and use the Task 1 functions. `_apply_overrides_and_mark_ambiguity()` must match on normalized name, then use provided `dataset` and `split` to narrow duplicates; it sets all unresolved duplicates to `ambiguous=True` and appends exactly one `ambiguous_metric` warning. It copies only fields explicitly present in `override.model_fields_set`, and sets the chosen item’s source to `manual_override`.

Use these concrete helpers:

```python
_OVERRIDE_FIELDS = (
    "reported_value",
    "dataset",
    "split",
    "dataset_id",
    "test_size",
    "random_state",
    "train_rows",
    "test_rows",
    "test_digest",
)


def _metrics_from_dossier(dossier: PaperDossier) -> list[DossierMetric]:
    return [
        DossierMetric(
            name=metric.name,
            normalized_name=normalize_metric_name(metric.name),
            reported_value=parse_reported_value(metric.reported_value),
            dataset=metric.dataset,
            split=metric.split,
            source="paper_dossier",
            evidence=[DossierEvidence.model_validate(item.model_dump()) for item in metric.evidence],
        )
        for metric in dossier.metrics
    ]


def _apply_overrides_and_mark_ambiguity(
    metrics: list[DossierMetric], overrides: list[ReportedMetricInput]
) -> list[str]:
    grouped: dict[str, list[DossierMetric]] = {}
    for metric in metrics:
        grouped.setdefault(metric.normalized_name, []).append(metric)
    for group in grouped.values():
        if len(group) > 1:
            for metric in group:
                metric.ambiguous = True

    for override in overrides:
        candidates = list(grouped.get(normalize_metric_name(override.name), []))
        if "dataset" in override.model_fields_set:
            candidates = [item for item in candidates if item.dataset == override.dataset]
        if "split" in override.model_fields_set:
            candidates = [item for item in candidates if item.split == override.split]
        if len(candidates) != 1:
            continue
        selected = candidates[0]
        for field in _OVERRIDE_FIELDS:
            if field in override.model_fields_set:
                value = getattr(override, field)
                if field == "reported_value":
                    value = parse_reported_value(value)
                setattr(selected, field, value)
        selected.source = "manual_override"
        selected.ambiguous = False

    return ["ambiguous_metric"] if any(item.ambiguous for item in metrics) else []
```

- [ ] **Step 6: Run domain tests**

Run: `python -m pytest tests/repro_runner/test_dossier.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the dossier domain unit**

```powershell
git add src/repro_runner/schemas.py src/repro_runner/dossier.py tests/repro_runner/test_dossier.py
git commit -m "feat: validate paper dossier metrics"
```

### Task 3: Expose the bounded dossier adapter API

**Files:**
- Modify: `src/repro_runner/config.py`
- Modify: `src/repro_runner/api.py`
- Modify: `tests/repro_runner/test_api.py`

**Interfaces:**
- Consumes: `parse_dossier(filename, content, overrides_json)` from Task 2.
- Produces: `POST /v1/parse-dossier` multipart endpoint with `DossierParseResponse`.
- Preserves: `/v1/validate-dataset`, `/v1/run-experiment`, `/v1/compare-result`.

- [ ] **Step 1: Write failing API success and semantic-error tests**

Add `import json` beside the existing test-module imports, then add:

```python
def _paper_dossier() -> bytes:
    return json.dumps(
        {
            "title": "Minimal Paper",
            "research_problem": "Binary classification.",
            "task_type": "classification",
            "datasets": [],
            "methods": [],
            "metrics": [{"name": "AUC", "reported_value": "91%", "evidence": []}],
            "gaps": [],
        }
    ).encode()


def test_parse_dossier_endpoint_returns_normalized_metrics(client: TestClient) -> None:
    response = client.post(
        "/v1/parse-dossier",
        data={"metric_overrides_json": "[]"},
        files={"file": ("paper.json", _paper_dossier(), "application/json")},
    )
    assert response.status_code == 200
    assert response.json()["metrics"][0]["reported_value"] == 0.91


def test_parse_dossier_endpoint_returns_200_for_bad_user_json(client: TestClient) -> None:
    response = client.post(
        "/v1/parse-dossier",
        files={"file": ("paper.json", b"{", "application/json")},
    )
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["errors"][0]["code"] == "invalid_dossier_json"
```

- [ ] **Step 2: Write failing size and sanitization tests**

```python
def test_parse_dossier_enforces_independent_five_mib_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "parse_dossier", lambda *_args, **_kwargs: pytest.fail("parser called"))
    response = client.post(
        "/v1/parse-dossier",
        files={"file": ("paper.json", b"x" * (5 * 1024 * 1024 + 1), "application/json")},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "dossier_file_too_large"


def test_parse_dossier_sanitizes_internal_failures(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("private dossier stack")

    monkeypatch.setattr(api, "parse_dossier", fail)
    response = client.post(
        "/v1/parse-dossier",
        files={"file": ("paper.json", _paper_dossier(), "application/json")},
    )
    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "dossier_parse_failed"
    assert "private" not in response.text
```

- [ ] **Step 3: Run the focused API tests and confirm 404 failures**

Run: `python -m pytest tests/repro_runner/test_api.py -k parse_dossier -q`

Expected: FAIL because `/v1/parse-dossier` returns 404.

- [ ] **Step 4: Add exact limits to settings**

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPRO_RUNNER_", extra="ignore")

    max_upload_mb: int = 100
    max_dossier_mb: int = Field(default=5, ge=1, le=20)
    max_metric_overrides_kb: int = Field(default=64, ge=1, le=256)
    max_columns: int = 256
    default_target_column: str = "Y_cls"
    storage_dir: Path = Path("/data/experiments")
    max_concurrent_experiments: int = Field(default=1, ge=1)
```

- [ ] **Step 5: Implement the multipart endpoint**

Add to `src/repro_runner/api.py`:

```python
@app.post("/v1/parse-dossier", response_model=DossierParseResponse)
async def parse_dossier_endpoint(
    file: UploadFile = File(...),
    metric_overrides_json: str = Form(default="[]"),
    settings: Settings = Depends(get_settings),
) -> DossierParseResponse:
    max_bytes = settings.max_dossier_mb * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "dossier_file_too_large",
                "message": "The dossier exceeds the configured size limit.",
                "request_id": _request_id(),
            },
        )
    if len(metric_overrides_json.encode("utf-8")) > settings.max_metric_overrides_kb * 1024:
        return DossierParseResponse(
            valid=False,
            errors=[
                ValidationErrorItem(
                    code="invalid_metric_overrides",
                    message="Metric overrides exceed the configured size limit.",
                )
            ],
        )
    try:
        return parse_dossier(file.filename or "", content, metric_overrides_json)
    except Exception:
        request_id = _request_id()
        logger.exception("dossier parsing failed request_id=%s", request_id)
        raise _internal_error("dossier_parse_failed", request_id) from None
```

Import `DossierParseResponse`, `ValidationErrorItem`, and `parse_dossier`. Do not alter `ComparisonRequest` or the compare route.

- [ ] **Step 6: Run API and regression tests**

Run: `python -m pytest tests/repro_runner/test_api.py tests/repro_runner/test_compare.py tests/repro_runner/test_dossier.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the API adapter**

```powershell
git add src/repro_runner/config.py src/repro_runner/api.py tests/repro_runner/test_api.py
git commit -m "feat: expose dossier metric adapter"
```

### Task 4: Implement deterministic Dify comparison helpers

**Files:**
- Create: `dify/code/comparison_workflow.py`
- Modify: `tests/test_dify_code.py`

**Interfaces:**
- Consumes: dossier JSON from `/v1/parse-dossier`, experiment JSON from V2, and comparison JSON from `/v1/compare-result`.
- Produces: code-node functions `parse_dossier_response`, `validate_thresholds`, `build_comparison_request`, `parse_comparison_response`, `score_approximate_similarity`, `format_comparison_report`, `normalize_dossier_http_failure`, and `normalize_comparison_http_failure`.

- [ ] **Step 1: Add failing tests for threshold validation and compare-request filtering**

```python
from dify.code.comparison_workflow import (
    build_comparison_request,
    format_comparison_report,
    normalize_comparison_http_failure,
    score_approximate_similarity,
    validate_thresholds,
)


def test_validate_thresholds_accepts_defaults_and_rejects_bad_order() -> None:
    assert validate_thresholds(0.05, 0.10)["thresholds_ok"] is True
    bad = validate_thresholds(0.10, 0.05)
    assert bad["thresholds_ok"] is False
    assert "invalid_similarity_thresholds" in bad["threshold_errors"]


def test_build_comparison_request_strips_display_fields_and_ambiguous_items() -> None:
    dossier = {
        "valid": True,
        "metrics": [
            {
                "name": "AUC",
                "normalized_name": "roc_auc",
                "reported_value": 0.91,
                "dataset": "test",
                "split": "test",
                "source": "paper_dossier",
                "evidence": [{"page": 8}],
                "ambiguous": False,
            },
            {
                "name": "F1",
                "normalized_name": "f1",
                "reported_value": 0.5,
                "source": "paper_dossier",
                "evidence": [],
                "ambiguous": True,
            },
        ],
    }
    experiment = {"experiment_id": "exp-123", "status": "succeeded"}
    result = build_comparison_request(json.dumps(dossier), json.dumps(experiment))
    request = json.loads(result["comparison_request_json"])
    assert request == {
        "experiment_id": "exp-123",
        "reported_metrics": [
            {"name": "roc_auc", "reported_value": 0.91, "dataset": "test", "split": "test"}
        ],
    }
```

- [ ] **Step 2: Add failing tests for strict status and approximate grades**

```python
def test_score_similarity_keeps_strict_and_approximate_status_separate() -> None:
    comparison = {
        "experiment_id": "exp-123",
        "items": [
            {
                "name": "roc_auc",
                "paper_value": 0.90,
                "independent_value": 0.87,
                "absolute_difference": 0.03,
                "relative_difference": -0.033333,
                "comparable": False,
                "reason": "paper metric is missing dataset identity",
            },
            {
                "name": "accuracy",
                "paper_value": 0.90,
                "independent_value": 0.81,
                "absolute_difference": 0.09,
                "relative_difference": -0.10,
                "comparable": False,
                "reason": "paper metric is missing dataset identity",
            },
        ],
    }
    result = score_approximate_similarity(json.dumps(comparison), 0.05, 0.10)
    assessment = json.loads(result["assessment_json"])
    assert assessment["strict_status"] == "not_comparable"
    assert assessment["approximate_status"] == "partially_similar"
    assert [item["grade"] for item in assessment["items"]] == [
        "highly_similar",
        "partially_similar",
    ]


def test_score_similarity_uses_absolute_difference_for_zero_paper_value() -> None:
    comparison = {
        "experiment_id": "exp-123",
        "items": [
            {
                "name": "f1",
                "paper_value": 0.0,
                "independent_value": 0.2,
                "absolute_difference": 0.2,
                "relative_difference": None,
                "comparable": False,
                "reason": "paper metric is missing dataset identity",
            }
        ],
    }
    assessment = json.loads(
        score_approximate_similarity(json.dumps(comparison), 0.05, 0.10)["assessment_json"]
    )
    assert assessment["items"][0]["grade"] == "materially_different"
    assert assessment["approximate_status"] == "materially_different"
```

- [ ] **Step 3: Add failing tests for response parsing, reports, and safe HTTP failures**

```python
def test_report_never_calls_approximate_similarity_a_successful_reproduction() -> None:
    result = format_comparison_report(
        dossier_json=json.dumps({"title": "Minimal Paper", "metrics": []}),
        validation_json=json.dumps({"valid": True, "dataset": {"rows": 40}}),
        experiment_json=json.dumps({"experiment_id": "exp-123", "status": "succeeded"}),
        comparison_json=json.dumps({"experiment_id": "exp-123", "items": []}),
        assessment_json=json.dumps(
            {"strict_status": "not_comparable", "approximate_status": "highly_similar", "items": []}
        ),
    )
    assert "高度接近" in result["markdown_report"]
    assert "复现成功" not in result["markdown_report"]


def test_comparison_http_failure_preserves_completed_experiment() -> None:
    result = normalize_comparison_http_failure(
        dossier_json=json.dumps({"valid": True}),
        validation_json=json.dumps({"valid": True}),
        experiment_json=json.dumps({"experiment_id": "exp-123", "status": "succeeded"}),
    )
    assert json.loads(result["experiment_json"])["experiment_id"] == "exp-123"
    assert json.loads(result["comparison_json"])["errors"][0]["code"] == "comparison_service_unavailable"
```

- [ ] **Step 4: Run focused tests and confirm import failures**

Run: `python -m pytest tests/test_dify_code.py -q`

Expected: FAIL because `dify.code.comparison_workflow` does not exist.

- [ ] **Step 5: Implement the pure Dify helper module**

The module must use only the Python standard library and expose plain `main`-compatible functions. Use this stable output shape for scoring:

```python
{
    "strict_status": "strictly_comparable | partially_comparable | not_comparable",
    "approximate_status": "highly_similar | partially_similar | materially_different | insufficient_metrics",
    "close_threshold": 0.05,
    "partial_threshold": 0.10,
    "items": [
        {
            "name": "roc_auc",
            "paper_value": 0.91,
            "independent_value": 0.87,
            "difference_for_grade": 0.043956,
            "grade": "highly_similar",
            "comparable": False,
            "reason": "paper metric is missing dataset identity"
        }
    ]
}
```

`build_comparison_request()` must allow only `name`, `reported_value`, `dataset`, `split`, `dataset_id`, `test_size`, `random_state`, `train_rows`, `test_rows`, and `test_digest`. It must use `normalized_name` as the outgoing `name` and omit `None` values. It must reject a missing experiment ID with `comparison_request_ok=False`.

`score_approximate_similarity()` must use `abs(relative_difference)` when present, otherwise `absolute_difference` only when `paper_value == 0`. It must count comparable and incomparable usable items to derive the strict status and use the conservative overall-grade rules from the design.

`format_comparison_report()` must return all six workflow output strings, copying input JSON strings unchanged and adding `markdown_report`. It must label source/evidence from the dossier and include the sentence `近似指标一致不等于严格复现。`.

Use these concrete cores for request construction and grading:

```python
import json


_COMPARE_FIELDS = (
    "reported_value",
    "dataset",
    "split",
    "dataset_id",
    "test_size",
    "random_state",
    "train_rows",
    "test_rows",
    "test_digest",
)


def _object(value, fallback):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, dict) else fallback


def validate_thresholds(close_threshold, partial_threshold):
    try:
        close_value = float(close_threshold)
        partial_value = float(partial_threshold)
    except (TypeError, ValueError):
        close_value = partial_value = -1.0
    ok = 0 < close_value < partial_value <= 1
    errors = [] if ok else [
        {
            "code": "invalid_similarity_thresholds",
            "message": "Thresholds must satisfy 0 < close < partial <= 1.",
        }
    ]
    return {
        "thresholds_ok": ok,
        "close_threshold": close_value,
        "partial_threshold": partial_value,
        "threshold_errors": json.dumps(errors, ensure_ascii=False),
    }


def build_comparison_request(dossier_json, experiment_json):
    dossier = _object(dossier_json, {})
    experiment = _object(experiment_json, {})
    experiment_id = experiment.get("experiment_id")
    reported = []
    for metric in dossier.get("metrics", []):
        if not isinstance(metric, dict) or metric.get("ambiguous") is True:
            continue
        item = {"name": metric.get("normalized_name") or metric.get("name")}
        for field in _COMPARE_FIELDS:
            if metric.get(field) is not None:
                item[field] = metric[field]
        reported.append(item)
    ok = isinstance(experiment_id, str) and bool(experiment_id) and bool(reported)
    request = {"experiment_id": experiment_id, "reported_metrics": reported} if ok else {}
    return {
        "comparison_request_ok": ok,
        "comparison_request_json": json.dumps(request, ensure_ascii=False),
        "comparison_request_errors": json.dumps(
            [] if ok else [{"code": "invalid_comparison_request", "message": "Experiment ID and unambiguous metrics are required."}],
            ensure_ascii=False,
        ),
    }


def score_approximate_similarity(comparison_json, close_threshold, partial_threshold):
    comparison = _object(comparison_json, {"items": []})
    close_value = float(close_threshold)
    partial_value = float(partial_threshold)
    graded = []
    for item in comparison.get("items", []):
        if not isinstance(item, dict):
            continue
        paper_value = item.get("paper_value")
        independent_value = item.get("independent_value")
        if paper_value is None or independent_value is None:
            continue
        difference = item.get("absolute_difference") if paper_value == 0 else item.get("relative_difference")
        if difference is None:
            continue
        difference = round(abs(float(difference)), 6)
        grade = (
            "highly_similar"
            if difference <= close_value
            else "partially_similar"
            if difference <= partial_value
            else "materially_different"
        )
        graded.append(
            {
                "name": item.get("name"),
                "paper_value": paper_value,
                "independent_value": independent_value,
                "difference_for_grade": difference,
                "grade": grade,
                "comparable": item.get("comparable") is True,
                "reason": item.get("reason"),
            }
        )
    comparable_count = sum(item["comparable"] for item in graded)
    strict_status = (
        "not_comparable"
        if comparable_count == 0
        else "strictly_comparable"
        if comparable_count == len(graded)
        else "partially_comparable"
    )
    grades = [item["grade"] for item in graded]
    approximate_status = (
        "insufficient_metrics"
        if not grades
        else "materially_different"
        if "materially_different" in grades
        else "highly_similar"
        if all(grade == "highly_similar" for grade in grades)
        else "partially_similar"
    )
    assessment = {
        "strict_status": strict_status,
        "approximate_status": approximate_status,
        "close_threshold": close_value,
        "partial_threshold": partial_value,
        "items": graded,
    }
    return {"assessment_json": json.dumps(assessment, ensure_ascii=False)}
```

Implement `parse_dossier_response()` and `parse_comparison_response()` with the same guarded `_object()` pattern and explicit Boolean success outputs. Implement the two HTTP normalizers as static structured errors; `normalize_comparison_http_failure()` must copy the three prior JSON strings after normalizing them to objects, and must add `comparison_service_unavailable` without echoing malformed input.

- [ ] **Step 6: Run Dify helper tests**

Run: `python -m pytest tests/test_dify_code.py -q`

Expected: PASS.

- [ ] **Step 7: Commit deterministic comparison helpers**

```powershell
git add dify/code/comparison_workflow.py tests/test_dify_code.py
git commit -m "feat: add dify comparison workflow helpers"
```

### Task 5: Document and smoke-test the V3 workflow contract

**Files:**
- Create: `dify/paper-comparison-workflow.md`
- Create: `dify/paper-comparison-workflow.yml`
- Create: `tests/fixtures/minimal-paper-dossier.json`
- Create: `scripts/smoke_comparison.ps1`
- Modify: `docs/configuration-guide.md`

**Interfaces:**
- Consumes: `/v1/parse-dossier`, `/v1/validate-dataset`, `/v1/run-experiment`, `/v1/compare-result`, and Task 4 code helpers.
- Produces: a deployable Dify DSL, its operator guide, and a host-side smoke command.

- [ ] **Step 1: Add a stable dossier fixture**

Create `tests/fixtures/minimal-paper-dossier.json` with one AUC metric, paper evidence on page 2, and no fabricated strict provenance:

```json
{
  "title": "Minimal Classification Paper",
  "research_problem": "Binary classification benchmark.",
  "task_type": "classification",
  "datasets": [],
  "methods": [],
  "metrics": [
    {
      "name": "AUC",
      "reported_value": "91%",
      "dataset": "test",
      "split": "test",
      "evidence": [
        {
          "page": 2,
          "source_text": "The held-out test AUC was 91%.",
          "source": "paper",
          "confidence": 1.0
        }
      ]
    }
  ],
  "gaps": ["The paper does not provide a dataset digest or random seed."]
}
```

- [ ] **Step 2: Write the PowerShell smoke script**

`scripts/smoke_comparison.ps1` must:

1. POST the fixture to `http://localhost:8001/v1/parse-dossier` when run on the host container mapping, or execute the equivalent request inside `repro-runner` when port 8001 is not published;
2. run an experiment with the supplied `-CsvPath`;
3. strip display-only dossier fields;
4. POST `experiment_id` plus reported metrics to `/v1/compare-result`;
5. assert that the experiment ID matches, at least one item exists, and missing provenance produces `comparable=false`.

Use this invocation contract:

```powershell
param(
    [Parameter(Mandatory = $true)]
    [string]$CsvPath,
    [string]$DossierPath = "$PSScriptRoot\..\tests\fixtures\minimal-paper-dossier.json",
    [string]$TargetColumn = "Y_cls"
)
```

Print only normalized summaries; never print CSV rows.

- [ ] **Step 3: Write the exact Dify setup document**

`dify/paper-comparison-workflow.md` must include:

- the nine Start variables and exact defaults, including `paper_dossier_json`
  as a custom `.JSON` File and `metric_overrides_json` as a Paragraph defaulting
  to `[]`;
- multipart fields for `parse_dossier`, `validate_dataset`, and `run_experiment`;
- bounded retry and finite timeout settings for all four HTTP nodes, with
  `run_experiment.idempotency_key` bound to `sys.workflow_run_id`;
- raw JSON body binding for `compare_result` from `build_comparison_request.comparison_request_json`;
- each Task 4 function copied into a separate Dify code node with exact input/output types;
- IF/ELSE conditions for dossier, thresholds, validation, experiment, request, and comparison success;
- static normalizers for all HTTP failure branches;
- six output variables on every terminal branch;
- the prohibition on LLM-based scoring and the wording restrictions.

`dify/paper-comparison-workflow.yml` is the canonical deployable contract and
must include the complete graph, code bodies, failure branches, six Variable
Aggregators, and one Output without secrets. Regression tests parse this DSL,
compile its Python code, and execute its embedded formatter rather than relying
on prose assertions.

- [ ] **Step 4: Extend the configuration guide**

Add exact commands for rebuilding only `repro-runner`, checking `/healthz`, running `smoke_comparison.ps1`, and inspecting the last 200 service-log lines. State that V2 remains published and unchanged.

- [ ] **Step 5: Verify docs and script syntax**

Run:

```powershell
$errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path .\scripts\smoke_comparison.ps1),
    [ref]$null,
    [ref]$errors
)
if ($errors.Count) { $errors | Format-List; exit 1 }
git diff --check
```

Expected: no PowerShell parse errors and no whitespace errors.

- [ ] **Step 6: Commit the workflow contract**

```powershell
git add dify/paper-comparison-workflow.md dify/paper-comparison-workflow.yml tests/fixtures/minimal-paper-dossier.json scripts/smoke_comparison.ps1 docs/configuration-guide.md
git commit -m "docs: define dify paper comparison workflow"
```

### Task 6: Rebuild, configure Dify, and verify all paths

**Files:**
- Create: `.superpowers/sdd/paper-comparison-workflow-report.md`
- Modify only if verification exposes a defect: files owned by Tasks 1–5, with a new failing regression test before each fix.

**Interfaces:**
- Consumes: all previous tasks and the existing Dify/Docker installation.
- Produces: a published V3 Dify workflow and reproducible verification report.

- [ ] **Step 1: Run the complete automated suite before deployment**

Run: `python -m pytest -q`

Expected: all tests pass; record the exact passed/skipped counts in the report.

- [ ] **Step 2: Rebuild and health-check only repro-runner**

Run:

```powershell
docker compose -f .\compose.yaml up -d --build repro-runner
docker inspect repro-runner --format '{{.State.Health.Status}}'
```

Expected: `healthy`. Do not restart PostgreSQL, Redis, or other stateful Dify services.

- [ ] **Step 3: Run the host/container smoke test with the user CSV**

Run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke_comparison.ps1 `
  -CsvPath 'E:\论文复现\成果\2training_samples_15180.csv'
```

Expected: dossier valid, experiment succeeded, comparison contains at least one item, AUC paper value is `0.91`, and strict comparison is false because the fixture intentionally lacks the dataset fingerprint.

- [ ] **Step 4: Create the V3 Dify workflow without modifying V2**

In Dify, create `论文对标复现 V3` and configure every node from `dify/paper-comparison-workflow.md`. Use `http://repro-runner:8001` inside the Dify Docker network. Add the six output strings to every success and failure Output node.

- [ ] **Step 5: Verify the successful UI path**

Upload `tests/fixtures/minimal-paper-dossier.json` and `E:\论文复现\成果\2training_samples_15180.csv`, keep defaults, and run once.

Expected:

- dossier `valid=true`;
- dataset rows `15180`, features `16`, missing values `0`, duplicate rows `66`;
- experiment status `succeeded` and non-empty ID;
- strict status `not_comparable`;
- approximate AUC grade derived from the actual independent result;
- report includes evidence page 2 and `近似指标一致不等于严格复现。`.

- [ ] **Step 6: Verify manual override and threshold boundaries**

Run with an override for `roc_auc`, then run focused code-node tests at exactly `0.05` and `0.10` difference ratios.

Expected: the override source is `manual_override`; exactly 5% is highly similar; exactly 10% is partially similar; neither run says “复现成功”.

- [ ] **Step 7: Verify rejection and service-failure branches**

Exercise:

- a damaged dossier JSON;
- a dossier with no metrics;
- thresholds `0.10` and `0.05` in reverse order;
- missing target column;
- dossier HTTP failure normalizer;
- experiment HTTP failure normalizer;
- comparison HTTP failure normalizer.

Expected: each run reaches a terminal Output node with six strings; comparison failure preserves the completed experiment ID; no branch references a node that did not run.

- [ ] **Step 8: Publish V3 and record evidence**

Publish only after the successful and failure paths pass. Record the Dify app ID, public URL, publish time, input files, experiment ID, exact metrics, strict status, approximate status, and tested error codes in `.superpowers/sdd/paper-comparison-workflow-report.md`.

- [ ] **Step 9: Re-run the full suite after any verification fixes**

Run: `python -m pytest -q`

Expected: all tests pass with the same or higher test count than Step 1.

- [ ] **Step 10: Commit the verification report**

```powershell
git add -f .superpowers/sdd/paper-comparison-workflow-report.md
git commit -m "docs: record v3 comparison verification"
```

## Final Review Checklist

- [ ] `git diff --check` is clean.
- [ ] `python -m pytest -q` passes from `paper-repro-agent`.
- [ ] `repro-runner` is healthy after rebuild.
- [ ] V2 remains published and behaves as before.
- [ ] V3 success, semantic rejection, and all three HTTP-failure paths terminate safely.
- [ ] The report distinguishes paper values, independent values, strict comparability, and approximate similarity.
- [ ] No output or log includes raw CSV rows.
- [ ] No approximate result is labeled “复现成功”.

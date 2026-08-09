# Dify Experiment Workflow Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Dify `drop_duplicates` input control the experiment request and convert validation/experiment HTTP failures into structured workflow outputs.

**Architecture:** Keep the `repro-runner` multipart API unchanged. Mirror every new Dify Code-node function in a small local Python module for automated tests, then apply the tested code to the live Dify workflow and connect HTTP failure branches to the existing stage-specific failure outputs.

**Tech Stack:** Python 3.12, pytest, Dify 1.16 Workflow UI, multipart HTTP requests, local Docker services.

## Global Constraints

- Do not send CSV rows or file contents to an LLM, prompt, knowledge base, template, or workflow output.
- `drop_duplicates_text` must be exactly `"true"` or `"false"`.
- HTTP error payloads expose only `code`, `message`, and `stage`; do not expose stack traces, database details, secrets, or full response headers.
- Keep the current published version active until all draft tests pass.
- Preserve the current success output names: `validation_json`, `experiment_json`, and `markdown_summary`.
- Preserve globally unique failure output names required by Dify 1.16.

---

### Task 1: Add tested Dify resilience helpers

**Files:**
- Create: `paper-repro-agent/dify/code/experiment_workflow.py`
- Modify: `paper-repro-agent/tests/test_dify_code.py`

**Interfaces:**
- Consumes: Dify Boolean input and normalized validation JSON String.
- Produces: `normalize_experiment_inputs(drop_duplicates) -> dict`, `normalize_validation_http_failure() -> dict`, and `normalize_experiment_http_failure(validation_json) -> dict`.

- [ ] **Step 1: Write failing tests for Boolean conversion and safe HTTP failures**

Append to `paper-repro-agent/tests/test_dify_code.py`:

```python
from dify.code.experiment_workflow import (
    normalize_experiment_http_failure,
    normalize_experiment_inputs,
    normalize_validation_http_failure,
)


def test_normalize_experiment_inputs_converts_boolean_to_form_text() -> None:
    assert normalize_experiment_inputs(False) == {"drop_duplicates_text": "false"}
    assert normalize_experiment_inputs(True) == {"drop_duplicates_text": "true"}


def test_normalize_validation_http_failure_returns_safe_payload() -> None:
    result = normalize_validation_http_failure()
    validation = json.loads(result["validation_json"])

    assert validation == {
        "valid": False,
        "errors": [
            {
                "code": "validation_service_unavailable",
                "message": "数据验证服务请求失败，请稍后重试。",
                "stage": "validate_dataset",
            }
        ],
    }
    assert result["experiment_json"] == "{}"
    assert "暂时不可用" in result["markdown_summary"]


def test_normalize_experiment_http_failure_preserves_validation() -> None:
    validation_json = json.dumps({"valid": True, "dataset": {"rows": 10}})
    result = normalize_experiment_http_failure(validation_json)
    experiment = json.loads(result["experiment_json"])

    assert json.loads(result["validation_json"])["dataset"]["rows"] == 10
    assert experiment == {
        "status": "failed",
        "errors": [
            {
                "code": "experiment_service_unavailable",
                "message": "实验服务请求失败，请稍后重试。",
                "stage": "run_experiment",
            }
        ],
    }
    assert "暂时不可用" in result["markdown_summary"]


def test_normalize_experiment_http_failure_does_not_echo_invalid_input() -> None:
    result = normalize_experiment_http_failure("not-json")

    assert json.loads(result["validation_json"]) == {"valid": True}
    assert "not-json" not in json.dumps(result, ensure_ascii=False)
```

- [ ] **Step 2: Run the focused tests and verify the module is missing**

Run:

```powershell
pytest paper-repro-agent/tests/test_dify_code.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'dify.code.experiment_workflow'`.

- [ ] **Step 3: Implement the three helpers**

Create `paper-repro-agent/dify/code/experiment_workflow.py`:

```python
import json


def normalize_experiment_inputs(drop_duplicates):
    return {"drop_duplicates_text": "true" if drop_duplicates is True else "false"}


def normalize_validation_http_failure():
    validation = {
        "valid": False,
        "errors": [
            {
                "code": "validation_service_unavailable",
                "message": "数据验证服务请求失败，请稍后重试。",
                "stage": "validate_dataset",
            }
        ],
    }
    return {
        "validation_json": json.dumps(validation, ensure_ascii=False),
        "experiment_json": "{}",
        "markdown_summary": "数据验证服务暂时不可用，请稍后重试。",
    }


def normalize_experiment_http_failure(validation_json):
    try:
        validation = (
            json.loads(validation_json)
            if isinstance(validation_json, str)
            else validation_json
        )
    except (TypeError, json.JSONDecodeError):
        validation = {"valid": True}
    if not isinstance(validation, dict):
        validation = {"valid": True}
    experiment = {
        "status": "failed",
        "errors": [
            {
                "code": "experiment_service_unavailable",
                "message": "实验服务请求失败，请稍后重试。",
                "stage": "run_experiment",
            }
        ],
    }
    return {
        "validation_json": json.dumps(validation, ensure_ascii=False),
        "experiment_json": json.dumps(experiment, ensure_ascii=False),
        "markdown_summary": "实验服务暂时不可用，请稍后重试。",
    }
```

- [ ] **Step 4: Run focused and full V2 tests**

Run:

```powershell
pytest paper-repro-agent/tests/test_dify_code.py -q
pytest paper-repro-agent/tests -q
```

Expected: all tests pass; any intentionally skipped integration test remains skipped.

- [ ] **Step 5: Commit the tested helpers**

```powershell
git add -- paper-repro-agent/dify/code/experiment_workflow.py paper-repro-agent/tests/test_dify_code.py
git commit -m "feat: add dify experiment resilience helpers"
```

### Task 2: Update the workflow runbook to match Dify 1.16

**Files:**
- Modify: `paper-repro-agent/dify/repro-experiment-workflow.md`

**Interfaces:**
- Consumes: tested helper functions from Task 1.
- Produces: exact node names, code, wiring, output fields, and test values used in Task 3.

- [ ] **Step 1: Add the input-normalization node instructions**

Insert before `run_experiment`:

```markdown
Add a Code node named `normalize_experiment_inputs` on the validation-success path.

Input: `drop_duplicates = {{#start.drop_duplicates#}}`
Output: `drop_duplicates_text` (String)

```python
def main(drop_duplicates):
    return {"drop_duplicates_text": "true" if drop_duplicates is True else "false"}
```

Set `run_experiment` form-data `drop_duplicates` to
`{{#normalize_experiment_inputs.drop_duplicates_text#}}` as Text.
```

- [ ] **Step 2: Replace the obsolete End-node contract**

Document these exact public names:

```text
Success: validation_json, experiment_json, markdown_summary
Validation failure: validation_failure_json, validation_failure_experiment_json, validation_failure_summary
Experiment failure: experiment_failure_validation_json, experiment_failure_json, experiment_failure_summary
```

Document that validation HTTP failures reuse `validation_failure_output` and experiment HTTP failures reuse `experiment_failure_output` through their stage-specific normalizer nodes.

- [ ] **Step 3: Add the exact Chinese HTTP failure code from Task 1**

Copy the bodies of `normalize_validation_http_failure` and `normalize_experiment_http_failure` into the runbook as Dify `main` functions with these signatures:

```python
def main():
    return normalize_validation_http_failure()


def main(validation_json):
    return normalize_experiment_http_failure(validation_json)
```

In the pasted Dify versions, inline the helper bodies because Dify Code nodes cannot import the local module.

- [ ] **Step 4: Check documentation consistency**

Run:

```powershell
rg -n "drop_duplicates|normalize_.*http_failure|validation_failure_|experiment_failure_" paper-repro-agent/dify/repro-experiment-workflow.md
git diff --check
```

Expected: all three node names and all six failure output names appear; `git diff --check` returns no errors.

- [ ] **Step 5: Commit the runbook update**

```powershell
git add -- paper-repro-agent/dify/repro-experiment-workflow.md
git commit -m "docs: align dify resilience workflow"
```

### Task 3: Apply the tested design to the live Dify workflow

**Files:**
- Modify through UI: Dify app `27f04e8d-3ba5-4b94-825b-021e3aa3017c`

**Interfaces:**
- Consumes: code and node names from Tasks 1-2.
- Produces: a saved Dify draft with dynamic deduplication and structured HTTP failure paths.

- [ ] **Step 1: Add `normalize_experiment_inputs`**

On the first condition's true path, insert a Code node before `run_experiment` with input `drop_duplicates` bound to `用户输入.drop_duplicates`, output `drop_duplicates_text` as String, and this code:

```python
def main(drop_duplicates):
    return {"drop_duplicates_text": "true" if drop_duplicates is True else "false"}
```

- [ ] **Step 2: Rebind the experiment request**

In `run_experiment`, keep multipart field type Text and replace literal `false` with `normalize_experiment_inputs.drop_duplicates_text`. Keep `model=random_forest` and every other field unchanged.

- [ ] **Step 3: Configure validation HTTP failure handling**

Set `validate_dataset` exception handling to `失败分支`. Connect the failure handle to a Code node named `normalize_validation_http_failure`, with no inputs and outputs `validation_json`, `experiment_json`, `markdown_summary`, using the Task 1 validation failure body. Connect that node to the existing `validation_failure_output`, mapping its three internal values to the existing three globally unique public names.

- [ ] **Step 4: Configure experiment HTTP failure handling**

Set `run_experiment` exception handling to `失败分支`. Connect the failure handle to a Code node named `normalize_experiment_http_failure`. Bind input `validation_json` to `parse_validation_response.validation_json`; add outputs `validation_json`, `experiment_json`, `markdown_summary`; paste the Task 1 experiment failure body. Connect it to the existing `experiment_failure_output`.

- [ ] **Step 5: Run Dify's check list**

Expected: `所有问题均已解决`. If Dify rejects two incoming edges to an existing Output node, insert a Variable Aggregator immediately before that Output, aggregate corresponding String values from the logical-failure and HTTP-failure normalizers, and bind the Output to the aggregator's selected values.

### Task 4: Verify every behavior and publish

**Files:**
- Read only: `E:\论文复现\成果\2training_samples_15180.csv`
- Modify through UI: Dify draft and published version.

**Interfaces:**
- Consumes: completed live workflow from Task 3.
- Produces: tested, published Dify workflow and recorded acceptance evidence.

- [ ] **Step 1: Test with deduplication disabled**

Use:

```text
training_csv = E:\论文复现\成果\2training_samples_15180.csv
target_column = Y_cls
test_size = 0.2
random_state = 42
drop_duplicates = false
```

Expected: `status=succeeded`, `reproducibility_status=baseline_only`, ROC AUC `0.871388`, Accuracy `0.921607`.

- [ ] **Step 2: Test with deduplication enabled**

Use the same values with `drop_duplicates=true`.

Expected: `status=succeeded`; the result's dataset or run metadata shows duplicate removal and does not silently behave as the fixed-false baseline.

- [ ] **Step 3: Test validation rejection**

Use the same CSV with `target_column=missing_target_column`.

Expected: the workflow finishes through `validation_failure_output`; `validation_failure_json` contains `valid=false` and a target-column error; no experiment node runs.

- [ ] **Step 4: Verify HTTP failure nodes without exposing secrets**

Run each normalizer Code node independently with its configured inputs. Expected validation code is `validation_service_unavailable`; expected experiment code is `experiment_service_unavailable`; both include the correct `stage` and no stack trace or response headers.

- [ ] **Step 5: Re-run the check list and publish**

Expected: all problems resolved. Click `发布` then `发布更新`, and verify the page reports `已发布 几秒前`.

- [ ] **Step 6: Verify the published run page**

Open `http://localhost/workflow/8fF5OEnIVVCskV2s` and confirm all five input fields remain visible: `training_csv`, `target_column`, `test_size`, `random_state`, and `drop_duplicates`.

- [ ] **Step 7: Record final verification**

Append the final run IDs, false/true deduplication evidence, validation-rejection evidence, check-list status, and published time to `.superpowers/sdd/dify-experiment-resilience-report.md`.

- [ ] **Step 8: Commit the verification record**

```powershell
git add -- .superpowers/sdd/dify-experiment-resilience-report.md
git commit -m "test: record dify resilience verification"
```

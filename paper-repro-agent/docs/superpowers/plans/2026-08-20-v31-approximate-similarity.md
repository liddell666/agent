# Paper Comparison V3.1 Approximate Similarity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the published Dify Paper comparison V3.1 workflow report a useful numeric similarity grade even when strict paper-reproduction comparability is unavailable, without weakening provenance requirements.

**Architecture:** Insert a deterministic `score_approximate_similarity` code node between parsed comparison data and markdown formatting. The scorer consumes only normalized metric values and configurable thresholds, while the formatter displays strict comparability and approximate numeric similarity as independent conclusions. Existing experiment execution, comparison parsing, success aggregation, and failure branches remain unchanged.

**Tech Stack:** Dify workflow DSL/UI, Python code nodes, pytest, browser-driven end-to-end verification.

## Global Constraints

- Strict comparability remains independent from approximate numeric similarity.
- Relative-difference grades are `highly_similar` for `difference <= 0.05`, `partially_similar` for `difference <= 0.10`, and `materially_different` otherwise.
- When the paper value is zero, grade with absolute difference instead of relative difference.
- Missing dataset or split provenance must not be converted into strict comparability.
- Invalid thresholds must return `approximate_status=insufficient_metrics` with a structured `invalid_similarity_thresholds` error.
- Add no model call and no external dependency to the scoring path.
- Preserve unrelated user changes in the dirty worktree.

---

### Task 1: Verify the tested local reference contract

**Files:**
- Read: `tests/test_dify_code.py:291`
- Read: `tests/test_dify_code.py:418`
- Read: `dify/code/comparison_workflow.py:610`
- Read: `dify/paper-comparison-workflow.yml:1512`

**Interfaces:**
- Consumes: `score_approximate_similarity(comparison_json: str, close_threshold: float, partial_threshold: float) -> dict[str, str]`.
- Produces: a green local reference for the exact scorer and report behavior to transplant into the live Dify workflow.

- [ ] **Step 1: Verify the exact current-case fixture is already covered**

Confirm `test_report_contains_complete_comparison_and_dataset_details_with_real_newlines` contains this exact comparison fixture:

```python
{
    "paper_value": 0.91,
    "independent_value": 0.871388,
    "absolute_difference": 0.038612,
    "relative_difference": -0.042431,
    "comparable": False,
    "reason": "paper metric is missing dataset identity",
}
```

- [ ] **Step 2: Run the focused local reference tests**

Run:

```powershell
pytest tests/test_dify_code.py -k "score_similarity or report_contains_complete_comparison" -q
```

Expected: all selected tests PASS, proving the local scorer keeps strict and approximate conclusions independent, includes threshold boundaries, uses absolute difference when the paper value is zero, and renders the exact AUC fixture.

- [ ] **Step 3: Verify the live published result is RED**

Open the installed app's latest successful result and evaluate the equivalent of:

```javascript
if (resultText.includes("approximate: highly_similar")) {
  throw new Error("expected the pre-change workflow to lack the new approximate grade");
}
if (!resultText.includes("approximate: not_available")) {
  throw new Error("unexpected pre-change approximate status");
}
```

Expected: the assertion passes because the current published workflow still reports `approximate: not_available`. This is the required RED observation for the live behavior change.

---

### Task 2: Capture the live workflow safely before mutation

**Files:**
- Modify externally: published Dify app `b9a766a0-0ad0-415b-8d42-60459c92bec7`
- Create: `.live-artifacts/v31-format-comparison-markdown-before.py`

**Interfaces:**
- Consumes: the complete current `FORMAT_COMPARISON_MARKDOWN` code and its input/output declarations.
- Produces: a credential-free rollback copy and a recorded graph baseline.

- [ ] **Step 1: Inspect the current graph and node declarations**

Record the node titles and edges around the comparison success branch. The expected baseline is:

```text
PARSE_COMPARISON_RESPONSE -> COMPARISON_OK -> FORMAT_COMPARISON_MARKDOWN -> FORMAT_SUCCESS_OUTPUT
```

- [ ] **Step 2: Copy and verify the complete live formatter code**

Select all code in `FORMAT_COMPARISON_MARKDOWN`, copy it, and verify:

```javascript
if (!formatterCode.startsWith("import ") || !formatterCode.includes("def main(")) {
  throw new Error("formatter backup is incomplete");
}
```

- [ ] **Step 3: Save the rollback artifact**

Use `apply_patch` to write the copied code verbatim to `.live-artifacts/v31-format-comparison-markdown-before.py`. Do not store session tokens, cookies, request headers, or file contents from the paper.

- [ ] **Step 4: Verify the backup on disk**

```powershell
Get-FileHash .live-artifacts/v31-format-comparison-markdown-before.py -Algorithm SHA256
Get-Content .live-artifacts/v31-format-comparison-markdown-before.py -TotalCount 5
```

Expected: a SHA-256 digest and the formatter's import/function header are displayed.

---

### Task 3: Wire deterministic scoring into the published Dify workflow

**Files:**
- Modify externally: published Dify app `b9a766a0-0ad0-415b-8d42-60459c92bec7`
- Reference: `dify/paper-comparison-workflow.yml:1512`
- Reference: `dify/code/comparison_workflow.py:610`

**Interfaces:**
- Consumes: `comparison_json` from `parse_comparison_response`, `close_threshold` and `partial_threshold` from Start.
- Produces: `assessment_json` for `format_comparison_markdown`.

- [ ] **Step 1: Verify the live workflow is RED before editing**

Open the installed app and inspect the most recent successful result. Assert that it does not contain `approximate: highly_similar`; the expected pre-change text is `approximate: not_available`.

- [ ] **Step 2: Back up the live formatter code in session memory**

Open `FORMAT_COMPARISON_MARKDOWN`, copy the complete code value, and retain it for rollback. Verify the copied text begins with its imports and ends with the node's return statement.

- [ ] **Step 3: Add the scoring node**

Create a Python code node titled `score_approximate_similarity` on the success branch after `COMPARISON_OK`. Configure inputs:

```text
comparison_json <- PARSE_COMPARISON_RESPONSE.comparison_json
close_threshold <- Start.close_threshold
partial_threshold <- Start.partial_threshold
```

Configure one string output named `assessment_json`. Use the exact implementation from `dify/code/comparison_workflow.py::score_approximate_similarity`, including finite-number checks, threshold validation, zero-paper-value handling, strict status aggregation, and conservative worst-grade aggregation.

- [ ] **Step 4: Connect the scorer to the formatter**

Set `FORMAT_COMPARISON_MARKDOWN.assessment_json` to `score_approximate_similarity.assessment_json`. Keep its existing comparison, reproducibility, dossier, dataset-profile, and experiment-summary inputs unchanged.

- [ ] **Step 5: Update the live formatter**

Apply the same rendering contract from Task 2:

```text
strict: <strict_status>
approximate: <approximate_status>
... grade=<metric_grade>
Note: numeric similarity does not establish strict reproduction; dataset, split, and evaluation provenance are assessed separately.
```

Keep `FORMAT_SUCCESS_OUTPUT` and all aggregation nodes unchanged.

- [ ] **Step 6: Run node-level synthetic tests**

Test the scorer with paper `0.91`, independent `0.871388`, absolute difference `0.038612`, relative difference `-0.042431`, `comparable=false`, and the missing-dataset reason. Expected JSON:

```json
{"strict_status":"not_comparable","approximate_status":"highly_similar","items":[{"grade":"highly_similar","difference_for_grade":0.042431,"comparable":false}]}
```

Test again with `close_threshold=0.10` and `partial_threshold=0.05`. Expected `approximate_status=insufficient_metrics` and error code `invalid_similarity_thresholds`.

- [ ] **Step 7: Publish the workflow**

Use Dify's `发布` then `发布更新` action. Verify the editor shows a fresh published timestamp and no unsaved-change indicator.

---

### Task 4: Verify the full paper-reproduction path

**Files:**
- Read: `E:\论文复现\资料\基于机器学习的滑坡易发性区...与降雨诱发滑坡预报预警研究_孙德亮.pdf`
- Read: the CSV already attached in the installed Dify app
- Create: `.live-artifacts/v31-approximate-similarity-verification.txt`

**Interfaces:**
- Consumes: published workflow, existing PDF, existing CSV, manual override `[{"name":"AUC (RF)","reported_value":0.91,"dataset":"奉节县","split":"全体样本（作为测试样本）"}]`.
- Produces: a saved verification transcript and a user-ready completion result.

- [ ] **Step 1: Run the installed app end to end**

Submit the existing PDF, CSV, and manual override with `drop_duplicates=false`, `close_threshold=0.05`, and `partial_threshold=0.10`. Wait for terminal status `Workflow completed`.

- [ ] **Step 2: Assert the numerical and semantic result**

Verify the final result contains all of:

```text
paper_value=0.91
independent_value=0.871388
absolute_difference=0.038612
relative_difference=-0.042431
strict: not_comparable
approximate: highly_similar
grade=highly_similar
paper metric is missing dataset identity
numeric similarity does not establish strict reproduction
```

Also verify the existing duplicate-row warning remains visible and no `no_supported_metrics`, `invalid_dossier`, or unsafe-stop error appears.

- [ ] **Step 3: Save fresh verification evidence**

Write the run timestamp, workflow/app IDs, published status, terminal run status, exact AUC values, strict status, approximate status, grade, provenance reason, and duplicate-row warning to `.live-artifacts/v31-approximate-similarity-verification.txt`. Do not include credentials, cookies, or authorization headers.

- [ ] **Step 4: Run local regression tests**

Run:

```powershell
pytest tests/test_dify_code.py -q
```

Expected: all tests PASS.

- [ ] **Step 5: Review the final diff without disturbing user changes**

Run:

```powershell
git diff -- docs/superpowers/plans/2026-08-20-v31-approximate-similarity.md tests/test_dify_code.py dify/code/comparison_workflow.py
git status --short
```

Expected: only intentional additions are attributed to this task; all unrelated modified and untracked files remain present and untouched.

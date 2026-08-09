# 论文对标复现 V3: deterministic comparison workflow

Create a **new** Dify Workflow named `论文对标复现 V3`. Do not edit or
republish the existing V2 table-experiment workflow. This workflow contains no
LLM node, no DeepSeek node, no prompt, and no knowledge-retrieval node: metric
comparison and similarity grading are deterministic code only. Never attach,
log, prompt, or emit CSV rows.

The two conclusions are intentionally independent:

- `strict_status` states whether provenance makes a strict comparison possible.
- `approximate_status` grades the available metric distance only.

**Warning:** 近似指标一致不等于严格复现。 Do not describe a high or partial
similarity grade as a strict reproduction.

## Start node

Add these nine Start variables exactly. `File` variables are single-file
uploads and have no default value. Keep the string defaults as literal text and
the numeric defaults as numbers.

| Variable | Dify type | Required | Default |
| --- | --- | --- | --- |
| `paper_dossier` | File | yes | none; accept one UTF-8 `.json` dossier |
| `training_csv` | File | yes | none; accept one CSV |
| `metric_overrides_json` | String | no | `[]` |
| `target_column` | String | no | `Y_cls` |
| `test_size` | Number | no | `0.2` |
| `random_state` | Number | no | `42` |
| `drop_duplicates` | Boolean | no | `false` |
| `close_threshold` | Number | no | `0.05` |
| `partial_threshold` | Number | no | `0.10` |

Use `http://repro-runner:8001` for all HTTP nodes. It is the Docker-network
name, not the host-only address. Before configuring the workflow, ensure the
Dify SSRF allow-list includes `repro-runner`.

## Workflow graph

```text
Start
  -> parse_dossier HTTP -> parse_dossier_response -> dossier_ok?
       HTTP error -> dossier_http_failure -> terminal aggregation
       false      -> dossier_semantic_failure -> terminal aggregation
  -> validate_thresholds -> thresholds_ok?
       false -> thresholds_failure -> terminal aggregation
  -> validate_dataset HTTP -> parse_validation_response -> validation_ok?
       HTTP error -> validation_http_failure -> terminal aggregation
       false      -> validation_semantic_failure -> terminal aggregation
  -> normalize_experiment_inputs -> run_experiment HTTP -> parse_experiment_response -> experiment_ok?
       HTTP error -> experiment_http_failure -> terminal aggregation
       false      -> experiment_semantic_failure -> terminal aggregation
  -> build_comparison_request -> comparison_request_ok?
       false -> request_failure -> terminal aggregation
  -> compare_result HTTP -> parse_comparison_response -> comparison_ok?
       HTTP error -> comparison_http_failure -> terminal aggregation
       false      -> comparison_semantic_failure -> terminal aggregation
  -> score_approximate_similarity -> format_comparison_report -> terminal aggregation

terminal aggregation:
  aggregate_dossier_json -> aggregate_validation_json -> aggregate_experiment_json
  -> aggregate_comparison_json -> aggregate_assessment_json
  -> aggregate_markdown_report -> Output
```

Every arrow labelled `HTTP error` is the HTTP node's error-handling branch;
never wire it into a node that reads the skipped HTTP response. Every terminal
producer has the six strings described in [Terminal outputs](#terminal-outputs),
then the Variable Aggregator chain joins the mutually exclusive branches before
the workflow's single Output node.

## HTTP nodes

Configure the three upload requests as **POST / multipart/form-data**. Do not
send a file URL or a CSV preview. Set the Dify HTTP file field value directly
from the Start File variable.

| Node | URL | Multipart field bindings |
| --- | --- | --- |
| `parse_dossier` | `http://repro-runner:8001/v1/parse-dossier` | `file` = `{{#start.paper_dossier#}}`; `metric_overrides_json` = `{{#start.metric_overrides_json#}}` |
| `validate_dataset` | `http://repro-runner:8001/v1/validate-dataset` | `file` = `{{#start.training_csv#}}`; `target_column` = `{{#start.target_column#}}` |
| `run_experiment` | `http://repro-runner:8001/v1/run-experiment` | `file` = `{{#start.training_csv#}}`; `target_column` = `{{#start.target_column#}}`; `test_size` = `{{#start.test_size#}}`; `random_state` = `{{#start.random_state#}}`; `drop_duplicates` = `{{#normalize_experiment_inputs.drop_duplicates_text#}}`; `model` = literal `random_forest` |

Use **POST / application/json** for `compare_result`. Its body mode must be
**Raw JSON**, with this single variable binding (do not rebuild the object in
the HTTP node):

```text
{{#build_comparison_request.comparison_request_json#}}
```

The URL is `http://repro-runner:8001/v1/compare-result`. Set `Content-Type` to
`application/json`.

## Dify code nodes

For every code node, select Python 3. Copy the indicated `main` code into the
node, then declare precisely the listed inputs and outputs. All JSON values are
**String** values. No code node receives a CSV value or returns CSV data.

### `parse_dossier_response`

Inputs: `dossier_response_json` (String). Outputs: `dossier_ok` (Boolean),
`dossier_json` (String), `dossier_errors` (String).

```python
import json

def main(dossier_response_json: str) -> dict:
    try:
        dossier = json.loads(dossier_response_json)
    except (TypeError, json.JSONDecodeError):
        dossier = {}
    ok = isinstance(dossier, dict) and dossier.get("valid") is True and isinstance(dossier.get("metrics"), list)
    errors = dossier.get("errors") if isinstance(dossier, dict) else None
    if not isinstance(errors, list):
        errors = [{"code": "invalid_dossier_response", "message": "Dossier response is invalid."}]
    return {
        "dossier_ok": ok,
        "dossier_json": json.dumps(dossier, ensure_ascii=False, separators=(",", ":")) if ok else "{}",
        "dossier_errors": json.dumps([] if ok else errors, ensure_ascii=False, separators=(",", ":")),
    }
```

Bind `dossier_response_json` to the parse HTTP response body. The `dossier_ok?`
IF condition is `parse_dossier_response.dossier_ok is true`.

### `validate_thresholds`

Inputs: `close_threshold` (Number), `partial_threshold` (Number). Outputs:
`thresholds_ok` (Boolean), `close_threshold` (Number), `partial_threshold`
(Number), `threshold_errors` (String).

```python
import json
import math

def main(close_threshold: float, partial_threshold: float) -> dict:
    try:
        close_value, partial_value = float(close_threshold), float(partial_threshold)
    except (TypeError, ValueError):
        close_value = partial_value = -1.0
    ok = math.isfinite(close_value) and math.isfinite(partial_value) and 0 < close_value < partial_value <= 1
    errors = [] if ok else [{"code": "invalid_similarity_thresholds", "message": "Thresholds must satisfy 0 < close < partial <= 1."}]
    return {"thresholds_ok": ok, "close_threshold": close_value, "partial_threshold": partial_value, "threshold_errors": json.dumps(errors, ensure_ascii=False, separators=(",", ":"))}
```

Bind both inputs from Start. The `thresholds_ok?` IF condition is
`validate_thresholds.thresholds_ok is true`.

### `normalize_experiment_inputs`

Inputs: `drop_duplicates` (Boolean). Output: `drop_duplicates_text` (String).

```python
def main(drop_duplicates: bool) -> dict:
    return {"drop_duplicates_text": "true" if drop_duplicates is True else "false"}
```

Bind its input from Start before `run_experiment`.

### `parse_validation_response`

Input: `body` (String) = `{{#validate_dataset.body#}}`. Outputs:
`validation_ok` (Boolean), `validation_json` (String), `validation_errors`
(String).

```python
import json

def main(body: str) -> dict:
    try:
        response = json.loads(body) if isinstance(body, str) else body
    except (TypeError, json.JSONDecodeError):
        response = {"valid": False, "errors": [{"code": "invalid_validation_response", "message": "The validation service returned invalid JSON."}]}
    if not isinstance(response, dict):
        response = {"valid": False, "errors": [{"code": "invalid_validation_response", "message": "The validation service response must be an object."}]}
    errors = response.get("errors", [])
    if not isinstance(errors, list):
        errors = [{"code": "invalid_validation_response", "message": "The validation error list is invalid."}]
    normalized = {**response, "valid": response.get("valid") is True, "errors": errors}
    return {"validation_ok": normalized["valid"], "validation_json": json.dumps(normalized, ensure_ascii=False), "validation_errors": json.dumps(errors, ensure_ascii=False)}
```

### `parse_experiment_response`

Input: `body` (String) = `{{#run_experiment.body#}}`. Outputs: `experiment_ok`
(Boolean), `experiment_json` (String), `experiment_errors` (String).

```python
import json

def main(body: str) -> dict:
    try:
        experiment = json.loads(body) if isinstance(body, str) else body
    except (TypeError, json.JSONDecodeError):
        experiment = {"status": "rejected", "errors": [{"code": "invalid_experiment_response", "message": "Experiment response could not be read."}]}
    if not isinstance(experiment, dict):
        experiment = {"status": "rejected", "errors": [{"code": "invalid_experiment_response", "message": "Experiment response must be an object."}]}
    errors = experiment.get("errors", [])
    if not isinstance(errors, list):
        errors = [{"code": "invalid_experiment_response", "message": "Experiment error list is invalid."}]
    ok = experiment.get("status") == "succeeded" and bool(experiment.get("experiment_id"))
    if not ok and not errors:
        errors = [{"code": "experiment_rejected", "message": "The experiment did not return a successful result."}]
    normalized = {**experiment, "errors": errors}
    return {"experiment_ok": ok, "experiment_json": json.dumps(normalized, ensure_ascii=False), "experiment_errors": json.dumps(errors, ensure_ascii=False)}
```

### `build_comparison_request`

Inputs: `dossier_json` (String), `experiment_json` (String). Outputs:
`comparison_request_ok` (Boolean), `comparison_request_json` (String),
`comparison_request_errors` (String).

```python
import json

FIELDS = ("reported_value", "dataset", "split", "dataset_id", "test_size", "random_state", "train_rows", "test_rows", "test_digest")

def object_or_empty(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

def main(dossier_json: str, experiment_json: str) -> dict:
    dossier, experiment = object_or_empty(dossier_json), object_or_empty(experiment_json)
    reported = []
    for metric in dossier.get("metrics", []):
        if not isinstance(metric, dict) or metric.get("ambiguous") is True:
            continue
        name = metric.get("normalized_name") or metric.get("name")
        if not isinstance(name, str) or not name.strip() or metric.get("reported_value") is None:
            continue
        item = {"name": name}
        for field in FIELDS:
            if metric.get(field) is not None:
                item[field] = metric[field]
        reported.append(item)
    experiment_id = experiment.get("experiment_id")
    ok = isinstance(experiment_id, str) and bool(experiment_id.strip()) and bool(reported)
    request = {"experiment_id": experiment_id, "reported_metrics": reported} if ok else {}
    errors = [] if ok else [{"code": "invalid_comparison_request", "message": "Experiment ID and unambiguous metrics are required."}]
    return {"comparison_request_ok": ok, "comparison_request_json": json.dumps(request, ensure_ascii=False, separators=(",", ":")), "comparison_request_errors": json.dumps(errors, ensure_ascii=False, separators=(",", ":"))}
```

Bind `dossier_json` from `parse_dossier_response.dossier_json` and
`experiment_json` from `parse_experiment_response.experiment_json`. The
`comparison_request_ok?` IF condition is
`build_comparison_request.comparison_request_ok is true`.

### `parse_comparison_response`

Inputs: `comparison_response_json` (String). Outputs: `comparison_ok`
(Boolean), `comparison_json` (String), `comparison_errors` (String).

```python
import json

def main(comparison_response_json: str) -> dict:
    try:
        comparison = json.loads(comparison_response_json)
    except (TypeError, json.JSONDecodeError):
        comparison = {}
    experiment_id = comparison.get("experiment_id") if isinstance(comparison, dict) else None
    ok = isinstance(experiment_id, str) and bool(experiment_id.strip()) and isinstance(comparison.get("items"), list)
    errors = comparison.get("errors") if isinstance(comparison, dict) else None
    if not isinstance(errors, list):
        errors = [{"code": "invalid_comparison_response", "message": "Comparison response is invalid."}]
    return {"comparison_ok": ok, "comparison_json": json.dumps(comparison, ensure_ascii=False, separators=(",", ":")) if ok else "{}", "comparison_errors": json.dumps([] if ok else errors, ensure_ascii=False, separators=(",", ":"))}
```

Bind from the compare HTTP response body. The `comparison_ok?` IF condition is
`parse_comparison_response.comparison_ok is true`.

### `score_approximate_similarity`

Inputs: `comparison_json` (String), `close_threshold` (Number),
`partial_threshold` (Number). Output: `assessment_json` (String).

```python
import json
import math

def main(comparison_json: str, close_threshold: float, partial_threshold: float) -> dict:
    try:
        comparison = json.loads(comparison_json)
    except (TypeError, json.JSONDecodeError):
        comparison = {"items": []}
    try:
        close, partial = float(close_threshold), float(partial_threshold)
    except (TypeError, ValueError):
        close = partial = -1.0
    if not (math.isfinite(close) and math.isfinite(partial) and 0 < close < partial <= 1):
        return {"assessment_json": json.dumps({"strict_status": "not_comparable", "approximate_status": "insufficient_metrics", "close_threshold": close, "partial_threshold": partial, "items": [], "errors": [{"code": "invalid_similarity_thresholds", "message": "Thresholds must satisfy 0 < close < partial <= 1."}]}, ensure_ascii=False, separators=(",", ":"))}
    graded = []
    for item in comparison.get("items", []) if isinstance(comparison, dict) else []:
        if not isinstance(item, dict) or item.get("paper_value") is None or item.get("independent_value") is None:
            continue
        try:
            paper = float(item["paper_value"])
            independent = float(item["independent_value"])
            difference = abs(float(item.get("absolute_difference") if paper == 0 else item.get("relative_difference")))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (paper, independent, difference)):
            continue
        grade = "highly_similar" if difference <= close else "partially_similar" if difference <= partial else "materially_different"
        graded.append({"name": item.get("name"), "paper_value": paper, "independent_value": independent, "difference_for_grade": round(difference, 6), "grade": grade, "comparable": item.get("comparable") is True, "reason": item.get("reason")})
    comparable = sum(item["comparable"] for item in graded)
    strict = "not_comparable" if not graded or comparable == 0 else "strictly_comparable" if comparable == len(graded) else "partially_comparable"
    grades = [item["grade"] for item in graded]
    approximate = "insufficient_metrics" if not grades else "materially_different" if "materially_different" in grades else "highly_similar" if all(item == "highly_similar" for item in grades) else "partially_similar"
    return {"assessment_json": json.dumps({"strict_status": strict, "approximate_status": approximate, "close_threshold": close, "partial_threshold": partial, "items": graded}, ensure_ascii=False, separators=(",", ":"))}
```

Bind `comparison_json` from `parse_comparison_response.comparison_json`, and
both thresholds from the successful threshold node, not directly from Start.

### `format_comparison_report`

Inputs: `dossier_json`, `validation_json`, `experiment_json`, `comparison_json`,
`assessment_json` (all String). Outputs: `dossier_json`, `validation_json`,
`experiment_json`, `comparison_json`, `assessment_json`, `markdown_report`
(all String).

```python
import json

def safe(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    return (value if isinstance(value, str) and isinstance(parsed, dict) else json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))), parsed if isinstance(parsed, dict) else {}

def main(dossier_json: str, validation_json: str, experiment_json: str, comparison_json: str, assessment_json: str) -> dict:
    dossier_string, dossier = safe(dossier_json)
    validation_string, _ = safe(validation_json)
    experiment_string, experiment = safe(experiment_json)
    comparison_string, comparison = safe(comparison_json)
    assessment_string, assessment = safe(assessment_json)
    lines = ["# 论文指标对比报告", "", "论文: " + str(dossier.get("title") or "未命名论文"), "实验 ID: " + str(experiment.get("experiment_id") or comparison.get("experiment_id") or "未提供"), "严格可比性: " + str(assessment.get("strict_status") or "not_comparable"), "近似相似度: " + str(assessment.get("approximate_status") or "insufficient_metrics"), "", "## 论文来源与证据"]
    for metric in dossier.get("metrics", []) if isinstance(dossier.get("metrics"), list) else []:
        if isinstance(metric, dict):
            pages = sorted({item.get("page") for item in metric.get("evidence", []) if isinstance(item, dict) and isinstance(item.get("page"), int)})
            lines.append("- {0}: 来源 {1}; 证据 {2}".format(metric.get("name") or "未命名指标", metric.get("source") or "未标注来源", ", ".join("p." + str(page) for page in pages) or "无页码证据"))
    lines.extend(["", "近似指标一致不等于严格复现。", "严格可比性和近似相似度是独立结论。"])
    return {"dossier_json": dossier_string, "validation_json": validation_string, "experiment_json": experiment_string, "comparison_json": comparison_string, "assessment_json": assessment_string, "markdown_report": "\\n".join(lines)}
```

Bind its five inputs from the preceding successful nodes. This is the only
success terminal producer.

## Static HTTP-failure normalizers

Create four separate Python 3 code nodes: `dossier_http_failure`,
`validation_http_failure`, `experiment_http_failure`, and
`comparison_http_failure`. They are static terminal normalizers: they return
all six terminal strings and do not read any failed HTTP node output. For the
validation and experiment nodes, bind only JSON produced by earlier successful
nodes; for the dossier node bind no inputs. For comparison, bind the completed
dossier, validation, and experiment JSON strings.

Inputs and outputs are String except where a node has no inputs. All four
outputs are exactly `dossier_json`, `validation_json`, `experiment_json`,
`comparison_json`, `assessment_json`, `markdown_report` (String).

```python
# dossier_http_failure: no inputs
import json
def main() -> dict:
    return terminal("dossier_service_unavailable", "parse_dossier", "Dossier service request failed.", {}, {}, {})

# validation_http_failure: input dossier_json (String)
def main(dossier_json: str) -> dict:
    return terminal("validation_service_unavailable", "validate_dataset", "Dataset validation service request failed.", obj(dossier_json), {}, {})

# experiment_http_failure: inputs dossier_json, validation_json (String)
def main(dossier_json: str, validation_json: str) -> dict:
    return terminal("experiment_service_unavailable", "run_experiment", "Experiment service request failed.", obj(dossier_json), obj(validation_json), {})

# comparison_http_failure: inputs dossier_json, validation_json, experiment_json (String)
def main(dossier_json: str, validation_json: str, experiment_json: str) -> dict:
    return terminal("comparison_service_unavailable", "compare_result", "Comparison service request failed.", obj(dossier_json), obj(validation_json), obj(experiment_json))

# Paste these helpers above the selected main() in each of the four nodes.
def obj(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

def terminal(code, stage, message, dossier, validation, experiment):
    skipped = lambda which: {"errors": [{"code": which + "_not_run", "message": "This stage did not run.", "stage": stage}]}
    if not dossier:
        dossier = {"valid": False, "errors": [{"code": code, "message": message, "stage": stage}]}
    if not validation:
        validation = skipped("validation")
    if not experiment:
        experiment = {"status": "failed", "errors": [{"code": "experiment_not_run", "message": "This stage did not run.", "stage": stage}]}
    comparison = {"experiment_id": experiment.get("experiment_id"), "items": [], "errors": [{"code": code if stage == "compare_result" else "comparison_not_run", "message": message if stage == "compare_result" else "This stage did not run.", "stage": stage}]}
    assessment = {"strict_status": "not_comparable", "approximate_status": "insufficient_metrics", "close_threshold": 0.05, "partial_threshold": 0.10, "items": []}
    return {"dossier_json": json.dumps(dossier, ensure_ascii=False, separators=(",", ":")), "validation_json": json.dumps(validation, ensure_ascii=False, separators=(",", ":")), "experiment_json": json.dumps(experiment, ensure_ascii=False, separators=(",", ":")), "comparison_json": json.dumps(comparison, ensure_ascii=False, separators=(",", ":")), "assessment_json": json.dumps(assessment, ensure_ascii=False, separators=(",", ":")), "markdown_report": "服务请求失败；未将近似结论表述为严格复现。"}
```

In Dify, place the helper definitions first and then exactly one matching
`main` definition in each node. The `comparison_http_failure` node is the
Task 4 `normalize_comparison_http_failure` contract; it preserves the completed
experiment ID. Do not reuse a skipped response to make a failure message.

### Authoritative standalone failure-node code

The composite listing above explains the common payload shape. The following
four standalone snippets are the copyable node bodies. Use these node names in
the graph; each returns the same six String outputs and never reads an HTTP
response that failed.

`normalize_dossier_http_failure` inputs: none.

```python
import json
def main() -> dict:
    return {"dossier_json": json.dumps({"valid": False, "errors": [{"code": "dossier_service_unavailable", "message": "Dossier service request failed.", "stage": "parse_dossier"}]}, ensure_ascii=False), "validation_json": json.dumps({"valid": False, "errors": [{"code": "validation_not_run", "message": "Dataset validation did not run.", "stage": "validate_dataset"}]}, ensure_ascii=False), "experiment_json": json.dumps({"status": "failed", "errors": [{"code": "experiment_not_run", "message": "Experiment did not run.", "stage": "run_experiment"}]}, ensure_ascii=False), "comparison_json": json.dumps({"items": [], "errors": [{"code": "comparison_not_run", "message": "Comparison did not run.", "stage": "compare_result"}]}, ensure_ascii=False), "assessment_json": json.dumps({"strict_status": "not_comparable", "approximate_status": "insufficient_metrics", "close_threshold": 0.05, "partial_threshold": 0.10, "items": []}, ensure_ascii=False), "markdown_report": "Dossier service is unavailable; later stages did not run."}
```

`normalize_validation_http_failure` input: `dossier_json` (String).

```python
import json
def main(dossier_json: str) -> dict:
    try: dossier = json.loads(dossier_json)
    except (TypeError, json.JSONDecodeError): dossier = {}
    if not isinstance(dossier, dict): dossier = {}
    return {"dossier_json": json.dumps(dossier, ensure_ascii=False), "validation_json": json.dumps({"valid": False, "errors": [{"code": "validation_service_unavailable", "message": "Dataset validation service request failed.", "stage": "validate_dataset"}]}, ensure_ascii=False), "experiment_json": json.dumps({"status": "failed", "errors": [{"code": "experiment_not_run", "message": "Experiment did not run.", "stage": "run_experiment"}]}, ensure_ascii=False), "comparison_json": json.dumps({"items": [], "errors": [{"code": "comparison_not_run", "message": "Comparison did not run.", "stage": "compare_result"}]}, ensure_ascii=False), "assessment_json": json.dumps({"strict_status": "not_comparable", "approximate_status": "insufficient_metrics", "close_threshold": 0.05, "partial_threshold": 0.10, "items": []}, ensure_ascii=False), "markdown_report": "Dataset validation service is unavailable; later stages did not run."}
```

`normalize_experiment_http_failure` inputs: `dossier_json`, `validation_json`
(String).

```python
import json
def obj(value):
    try: value = json.loads(value)
    except (TypeError, json.JSONDecodeError): return {}
    return value if isinstance(value, dict) else {}
def main(dossier_json: str, validation_json: str) -> dict:
    return {"dossier_json": json.dumps(obj(dossier_json), ensure_ascii=False), "validation_json": json.dumps(obj(validation_json), ensure_ascii=False), "experiment_json": json.dumps({"status": "failed", "errors": [{"code": "experiment_service_unavailable", "message": "Experiment service request failed.", "stage": "run_experiment"}]}, ensure_ascii=False), "comparison_json": json.dumps({"items": [], "errors": [{"code": "comparison_not_run", "message": "Comparison did not run.", "stage": "compare_result"}]}, ensure_ascii=False), "assessment_json": json.dumps({"strict_status": "not_comparable", "approximate_status": "insufficient_metrics", "close_threshold": 0.05, "partial_threshold": 0.10, "items": []}, ensure_ascii=False), "markdown_report": "Experiment service is unavailable; comparison did not run."}
```

`normalize_comparison_http_failure` inputs: `dossier_json`, `validation_json`,
`experiment_json` (String). It retains a completed experiment ID.

```python
import json
def obj(value):
    try: value = json.loads(value)
    except (TypeError, json.JSONDecodeError): return {}
    return value if isinstance(value, dict) else {}
def main(dossier_json: str, validation_json: str, experiment_json: str) -> dict:
    dossier, validation, experiment = obj(dossier_json), obj(validation_json), obj(experiment_json)
    comparison = {"experiment_id": experiment.get("experiment_id") if isinstance(experiment.get("experiment_id"), str) else None, "items": [], "errors": [{"code": "comparison_service_unavailable", "message": "Comparison service request failed.", "stage": "compare_result"}]}
    return {"dossier_json": json.dumps(dossier, ensure_ascii=False), "validation_json": json.dumps(validation, ensure_ascii=False), "experiment_json": json.dumps(experiment, ensure_ascii=False), "comparison_json": json.dumps(comparison, ensure_ascii=False), "assessment_json": json.dumps({"strict_status": "not_comparable", "approximate_status": "insufficient_metrics", "close_threshold": 0.05, "partial_threshold": 0.10, "items": []}, ensure_ascii=False), "markdown_report": "Comparison service is unavailable; no approximate similarity conclusion was generated."}
```

## IF/ELSE and semantic-failure terminals

Add these six IF/ELSE conditions in this order:

1. `dossier_ok?`: `parse_dossier_response.dossier_ok is true`.
2. `thresholds_ok?`: `validate_thresholds.thresholds_ok is true`.
3. `validation_ok?`: `parse_validation_response.validation_ok is true`.
4. `experiment_ok?`: `parse_experiment_response.experiment_ok is true`.
5. `comparison_request_ok?`: `build_comparison_request.comparison_request_ok
   is true`.
6. `comparison_ok?`: `parse_comparison_response.comparison_ok is true`.

For each false branch, use the copyable normalizers below with the same six
String outputs. Its error must be a fixed object (`invalid_dossier`,
`invalid_similarity_thresholds`, `dataset_validation_failed`,
`experiment_failed`, `invalid_comparison_request`, or
`invalid_comparison_response`) and must use only inputs from nodes already run
on that branch. Do not expose a raw HTTP body or any CSV content.

For all six nodes below, declare outputs `dossier_json`, `validation_json`,
`experiment_json`, `comparison_json`, `assessment_json`, and `markdown_report`
as String. The snippets are standalone and their listed inputs are the exact
bindings; an empty object represents a stage that did not run.

`dossier_semantic_failure` inputs: `dossier_json` (String) =
`{{#parse_dossier_response.dossier_json#}}`, `dossier_errors` (String) =
`{{#parse_dossier_response.dossier_errors#}}`.

```python
import json
def main(dossier_json: str, dossier_errors: str) -> dict:
    try: dossier = json.loads(dossier_json)
    except (TypeError, json.JSONDecodeError): dossier = {}
    dossier = dossier if isinstance(dossier, dict) else {}
    dossier.update({"valid": False, "errors": [{"code": "invalid_dossier", "message": "Dossier validation did not pass."}]})
    return {"dossier_json": json.dumps(dossier, ensure_ascii=False), "validation_json": "{}", "experiment_json": "{}", "comparison_json": "{}", "assessment_json": json.dumps({"strict_status":"not_comparable","approximate_status":"insufficient_metrics","items":[]}), "markdown_report": "Dossier validation did not pass."}
```

`thresholds_failure` inputs: `dossier_json` (String) =
`{{#parse_dossier_response.dossier_json#}}`, `threshold_errors` (String) =
`{{#validate_thresholds.threshold_errors#}}`.

```python
import json
def main(dossier_json: str, threshold_errors: str) -> dict:
    try: dossier = json.loads(dossier_json)
    except (TypeError, json.JSONDecodeError): dossier = {}
    return {"dossier_json": json.dumps(dossier if isinstance(dossier, dict) else {}, ensure_ascii=False), "validation_json": "{}", "experiment_json": "{}", "comparison_json": "{}", "assessment_json": json.dumps({"strict_status":"not_comparable","approximate_status":"insufficient_metrics","items":[],"errors":[{"code":"invalid_similarity_thresholds","message":"Thresholds must satisfy 0 < close < partial <= 1."}]}, ensure_ascii=False), "markdown_report": "Similarity thresholds are invalid."}
```

`validation_semantic_failure` inputs: `dossier_json` (String) =
`{{#parse_dossier_response.dossier_json#}}`, `validation_json` (String) =
`{{#parse_validation_response.validation_json#}}`.

```python
import json
def obj(value):
    try: value = json.loads(value)
    except (TypeError, json.JSONDecodeError): return {}
    return value if isinstance(value, dict) else {}
def main(dossier_json: str, validation_json: str) -> dict:
    validation = obj(validation_json); validation.update({"valid":False, "errors":[{"code":"dataset_validation_failed","message":"Dataset validation did not pass."}]})
    return {"dossier_json":json.dumps(obj(dossier_json), ensure_ascii=False), "validation_json":json.dumps(validation, ensure_ascii=False), "experiment_json":"{}", "comparison_json":"{}", "assessment_json":json.dumps({"strict_status":"not_comparable","approximate_status":"insufficient_metrics","items":[]}), "markdown_report":"Dataset validation did not pass."}
```

`experiment_semantic_failure` inputs: `dossier_json` (String) =
`{{#parse_dossier_response.dossier_json#}}`, `validation_json` (String) =
`{{#parse_validation_response.validation_json#}}`, `experiment_json` (String) =
`{{#parse_experiment_response.experiment_json#}}`.

```python
import json
def obj(value):
    try: value = json.loads(value)
    except (TypeError, json.JSONDecodeError): return {}
    return value if isinstance(value, dict) else {}
def main(dossier_json: str, validation_json: str, experiment_json: str) -> dict:
    experiment = obj(experiment_json); experiment.update({"status":"failed", "errors":[{"code":"experiment_failed","message":"Experiment did not return a successful result."}]})
    return {"dossier_json":json.dumps(obj(dossier_json), ensure_ascii=False), "validation_json":json.dumps(obj(validation_json), ensure_ascii=False), "experiment_json":json.dumps(experiment, ensure_ascii=False), "comparison_json":"{}", "assessment_json":json.dumps({"strict_status":"not_comparable","approximate_status":"insufficient_metrics","items":[]}), "markdown_report":"Experiment did not return a successful result."}
```

`request_failure` inputs: `dossier_json` (String) =
`{{#parse_dossier_response.dossier_json#}}`, `validation_json` (String) =
`{{#parse_validation_response.validation_json#}}`, `experiment_json` (String) =
`{{#parse_experiment_response.experiment_json#}}`.

```python
import json
def obj(value):
    try: value = json.loads(value)
    except (TypeError, json.JSONDecodeError): return {}
    return value if isinstance(value, dict) else {}
def main(dossier_json: str, validation_json: str, experiment_json: str) -> dict:
    comparison = {"experiment_id":obj(experiment_json).get("experiment_id"), "items":[], "errors":[{"code":"invalid_comparison_request","message":"Experiment ID and unambiguous metrics are required."}]}
    return {"dossier_json":json.dumps(obj(dossier_json), ensure_ascii=False), "validation_json":json.dumps(obj(validation_json), ensure_ascii=False), "experiment_json":json.dumps(obj(experiment_json), ensure_ascii=False), "comparison_json":json.dumps(comparison, ensure_ascii=False), "assessment_json":json.dumps({"strict_status":"not_comparable","approximate_status":"insufficient_metrics","items":[]}), "markdown_report":"Comparison request could not be built."}
```

`comparison_semantic_failure` inputs: `dossier_json` (String) =
`{{#parse_dossier_response.dossier_json#}}`, `validation_json` (String) =
`{{#parse_validation_response.validation_json#}}`, `experiment_json` (String) =
`{{#parse_experiment_response.experiment_json#}}`, `comparison_json` (String) =
`{{#parse_comparison_response.comparison_json#}}`.

```python
import json
def obj(value):
    try: value = json.loads(value)
    except (TypeError, json.JSONDecodeError): return {}
    return value if isinstance(value, dict) else {}
def main(dossier_json: str, validation_json: str, experiment_json: str, comparison_json: str) -> dict:
    comparison = obj(comparison_json); comparison.update({"items":comparison.get("items", []), "errors":[{"code":"invalid_comparison_response","message":"Comparison response did not pass validation."}]})
    return {"dossier_json":json.dumps(obj(dossier_json), ensure_ascii=False), "validation_json":json.dumps(obj(validation_json), ensure_ascii=False), "experiment_json":json.dumps(obj(experiment_json), ensure_ascii=False), "comparison_json":json.dumps(comparison, ensure_ascii=False), "assessment_json":json.dumps({"strict_status":"not_comparable","approximate_status":"insufficient_metrics","items":[]}), "markdown_report":"Comparison response did not pass validation."}
```

## Terminal outputs

Dify requires Output variable names to be unique across the workflow. Therefore
the graph must not create a separate Output node on every mutually exclusive
branch: doing so makes publication fail because each branch repeats the same six
names. Every success or failure terminal **producer** still returns these six
String values with the same names:

1. `dossier_json`
2. `validation_json`
3. `experiment_json`
4. `comparison_json`
5. `assessment_json`
6. `markdown_report`

Add six Variable Aggregator nodes with String output type, in this exact order:

1. `aggregate_dossier_json`
2. `aggregate_validation_json`
3. `aggregate_experiment_json`
4. `aggregate_comparison_json`
5. `aggregate_assessment_json`
6. `aggregate_markdown_report`

For each aggregator, add the matching field from all eleven mutually exclusive
terminal producers, in this order: `format_comparison_report`,
`normalize_dossier_http_failure`, `dossier_semantic_failure`,
`thresholds_failure`, `normalize_validation_http_failure`,
`validation_semantic_failure`, `normalize_experiment_http_failure`,
`experiment_semantic_failure`, `request_failure`,
`normalize_comparison_http_failure`, and `comparison_semantic_failure`.

Connect every terminal producer to `aggregate_dossier_json`, then connect the
six aggregators as a chain in the order above. A Variable Aggregator is the Dify
join primitive for mutually exclusive branches: it returns the one available
candidate without evaluating a skipped producer. Finally create exactly one
Output node. Bind its six uniquely named String outputs to the corresponding
aggregator's `output` value. No semantic or HTTP failure normalizer reads an
unexecuted HTTP response, and the single shared Output node never binds directly
to branch-local values.

## Manual acceptance checks

1. Upload `tests/fixtures/minimal-paper-dossier.json` and a CSV, retain all
   defaults, and run the workflow.
2. Confirm the dossier metric becomes `roc_auc` with paper value `0.91` and
   evidence page 2.
3. Confirm strict status is `not_comparable`: the fixture deliberately omits
   a dataset digest and random seed.
4. Confirm the approximate grade is deterministic and the report includes
   `近似指标一致不等于严格复现。`.
5. Test damaged JSON, no metrics, reversed thresholds, a missing target column,
   and each HTTP error branch. Each must reach the single shared Output node
   with all six strings.

Publish V3 only after those checks. V2 remains published and unchanged.

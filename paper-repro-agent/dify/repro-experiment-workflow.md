# Dify table-experiment workflow (V2)

This is a **new, separate** Dify workflow for the `repro-runner` service. Do not edit
the existing paper-dossier workflow. The output is an independent, deterministic
baseline experiment, not an exact reproduction of paper results.

## 1. Start node

Create a Workflow application named `表格实验复现` and add these start variables.

| Name | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `training_csv` | File | Yes | — | CSV only; used only as an HTTP multipart file. |
| `target_column` | Short text | No | `Y_cls` | Binary target column. |
| `test_size` | Number | No | `0.2` | Must be between 0.1 and 0.5. |
| `random_state` | Number | No | `42` | Non-negative deterministic seed. |
| `drop_duplicates` | Boolean | No | `false` | Whether training removes duplicate rows. |

The uploaded CSV goes only to `repro-runner`; its raw rows must never be put in a
model context, knowledge base, template, prompt, or workflow output.

## 2. Validate dataset node

Add an **HTTP Request** node named `validate_dataset`.

| Setting | Value |
| --- | --- |
| Method | `POST` |
| URL | `http://repro-runner:8001/v1/validate-dataset` |
| Body type | `form-data` |
| Field `file` | `{{#start.training_csv#}}` (File) |
| Field `target_column` | `{{#start.target_column#}}` (Text) |

Immediately after that HTTP node, add a **Code** node named
`parse_validation_response`. It converts Dify's HTTP `body` string to safe, normalized
values before any branch reads it. Configure one input and three outputs:

```text
Input:  body = {{#validate_dataset.body#}}
Outputs: validation_ok (Boolean), validation_json (String), validation_errors (String)
```

Use this code:

```python
import json


def main(body):
    try:
        response = json.loads(body) if isinstance(body, str) else body
    except (TypeError, json.JSONDecodeError):
        response = {
            "valid": False,
            "errors": [{"code": "invalid_validation_response", "message": "The validation service returned invalid JSON."}],
        }

    if not isinstance(response, dict):
        response = {
            "valid": False,
            "errors": [{"code": "invalid_validation_response", "message": "The validation service response must be an object."}],
        }

    errors = response.get("errors", [])
    if not isinstance(errors, list):
        errors = [{"code": "invalid_validation_response", "message": "The validation error list is invalid."}]
    normalized = {**response, "errors": errors, "valid": response.get("valid") is True}
    return {
        "validation_ok": normalized["valid"],
        "validation_json": json.dumps(normalized, ensure_ascii=False),
        "validation_errors": json.dumps(errors, ensure_ascii=False),
    }
```

Add an IF/ELSE node named `validation_ok` after the Code node. Its successful condition is:

```text
{{#parse_validation_response.validation_ok#}} equals true
```

The ELSE branch goes straight to the End node and returns:

- `validation_json`: `{{#parse_validation_response.validation_json#}}`
- `experiment_json`: `{}`
- `markdown_summary`: `数据校验失败。请检查标签列、数值特征、缺失值和错误详情。`

If the HTTP node itself fails (network, 4xx, or 5xx), use its error-handling branch to
the same End node. Do not retry through a model.

## 3. Run experiment node

On the `validation_ok` true branch, add an **HTTP Request** node named
`run_experiment`.

| Setting | Value |
| --- | --- |
| Method | `POST` |
| URL | `http://repro-runner:8001/v1/run-experiment` |
| Body type | `form-data` |
| Field `file` | `{{#start.training_csv#}}` (File) |
| Field `target_column` | `{{#start.target_column#}}` (Text) |
| Field `test_size` | `{{#start.test_size#}}` (Text) |
| Field `random_state` | `{{#start.random_state#}}` (Text) |
| Field `drop_duplicates` | `{{#start.drop_duplicates#}}` (Text) |
| Field `model` | `random_forest` (Text) |

Use the HTTP node's error-handling branch to return the validation response and a
clear failure summary. No LLM node is needed for the V2 experiment workflow.

## 4. Report formatter code node

After `run_experiment`, add a **Code** node named `format_experiment_report` with one
input variable:

```text
experiment = {{#run_experiment.body#}}
```

Set its output variable to `markdown_summary` (String) and use this Python code:

```python
import json


def main(experiment):
    if isinstance(experiment, str):
        experiment = json.loads(experiment)

    dataset = experiment["dataset"]
    metrics = experiment["metrics"]
    importances = experiment["feature_importance"]
    top_features = "\n".join(
        f"- {item['feature']}: {item['importance']:.6f}"
        for item in importances[:10]
    ) or "- 无"

    summary = f"""# 表格实验复现结果

- 实验 ID：{experiment['experiment_id']}
- 状态：{experiment['status']}
- 复现标记：{experiment['reproducibility_status']}

## 数据概况

- 行数：{dataset['rows']}
- 特征数：{dataset['features']}
- 标签列：{dataset['target']}
- 缺失值：{dataset['missing_values']}
- 重复行：{dataset['duplicate_rows']}

## 指标

- ROC AUC：{metrics['roc_auc']:.6f}
- Accuracy：{metrics['accuracy']:.6f}
- Balanced Accuracy：{metrics['balanced_accuracy']:.6f}
- Precision：{metrics['precision']:.6f}
- Recall：{metrics['recall']:.6f}
- F1：{metrics['f1']:.6f}

## 特征重要性（前 10）

{top_features}

> `baseline_only` 表示这是依据当前数据划分、随机种子和默认随机森林参数运行的独立基线，不能称为论文结果的精确复现。
"""
    return {"markdown_summary": summary}
```

## 5. End node outputs

Expose exactly these three outputs:

| Output name | Value |
| --- | --- |
| `experiment_json` | `{{#run_experiment.body#}}` |
| `validation_json` | `{{#parse_validation_response.validation_json#}}` |
| `markdown_summary` | `{{#format_experiment_report.markdown_summary#}}` |

The Start file is deliberately absent from End outputs. The service stores result
metadata only and does not store a copy of the uploaded CSV.

## LLM safety

No LLM node is used in this V2 workflow. The CSV is posted only as multipart form data
to `repro-runner`; neither raw file content nor individual rows are sent to a model.

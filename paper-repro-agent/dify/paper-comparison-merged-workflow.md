# Merged paper-comparison workflow: Dify import and operator guide

This guide is the runbook for `dify/paper-comparison-merged-workflow.yml`. The
default/shared artifact remains the DeepSeek profile. For local Ollama
acceptance, first generate and import
`dify/paper-comparison-merged-workflow-ollama.yml` as a separate Dify app
rather than replacing the DeepSeek app. The merged workflow combines paper
preparation and the confirmed multi-model experiment in one workflow with two
modes:

- `prepare` parses the paper, diagnoses the CSV, creates a short-lived protocol
  draft, and returns a preview plus the values needed for the next run.
- `run` confirms that preview, reloads the server-side draft, verifies the CSV
  identity, submits the asynchronous job, and produces the comparison report.

The two original workflows remain rollback targets and must stay available:

- `dify/paper-comparison-prepare-workflow.yml`
- `dify/paper-comparison-multimodel-workflow.yml`

Do not overwrite, delete, or repurpose either original workflow while importing
or verifying the merged workflow.

## 1. Prerequisites and shared configuration

Before the first run, make sure the Dify runtime can reach the service names
used by the imported DSL:

- `http://paper-parser:8000/v1/parse`
- `http://repro-runner:8001/v1/diagnose-dataset`
- `http://repro-runner:8001/v1/protocol-drafts`
- `http://repro-runner:8001/v1/validate-dataset`
- `http://repro-runner:8001/v1/jobs`
- `http://repro-runner:8001/v1/compare-model-suite-result`

The parser authentication variable `PARSER_API_TOKEN` must also be configured
in Dify and match the parser service configuration. This guide intentionally
does not contain its value.

Configure the protocol signing secret in both runtimes before testing:

| Runtime | Variable | Required setting |
| --- | --- | --- |
| Dify workflow environment | `DIFY_PROTOCOL_SECRET` | Set it through the Dify secret mechanism to the externally supplied protocol value. |
| `repro-runner` environment | `REPRO_RUNNER_PROTOCOL_SECRET` | Set it through the deployment secret mechanism to that exact same externally supplied value. |

The two variables must have the same bytes, including no accidental leading or
trailing whitespace. Never put the value in the YAML, this guide, a Dify text
input, a URL, a log message, or a support screenshot. The exported DSL contains
an empty secret field by design. Restart or redeploy both runtimes after
changing the configuration.

`repro-runner` reads `REPRO_RUNNER_PROTOCOL_SECRET` through its
`REPRO_RUNNER_` settings prefix. Its default draft lifetime is 900 seconds;
the configured lifetime is bounded by the service to 60 through 3600 seconds.

## 2. Import the workflow as a new Dify workflow

For local Ollama acceptance, generate the profile-specific bundle first:

```powershell
python scripts/build_multimodel_dsl.py --profile ollama
```

That command leaves the existing DeepSeek files as the default output and adds
the separate `-ollama` artifacts.

1. In Dify, open the workflow import action and select
   `dify/paper-comparison-merged-workflow-ollama.yml` for local Ollama
   acceptance, or `dify/paper-comparison-merged-workflow.yml` when you intend
   to keep the default DeepSeek profile.
2. Import it as a new workflow/application. Do not import over either rollback
   workflow listed above.
3. Install the pinned Ollama marketplace dependency declared in the imported
   DSL when using the `-ollama` artifact.
4. In the model provider settings for the imported Ollama app, select model
   `qwen3:8b` and set the provider Base URL to `http://ollama:11434` from the
   Dify Docker network. Do not append `/api`.
5. In the new workflow's environment settings, configure
   `PARSER_API_TOKEN` and `DIFY_PROTOCOL_SECRET` as secret variables. Keep the
   variable names unchanged, and enter both values only in the Dify UI.
6. Verify that the imported `Start` node has `run_mode` with options
   `prepare` and `run`, and that `training_csv` is required while `paper_pdf`
   is conditionally required by `prepare`.
7. Verify that the service containers use the network names above and that
   `repro-runner` is healthy before starting a real run.

Before opening Dify, run the read-only Ollama health checks:

```powershell
Invoke-RestMethod http://localhost:11434/api/tags
docker run --rm --network docker_default curlimages/curl:8.10.1 http://ollama:11434/api/tags
```

Both responses should list `qwen3:8b`. If the temporary curl image is
unavailable, substitute an equivalent read-only request from an existing Dify
container and record that substitution in the acceptance notes.

The imported workflow owns the following internal sequence. Operators do not
need to recreate these nodes manually:

```text
prepare: paper_pdf -> parse -> dossier -> diagnose training_csv
         -> prepare_protocol_artifacts -> save protocol draft -> Output_prepare
run:     confirm protocol -> GET protocol draft -> validate training_csv
         -> submit job -> wait for result -> compare -> Output_run
```

The GET request is made with `X-Protocol-Token`; the protocol value is not put
in the URL query string.

## 3. Start inputs

These are the exact user-facing Start variables in the merged DSL.

| Name | Type/default | Use |
| --- | --- | --- |
| `run_mode` | select, required; default `prepare` | Selects the branch. Allowed values are `prepare` and `run`. |
| `paper_pdf` | local PDF file, not required at Start | Required only when `run_mode=prepare`; omit it when `run_mode=run`. |
| `training_csv` | local CSV file, required | Upload it in both modes. The run upload must be the exact same file bytes used for prepare. |
| `protocol_token` | paragraph, default empty | Leave empty for prepare; in run, paste the exact value returned by prepare. Never edit or reconstruct it. |
| `confirm_protocol` | checkbox, default `false` | Must be `true` for run after reviewing `protocol_preview_json`. |
| `target_column` | text, default `Y_cls` | Target used for diagnosis and confirmation. Keep it consistent with the reviewed protocol. |
| `test_size` | number, default `0.2` | Confirmed outer test fraction. |
| `random_state` | number, default `42` | Confirmed deterministic split seed. |
| `models_json` | paragraph; default is the seven-model suite | JSON array of supported model names for the confirmed run. |
| `cv_folds` | number, default `5` | Cross-validation folds; the runner accepts bounded values from 3 through 10. |
| `optimization_metric` | text, default `roc_auc` | Suite optimization metric. |
| `n_iter` | number, default `8` | Bounded model-search iteration count. |
| `use_gpu` | checkbox, default `false` | Opt in only when the runner has the supported GPU runtime. |
| `drop_duplicates` | checkbox, default `false` | Dataset loading option. |
| `close_threshold` | number, default `0.05` | Approximate comparison threshold. |
| `partial_threshold` | number, default `0.1` | Approximate comparison threshold. |
| `protocol_notes` | paragraph, default empty | Optional prepare-only notes; only a digest is retained in the protocol artifacts. |

There is no `paper_dossier_json` Start input in the merged workflow. The run
branch reads the validated dossier from the protocol draft created by prepare.

## 4. Prepare run

Start the merged workflow with this input contract:

```text
run_mode: prepare
paper_pdf: upload the paper PDF file
training_csv: upload the training CSV file
protocol_token: leave empty
confirm_protocol: false
```

The optional target and protocol notes may be set before starting prepare. The
CSV is sent to `repro-runner` for diagnosis and the PDF is sent to
`paper-parser`; raw CSV rows are not a prompt input.

When prepare succeeds, inspect `Output_prepare`:

| Output | Meaning |
| --- | --- |
| `protocol_preview_json` | Reviewable preview containing the protocol version, dataset/profile metadata, manifest draft, paper summary, warnings, risks, and unresolved protocol fields. |
| `protocol_token` | Opaque, signed, short-lived value needed by the run branch. Keep it exactly as returned. |
| `draft_expires_at` | Expiry time as decimal epoch seconds. Keep it with the protocol value and finish the run before it expires. |

Proceed only when the preview is acceptable and the protocol is ready. Do not
copy raw CSV rows or PDF text into any Dify input or report. A prepare failure
returns safe prepare outputs with no usable protocol value; fix the reported
code and run `prepare` again.

The server-side `POST /v1/protocol-drafts` call stores only the validated
dossier and safe manifest/dataset metadata. It does not store the PDF, CSV
bytes or raw rows, the protocol value, or a runtime secret. The draft is bound
to its draft ID, manifest identity, dataset identity, signature, and expiry.

## 5. Run with the prepared protocol

Use the same imported workflow for the second execution. Set the inputs as
follows:

```text
run_mode: run
paper_pdf: omit this input
training_csv: re-upload the exact same file bytes used for prepare
protocol_token: paste the exact prepare output
confirm_protocol: true
```

Do not upload a PDF in this mode. Do not paste a token into a URL, change any
character, or replace it with a newly constructed value. If the target or
confirmed run options no longer represent the reviewed protocol, start over
with a fresh prepare run rather than trying to repair the old value.

The run branch performs these checks before training:

1. `confirm_protocol` must be exactly true.
2. The signed protocol must be well formed, valid, unexpired, and bound to its
   draft.
3. `GET /v1/protocol-drafts/{draft_id}` must return the matching manifest and
   dossier. The value is authenticated with `X-Protocol-Token`.
4. The uploaded `training_csv` must produce the same dataset identity as the
   confirmed manifest. Same filename or same columns is not sufficient; reuse
   the exact same file bytes.
5. The runner submits `POST /v1/jobs`, waits for a terminal result, and sends
   the safe result to the suite comparison endpoint.

`Output_run` contains these six string outputs on the success path:

- `dossier_json`
- `validation_json`
- `experiment_json`
- `comparison_json`
- `assessment_json`
- `markdown_report`

Every direct run failure End exposes the same six output names. Read the
structured `errors[].code` in the relevant JSON and the safe
`markdown_report`; do not treat a failure payload as a successful empty
comparison.

## 6. Draft and token lifecycle

- The prepare token and draft are short-lived. Use `draft_expires_at` as the
  operator deadline; the server also enforces the stored draft expiry.
- The protocol binds the draft, manifest, and dataset. Changing the CSV,
  target, or a bound value can invalidate the run even if the filename is
  unchanged.
- A token is opaque. Do not decode, edit, extend, re-sign, or manually replace
  it. Do not put it in query parameters or logs.
- If the token or draft has expired, rerun `prepare` with the paper PDF and the
  training CSV, review the new preview, retain the new outputs, and then run
  again. Never edit an expired value or bypass `confirm_protocol`.
- If the run fails for a transient runner or comparison-service reason while
  the protocol is still valid, retry the run with the same protocol and exact
  CSV. If the value expires before retry, rerun prepare.

## 7. Error actions

The workflow and runner return stable codes rather than raw exception text.
Use the first/root error when several downstream fields also report a safe
failure. The actions below match the code contracts.

| Code | Contract | User action |
| --- | --- | --- |
| `paper_pdf_required` | The prepare input guard did not receive a PDF while `run_mode=prepare`; no usable protocol is created. | Select `prepare`, upload the paper PDF and the training CSV, then run prepare again. |
| `protocol_not_confirmed` | The run confirmation helper received `confirm_protocol` other than the boolean `true`; job submission is not reached. | Review `protocol_preview_json`, set `confirm_protocol=true`, and use the unchanged protocol value from prepare. |
| `protocol_token_malformed` | The protocol value failed the signed-token shape or payload decoding checks. | Paste the exact value returned by the latest successful prepare. Do not type, trim, decode, or reconstruct it; if unavailable, rerun prepare. |
| `protocol_token_expired` | The Dify run-side expiry check found the protocol value at or past its `exp` time. | Rerun prepare, retain the new preview/value/expiry, and then run. Never extend or edit the old value. |
| `protocol_draft_not_found` | The runner GET returned 404 because the draft path is absent. | Rerun prepare to create a new server-side draft; do not create or edit a draft directory manually. |
| `protocol_draft_expired` | The runner rejected the draft at or past its stored expiry and returns the draft-expired contract. | Rerun prepare and use the new protocol; do not bypass confirmation. |
| `protocol_draft_token_mismatch` | The draft ID, signature binding, manifest identity, or dataset identity did not match the supplied protocol/draft response. | Stop the run, discard the mismatched value, rerun prepare, and use the new value with the exact same CSV. |
| `manifest_dataset_mismatch` | `/v1/jobs` (and the execution guard) found that the uploaded CSV dataset identity differs from the confirmed manifest; the job is not admitted for that request. | Upload the exact same training CSV bytes used for prepare. If that file changed, rerun prepare with the changed file before running. |
| `job_submit_failed` | The workflow's job-submit HTTP failure path could not obtain a successful asynchronous submission; downstream experiment/comparison stages do not run. | Check `repro-runner` health and logs without exposing inputs, then retry run with the same valid protocol and exact CSV. Rerun prepare only if the protocol expires. |
| `comparison_not_run` | A safe terminal payload records that comparison was skipped because an earlier protocol, validation, experiment, or request stage failed. It is not a numeric comparison result. | Fix the earlier error code shown in `experiment_json`, `validation_json`, or `comparison_json`, then rerun the required mode. Do not interpret this code as evidence that the paper and experiment are comparable. |

Other safe codes may identify a tampered value, invalid protocol payload,
dataset validation failure, rejected job, job failure, polling timeout, or
comparison-service failure. Follow the same rule: fix the earliest reported
stage, keep inputs private, and rerun `prepare` when the protocol or CSV
identity has changed.

## 8. Safe failure and rollback behavior

- The workflow uses direct terminal End nodes for protocol, draft, validation,
  job, and comparison failures. A skipped branch does not pass an unexecuted
  aggregator and silently return `{}` as if it succeeded.
- Failure outputs contain bounded JSON objects, stable codes, and safe Markdown
  messages. They must not contain a protocol value, secret, raw CSV rows, PDF
  text, or a traceback.
- The runner draft file contains dossier data and safe metadata only. Do not
  inspect or copy it into a ticket as a substitute for the protocol value.
- Keep both original workflows published or otherwise recoverable. If the
  imported workflow, service health, or live verification fails, switch Dify
  back to the saved original prepare and multi-model workflows; do not delete
  the merged DSL or overwrite either rollback target.

## 9. Verification checklist

Before calling the import usable, verify all of the following without pasting
private input contents into logs or documentation:

- For local Ollama acceptance, `python scripts/build_multimodel_dsl.py --profile ollama`
  was run and `dify/paper-comparison-merged-workflow-ollama.yml` was imported
  as a separate app while the DeepSeek artifact remained the default/shared
  profile.
- The imported Ollama app uses the pinned marketplace dependency, model
  `qwen3:8b`, and Base URL `http://ollama:11434` from the Dify Docker network.
- `PARSER_API_TOKEN` and `DIFY_PROTOCOL_SECRET` were entered only in the Dify
  UI and not written into the DSL, repository files, logs, or screenshots.
- The read-only localhost and Docker-network Ollama health checks both listed
  `qwen3:8b` before the Dify run.
- The new Dify workflow was imported from the intended profile artifact:
  `dify/paper-comparison-merged-workflow.yml` for the default DeepSeek profile
  or `dify/paper-comparison-merged-workflow-ollama.yml` for local Ollama
  acceptance.
- `DIFY_PROTOCOL_SECRET` and `REPRO_RUNNER_PROTOCOL_SECRET` are configured to
  the same externally supplied value, and neither value appears in the DSL,
  docs, logs, or screenshots.
- A prepare run used both `paper_pdf` and `training_csv`, produced a non-empty
  `protocol_preview_json`, and returned protocol/expiry outputs.
- A run used the exact same `training_csv`, omitted `paper_pdf`, pasted the
  unchanged protocol value, and set `confirm_protocol=true`.
- `Output_run` exposed `dossier_json`, `validation_json`, `experiment_json`,
  `comparison_json`, `assessment_json`, and `markdown_report`.
- The local acceptance record retained only status, duration, validation
  aggregates, experiment status, model status, comparison status, and runner
  provenance; it did not include PDF text, CSV rows, API keys, protocol
  values, or full request payloads.
- The final report included `git_commit`, `source_digest`, and
  `workflow_version`.
- An expired value was handled by rerunning prepare rather than editing it or
  bypassing confirmation.
- The two original workflows remain available as rollback targets.

All examples in this guide are field-level instructions only. They contain no
CSV rows, PDF text, protocol value, secret value, or mutable placeholder.

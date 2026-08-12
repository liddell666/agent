# Paper comparison protocol preparation workflow

This workflow is the first step of the confirmed asynchronous experiment path.
Import it as a separate Dify workflow/app version; do not overwrite the published
V3 workflow.

## Inputs

- `paper_pdf`: the paper PDF used for dossier parsing;
- `training_csv`: a UTF-8 ordinary tabular binary-classification CSV;
- `protocol_notes`: optional notes, retained only as a digest in the preview and
  short-lived token;
- `target_column`: optional suggestion, which must be checked against the preview.

## Outputs

- `protocol_preview_json`: aggregate dataset/profile metadata, target candidates,
  final feature proposal, risk flags, class counts/ratios, paper metric summary,
  and unresolved protocol fields;
- `protocol_token`: opaque, HMAC-signed, dataset-bound token with a bounded expiry.

The preview and token never contain CSV rows, PDF source text, traceback text, or
arbitrary backend error details. If the preview lists unresolved fields, the token
cannot pass confirmation until the prepare step is rerun with valid inputs.

The local fallback deliberately does not persist PDF or CSV bytes between the two
workflows. Re-upload the same CSV for the confirmed run; the `/v1/jobs` endpoint
recomputes and verifies `dataset_id` before staging it for the worker. The token
therefore binds the confirmed protocol without turning Dify variables into a file
store.

Set `DIFY_PROTOCOL_SECRET` as a runtime secret in both imported Dify workflows.
The embedded local fallback is only for tests and single-user development; never
use it for a shared or production deployment.

## Handoff to the confirmed run

Pass `protocol_token` to `paper-comparison-multimodel-workflow.yml`, set
`confirm_protocol=true`, and review the final target/features before submitting.
An expired or tampered token must be replaced by rerunning this workflow; do not
edit or extend it manually.

## Rollback

If preparation or live verification fails, keep using the existing V3 workflow at
`dify/paper-comparison-workflow.yml` and import each generated workflow version
explicitly.

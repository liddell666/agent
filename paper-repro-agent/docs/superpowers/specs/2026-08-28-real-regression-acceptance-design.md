# Real-World Regression Acceptance Design

## Objective

Move the isolated regression Dify candidate from synthetic-path acceptance to
evidence-backed real-world acceptance. The acceptance suite will exercise five
real research papers paired with public tabular regression datasets. At least
four cases must complete without source-code changes, and no case may be
reported as strictly comparable unless dataset and held-out evaluation identity
are actually established.

This phase validates the candidate. It does not promote or overwrite any
production Dify application, alter the six user-owned workflow DSL changes, or
add new estimators.

## Acceptance corpus

The suite uses five deliberately different regression cases:

1. Energy Efficiency: heating-load target from the Tsanas and Xifara dataset.
2. Concrete Compressive Strength: compressive-strength target from the Yeh
   dataset.
3. Wine Quality: red-wine quality target from the Cortez et al. dataset.
4. Appliances Energy Prediction: appliance-energy target from the Candanedo et
   al. dataset.
5. Real Estate Valuation: house-price-per-unit-area target from the Yeh and Hsu
   dataset.

For every case, the paper must come from a publisher, author, or institutional
repository, and the dataset must come from UCI or another primary institutional
repository. The registry records the canonical source URL, expected SHA-256,
target column, task type, license/source attribution, and any explicit paper
metric overrides. Redirected downloads are accepted only after their final URL
and digest are recorded.

PDF and CSV bodies are downloaded to an ignored local cache. They are never
committed to Git or copied into acceptance evidence.

## Architecture

### Case registry

A versioned YAML registry defines only reproducible metadata and expectations.
Each entry contains a stable case ID, source identities, expected file digests,
target column, optional columns to exclude, and expected protocol facts. It
does not contain document text, CSV rows, credentials, or Dify tokens.

### Acquisition boundary

A command-line acquisition tool downloads one named case at a time into an
ignored cache, enforces HTTPS, applies size and timeout limits, verifies the
recorded digest before use, and rejects HTML/error pages masquerading as PDFs or
CSVs. Existing cache files are reused only after digest verification.

### Acceptance driver

The driver addresses only the regression candidate App UUID
`17fe51d4-091f-4729-87ee-3c0a2e920918`. It submits the real PDF and CSV through
the same prepare/confirm workflow boundary used by the synthetic acceptance.
The driver applies declared target/exclusion choices, waits with bounded status
polling, and records safe aggregate results.

The driver never automatically changes the Dify graph, republishes the app, or
retries with modified scientific choices. A protocol ambiguity is an observed
outcome, not a reason to silently invent a split or metric mapping.

### Safe evidence report

One JSON report summarizes each case with identifiers, source digests, workflow
and experiment status, extracted metric names, model status counts, rankings,
strict and approximate comparison statuses, test digest, categorized failure
code, and elapsed time. It excludes raw paper text, CSV rows, filenames supplied
by remote servers, cookies, authorization headers, protocol secrets, and full
request/response payloads.

## Data flow

1. Read and validate one registry entry.
2. Download or verify its cached PDF and CSV.
3. Confirm MIME signature, bounded size, and SHA-256 identity.
4. Submit files to the candidate prepare workflow.
5. Validate extracted task, metrics, dataset profile, and unresolved fields.
6. Confirm only the registry-declared scientific choices.
7. Wait for the experiment and final comparison.
8. Sanitize the result into the aggregate evidence schema.
9. Run leak checks over every persisted artifact.
10. Evaluate the corpus gates without mutating the candidate.

## Comparability rules

- Strict comparability requires a paper metric to identify the same dataset,
  evaluation split, and compatible metric definition as the independent run.
- A matching metric name and numerically close value are insufficient.
- Missing split provenance produces `not_comparable`, never an inferred match.
- Approximate comparison remains available as a separately labelled numerical
  observation and cannot upgrade strict status.
- R² variants, normalized RMSE, cross-validation means, and held-out test
  metrics remain distinct unless the paper explicitly defines equivalence.

## Failure classification

Every unsuccessful case receives one stable category:

- `acquisition_failed`: primary source unavailable or digest mismatch;
- `paper_parse_failed`: the PDF could not be converted or parsed;
- `evidence_ambiguous`: required metric/protocol evidence is unresolved;
- `dataset_invalid`: schema, target, or value validation failed;
- `experiment_failed`: the runner failed after valid confirmation;
- `comparison_failed`: experiment succeeded but comparison/reporting failed;
- `privacy_gate_failed`: persisted output contained prohibited content;
- `service_unavailable`: a required local service was unhealthy.

Failures preserve enough aggregate context to diagnose the boundary without
retaining the underlying paper or dataset content.

## Acceptance gates

The phase passes only when all of the following are true:

- all five source pairs are digest-pinned and reproducibly acquired;
- at least four of five cases finish end to end without code changes between
  cases;
- all completed experiments use `task_type=regression` and return finite MAE,
  RMSE, and R² for every successful model;
- there are zero false `strictly_comparable` outcomes;
- every `not_comparable` result contains a specific provenance reason;
- the safe evidence report passes automated raw-content and secret scans;
- synthetic regression acceptance still passes unchanged;
- focused tests and the complete repository suite pass;
- candidate draft and published graph/metadata digests remain unchanged during
  corpus execution.

The four-of-five threshold measures operational robustness, not scientific
agreement. A scientifically honest `not_comparable` result counts as an
end-to-end completion when parsing, validation, experiment, and reporting all
succeed.

## Testing strategy

Unit tests cover registry validation, URL policy, digest mismatch, MIME
rejection, cache reuse, result sanitization, failure categorization, timeout
handling, comparability aggregation, and leak detection. Integration tests use
small local HTTP fixtures and fake workflow clients; they do not depend on the
public internet. The live acceptance is a separately invoked, resumable gate
that requires healthy local Dify and runner services.

Each defect discovered by a real case first becomes a minimized failing fixture
and test. Fixes must generalize to the input class and must not special-case a
paper title, author, dataset name, or corpus case ID.

## Delivery and rollback

The first delivery contains the registry, acquisition/acceptance tooling,
tests, safe aggregate evidence, and operating documentation. The candidate is
not republished merely to run the corpus. If a generalized code or DSL fix is
required, it follows the existing digest-checked candidate publication process
with a new explicit rollback backup and independent readback before the affected
cases are rerun.

The next optimization phase begins only after this corpus gate is complete. Its
input will be the observed failure distribution, which determines whether the
highest-value follow-up is manual protocol confirmation, report explanation, or
service hardening.

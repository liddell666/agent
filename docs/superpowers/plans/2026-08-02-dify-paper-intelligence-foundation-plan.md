# Dify Paper Intelligence Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing local Dify 1.16.0 deployment so a user can upload one research-paper PDF and receive a schema-valid, page-cited paper reproduction dossier generated with DeepSeek.

**Architecture:** Keep the existing Dify Compose project at `E:\Docker\Projects\dify\docker` unchanged except for explicit size and SSRF allow-list settings. Add a separate FastAPI paper-parser service in this repository, attach it to Dify's existing `docker_default` network, parse PDFs with Docling, and let a deterministic Dify Workflow call the service and DeepSeek. The first vertical slice stops at the reproduction dossier; Docker experiment execution is a separate implementation plan after this slice passes acceptance.

**Tech Stack:** Windows 11, Docker Desktop/WSL2, Dify 1.16.0, DeepSeek Dify plugin, Python 3.12, FastAPI, Pydantic 2, Docling, pytest, Docker Compose.

## Global Constraints

- Deployment is single-user and accessible only from the local computer.
- The existing Dify 1.16.0 containers, PostgreSQL, Redis, Weaviate, plugin daemon, Dify Sandbox, and local Agent Sandbox must be reused rather than redeployed.
- At most one paper-parsing job runs at a time on the 16 GB host.
- DeepSeek remains a cloud API; no large general-purpose model is loaded locally.
- A paper PDF is required; author code, datasets, supplements, links, and target metrics remain optional.
- Every key extracted fact must include a page number and source quotation or be marked `uncertain`/`inferred`.
- No ArcGIS Desktop control, experiment container, automatic dependency installation, training, or metric reproduction is included in this plan.
- Never print or commit DeepSeek API keys, Dify secrets, database passwords, or generated internal tokens.
- Existing user ArcGIS files and unrelated untracked files in `C:\Users\17716\Documents\arcgis` must not be staged or modified.
- Changes to `E:\Docker\Projects\dify\docker\.env` require a timestamped backup and a before/after key-only diff.

---

## Planned File Structure

```text
paper-repro-agent/
├─ .env.example                       # Non-secret parser settings
├─ .gitignore                         # Runtime, model-cache, and secret exclusions
├─ compose.yaml                       # Parser service joined to docker_default
├─ pyproject.toml                     # Locked Python package and test configuration
├─ src/paper_parser/
│  ├─ __init__.py                     # Package version
│  ├─ api.py                          # FastAPI endpoints and error mapping
│  ├─ config.py                       # Environment-backed settings
│  ├─ docling_adapter.py              # Docling conversion boundary
│  ├─ paddle_adapter.py               # Low-confidence page OCR fallback
│  ├─ service.py                      # Validation and parsing orchestration
│  └─ schemas.py                      # API and dossier contracts
├─ tests/
│  ├─ fixtures/minimal-paper.pdf      # Small generated two-page test paper
│  ├─ test_api.py                     # HTTP contract tests
│  ├─ test_config.py                  # Configuration tests
│  ├─ test_docling_adapter.py         # Parser boundary tests
│  ├─ test_paddle_adapter.py          # OCR fallback tests
│  ├─ test_schemas.py                 # Evidence and dossier validation tests
│  └─ test_service.py                 # Orchestration tests
├─ dify/
│  ├─ paper-dossier-workflow.yml      # Exported Dify DSL after UI configuration
│  ├─ paper-dossier-schema.json       # LLM structured-output schema
│  └─ paper-dossier-system-prompt.md  # Versioned extraction prompt
├─ scripts/
│  ├─ check_environment.ps1           # Read-only Dify and parser checks
│  └─ smoke_parse.ps1                 # Local parser smoke request
└─ docs/
   └─ configuration-guide.md          # Exact operator configuration and recovery steps
```

Each file has one responsibility. The Dify workflow depends only on the parser HTTP contract and the JSON schema, so Docling can later be replaced or supplemented without changing workflow nodes.

### Task 1: Establish the project and configuration boundary

**Files:**
- Create: `paper-repro-agent/pyproject.toml`
- Create: `paper-repro-agent/.env.example`
- Create: `paper-repro-agent/.gitignore`
- Create: `paper-repro-agent/src/paper_parser/__init__.py`
- Create: `paper-repro-agent/src/paper_parser/config.py`
- Create: `paper-repro-agent/tests/test_config.py`

**Interfaces:**
- Consumes: environment variables supplied by `compose.yaml`.
- Produces: `Settings` and `get_settings() -> Settings` for all parser modules.

- [ ] **Step 1: Write the failing configuration tests**

```python
from paper_parser.config import Settings


def test_default_settings_are_safe():
    settings = Settings(parser_api_token="x" * 32)
    assert settings.max_upload_mb == 50
    assert settings.max_pages == 400
    assert settings.max_concurrent_jobs == 1
    assert settings.work_dir == "/data/jobs"


def test_short_token_is_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(parser_api_token="short")
```

- [ ] **Step 2: Run the test and confirm the module is missing**

Run from `C:\Users\17716\Documents\arcgis\paper-repro-agent`:

```powershell
python -m pytest tests/test_config.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'paper_parser'`.

- [ ] **Step 3: Create the package metadata and settings implementation**

`pyproject.toml` must define Python `>=3.12,<3.13`, package source directory `src`, and these exact direct dependencies: `fastapi==0.141.1`, `uvicorn[standard]==0.52.1`, `pydantic-settings==2.14.2`, `python-multipart==0.0.32`, `docling==2.117.0`, `paddleocr==3.7.0`, and CPU runtime `paddlepaddle==3.3.1`. The `test` optional dependency must contain `pytest==9.1.1`, `pytest-asyncio==1.4.0`, and `httpx==0.28.1`. Generate a lock file and commit both `pyproject.toml` and the lock file.

`config.py`:

```python
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAPER_PARSER_", extra="ignore")

    parser_api_token: str = Field(min_length=32)
    max_upload_mb: int = Field(default=50, ge=1, le=100)
    max_pages: int = Field(default=400, ge=1, le=1000)
    max_concurrent_jobs: int = Field(default=1, ge=1, le=1)
    work_dir: str = "/data/jobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`.env.example` must contain only:

```dotenv
PAPER_PARSER_API_TOKEN=replace-with-a-random-value-of-at-least-32-characters
PAPER_PARSER_MAX_UPLOAD_MB=50
PAPER_PARSER_MAX_PAGES=400
PAPER_PARSER_MAX_CONCURRENT_JOBS=1
PAPER_PARSER_WORK_DIR=/data/jobs
```

`.gitignore` must exclude `.env`, `.venv/`, `.pytest_cache/`, `__pycache__/`, `.cache/`, `data/jobs/`, and generated model caches.

- [ ] **Step 4: Install the locked test environment and verify the tests pass**

Run:

```powershell
python -m pip install -e '.[test]'
python -m pytest tests/test_config.py -v
```

Expected: two tests pass.

- [ ] **Step 5: Commit the configuration boundary**

```powershell
git add paper-repro-agent/pyproject.toml paper-repro-agent/.env.example paper-repro-agent/.gitignore paper-repro-agent/src/paper_parser/__init__.py paper-repro-agent/src/paper_parser/config.py paper-repro-agent/tests/test_config.py
git commit -m "feat: establish paper parser configuration"
```

### Task 2: Define evidence-preserving parser contracts

**Files:**
- Create: `paper-repro-agent/src/paper_parser/schemas.py`
- Create: `paper-repro-agent/tests/test_schemas.py`
- Create: `paper-repro-agent/dify/paper-dossier-schema.json`

**Interfaces:**
- Consumes: raw page elements from the Docling adapter.
- Produces: `Evidence`, `PaperElement`, `ParsedPaper`, and `PaperDossier` Pydantic models; JSON Schema used by Dify structured output.

- [ ] **Step 1: Write failing evidence-validation tests**

```python
import pytest
from pydantic import ValidationError

from paper_parser.schemas import Evidence, PaperDossier


def test_evidence_requires_page_and_quote():
    evidence = Evidence(page=3, source_text="AUC was 0.91.", source="paper")
    assert evidence.page == 3


def test_reported_fact_without_evidence_is_rejected():
    with pytest.raises(ValidationError):
        PaperDossier(
            title="Example",
            research_problem="Classification",
            task_type="machine_learning",
            datasets=[],
            methods=[],
            metrics=[{"name": "AUC", "reported_value": 0.91, "evidence": []}],
            gaps=[],
        )
```

- [ ] **Step 2: Run the focused tests and confirm failure**

```powershell
python -m pytest tests/test_schemas.py -v
```

Expected: import fails because `paper_parser.schemas` does not exist.

- [ ] **Step 3: Implement strict Pydantic contracts**

The implementation must use the following complete contract. `Evidence.page` is 1-based and positive; `source_text` is non-empty and limited to 2,000 characters; a metric with `reported_value` must contain at least one evidence item; inferred evidence cannot claim full confidence.

```python
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1)
    source_text: str = Field(min_length=1, max_length=2000)
    source: Literal["paper", "supplement", "repository", "user", "inferred"]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def inferred_is_not_certain(self) -> "Evidence":
        if self.source == "inferred" and self.confidence >= 1.0:
            raise ValueError("inferred evidence must have confidence below 1.0")
        return self


class PaperElement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "table", "picture", "formula", "caption"]
    page: int | None = Field(default=None, ge=1)
    text: str
    bbox: tuple[float, float, float, float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceBackedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class ReportedMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    reported_value: float | str | None = None
    dataset: str | None = None
    split: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def reported_value_has_evidence(self) -> "ReportedMetric":
        if self.reported_value is not None and not self.evidence:
            raise ValueError("reported_value requires evidence")
        return self


class ParsedPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    file_name: str
    page_count: int
    markdown: str
    elements: list[PaperElement]
    warnings: list[str]


class PaperDossier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    research_problem: str
    task_type: str
    datasets: list[EvidenceBackedFact]
    methods: list[EvidenceBackedFact]
    metrics: list[ReportedMetric]
    gaps: list[str]
```

Export `PaperDossier.model_json_schema()` to `dify/paper-dossier-schema.json`; do not maintain a second handwritten schema.

- [ ] **Step 4: Verify the schema tests and exported schema**

```powershell
python -m pytest tests/test_schemas.py -v
python -c "import json; from pathlib import Path; from paper_parser.schemas import PaperDossier; Path('dify/paper-dossier-schema.json').write_text(json.dumps(PaperDossier.model_json_schema(), ensure_ascii=False, indent=2), encoding='utf-8')"
python -m json.tool dify/paper-dossier-schema.json > $null
```

Expected: schema tests pass and JSON validation exits with code 0.

- [ ] **Step 5: Commit the contracts**

```powershell
git add paper-repro-agent/src/paper_parser/schemas.py paper-repro-agent/tests/test_schemas.py paper-repro-agent/dify/paper-dossier-schema.json
git commit -m "feat: define evidence-preserving paper contracts"
```

### Task 3: Add the Docling parsing boundary

**Files:**
- Create: `paper-repro-agent/src/paper_parser/docling_adapter.py`
- Create: `paper-repro-agent/src/paper_parser/paddle_adapter.py`
- Create: `paper-repro-agent/src/paper_parser/service.py`
- Create: `paper-repro-agent/tests/test_docling_adapter.py`
- Create: `paper-repro-agent/tests/test_paddle_adapter.py`
- Create: `paper-repro-agent/tests/test_service.py`
- Create: `paper-repro-agent/tests/fixtures/minimal-paper.pdf`

**Interfaces:**
- Consumes: `parse_pdf(content: bytes, file_name: str, settings: Settings) -> ParsedPaper`.
- Produces: normalized `ParsedPaper`; Docling- and Paddle-specific types never escape their adapter files.

- [ ] **Step 1: Generate a deterministic two-page PDF fixture**

Create a two-page PDF during test setup with text stating a dataset name on page 1 and `AUC = 0.91` on page 2. Use a small test-only PDF library and commit the resulting fixture so the same bytes are used in local and container tests.

- [ ] **Step 2: Write failing adapter and service tests**

```python
from pathlib import Path

from paper_parser.config import Settings
from paper_parser.service import parse_pdf


def test_parse_pdf_preserves_pages_and_text():
    pdf = Path("tests/fixtures/minimal-paper.pdf").read_bytes()
    result = parse_pdf(pdf, "minimal-paper.pdf", Settings(parser_api_token="x" * 32))
    assert result.page_count == 2
    assert "AUC" in result.markdown
    assert {element.page for element in result.elements} == {1, 2}
```

Add separate tests that reject a non-PDF signature, a file larger than `max_upload_mb`, and a PDF whose page count exceeds `max_pages`. Add a mocked fallback test proving that a page with fewer than 20 extracted non-whitespace characters is sent once to the Paddle adapter and replaced only when the fallback result has more text.

- [ ] **Step 3: Run the tests and confirm the service is missing**

```powershell
python -m pytest tests/test_docling_adapter.py tests/test_paddle_adapter.py tests/test_service.py -v
```

Expected: import failure for `paper_parser.service`.

- [ ] **Step 4: Implement validation and the adapter**

`service.py` must validate `%PDF-` signature and byte limit before calling the adapter. `docling_adapter.py` must create one `DocumentConverter` per process, convert a temporary PDF, export Markdown, and map text/table/picture/formula items to `PaperElement` with a 1-based page. Temporary files must live under a per-request directory and be deleted in `finally`.

`paddle_adapter.py` must expose `parse_pages(pdf_path: Path, pages: list[int]) -> list[PaperElement]`, use the PP-StructureV3 lightweight pipeline, and return only normalized elements for the requested 1-based pages. `service.py` sends a page to this adapter only when Docling extracted fewer than 20 non-whitespace characters or emitted an OCR failure warning. A fallback result replaces the primary page only when it contains more text; otherwise the primary result remains and warning `low_confidence_page:<page>` is added. At most 20 pages may enter fallback in one request; additional pages are reported in warnings rather than consuming unbounded resources.

Use this public boundary only:

```python
def parse_pdf(content: bytes, file_name: str, settings: Settings) -> ParsedPaper:
    """Validate and parse one PDF into a normalized, page-aware document."""
```

If a Docling item has no page provenance, retain it with `page=None` and append `element_without_page_provenance` to `warnings`; never invent a page number.

- [ ] **Step 5: Run parser tests**

```powershell
python -m pytest tests/test_docling_adapter.py tests/test_paddle_adapter.py tests/test_service.py -v
```

Expected: all tests pass. On the first run, Docling model download is allowed and the cache location is recorded.

- [ ] **Step 6: Commit the parser boundary**

```powershell
git add paper-repro-agent/src/paper_parser/docling_adapter.py paper-repro-agent/src/paper_parser/paddle_adapter.py paper-repro-agent/src/paper_parser/service.py paper-repro-agent/tests/test_docling_adapter.py paper-repro-agent/tests/test_paddle_adapter.py paper-repro-agent/tests/test_service.py paper-repro-agent/tests/fixtures/minimal-paper.pdf
git commit -m "feat: parse PDFs with page provenance"
```

### Task 4: Expose the authenticated parser API

**Files:**
- Create: `paper-repro-agent/src/paper_parser/api.py`
- Create: `paper-repro-agent/tests/test_api.py`

**Interfaces:**
- Consumes: multipart field `file` and header `X-Parser-Token`.
- Produces: `GET /healthz -> {"status":"ok"}` and `POST /v1/parse -> ParsedPaper`.

- [ ] **Step 1: Write failing HTTP contract tests**

```python
from fastapi.testclient import TestClient

from paper_parser.api import app


client = TestClient(app)


def test_health_is_public():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_parse_requires_token():
    response = client.post(
        "/v1/parse",
        files={"file": ("paper.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 401
```

Also test a valid mocked parse response, bad PDF as HTTP 422, oversized file as HTTP 413, parser failure as HTTP 500 with a stable error code, and absence of exception traces in responses.

- [ ] **Step 2: Run the API tests and confirm failure**

```powershell
$env:PAPER_PARSER_API_TOKEN='xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
python -m pytest tests/test_api.py -v
```

Expected: import failure for `paper_parser.api`.

- [ ] **Step 3: Implement the API and stable error envelope**

```python
@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/parse", response_model=ParsedPaper)
async def parse_endpoint(
    file: UploadFile,
    token: Annotated[str | None, Header(alias="X-Parser-Token")] = None,
    settings: Settings = Depends(get_settings),
) -> ParsedPaper:
    if token is None or not secrets.compare_digest(token, settings.parser_api_token):
        raise HTTPException(status_code=401, detail={"code": "invalid_token"})
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    return await run_in_threadpool(parse_pdf, content, file.filename or "paper.pdf", settings)
```

Map service validation errors to 413 or 422 and unknown parser exceptions to `{"detail":{"code":"parse_failed"}}`. Log exception details server-side with a request ID, never in the response body.

- [ ] **Step 4: Run all unit tests**

```powershell
$env:PAPER_PARSER_API_TOKEN='xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
python -m pytest -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit the HTTP API**

```powershell
git add paper-repro-agent/src/paper_parser/api.py paper-repro-agent/tests/test_api.py
git commit -m "feat: expose authenticated paper parser API"
```

### Task 5: Containerize and connect the parser to Dify safely

**Files:**
- Create: `paper-repro-agent/Dockerfile`
- Create: `paper-repro-agent/compose.yaml`
- Create: `paper-repro-agent/scripts/check_environment.ps1`
- Create: `paper-repro-agent/scripts/smoke_parse.ps1`
- Create locally, never commit: `paper-repro-agent/.env`
- Modify after backup: `E:\Docker\Projects\dify\docker\.env`

**Interfaces:**
- Consumes: external Docker network `docker_default` and parser secret from local `.env`.
- Produces: internal endpoint `http://paper-parser:8000/v1/parse`, reachable through Dify's SSRF proxy only for the allow-listed service name.

- [ ] **Step 1: Write the environment check script**

The script must fail unless Docker is reachable, `docker-api-1` reports image tag `1.16.0`, network `docker_default` exists, and ports 80/443 are owned by the existing Dify Nginx container. It must print no environment variables or secrets.

- [ ] **Step 2: Run the check before adding the service**

```powershell
.\scripts\check_environment.ps1
```

Expected: Dify checks pass and parser service reports `NOT_DEPLOYED`.

- [ ] **Step 3: Add the container image and Compose service**

`compose.yaml` must contain one service with no host port:

```yaml
services:
  paper-parser:
    build:
      context: .
      target: runtime
    restart: unless-stopped
    env_file: .env
    command: ["uvicorn", "paper_parser.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
    volumes:
      - ./data/jobs:/data/jobs
      - parser-model-cache:/home/app/.cache
    networks:
      dify:
        aliases: [paper-parser]
    mem_limit: 4g
    cpus: 4
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"]
      interval: 30s
      timeout: 5s
      retries: 5

networks:
  dify:
    external: true
    name: docker_default

volumes:
  parser-model-cache:
```

The Dockerfile must use a Python 3.12 slim base and three stages: `base` installs the locked runtime dependencies and required system libraries; `test` adds the locked test extras and source/tests; `runtime` creates a non-root `app` user, copies only application source, owns `/home/app/.cache` and `/data/jobs`, and uses `paper_parser.api:app` as the entry point.

- [ ] **Step 4: Generate the parser token and create local `.env`**

Generate a 32-byte random base64 token. Write it to `paper-repro-agent/.env` as `PAPER_PARSER_API_TOKEN`; do not display the token in terminal output or place it in shell history.

- [ ] **Step 5: Back up and change only the necessary Dify keys**

Create `E:\Docker\Projects\dify\docker\.env.backup-YYYYMMDD-HHMMSS`. In the live `.env`, set:

```dotenv
UPLOAD_FILE_SIZE_LIMIT=50
NGINX_CLIENT_MAX_BODY_SIZE=100M
HTTP_REQUEST_NODE_MAX_BINARY_SIZE=52428800
HTTP_REQUEST_NODE_MAX_TEXT_SIZE=2097152
SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=paper-parser
```

Do not set `SSRF_PROXY_ALLOW_PRIVATE_IPS`; allowing the named service is narrower than allowing the full private subnet.

- [ ] **Step 6: Start the parser and recreate only affected Dify services**

```powershell
docker compose -f .\compose.yaml up -d --build
docker compose -f E:\Docker\Projects\dify\docker\docker-compose.yaml up -d --force-recreate api worker worker_beat ssrf_proxy nginx
```

Expected: existing database, Redis, Weaviate, plugin daemon, and Dify storage volumes remain intact; parser becomes healthy.

- [ ] **Step 7: Verify network access through the same proxy path Dify uses**

Run a public health request from `docker-api-1`, then a proxied request using the container's configured `SSRF_PROXY_HTTP_URL`. Confirm `paper-parser` succeeds while an unlisted private hostname remains blocked. Never print the parser token.

- [ ] **Step 8: Run the PDF smoke request and unit tests inside the image**

```powershell
.\scripts\smoke_parse.ps1 -PdfPath .\tests\fixtures\minimal-paper.pdf
docker build --target test -t paper-parser-test .
docker run --rm --env-file .env paper-parser-test python -m pytest -v
```

Expected: the smoke response contains `page_count: 2`; all tests pass.

- [ ] **Step 9: Commit container and scripts, excluding secrets and Dify backup**

```powershell
git add paper-repro-agent/Dockerfile paper-repro-agent/compose.yaml paper-repro-agent/scripts/check_environment.ps1 paper-repro-agent/scripts/smoke_parse.ps1
git commit -m "feat: deploy parser beside local Dify"
```

### Task 6: Configure the DeepSeek model in Dify

**Files:**
- Create: `paper-repro-agent/docs/configuration-guide.md`

**Interfaces:**
- Consumes: user's DeepSeek API key entered only in the Dify credential UI.
- Produces: one configured DeepSeek chat model available to Workflow LLM nodes.

- [ ] **Step 1: Record the pre-configuration state without secrets**

Open local Dify at `http://localhost`, record Dify version `1.16.0`, and confirm the workspace can open Plugins and Model Providers. Do not capture API keys in screenshots or logs.

- [ ] **Step 2: Install or update the official DeepSeek plugin**

In Dify, open **Plugins → Marketplace**, select the publisher `langgenius` DeepSeek model plugin, install it, and reject similarly named community plugins. If already installed, record its current version and update only after reviewing permissions.

- [ ] **Step 3: Add the credential**

Open **Settings → Model Provider → DeepSeek**, add the API key, retain the official API endpoint, and save. Do not add the key to `.env`, Markdown, screenshots, Dify workflow variables, or source control.

- [ ] **Step 4: Verify a minimal model call**

Create a temporary one-node Workflow using the configured chat model, temperature `0.1`, and prompt `Return exactly: DEEPSEEK_OK`. Test run must return `DEEPSEEK_OK`. Delete the temporary workflow after verification.

- [ ] **Step 5: Document the UI path and recovery checks**

Add to `docs/configuration-guide.md`: plugin publisher verification, credential location, successful test result, and troubleshooting checks for quota, network, plugin daemon logs, and model visibility. Do not include the credential value.

- [ ] **Step 6: Commit the credential-free guide**

```powershell
git add paper-repro-agent/docs/configuration-guide.md
git commit -m "docs: explain DeepSeek configuration in Dify"
```

### Task 7: Build the deterministic paper-dossier Workflow

**Files:**
- Create: `paper-repro-agent/dify/paper-dossier-system-prompt.md`
- Create by Dify export: `paper-repro-agent/dify/paper-dossier-workflow.yml`
- Modify: `paper-repro-agent/docs/configuration-guide.md`

**Interfaces:**
- Consumes: one required PDF file and optional user notes; parser `ParsedPaper` JSON.
- Produces: `PaperDossier` JSON plus a concise Chinese Markdown summary.

- [ ] **Step 1: Write the versioned extraction prompt**

The system prompt must instruct the model to use only the supplied parsed paper, attach evidence to every dataset, method, and reported metric, mark missing facts in `gaps`, preserve the paper's reported values exactly, and never invent page numbers. Include one positive example and one example where the correct answer is `uncertain`.

- [ ] **Step 2: Create a blank Dify Workflow named `论文复现档案`**

Start variables:

- `paper_pdf`: single file, document type, required.
- `user_notes`: paragraph, optional, maximum 2,000 characters.
- `target_language`: select with `简体中文` and `English`, default `简体中文`.

- [ ] **Step 3: Add the parser HTTP Request node**

Configure `POST http://paper-parser:8000/v1/parse`, multipart body field `file = paper_pdf`, and secret header `X-Parser-Token`. Store the token as a Dify secret/credential input, not plain text in the exported DSL. Set connection timeout to 10 seconds and read timeout to 600 seconds. Map HTTP 401, 413, 422, and 500 to clear user-facing error branches.

- [ ] **Step 4: Add a Code node that validates parser response size and shape**

The node must reject missing `page_count`, empty `markdown`, an empty `elements` array, or JSON text larger than 2 MB. Its outputs are `parsed_json`, `parser_warnings`, and `can_continue`. This is validation only; it must not parse the PDF itself.

- [ ] **Step 5: Add the DeepSeek structured-output node**

Use the configured DeepSeek chat model with temperature `0.1`. Supply the versioned system prompt, parser JSON, user notes, and target language. Enable structured output using `paper-dossier-schema.json`. If the provider does not expose native structured output, add a JSON repair branch followed by the same schema validation; allow at most two repair attempts.

- [ ] **Step 6: Add evidence validation and output branches**

A Code node must reject any metric with a `reported_value` but no evidence, any evidence page outside `1..page_count`, and any empty `source_text`. Success output contains the dossier JSON and Chinese Markdown summary. Failure output lists exact invalid JSON paths and asks the user to review the referenced pages.

- [ ] **Step 7: Test the Workflow with the two-page fixture**

Expected assertions:

- PDF accepted and parser called once.
- Dataset statement cites page 1.
- AUC value `0.91` cites page 2.
- No reported metric appears without evidence.
- Output validates against `paper-dossier-schema.json`.

- [ ] **Step 8: Export and sanitize the Workflow DSL**

Export the Dify DSL to `dify/paper-dossier-workflow.yml`. Search the file for the parser token, DeepSeek key, `api_key`, and credential-looking strings. If any secret is embedded, remove it from the workflow credential field in Dify, replace it with a secure credential reference, re-export, and repeat the scan.

- [ ] **Step 9: Commit the Workflow assets**

```powershell
git add paper-repro-agent/dify/paper-dossier-system-prompt.md paper-repro-agent/dify/paper-dossier-workflow.yml paper-repro-agent/docs/configuration-guide.md
git commit -m "feat: add paper dossier Dify workflow"
```

### Task 8: Run acceptance tests on real papers and document operations

**Files:**
- Create: `paper-repro-agent/tests/acceptance/cases.json`
- Create: `paper-repro-agent/docs/acceptance-report.md`
- Modify: `paper-repro-agent/docs/configuration-guide.md`

**Interfaces:**
- Consumes: minimal fixture, current Fengjie landslide PDF, and one formula/table-heavy public paper.
- Produces: evidence accuracy results, resource measurements, known limitations, backup and recovery instructions.

- [ ] **Step 1: Define acceptance cases before running them**

`cases.json` must list expected title, known pages for at least three facts, expected reported metrics, maximum parser duration, and whether OCR is required. Do not derive expectations from the Agent output; verify them manually from the PDF first.

- [ ] **Step 2: Run the fixture case**

Expected: 100% of the two known facts cite the correct page, schema validation passes, and the workflow completes without repair.

- [ ] **Step 3: Run the current Fengjie paper case**

Use `C:\Users\17716\Documents\arcgis\tmp\pdfs\fengjie-paper.pdf`. Manually verify at least ten extracted facts across title, dataset, method, parameter, and metric categories. Record correct, incorrect, missing, and unsupported evidence references separately.

- [ ] **Step 4: Run one formula/table-heavy public paper case**

Verify at least one formula, one table metric, one figure caption, and multi-column reading order. If Docling cannot preserve an item, record the exact page and limitation; do not silently switch the acceptance expectation.

- [ ] **Step 5: Measure host resource use**

During parsing, record peak container memory, total duration, model-cache size, and whether the Dify UI remains responsive. Acceptance limits are parser memory at or below 4 GB, one active parse, and no Dify container restart.

- [ ] **Step 6: Test failure and recovery paths**

Verify wrong token returns 401, oversized file returns 413, corrupt PDF returns 422, parser restart leaves Dify data intact, and restoring the timestamped Dify `.env` backup returns the deployment to its prior configuration.

- [ ] **Step 7: Complete the operator guide**

Document start, stop, health check, logs, cache location, parser-token rotation, DeepSeek credential rotation, Dify workflow import/export, Dify `.env` backup restoration, and the exact point at which the next Docker experiment-executor plan begins.

- [ ] **Step 8: Run final checks**

```powershell
python -m pytest -v
docker compose config --quiet
docker compose ps
git diff --check
git status --short
```

Expected: tests pass, Compose configuration is valid, parser is healthy, no whitespace errors, no secret or runtime files are staged, and unrelated ArcGIS files remain untouched.

- [ ] **Step 9: Commit acceptance evidence**

```powershell
git add paper-repro-agent/tests/acceptance/cases.json paper-repro-agent/docs/acceptance-report.md paper-repro-agent/docs/configuration-guide.md
git commit -m "test: validate paper intelligence foundation"
```

## Completion Gate

This plan is complete only when:

- the existing Dify 1.16.0 deployment remains healthy;
- the official DeepSeek provider passes a minimal call;
- the parser is reachable only through the intended internal path and requires a token;
- a PDF upload produces a schema-valid dossier with page-cited evidence;
- the fixture, Fengjie paper, and formula/table-heavy paper have recorded acceptance results;
- no secrets or unrelated ArcGIS files are committed;
- the operator guide contains configuration, recovery, and credential-rotation instructions.

After this gate, create a separate implementation plan for the asynchronous Docker experiment executor and do not add execution privileges to the parser service.

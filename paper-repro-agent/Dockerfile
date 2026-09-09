FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-parser.lock /tmp/requirements-parser.lock
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    python -m pip install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        --requirement /tmp/requirements-parser.lock

WORKDIR /app


FROM base AS test

COPY requirements-test.lock /tmp/requirements-test.lock
RUN python -m pip install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        --requirement /tmp/requirements-test.lock
# The parser test lock already pins FastAPI, NumPy, pandas, and SciPy.  Add only
# the V2-only packages so its independently pinned PyYAML version is not replaced.
RUN python -m pip install --no-deps \
        joblib==1.5.3 \
        narwhals==2.24.0 \
        scikit-learn==1.9.0 \
        threadpoolctl==3.6.0
COPY pyproject.toml ./
COPY compose.yaml Dockerfile.repro requirements-repro.in requirements-repro.lock ./
COPY src ./src
COPY dify ./dify
COPY tests ./tests
ENV PYTHONPATH=/app/src
CMD ["python", "-m", "pytest", "-v"]


FROM base AS runtime

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /home/app --create-home app \
    && mkdir -p /data/jobs /home/app/.cache \
    && chown -R app:app /data/jobs /home/app/.cache
COPY --chown=app:app src ./src
ENV PYTHONPATH=/app/src
USER app
EXPOSE 8000
CMD ["uvicorn", "paper_parser.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

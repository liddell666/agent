FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-parser.lock /tmp/requirements-parser.lock
RUN python -m pip install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        --requirement /tmp/requirements-parser.lock

WORKDIR /app


FROM base AS test

COPY requirements-test.lock /tmp/requirements-test.lock
RUN python -m pip install \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        --requirement /tmp/requirements-test.lock
COPY pyproject.toml ./
COPY src ./src
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

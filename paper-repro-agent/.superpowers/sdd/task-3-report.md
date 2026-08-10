# Task 3 Report: Dossier parser adapter API

## Status

Completed.

## Commits

- `6d72215` — `feat: expose dossier metric adapter`
- `8b3f41a` — `fix: sanitize dossier parser failure logs`

## Tests and results

- TDD red check: `.venv312\\Scripts\\python.exe -m pytest tests\\repro_runner\\test_api.py -k parse_dossier -q` — 5 failures with the route absent (HTTP 404).
- `.venv312\\Scripts\\python.exe -m pytest tests\\repro_runner\\test_api.py -k parse_dossier -q` — 5 passed.
- `.venv312\\Scripts\\python.exe -m pytest tests\\repro_runner\\test_api.py tests\\repro_runner\\test_compare.py tests\\repro_runner\\test_dossier.py -q` — 78 passed.
- `.venv312\\Scripts\\python.exe -m pytest -q` — 147 passed.
- Review-fix TDD red check: the captured log test failed because `logger.exception` rendered the parser's private text and traceback.
- Review-fix verification: focused dossier API tests — 5 passed; API/compare/dossier regression tests — 78 passed; full suite — 147 passed.

## Changed files

- `src/repro_runner/config.py`
- `src/repro_runner/api.py`
- `tests/repro_runner/test_api.py`

## Concerns

- The full suite retains one third-party Starlette TestClient deprecation warning; no new test failures or endpoint concerns observed.
- Parser failures now log only stable context plus the request ID, without exception details or tracebacks.

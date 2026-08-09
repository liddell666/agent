# Task 3 Report: Dossier parser adapter API

## Status

Completed.

## Commits

- `6d72215` — `feat: expose dossier metric adapter`

## Tests and results

- TDD red check: `.venv312\\Scripts\\python.exe -m pytest tests\\repro_runner\\test_api.py -k parse_dossier -q` — 5 failures with the route absent (HTTP 404).
- `.venv312\\Scripts\\python.exe -m pytest tests\\repro_runner\\test_api.py -k parse_dossier -q` — 5 passed.
- `.venv312\\Scripts\\python.exe -m pytest tests\\repro_runner\\test_api.py tests\\repro_runner\\test_compare.py tests\\repro_runner\\test_dossier.py -q` — 78 passed.
- `.venv312\\Scripts\\python.exe -m pytest -q` — 147 passed.

## Changed files

- `src/repro_runner/config.py`
- `src/repro_runner/api.py`
- `tests/repro_runner/test_api.py`

## Concerns

- The full suite retains one third-party Starlette TestClient deprecation warning; no new test failures or endpoint concerns observed.

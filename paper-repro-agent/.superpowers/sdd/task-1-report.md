# Task 1 Report: Normalize paper metric names and values

- Status: DONE_WITH_CONCERNS
- Implementation commit: `cfb3c0cbb9958d860ca0159865eafb6d9e07c312`
- Focused tests: `.venv312\\Scripts\\python.exe -m pytest tests\\repro_runner\\test_dossier.py -q` — 26 passed.
- Full tests: `.venv312\\Scripts\\python.exe -m pytest -q` — 132 passed, 1 warning.
- Changed files:
  - `src/repro_runner/dossier.py`
  - `tests/repro_runner/test_dossier.py`
  - `.superpowers/sdd/task-1-report.md`
- Concerns: The full suite emits a pre-existing FastAPI/Starlette `TestClient` deprecation warning about `httpx`; it does not affect the Task 1 tests.

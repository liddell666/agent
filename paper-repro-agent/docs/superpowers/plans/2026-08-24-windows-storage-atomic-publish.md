# Windows-Safe Atomic Result Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Windows path-length failures from result persistence while preserving same-filesystem atomic publication and strict duplicate-ID rejection.

**Architecture:** Published experiment directories keep their existing experiment-ID names and JSON contracts. New results are assembled in a short random sibling directory and published with one directory rename; updates to an existing `result.json` use a short random sibling file and one file replacement.

**Tech Stack:** Python 3.12, `pathlib`, `secrets`, `shutil`, `concurrent.futures`, pytest 9, Pydantic models.

## Global Constraints

- Do not change public storage function signatures, final experiment directory names, JSON filenames, or payload schemas.
- A duplicate experiment ID must still fail and must never overwrite the existing result.
- Temporary paths must remain on the same filesystem as their publication target.
- Do not retry permission, disk-space, or path errors.
- Cleanup must never remove another writer's staging path or a published result.
- Do not store raw CSV bytes, paper text, credentials, or arbitrary model attributes.
- Run the final repository suite with the default pytest `basetemp`; do not shorten it through a command-line override.
- Preserve the six pre-existing user DSL modifications without staging or editing them.

---

## File Responsibility Map

- `src/repro_runner/storage.py`: short temporary path construction, direct writes inside unpublished staging directories, atomic directory publication, and atomic published-file update.
- `tests/repro_runner/test_compare.py`: storage regression fixtures and behavioral tests for long paths, failed writes, failed updates, and concurrent duplicate saves.

### Task 1: Shorten Atomic File-Update Paths

**Files:**
- Modify: `src/repro_runner/storage.py:148-160`
- Test: `tests/repro_runner/test_compare.py`

**Interfaces:**
- Preserves: `update_suite_result(result: ExperimentSuiteResult, settings: Settings) -> str`.
- Preserves: `_write_json_atomic(path: Path, payload: object) -> None`.
- Produces: a temporary basename shaped as `.tmp-<16 hex characters>` in `path.parent`.

- [ ] **Step 1: Add imports and a failing update-preservation regression**

Add `Path` to the test imports and import `load_suite_result`,
`save_suite_result`, and `update_suite_result` from `repro_runner.storage`.
Then add this test after the existing storage failure test:

```python
def test_suite_update_uses_short_temp_and_preserves_published_result_on_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = make_suite_result()
    settings = Settings(storage_dir=tmp_path)
    save_suite_result(result, settings)
    result_path = tmp_path / result.experiment_id / "result.json"
    original = result_path.read_bytes()
    replace_sources: list[str] = []
    real_replace = Path.replace

    def fail_result_replacement(source: Path, target: Path) -> Path:
        if target == result_path:
            replace_sources.append(source.name)
            raise PermissionError("simulated locked destination")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_result_replacement)

    with pytest.raises(PermissionError, match="locked destination"):
        update_suite_result(result, settings)

    assert len(replace_sources) == 1
    assert replace_sources[0].startswith(".tmp-")
    assert "result.json" not in replace_sources[0]
    assert result_path.read_bytes() == original
    assert load_suite_result(result.experiment_id, settings) == result
    assert list(result_path.parent.glob(".tmp-*")) == []
```

- [ ] **Step 2: Run the regression and verify RED**

Run:

```powershell
python -m pytest tests/repro_runner/test_compare.py::test_suite_update_uses_short_temp_and_preserves_published_result_on_failure -q
```

Expected: FAIL because the captured source basename starts with
`.result.json.` rather than `.tmp-`.

- [ ] **Step 3: Use a short sibling name in `_write_json_atomic`**

Replace the temporary-path construction only; retain the current replacement
and cleanup semantics:

```python
def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_name(f".tmp-{secrets.token_hex(8)}")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
```

- [ ] **Step 4: Run the update regression and storage compatibility tests**

Run:

```powershell
python -m pytest tests/repro_runner/test_compare.py -k "storage or suite_update" -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the file-update boundary**

```powershell
git add -- src/repro_runner/storage.py tests/repro_runner/test_compare.py
git commit -m "fix: shorten atomic result update paths"
```

### Task 2: Publish New Results Through Short Staging Directories

**Files:**
- Modify: `src/repro_runner/storage.py:36-84,148-160`
- Test: `tests/repro_runner/test_compare.py`

**Interfaces:**
- Preserves: `save_result(result: ExperimentResult, settings: Settings) -> str`.
- Preserves: `save_suite_result(result: ExperimentSuiteResult, settings: Settings) -> str`.
- Produces: `_write_json(path: Path, payload: object) -> None`.
- Produces: `_save_payloads(*, experiment_id: str, settings: Settings, result_payload: object, config_payload: object, dataset_profile_payload: object) -> str`.

- [ ] **Step 1: Add a failing Windows long-path regression**

Add `os` to the test imports. The test deliberately constructs the old nested
temporary path at 262 characters while keeping the proposed short path below
260 characters:

```python
@pytest.mark.skipif(os.name != "nt", reason="Windows path-boundary regression")
def test_storage_operations_succeed_beyond_the_legacy_nested_temp_boundary(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "a" * 16
    single = make_result()
    suite = make_suite_result()
    legacy_tail = (
        Path(f".{single.experiment_id}.{token}.tmp")
        / f".dataset_profile.json.{token}.tmp"
    )
    fixed_length = len(str(tmp_path / "x" / legacy_tail)) - 1
    padding_length = 262 - fixed_length
    assert 1 <= padding_length <= 255
    storage_root = tmp_path / ("x" * padding_length)
    short_tail = Path(f".tmp-{token}") / "dataset_profile.json"

    assert len(str(storage_root / legacy_tail)) == 262
    assert len(str(storage_root / short_tail)) < 260
    monkeypatch.setattr(storage.secrets, "token_hex", lambda _length: token)
    settings = Settings(storage_dir=storage_root)

    assert save_result(single, settings) == single.experiment_id
    assert load_result(single.experiment_id, settings) == single
    assert save_suite_result(suite, settings) == suite.experiment_id
    assert load_suite_result(suite.experiment_id, settings) == suite
    assert update_suite_result(suite, settings) == suite.experiment_id
    assert list(storage_root.glob(".tmp-*")) == []
```

- [ ] **Step 2: Run the long-path regression and verify RED on Windows**

Run with the repository default pytest configuration:

```powershell
python -m pytest tests/repro_runner/test_compare.py::test_storage_operations_succeed_beyond_the_legacy_nested_temp_boundary -q
```

Expected on Windows: FAIL in the old nested temporary path with
`FileNotFoundError`, `PermissionError`, or another path-related `OSError`.

- [ ] **Step 3: Add failing cleanup and concurrency regressions**

Add these imports:

```python
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
```

Then add:

```python
def test_staged_result_failure_cleans_only_its_short_directory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = make_result()
    settings = Settings(storage_dir=tmp_path)
    real_write = storage._write_json
    write_count = 0

    def fail_second_write(path: Path, payload: object) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError("simulated disk full")
        real_write(path, payload)

    monkeypatch.setattr(storage, "_write_json", fail_second_write)

    with pytest.raises(OSError, match="disk full"):
        save_result(result, settings)

    assert not (tmp_path / result.experiment_id).exists()
    assert list(tmp_path.glob(".tmp-*")) == []


def test_concurrent_duplicate_saves_publish_exactly_one_complete_result(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = make_result()
    settings = Settings(storage_dir=tmp_path)
    first_write = Barrier(2)
    real_write = storage._write_json

    def synchronize_first_write(path: Path, payload: object) -> None:
        if path.name == "result.json":
            first_write.wait(timeout=5)
        real_write(path, payload)

    monkeypatch.setattr(storage, "_write_json", synchronize_first_write)
    outcomes: list[object] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save_result, result, settings) for _ in range(2)]
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except OSError as exc:
                outcomes.append(exc)

    assert sum(value == result.experiment_id for value in outcomes) == 1
    assert sum(isinstance(value, OSError) for value in outcomes) == 1
    assert load_result(result.experiment_id, settings) == result
    assert list(tmp_path.glob(".tmp-*")) == []
```

- [ ] **Step 4: Run the two tests and verify RED**

Run:

```powershell
python -m pytest tests/repro_runner/test_compare.py -k "staged_result_failure or concurrent_duplicate_saves" -q
```

Expected: collection or execution fails because `storage._write_json` does not
exist.

- [ ] **Step 5: Add the direct JSON writer and shared payload publisher**

Add the two helpers near `_write_json_atomic(...)`:

```python
def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def _save_payloads(
    *,
    experiment_id: str,
    settings: Settings,
    result_payload: object,
    config_payload: object,
    dataset_profile_payload: object,
) -> str:
    root = settings.storage_dir.resolve()
    directory = _experiment_directory(experiment_id, settings)
    temporary = root / f".tmp-{secrets.token_hex(8)}"
    root.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        raise FileExistsError("experiment result already exists")

    temporary.mkdir()
    try:
        _write_json(temporary / "result.json", result_payload)
        _write_json(temporary / "config.json", config_payload)
        _write_json(temporary / "dataset_profile.json", dataset_profile_payload)
        temporary.replace(directory)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return experiment_id
```

Refactor `_write_json_atomic(...)` to call `_write_json(temporary, payload)` so
the JSON serialization contract has one implementation.

- [ ] **Step 6: Delegate both public save functions to `_save_payloads`**

Replace their duplicated staging logic with:

```python
def save_result(result: ExperimentResult, settings: Settings) -> str:
    """Store only result metadata, configuration, and aggregate dataset profile."""
    experiment_id = _validate_experiment_id(result.experiment_id)
    return _save_payloads(
        experiment_id=experiment_id,
        settings=settings,
        result_payload=_result_payload(result),
        config_payload=_stored_config_payload(result),
        dataset_profile_payload=_dataset_profile_payload(result),
    )


def save_suite_result(result: ExperimentSuiteResult, settings: Settings) -> str:
    """Store only the public suite result contract and aggregate metadata."""
    experiment_id = _validate_experiment_id(result.experiment_id)
    return _save_payloads(
        experiment_id=experiment_id,
        settings=settings,
        result_payload=_suite_result_payload(result),
        config_payload=_stored_suite_config_payload(result),
        dataset_profile_payload=_suite_dataset_profile_payload(result),
    )
```

- [ ] **Step 7: Run the new regressions and storage/API focused suite**

Run:

```powershell
python -m pytest tests/repro_runner/test_compare.py tests/repro_runner/test_api.py tests/repro_runner/test_suite_api.py tests/repro_runner/test_job_runner.py tests/repro_runner/test_job_api.py -q
```

Expected: all tests pass; the Windows long-path regression is executed rather
than skipped on the target machine.

- [ ] **Step 8: Run the complete repository suite with default basetemp**

Run exactly:

```powershell
python -m pytest -q
```

Expected: all tests pass using `pyproject.toml`'s
`--basetemp=.pytest-tmp`; only the existing Starlette/httpx deprecation warning
is allowed.

- [ ] **Step 9: Verify scope and commit the directory publication change**

Run:

```powershell
git diff --check
git status --short
```

Require only `src/repro_runner/storage.py` and
`tests/repro_runner/test_compare.py` in this task's staged diff. The six user
DSL files must remain unstaged.

Then commit:

```powershell
git add -- src/repro_runner/storage.py tests/repro_runner/test_compare.py
git commit -m "fix: shorten atomic result staging paths"
```

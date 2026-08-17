from pathlib import Path
import os
import shutil
import subprocess
import tempfile

import pytest


SCRIPT = Path("scripts/switch_repro_runner.ps1")


def _script_source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _between(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _assert_in_order(source: str, *needles: str) -> None:
    positions = [source.index(needle) for needle in needles]
    assert positions == sorted(positions), f"expected ordered tokens: {needles}"


def test_cutover_script_has_read_only_job_guard_and_reversible_steps() -> None:
    source = _script_source()
    for required in (
        "queued",
        "running",
        "sqlite3",
        "docker stop",
        "docker rename",
        "repro-runner-legacy-",
        "docker network disconnect",
        "docker network connect",
        "docker compose up -d --no-deps repro-runner",
        "/healthz",
        "REPRO_RUNNER_GIT_COMMIT",
        "REPRO_RUNNER_WORKFLOW_VERSION",
        "-Rollback",
    ):
        assert required in source


def test_cutover_script_never_uses_broad_data_deletion() -> None:
    source = _script_source().lower()
    assert "docker volume rm" not in source
    assert "docker system prune" not in source
    assert "remove-item" not in source


def test_cutover_script_validates_image_before_and_container_after_switch() -> None:
    source = _script_source()
    for required in (
        "docker run",
        "Invoke-RestMethod",
        "source_digest",
        "expectedCommit",
        "workflow_version",
        "/data/experiments",
        "Aliases",
        "127.0.0.1",
    ):
        assert required in source


def test_forward_cutover_uses_a_fresh_compose_project_after_renaming_runner() -> None:
    source = _script_source()
    cutover = source[source.index("$legacyName=$null") :]

    assert "$cutoverProjectName = \"repro-runner-cutover-$(Get-Date -Format yyyyMMdd-HHmmss)-$PID\"" in cutover
    assert (
        'Invoke-DockerChecked -Arguments (@("compose", "-p", $cutoverProjectName) + '
        '$composeFileArguments + @("up", "-d", "--no-build", "--no-deps", "repro-runner"))'
    ) in cutover
    _assert_in_order(
        cutover,
        'Invoke-DockerChecked @("rename", $ContainerName, $legacyName)',
        "$cutoverProjectName =",
        'Invoke-DockerChecked -Arguments (@("compose", "-p", $cutoverProjectName)',
    )


def test_external_data_source_override_pins_the_built_runner_image() -> None:
    override = Path("compose.runner-data-source.yaml").read_text(encoding="utf-8")
    assert "image: paper-repro-agent-repro-runner" in override


def test_rollback_requires_no_active_jobs_and_restores_alias() -> None:
    source = _script_source()
    assert "Sort-Object Name -Descending" in source
    assert "docker rm -f" in source
    assert "docker rename" in source
    assert "--alias" in source
    assert "Assert-NoActiveJobs" in source


def test_image_resolution_handles_compose_without_an_existing_container() -> None:
    source = _script_source()
    resolver = _between(
        source,
        "function Resolve-ReproRunnerImageId {",
        "function Get-RunnerContainerJson {",
    )

    _assert_in_order(
        resolver,
        'Invoke-ComposeChecked -Arguments @("images", "-q", "repro-runner")',
        'Invoke-ComposeChecked -Arguments @("config", "--images")',
        'docker image inspect --format "{{.Id}}"',
    )
    assert "Select-Object -Unique" in resolver
    assert r"(^|[\/_-])repro-runner($|[:@])" in resolver
    assert "$runnerImages.Count -ne 1" in resolver
    assert '$composeImageId -match "^sha256:"' in resolver
    assert '$LASTEXITCODE -eq 0 -and $inspectedImageId -match "^sha256:"' in resolver
    cutover = source[source.index("$imageId = Resolve-ReproRunnerImageId") :]
    _assert_in_order(
        cutover,
        'Invoke-DockerChecked @("stop", $ContainerName)',
        'Invoke-DockerChecked @("rename", $ContainerName, $legacyName)',
    )


def test_forward_cutover_preserves_experiment_data_mount_source() -> None:
    source = _script_source()
    compose_resolver = _between(
        source,
        "function Get-ComposeExperimentDataSource {",
        "function Get-ActiveJobs {",
    )
    assert 'Invoke-ComposeChecked -Arguments @("config", "--format", "json")' in compose_resolver
    assert "ConvertFrom-Json" in compose_resolver
    assert "target -eq \"/data/experiments\"" in compose_resolver
    assert "IsNullOrWhiteSpace" in compose_resolver
    assert "Normalize-HostPath" in compose_resolver
    assert ".source" in compose_resolver
    assert "return Normalize-HostPath -Path $composeDataMount.source" in compose_resolver
    assert "if ($composeDataMounts.Count -ne 1)" in compose_resolver
    mount_guard = source[
        source.index("$oldDataMounts = @(") : source.index(
            'Invoke-DockerChecked @("rename", $ContainerName, $legacyName)'
        )
    ]
    assert "/data/experiments" in mount_guard
    assert "cutover aborted" in mount_guard
    assert "if ($oldDataMounts.Count -ne 1)" in mount_guard
    assert "$expectedDataSource = Get-ComposeExperimentDataSource" in mount_guard
    assert "$actualDataSource = Normalize-HostPath -Path $oldDataMount.Source" in mount_guard
    assert "[System.StringComparison]::OrdinalIgnoreCase" in mount_guard
    assert "[string]::Equals($actualDataSource, $expectedDataSource" in mount_guard
    _assert_in_order(
        mount_guard,
        "$oldDataMounts = @(",
        "if ($oldDataMounts.Count -ne 1)",
        "$oldDataMount = $oldDataMounts[0]",
        "$expectedDataSource = Get-ComposeExperimentDataSource",
        "$actualDataSource = Normalize-HostPath -Path $oldDataMount.Source",
        "if (-not [string]::Equals($actualDataSource, $expectedDataSource",
        'Invoke-DockerChecked @("stop", $ContainerName)',
    )
    assert (
        "if (-not [string]::Equals($actualDataSource, $expectedDataSource, [System.StringComparison]::OrdinalIgnoreCase)) {\n"
        '    throw "runner data mount cutover aborted:'
    ) in mount_guard


def test_active_job_guard_uses_effective_job_store_path() -> None:
    source = _script_source()
    assert "function Get-RunnerJobStorePath" in source
    assert "REPRO_RUNNER_JOB_STORE_PATH" in source
    store_resolver = _between(
        source,
        "function Get-RunnerJobStorePath {",
        "function Get-ActiveJobs {",
    )
    assert "Config.Env" in store_resolver
    assert "/data/experiments/jobs.sqlite3" in store_resolver
    assert "[-1]" in store_resolver
    assert "IsNullOrWhiteSpace($jobStorePath)" in store_resolver
    active_jobs = _between(
        source,
        "function Get-ActiveJobs {",
        "function Assert-NoActiveJobs {",
    )
    assert "$jobStorePath = Get-RunnerJobStorePath" in active_jobs
    assert "sys.argv[1]" in active_jobs
    assert "Invoke-DockerPython -Container $Name -Source $query -Arguments @($jobStorePath)" in active_jobs


def test_active_job_guard_transports_python_source_safely_for_windows_powershell() -> None:
    source = _script_source()
    active_jobs = _between(
        source,
        "function Get-ActiveJobs {",
        "function Assert-NoActiveJobs {",
    )

    assert "function Invoke-DockerPython" in source
    assert "[Convert]::ToBase64String" in source
    assert "base64.b64decode" in source
    assert "Invoke-DockerPython -Container $Name -Source $query -Arguments @($jobStorePath)" in active_jobs
    assert '"python", "-c", $query, $jobStorePath' not in active_jobs


def test_checked_docker_accepts_successful_stderr_under_windows_powershell_5_1() -> None:
    source = _script_source()
    checked = _between(
        source,
        "function Invoke-DockerChecked {",
        "function Invoke-DockerPython {",
    )
    assert "$previousErrorActionPreference" in checked
    assert '$ErrorActionPreference = "Continue"' in checked
    assert "$ErrorActionPreference = $previousErrorActionPreference" in checked
    command = f'''if ($PSVersionTable.PSVersion.Major -ne 5) {{
    throw "regression must execute under Windows PowerShell 5.1"
}}
{checked}
'''

    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the native stderr compatibility regression")
    image_check = subprocess.run(
        ["docker", "image", "inspect", "paper-repro-agent-repro-runner"],
        check=False,
        capture_output=True,
        text=True,
    )
    if image_check.returncode != 0:
        pytest.skip("the locally built runner image is required for the native stderr compatibility regression")

    with tempfile.TemporaryDirectory() as compose_dir:
        compose_file = Path(compose_dir, "compose.yaml")
        compose_file.write_text(
            "services:\n"
            "  stderr-probe:\n"
            "    image: paper-repro-agent-repro-runner\n"
            "    command: [\"sh\", \"-c\", \"sleep 2\"]\n",
            encoding="utf-8",
        )
        project_name = f"runner-ps51-stderr-{os.getpid()}"
        escaped_compose_file = str(compose_file).replace("'", "''")
        command += (
            f"Invoke-DockerChecked -Arguments @('compose', '-p', '{project_name}', "
            f"'-f', '{escaped_compose_file}', 'up', '-d') | Out-Null\n"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                check=False,
                capture_output=True,
                text=True,
            )
        finally:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    project_name,
                    "-f",
                    str(compose_file),
                    "down",
                    "--remove-orphans",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

    assert result.returncode == 0 and not result.stderr, result.stderr


def test_explicit_data_source_is_validated_before_build() -> None:
    source = _script_source()
    for required in (
        "ExperimentDataSource",
        "Resolve-ExperimentDataSource",
        "REPRO_RUNNER_CUTOVER_DATA_SOURCE",
        "compose.runner-data-source.yaml",
        "ComposeOverrideFile",
        "composeFileArguments",
        "GetPathRoot",
        "PSIsContainer",
    ):
        assert required in source
    assert "IsPathFullyQualified" not in source


def test_compose_wrapper_uses_effective_file_arguments() -> None:
    source = _script_source()
    wrapper = _between(source, "function Invoke-ComposeChecked {", "function Resolve-ReproRunnerImageId {")
    assert "($composeFileArguments + $Arguments)" in wrapper


def test_normalize_host_path_preserves_filesystem_roots() -> None:
    source = _script_source()
    normalizer = _between(
        source,
        "function Normalize-HostPath {",
        "function Resolve-ExperimentDataSource {",
    )
    command = f'''{normalizer}
$roots = @("C:\\", "/")
foreach ($root in $roots) {{
    $expected = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($root))
    $actual = Normalize-HostPath -Path $root
    if ($actual -ne $expected) {{
        throw "normalized root path was malformed"
    }}
}}
'''
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_resolve_experiment_data_source_runs_under_windows_powershell_5_1() -> None:
    source = _script_source()
    normalize_and_resolve = (
        _between(
            source,
            "function Normalize-HostPath {",
            "function Resolve-ExperimentDataSource {",
        )
        + _between(
            source,
            "function Resolve-ExperimentDataSource {",
            "function Get-ComposeExperimentDataSource {",
        )
    )
    command = f'''if ($PSVersionTable.PSVersion.Major -ne 5) {{
    throw "regression must execute under Windows PowerShell 5.1"
}}
{normalize_and_resolve}
$resolved = Resolve-ExperimentDataSource -Path $env:CUTOVER_TEST_DATA_SOURCE
$expected = [System.IO.Path]::GetFullPath($env:CUTOVER_TEST_DATA_SOURCE)
if ($resolved -ne $expected) {{
    throw "resolved experiment data source was not normalized correctly"
}}
'''
    with tempfile.TemporaryDirectory() as data_source:
        environment = os.environ.copy()
        environment["CUTOVER_TEST_DATA_SOURCE"] = data_source
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    assert result.returncode == 0 and not result.stderr, result.stderr


def test_configuration_guide_documents_explicit_data_source_cutover() -> None:
    guide = Path("docs/configuration-guide.md").read_text(encoding="utf-8")
    assert "-ExperimentDataSource" in guide
    assert "paper-repro-agent\\data\\experiments" in guide
    assert "does not copy" in guide.lower()
    assert "does not delete" in guide.lower()

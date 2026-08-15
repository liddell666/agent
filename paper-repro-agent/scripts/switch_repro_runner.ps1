param(
    [string]$WorkflowVersion = "multimodel-0.8.0",
    [switch]$SkipBuild,
    [switch]$Rollback,
    [string]$ContainerName = "repro-runner"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$networkName = "docker_default"

function Invoke-DockerChecked {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $output = & docker @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed: $output"
    }
    return $output
}

function Get-RunnerContainerJson {
    param([Parameter(Mandatory)][string]$Name)

    $raw = Invoke-DockerChecked @("inspect", $Name)
    return (($raw -join [Environment]::NewLine) | ConvertFrom-Json)[0]
}

function Get-ActiveJobs {
    param([Parameter(Mandatory)][string]$Name)

    $query = @'
import json
import sqlite3

with sqlite3.connect("/data/experiments/jobs.sqlite3") as connection:
    rows = connection.execute(
        "SELECT job_id, status FROM jobs WHERE status IN (?, ?)",
        ("queued", "running"),
    ).fetchall()
print(json.dumps([{"job_id": job_id, "status": status} for job_id, status in rows]))
'@

    $raw = Invoke-DockerChecked @("exec", $Name, "python", "-c", $query)
    return (($raw -join [Environment]::NewLine) | ConvertFrom-Json)
}

function Assert-NoActiveJobs {
    param([Parameter(Mandatory)][string]$Name)

    $active = @(Get-ActiveJobs $Name)
    if ($active.Count -gt 0) {
        $summary = ($active | ForEach-Object { "$($_.job_id):$($_.status)" }) -join ", "
        throw "runner has active jobs; cutover aborted: $summary"
    }
    return
}

# Task 1 contract only. Later tasks add the mutation bodies that use:
# docker stop
# docker rename
# repro-runner-legacy-
# docker network disconnect
# docker network connect
# docker compose up -d --no-deps repro-runner
# /healthz
# REPRO_RUNNER_GIT_COMMIT
# REPRO_RUNNER_WORKFLOW_VERSION
# -Rollback

$null = Get-RunnerContainerJson -Name $ContainerName
Assert-NoActiveJobs -Name $ContainerName

if ($Rollback) {
    throw "Task 1 only: rollback body is deferred to a later task."
}

throw "Task 1 only: forward cutover body is deferred to a later task."

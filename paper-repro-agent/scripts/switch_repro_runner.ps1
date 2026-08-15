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

function Invoke-ComposeChecked {
    param([Parameter(Mandatory)][string[]]$Arguments)

    Push-Location $projectRoot
    try {
        return Invoke-DockerChecked -Arguments (@("compose") + $Arguments)
    } finally {
        Pop-Location
    }
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

function Wait-RunnerHealth {
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$ExpectedCommit,
        [Parameter(Mandatory)][string]$ExpectedWorkflowVersion,
        [Parameter(Mandatory)][string]$Context,
        [int]$MaxAttempts = 30,
        [int]$DelaySeconds = 1
    )

    $health = $null
    for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri $Url
            if (
                $health.status -eq "ok" -and
                $health.git_commit -eq $ExpectedCommit -and
                $health.workflow_version -eq $ExpectedWorkflowVersion -and
                $health.source_digest -match "^sha256:[0-9a-f]{64}$"
            ) {
                return $health
            }
        } catch {
            $health = $null
        }

        Start-Sleep -Seconds $DelaySeconds
    }

    throw "$Context failed provenance health check."
}

function Disconnect-RunnerNetworkIfPresent {
    param(
        [Parameter(Mandatory)][string]$NetworkName,
        [Parameter(Mandatory)][string]$Container
    )

    $output = & docker network disconnect $NetworkName $Container 2>&1
    if ($LASTEXITCODE -eq 0) {
        return
    }

    $detail = ($output -join [Environment]::NewLine)
    if ($detail -match "is not connected to network") {
        return
    }

    throw "docker network disconnect failed for $Container on ${NetworkName}: $detail"
}

function Restore-RunnerState {
    param(
        [Parameter(Mandatory)][string]$Container,
        [Parameter(Mandatory)][string]$LegacyName,
        [Parameter(Mandatory)][string]$NetworkName,
        [Parameter(Mandatory)][bool]$OldStopped,
        [Parameter(Mandatory)][bool]$OldRenamed,
        [Parameter(Mandatory)][bool]$ReplacementMayExist
    )

    if ($ReplacementMayExist) {
        & docker rm -f $Container 2>$null | Out-Null
    }

    if ($OldRenamed) {
        Invoke-DockerChecked @("rename", $LegacyName, $Container) | Out-Null

        $restored = Get-RunnerContainerJson -Name $Container
        $restoredNetwork = $restored.NetworkSettings.Networks.$NetworkName
        if ($null -eq $restoredNetwork) {
            Invoke-DockerChecked @("network", "connect", "--alias", "repro-runner", $NetworkName, $Container) | Out-Null
        } elseif (@($restoredNetwork.Aliases) -notcontains "repro-runner") {
            Invoke-DockerChecked @("network", "disconnect", $NetworkName, $Container) | Out-Null
            Invoke-DockerChecked @("network", "connect", "--alias", "repro-runner", $NetworkName, $Container) | Out-Null
        }

        Invoke-DockerChecked @("start", $Container) | Out-Null
        return
    }

    if ($OldStopped) {
        Invoke-DockerChecked @("start", $Container) | Out-Null
    }
}

# The forward cutover mutates only after preflight succeeds:
# docker stop
# docker rename
# repro-runner-legacy-
# docker network disconnect
# docker network connect
# docker compose up -d --no-deps repro-runner
# docker run
# /healthz
# REPRO_RUNNER_GIT_COMMIT
# REPRO_RUNNER_WORKFLOW_VERSION
# -Rollback

if ($Rollback) {
    throw "Rollback is deferred to Task 3. Task 2 implements forward cutover only."
}

$expectedCommit = (& git -C $projectRoot rev-parse HEAD 2>$null).Trim()
if ($expectedCommit -notmatch "^[0-9a-f]{7,64}$") {
    throw "could not resolve a valid Git commit"
}

if (-not $SkipBuild) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot "scripts/build_repro_runner.ps1") -WorkflowVersion $WorkflowVersion
    if ($LASTEXITCODE -ne 0) {
        throw "runner image build failed"
    }
}

$imageId = (Invoke-ComposeChecked -Arguments @("images", "-q", "repro-runner") | Select-Object -Last 1).Trim()
if ($imageId -notmatch "^sha256:") {
    throw "Compose did not produce a runner image"
}

$smokeName = "repro-runner-provenance-$PID"
$smokePort = 18081
try {
    Invoke-DockerChecked @("run", "-d", "--name", $smokeName, "-p", "127.0.0.1:$($smokePort):8001", $imageId) | Out-Null
    $smokeContainer = Get-RunnerContainerJson -Name $smokeName
    $smokeNetworks = @($smokeContainer.NetworkSettings.Networks.PSObject.Properties.Name)
    if ($smokeNetworks -contains $networkName) {
        throw "smoke container must not be attached to $networkName"
    }

    $smokeHealth = Wait-RunnerHealth -Url "http://127.0.0.1:$($smokePort)/healthz" -ExpectedCommit $expectedCommit -ExpectedWorkflowVersion $WorkflowVersion -Context "new runner image" -MaxAttempts 30 -DelaySeconds 1
} finally {
    & docker rm -f $smokeName 2>$null | Out-Null
}

$null = Get-RunnerContainerJson -Name $ContainerName
Assert-NoActiveJobs -Name $ContainerName

$legacyName = "repro-runner-legacy-$(Get-Date -Format yyyyMMdd-HHmmss)"
$oldStopped = $false
$oldRenamed = $false
$replacementMayExist = $false
$cutoverHealth = $null

try {
    Invoke-DockerChecked @("stop", $ContainerName) | Out-Null
    $oldStopped = $true

    Invoke-DockerChecked @("rename", $ContainerName, $legacyName) | Out-Null
    $oldRenamed = $true

    Disconnect-RunnerNetworkIfPresent -NetworkName $networkName -Container $legacyName

    $replacementMayExist = $true
    Invoke-ComposeChecked -Arguments @("up", "-d", "--no-deps", "repro-runner") | Out-Null

    $cutoverHealth = Wait-RunnerHealth -Url "http://127.0.0.1:8001/healthz" -ExpectedCommit $expectedCommit -ExpectedWorkflowVersion $WorkflowVersion -Context "active runner" -MaxAttempts 60 -DelaySeconds 2

    $active = Get-RunnerContainerJson -Name $ContainerName
    $mounts = @($active.Mounts | ForEach-Object { $_.Destination })
    if ($mounts -notcontains "/data/experiments") {
        throw "active runner lost /data/experiments mount"
    }

    $network = $active.NetworkSettings.Networks.$networkName
    if ($null -eq $network) {
        throw "active runner is not attached to $networkName"
    }

    $aliases = @($network.Aliases)
    if ($aliases -notcontains "repro-runner") {
        throw "active runner lost repro-runner network alias"
    }
} catch {
    $failure = $_
    try {
        Restore-RunnerState -Container $ContainerName -LegacyName $legacyName -NetworkName $networkName -OldStopped $oldStopped -OldRenamed $oldRenamed -ReplacementMayExist $replacementMayExist
    } catch {
        Write-Warning "Cutover restoration encountered an additional error while attempting to restore the legacy runner."
    }
    throw $failure
}

Write-Host "Runner cutover succeeded."
Write-Host "Status: $($cutoverHealth.status)"
Write-Host "Git commit: $($cutoverHealth.git_commit)"
Write-Host "Workflow version: $($cutoverHealth.workflow_version)"
Write-Host "Source digest: $($cutoverHealth.source_digest)"
Write-Host "Legacy container retained as $legacyName"

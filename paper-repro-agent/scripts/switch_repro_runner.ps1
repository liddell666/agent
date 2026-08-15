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

function Resolve-ReproRunnerImageId {
    $composeImageId = "$((Invoke-ComposeChecked -Arguments @("images", "-q", "repro-runner") | Select-Object -Last 1))".Trim()
    if ($composeImageId -match "^sha256:") {
        return $composeImageId
    }

    $imageNames = @(
        Invoke-ComposeChecked -Arguments @("config", "--images") |
            ForEach-Object { "$_".Trim() } |
            Where-Object { $_ } |
            Select-Object -Unique
    )
    $runnerImages = @(
        $imageNames | Where-Object { $_ -match '(^|[\/_-])repro-runner($|[:@])' }
    )
    if ($runnerImages.Count -ne 1) {
        throw "Compose did not produce a runner image"
    }

    $inspectedImageId = "$((& docker image inspect --format "{{.Id}}" $runnerImages[0] 2>$null | Select-Object -Last 1))".Trim()
    if ($LASTEXITCODE -eq 0 -and $inspectedImageId -match "^sha256:") {
        return $inspectedImageId
    }

    throw "Compose did not produce a runner image"
}

function Get-RunnerContainerJson {
    param([Parameter(Mandatory)][string]$Name)

    $raw = Invoke-DockerChecked @("inspect", $Name)
    return (($raw -join [Environment]::NewLine) | ConvertFrom-Json)[0]
}

function Normalize-HostPath {
    param([Parameter(Mandatory)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Get-ComposeExperimentDataSource {
    $composeConfigRaw = Invoke-ComposeChecked -Arguments @("config", "--format", "json")
    $composeConfig = (($composeConfigRaw -join [Environment]::NewLine) | ConvertFrom-Json)
    $composeDataMounts = @(
        $composeConfig.services.'repro-runner'.volumes |
            Where-Object { $_.target -eq "/data/experiments" }
    )
    if ($composeDataMounts.Count -ne 1) {
        throw "runner data mount cutover aborted: expected exactly one /data/experiments volume in compose config"
    }

    $composeDataMount = $composeDataMounts[0]
    if ([string]::IsNullOrWhiteSpace($composeDataMount.source)) {
        throw "runner data mount cutover aborted: compose /data/experiments volume is missing a source"
    }

    return Normalize-HostPath -Path $composeDataMount.source
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

function Connect-RunnerNetworkAlias {
    param(
        [Parameter(Mandatory)][string]$NetworkName,
        [Parameter(Mandatory)][string]$Container
    )

    $output = & docker network connect --alias repro-runner $NetworkName $Container 2>&1
    if ($LASTEXITCODE -eq 0) {
        return
    }

    $detail = ($output -join [Environment]::NewLine)
    if ($detail -notmatch "already exists in network|is already connected to network") {
        throw "docker network connect failed for $Container on $NetworkName"
    }

    $connected = Get-RunnerContainerJson -Name $Container
    $connectedNetwork = $connected.NetworkSettings.Networks.$NetworkName
    if ($null -ne $connectedNetwork -and @($connectedNetwork.Aliases) -contains "repro-runner") {
        return
    }

    Disconnect-RunnerNetworkIfPresent -NetworkName $NetworkName -Container $Container
    Invoke-DockerChecked @("network", "connect", "--alias", "repro-runner", $NetworkName, $Container) | Out-Null
}

function Wait-RollbackHealth {
    param(
        [Parameter(Mandatory)][string]$Url,
        [int]$MaxAttempts = 60,
        [int]$DelaySeconds = 2
    )

    for ($attempt = 0; $attempt -lt $MaxAttempts; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri $Url
            if ($health.status -eq "ok") {
                return $health
            }
        } catch {
            $health = $null
        }

        Start-Sleep -Seconds $DelaySeconds
    }

    throw "restored runner failed health check."
}

function Restore-RollbackLegacy {
    param(
        [Parameter(Mandatory)][string]$Container,
        [Parameter(Mandatory)][string]$LegacyName,
        [Parameter(Mandatory)][string]$NetworkName,
        [Parameter(Mandatory)][bool]$LegacyRenamed
    )

    $restoreErrors = 0
    if (-not $LegacyRenamed) {
        try {
            Invoke-DockerChecked @("rename", $LegacyName, $Container) | Out-Null
            $LegacyRenamed = $true
        } catch {
            $restoreErrors++
        }
    }

    if ($LegacyRenamed) {
        try {
            Connect-RunnerNetworkAlias -NetworkName $NetworkName -Container $Container
        } catch {
            $restoreErrors++
        }

        try {
            $restored = Get-RunnerContainerJson -Name $Container
            if (-not [bool]$restored.State.Running) {
                Invoke-DockerChecked @("start", $Container) | Out-Null
            }
        } catch {
            $restoreErrors++
        }
    }

    if ($restoreErrors -gt 0) {
        throw "legacy runner restoration was incomplete."
    }
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
    $legacy = @(
        Invoke-DockerChecked @("ps", "-a", "--format", "{{.Names}}") |
            ForEach-Object { [pscustomobject]@{ Name = ([string]$_).Trim() } } |
            Where-Object { $_.Name -like "repro-runner-legacy-*" } |
            Sort-Object Name -Descending |
            Select-Object -First 1
    )
    if ($legacy.Count -eq 0 -or [string]::IsNullOrWhiteSpace($legacy[0].Name)) {
        throw "no retained legacy runner is available for rollback."
    }

    $rollbackLegacyName = $legacy[0].Name
    if ($rollbackLegacyName -eq $ContainerName) {
        throw "the selected legacy runner cannot also be the current runner."
    }

    $null = Get-RunnerContainerJson -Name $rollbackLegacyName
    $null = Get-RunnerContainerJson -Name $ContainerName
    Assert-NoActiveJobs -Name $ContainerName

    $currentStopped = $false
    $replacementRemoved = $false
    $legacyRenamed = $false
    $rollbackHealth = $null

    try {
        Invoke-DockerChecked @("stop", $ContainerName) | Out-Null
        $currentStopped = $true

        Invoke-DockerChecked @("rm", "-f", $ContainerName) | Out-Null
        $replacementRemoved = $true

        Invoke-DockerChecked @("rename", $rollbackLegacyName, $ContainerName) | Out-Null
        $legacyRenamed = $true

        Connect-RunnerNetworkAlias -NetworkName $networkName -Container $ContainerName

        $restored = Get-RunnerContainerJson -Name $ContainerName
        if (-not [bool]$restored.State.Running) {
            Invoke-DockerChecked @("start", $ContainerName) | Out-Null
        }

        $rollbackHealth = Wait-RollbackHealth -Url "http://127.0.0.1:8001/healthz" -MaxAttempts 60 -DelaySeconds 2
    } catch {
        $failure = $_
        if ($replacementRemoved) {
            try {
                Restore-RollbackLegacy -Container $ContainerName -LegacyName $rollbackLegacyName -NetworkName $networkName -LegacyRenamed $legacyRenamed
            } catch {
                Write-Warning "Rollback restoration encountered an additional error while restoring the legacy runner."
            }
        } elseif ($currentStopped) {
            try {
                Invoke-DockerChecked @("start", $ContainerName) | Out-Null
            } catch {
                Write-Warning "Rollback restoration encountered an additional error while restarting the current runner."
            }
        }
        throw $failure
    }

    Write-Host "Status: $($rollbackHealth.status)"
    Write-Host "Service version: $($rollbackHealth.service_version)"
    Write-Host "Git commit: $($rollbackHealth.git_commit)"
    Write-Host "Source digest: $($rollbackHealth.source_digest)"
    Write-Host "Workflow version: $($rollbackHealth.workflow_version)"
    exit 0
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

$imageId = Resolve-ReproRunnerImageId

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

$oldRunner = Get-RunnerContainerJson -Name $ContainerName
Assert-NoActiveJobs -Name $ContainerName

$oldDataMounts = @($oldRunner.Mounts | Where-Object { $_.Destination -eq "/data/experiments" })
if ($oldDataMounts.Count -ne 1) {
    throw "runner data mount cutover aborted: expected exactly one /data/experiments mount on $ContainerName"
}

$oldDataMount = $oldDataMounts[0]
$expectedDataSource = Get-ComposeExperimentDataSource
$actualDataSource = Normalize-HostPath -Path $oldDataMount.Source
if (-not [string]::Equals($actualDataSource, $expectedDataSource, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "runner data mount cutover aborted: live /data/experiments source '$actualDataSource' does not match expected '$expectedDataSource'"
}

$legacyName=$null
$oldStopped = $false
$oldRenamed = $false
$replacementMayExist = $false
$cutoverHealth = $null

try {
    Invoke-DockerChecked @("stop", $ContainerName) | Out-Null
    $oldStopped = $true

    $legacyName = "repro-runner-legacy-$(Get-Date -Format yyyyMMdd-HHmmss)"
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

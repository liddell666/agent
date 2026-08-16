param(
    [string]$WorkflowVersion = "multimodel-0.8.0",
    [string]$ComposeOverrideFile = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$commit = "unknown"

try {
    $candidate = (& git -C $projectRoot rev-parse HEAD 2>$null).Trim()
    if ($candidate -match "^[0-9a-f]{7,64}$") {
        $commit = $candidate
    }
} catch {
    $commit = "unknown"
}

$env:REPRO_RUNNER_GIT_COMMIT = $commit
$env:REPRO_RUNNER_WORKFLOW_VERSION = $WorkflowVersion
$composeArguments = @()
if (-not [string]::IsNullOrWhiteSpace($ComposeOverrideFile)) {
    $overridePath = (Resolve-Path -LiteralPath $ComposeOverrideFile -ErrorAction Stop).Path
    $composeArguments = @(
        "-f",
        (Join-Path $projectRoot "compose.yaml"),
        "-f",
        $overridePath
    )
}

Push-Location $projectRoot
try {
    & docker compose @composeArguments build repro-runner
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose build repro-runner failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

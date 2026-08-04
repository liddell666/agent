param(
    [Parameter(Mandatory = $true)]
    [string]$PdfPath
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
$clientPath = Join-Path $PSScriptRoot "smoke_client.py"
$resolvedPdf = (Resolve-Path -LiteralPath $PdfPath).Path
if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Local .env is missing. Run scripts/init_local_env.ps1 first."
}

$dockerArgs = @(
    "run"
    "--rm"
    "--network", "docker_default"
    "--env-file", $envPath
    "--volume", "${resolvedPdf}:/input.pdf:ro"
    "--volume", "${clientPath}:/smoke_client.py:ro"
    "python:3.12-slim"
    "python"
    "/smoke_client.py"
)
$response = & docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Parser smoke request failed."
}

$parsed = $response | ConvertFrom-Json
if ($parsed.page_count -ne 2) {
    throw "Expected fixture page_count 2, received $($parsed.page_count)."
}
Write-Host "OK: parser returned page_count 2 with $($parsed.element_count) elements."

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
if (Test-Path -LiteralPath $envPath) {
    throw "Local .env already exists; refusing to overwrite its secret."
}

function New-LocalToken {
$bytes = [byte[]]::new(32)
$generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $generator.GetBytes($bytes)
} finally {
    $generator.Dispose()
}
return [Convert]::ToBase64String($bytes)
}
$token = New-LocalToken
$extractorToken = New-LocalToken
$protocolSecret = New-LocalToken
$content = @(
    "PAPER_PARSER_API_TOKEN=$token"
    "PAPER_PARSER_MAX_UPLOAD_MB=50"
    "PAPER_PARSER_MAX_PAGES=400"
    "PAPER_PARSER_MAX_CONCURRENT_JOBS=1"
    "PAPER_PARSER_WORK_DIR=/data/jobs"
    "PAPER_DOSSIER_EXTRACTOR_API_TOKEN=$extractorToken"
    "PAPER_DOSSIER_EXTRACTOR_OLLAMA_BASE_URL=http://ollama:11434"
    "REPRO_RUNNER_PROTOCOL_SECRET=$protocolSecret"
    "REPRO_RUNNER_MAX_UPLOAD_MB=100"
    "REPRO_RUNNER_MAX_COLUMNS=256"
    "REPRO_RUNNER_DEFAULT_TARGET_COLUMN=Y_cls"
    "REPRO_RUNNER_STORAGE_DIR=/data/experiments"
    "REPRO_RUNNER_JOB_STORE_PATH=/data/experiments/jobs.sqlite3"
    "REPRO_RUNNER_JOB_WORK_DIR=/data/experiments/jobs"
    "REPRO_RUNNER_MAX_CONCURRENT_EXPERIMENTS=1"
    "REPRO_RUNNER_WORKFLOW_VERSION=multimodel-0.8.0"
) -join [Environment]::NewLine

[System.IO.File]::WriteAllText($envPath, $content + [Environment]::NewLine)
Write-Host "Created local .env for all three services (secrets not displayed). Configure the matching parser, extractor and protocol secrets in Dify."

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
if (Test-Path -LiteralPath $envPath) {
    throw "Local .env already exists; refusing to overwrite its secret."
}

$bytes = [byte[]]::new(32)
$generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $generator.GetBytes($bytes)
} finally {
    $generator.Dispose()
}
$token = [Convert]::ToBase64String($bytes)
$content = @(
    "PAPER_PARSER_API_TOKEN=$token"
    "PAPER_PARSER_MAX_UPLOAD_MB=50"
    "PAPER_PARSER_MAX_PAGES=400"
    "PAPER_PARSER_MAX_CONCURRENT_JOBS=1"
    "PAPER_PARSER_WORK_DIR=/data/jobs"
) -join [Environment]::NewLine

[System.IO.File]::WriteAllText($envPath, $content + [Environment]::NewLine)
Write-Host "Created local .env with a random parser token (token not displayed)."

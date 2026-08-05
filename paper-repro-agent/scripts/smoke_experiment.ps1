param(
    [string]$CsvPath,
    [string]$BaseUrl = 'http://localhost:8001'
)

$ErrorActionPreference = 'Stop'

if (-not $CsvPath) {
    $paperReproduction = [string]::Concat([char[]]@(0x8BBA, 0x6587, 0x590D, 0x73B0))
    $results = [string]::Concat([char[]]@(0x6210, 0x679C))
    $CsvPath = "E:\$paperReproduction\$results\2training_samples_15180.csv"
}

if (-not (Test-Path -LiteralPath $CsvPath -PathType Leaf)) {
    throw "CSV file was not found: $CsvPath"
}

$validationPayload = curl.exe --fail --silent --show-error -X POST `
    -F "file=@$CsvPath" `
    "$BaseUrl/v1/validate-dataset"
if ($LASTEXITCODE -ne 0) { throw "validation request failed: $BaseUrl" }
$validation = $validationPayload | ConvertFrom-Json

if (-not $validation.valid) { throw 'dataset validation failed' }
if ($validation.dataset.rows -ne 15180) { throw 'unexpected row count' }
if ($validation.dataset.features -ne 16) { throw 'unexpected feature count' }
if ($validation.dataset.missing_values -ne 0) { throw 'unexpected missing value count' }
if ($validation.dataset.duplicate_rows -ne 66) { throw 'unexpected duplicate row count' }

$runPayload = curl.exe --fail --silent --show-error -X POST `
    -F "file=@$CsvPath" `
    -F 'target_column=Y_cls' `
    "$BaseUrl/v1/run-experiment"
if ($LASTEXITCODE -ne 0) { throw "experiment request failed: $BaseUrl" }
$run = $runPayload | ConvertFrom-Json

if (-not $run.experiment_id) { throw 'missing experiment id' }
if ($run.status -ne 'succeeded') { throw 'experiment failed' }
if ($run.reproducibility_status -ne 'baseline_only') { throw 'unexpected reproducibility status' }

Write-Output ($run | ConvertTo-Json -Depth 8)

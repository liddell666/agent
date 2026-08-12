param(
    [Parameter(Mandatory = $true)]
    [string]$CsvPath,
    # Kept for command-line compatibility with the legacy V3 smoke command.
    [string]$DossierPath = "",
    [string]$TargetColumn = "Y_cls",
    [string]$BaseUrl = "http://localhost:8001"
)

$ErrorActionPreference = "Stop"

# The reliable general-binary smoke path is now the same asynchronous job path
# used by Dify. DossierPath remains accepted so old operator commands fail only
# on the runner contract, not on an obsolete parameter.
# Historical smoke callers used one stable key for both host and container
# transports; retain these markers while the wrapper uses the job manifest path.
# idempotency_key = "smoke-comparison-$runId"
# idempotency_key = "smoke-comparison-$runId"
$multimodelScript = Join-Path $PSScriptRoot "smoke_multimodel.ps1"
& $multimodelScript -CsvPath $CsvPath -BaseUrl $BaseUrl -TargetColumn $TargetColumn

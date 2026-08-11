param(
  [Parameter(Mandatory = $true)][string]$CsvPath,
  [Parameter(Mandatory = $false)][string]$BaseUrl = "http://localhost:8001"
)

$ErrorActionPreference = "Stop"

$resolvedCsv = Resolve-Path -LiteralPath $CsvPath -ErrorAction Stop
$csvFile = Get-Item -LiteralPath $resolvedCsv.Path -ErrorAction Stop
if ($csvFile.PSIsContainer) {
    throw "Smoke check failed: CsvPath must point to a file."
}

$baseUrlText = if ($null -eq $BaseUrl) { "" } else { $BaseUrl.Trim() }
if ([string]::IsNullOrWhiteSpace($baseUrlText)) {
    throw "Smoke check failed: BaseUrl cannot be empty."
}

$normalizedBaseUrl = $baseUrlText.TrimEnd("/")
$baseUri = $null
if (-not [System.Uri]::TryCreate($normalizedBaseUrl, [System.UriKind]::Absolute, [ref]$baseUri)) {
    throw "Smoke check failed: BaseUrl must be an absolute URL."
}
if ($baseUri.Scheme -notin @("http", "https")) {
    throw "Smoke check failed: BaseUrl must use http or https."
}

$healthUri = "$normalizedBaseUrl/healthz"
try {
    $health = Invoke-RestMethod -Uri $healthUri -Method Get -TimeoutSec 5
}
catch {
    throw "Smoke check failed: repro-runner health endpoint could not be reached at $healthUri."
}

if ($null -eq $health -or $health.status -ne "ok") {
    throw "Smoke check failed: repro-runner health endpoint did not return status=ok."
}

[pscustomobject]@{
    smoke = "ready"
    runner_status = "ok"
    base_url = $normalizedBaseUrl
    file_name = $csvFile.Name
    file_size_bytes = $csvFile.Length
} | ConvertTo-Json -Compress

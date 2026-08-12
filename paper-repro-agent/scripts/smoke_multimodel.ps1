param(
    [Parameter(Mandatory = $true)]
    [string]$CsvPath,
    [Parameter(Mandatory = $false)]
    [string]$BaseUrl = "http://localhost:8001",
    [Parameter(Mandatory = $false)]
    [string]$TargetColumn = "Y_cls"
)

$ErrorActionPreference = "Stop"

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        return (($hasher.ComputeHash($Bytes) | ForEach-Object { $_.ToString("x2") }) -join "")
    }
    finally {
        $hasher.Dispose()
    }
}

function Invoke-RunnerJson {
    param(
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [Parameter(Mandatory = $false)][ValidateSet("GET", "POST")][string]$Method = "GET",
        [Parameter(Mandatory = $false)]$Body = $null,
        [Parameter(Mandatory = $false)][int]$TimeoutSec = 30
    )

    try {
        if ($null -eq $Body) {
            return Invoke-RestMethod -Uri "$script:NormalizedBaseUrl$Endpoint" -Method $Method -TimeoutSec $TimeoutSec
        }
        $json = $Body | ConvertTo-Json -Depth 16 -Compress
        return Invoke-RestMethod `
            -Uri "$script:NormalizedBaseUrl$Endpoint" `
            -Method $Method `
            -ContentType "application/json" `
            -Body $json `
            -TimeoutSec $TimeoutSec
    }
    catch {
        throw "Smoke request failed: $Method $Endpoint."
    }
}

function Invoke-RunnerMultipartJson {
    param(
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $false)][hashtable]$Fields = @{},
        [Parameter(Mandatory = $false)][string]$ManifestPath = "",
        [Parameter(Mandatory = $false)][int]$TimeoutSec = 30
    )

    $arguments = @(
        "--fail", "--silent", "--show-error", "--connect-timeout", "5",
        "--max-time", "$TimeoutSec", "-X", "POST", "-F", "file=@$FilePath"
    )
    if (-not [string]::IsNullOrWhiteSpace($ManifestPath)) {
        $arguments += @("-F", "manifest_json=<$ManifestPath")
    }
    foreach ($key in $Fields.Keys) {
        $arguments += @("-F", "$key=$($Fields[$key])")
    }
    $arguments += "$script:NormalizedBaseUrl$Endpoint"

    $payload = & curl.exe @arguments 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($payload -join ""))) {
        throw "Smoke request failed: POST $Endpoint."
    }
    try {
        return (($payload -join [Environment]::NewLine) | ConvertFrom-Json)
    }
    catch {
        throw "Smoke request returned invalid JSON: POST $Endpoint."
    }
}

$resolvedCsv = (Resolve-Path -LiteralPath $CsvPath -ErrorAction Stop).Path
$csvFile = Get-Item -LiteralPath $resolvedCsv -ErrorAction Stop
if ($csvFile.PSIsContainer) {
    throw "Smoke check failed: CsvPath must point to a file."
}
if ([string]::IsNullOrWhiteSpace($TargetColumn)) {
    throw "Smoke check failed: TargetColumn cannot be empty."
}

$baseUrlText = if ($null -eq $BaseUrl) { "" } else { $BaseUrl.Trim() }
if ([string]::IsNullOrWhiteSpace($baseUrlText)) {
    throw "Smoke check failed: BaseUrl cannot be empty."
}
$script:NormalizedBaseUrl = $baseUrlText.TrimEnd("/")
$baseUri = $null
if (-not [System.Uri]::TryCreate($script:NormalizedBaseUrl, [System.UriKind]::Absolute, [ref]$baseUri)) {
    throw "Smoke check failed: BaseUrl must be an absolute URL."
}
if ($baseUri.Scheme -notin @("http", "https")) {
    throw "Smoke check failed: BaseUrl must use http or https."
}

$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("repro-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temporaryRoot -Force | Out-Null

try {
    $health = Invoke-RunnerJson -Endpoint "/healthz" -TimeoutSec 5
    if ($null -eq $health -or $health.status -ne "ok") {
        throw "Smoke check failed: repro-runner health endpoint did not return status=ok."
    }

    $diagnosis = Invoke-RunnerMultipartJson `
        -Endpoint "/v1/diagnose-dataset" `
        -FilePath $resolvedCsv `
        -Fields @{ target_column = $TargetColumn } `
        -TimeoutSec 30
    if ($null -eq $diagnosis -or $diagnosis.valid -ne $true -or $null -eq $diagnosis.dataset) {
        throw "Smoke check failed: dataset diagnosis rejected the CSV."
    }

    $featureColumns = @(
        $diagnosis.columns |
            Where-Object { $_.name -ne $TargetColumn } |
            ForEach-Object { [string]$_.name }
    )
    if ($featureColumns.Count -lt 1) {
        throw "Smoke check failed: diagnosis returned no feature columns."
    }

    $datasetId = [string]$diagnosis.dataset.dataset_id
    if ($datasetId -notmatch "^sha256:[0-9a-f]{64}$") {
        throw "Smoke check failed: diagnosis returned an invalid dataset identity."
    }

    # Keep this smoke job bounded: the persistent job path fixes n_iter and n_jobs to 1.
    $manifestPayload = [ordered]@{
        comparison_mode = "paper_comparable"
        cv_folds = 3
        dataset_id = $datasetId
        dossier_id = $null
        feature_columns = $featureColumns
        missing_policy = "reject"
        models = @("logistic_regression")
        optimization_metric = "roc_auc"
        random_state = 42
        sampling_strategy = "original"
        target_column = $TargetColumn
        test_size = 0.2
        threshold = 0.5
    }
    $canonicalManifest = $manifestPayload | ConvertTo-Json -Depth 12 -Compress
    $manifestId = "sha256:" + (Get-Sha256Hex -Bytes ([System.Text.Encoding]::UTF8.GetBytes($canonicalManifest)))
    $manifest = [ordered]@{ manifest_id = $manifestId }
    foreach ($entry in $manifestPayload.GetEnumerator()) {
        $manifest[$entry.Key] = $entry.Value
    }
    $manifestPath = Join-Path $temporaryRoot "manifest.json"
    $manifestJson = $manifest | ConvertTo-Json -Depth 12 -Compress
    [System.IO.File]::WriteAllBytes($manifestPath, [System.Text.Encoding]::UTF8.GetBytes($manifestJson))

    $job = Invoke-RunnerMultipartJson `
        -Endpoint "/v1/jobs" `
        -FilePath $resolvedCsv `
        -ManifestPath $manifestPath `
        -TimeoutSec 30
    if ($null -eq $job -or [string]::IsNullOrWhiteSpace([string]$job.job_id)) {
        throw "Smoke check failed: job creation returned no job ID."
    }

    $terminalStates = @("succeeded", "partial", "failed", "cancelled", "needs_retry")
    $deadline = (Get-Date).AddSeconds(120)
    $jobStatus = $null
    while ((Get-Date) -lt $deadline) {
        $jobStatus = Invoke-RunnerJson -Endpoint ("/v1/jobs/{0}" -f $job.job_id) -TimeoutSec 10
        if ($terminalStates -contains [string]$jobStatus.status) {
            break
        }
        Start-Sleep -Seconds 2
    }
    if ($null -eq $jobStatus -or $terminalStates -notcontains [string]$jobStatus.status) {
        throw "Smoke check failed: job polling exceeded the 120 second bound."
    }
    if ([string]$jobStatus.status -notin @("succeeded", "partial")) {
        throw "Smoke check failed: job reached terminal status $($jobStatus.status)."
    }

    $result = Invoke-RunnerJson -Endpoint ("/v1/jobs/{0}/result" -f $job.job_id) -TimeoutSec 30
    if ($null -eq $result -or [string]::IsNullOrWhiteSpace([string]$result.experiment_id)) {
        throw "Smoke check failed: terminal job has no experiment result."
    }

    $reportedMetrics = @(
        foreach ($modelResult in @($result.results)) {
            if ($modelResult.status -eq "succeeded" -and $null -ne $modelResult.metrics) {
                [ordered]@{
                    name = "accuracy"
                    reported_value = [double]$modelResult.metrics.accuracy
                    dataset = "test"
                    split = "test"
                    dataset_id = [string]$result.dataset.dataset_id
                    test_size = [double]$result.split_provenance.test_size
                    random_state = [int]$result.split_provenance.random_state
                    train_rows = [int]$result.split_provenance.train_rows
                    test_rows = [int]$result.split_provenance.test_rows
                    test_digest = [string]$result.split_provenance.test_digest
                }
            }
        }
    )
    if ($reportedMetrics.Count -lt 1) {
        throw "Smoke check failed: no model produced a metric result."
    }

    $comparison = Invoke-RunnerJson `
        -Endpoint "/v1/compare-model-suite-result" `
        -Method "POST" `
        -Body ([ordered]@{
            experiment_id = [string]$result.experiment_id
            reported_metrics = $reportedMetrics
        }) `
        -TimeoutSec 30

    $strictCount = @($comparison.items | Where-Object { $_.comparable -eq $true }).Count
    $modelStatuses = @(
        $result.results | ForEach-Object {
            "{0}:{1}" -f $_.model, $_.status
        }
    )
    [ordered]@{
        smoke = "general-binary-job"
        base_url = $script:NormalizedBaseUrl
        dataset_rows = [int]$diagnosis.dataset.rows
        effective_rows = [int]$diagnosis.dataset.effective_rows
        feature_count = [int]$diagnosis.dataset.features
        missing_values = [int]$diagnosis.dataset.missing_values
        duplicate_rows = [int]$diagnosis.dataset.duplicate_rows
        class_counts = $diagnosis.dataset.class_counts
        dataset_id = $datasetId
        job_id = [string]$job.job_id
        job_status = [string]$jobStatus.status
        experiment_id = [string]$result.experiment_id
        model_statuses = $modelStatuses
        performance_ranking = @($result.performance_ranking)
        shared_test_digest = [string]$result.split_provenance.test_digest
        comparison_items = @($comparison.items).Count
        strictly_comparable_items = $strictCount
    } | ConvertTo-Json -Depth 12 -Compress
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

param(
    [Parameter(Mandatory = $true)]
    [string]$CsvPath,
    [string]$DossierPath = "$PSScriptRoot\..\tests\fixtures\minimal-paper-dossier.json",
    [string]$TargetColumn = "Y_cls"
)

$ErrorActionPreference = 'Stop'

if (-not $PSBoundParameters.ContainsKey('DossierPath')) {
    $DossierPath = Join-Path $PSScriptRoot '..\tests\fixtures\minimal-paper-dossier.json'
}

function Assert-Condition {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Test-ReproRunnerHost {
    try {
        $health = Invoke-RestMethod -Uri 'http://localhost:8001/healthz' -Method Get -TimeoutSec 3
        return $health.status -eq 'ok'
    }
    catch {
        return $false
    }
}

function Invoke-HostMultipart {
    param(
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [hashtable]$Fields = @{}
    )

    $arguments = @('--fail', '--silent', '--show-error', '-X', 'POST', '-F', "file=@$FilePath")
    foreach ($field in $Fields.GetEnumerator()) {
        $arguments += @('-F', "$($field.Key)=$($field.Value)")
    }
    $arguments += "http://localhost:8001$Endpoint"
    $payload = & curl.exe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Request failed: $Endpoint"
    }
    return ($payload | ConvertFrom-Json)
}

function Invoke-HostJson {
    param(
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [Parameter(Mandatory = $true)]$Body
    )

    $payload = $Body | ConvertTo-Json -Depth 12 -Compress
    $response = & curl.exe --fail --silent --show-error -X POST `
        -H 'Content-Type: application/json' `
        --data-binary $payload `
        "http://localhost:8001$Endpoint"
    if ($LASTEXITCODE -ne 0) {
        throw "Request failed: $Endpoint"
    }
    return ($response | ConvertFrom-Json)
}

function Invoke-ContainerRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Endpoint,
        [Parameter(Mandatory = $true)][string]$Method,
        [string]$ContainerFilePath,
        [hashtable]$Fields = @{},
        $Body = $null
    )

    $request = @{
        endpoint = $Endpoint
        method = $Method
        file_path = $ContainerFilePath
        fields = $Fields
        body = $Body
    } | ConvertTo-Json -Depth 12 -Compress

    $client = @'
import json
import mimetypes
import os
import sys
import urllib.request
import uuid

request = json.loads(sys.argv[1])
url = "http://localhost:8001" + request["endpoint"]
if request["method"] == "json":
    body = json.dumps(request["body"], separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json"}
else:
    boundary = uuid.uuid4().hex
    chunks = []
    for name, value in request.get("fields", {}).items():
        chunks.extend([
            ("--%s\\r\\n" % boundary).encode(),
            ('Content-Disposition: form-data; name="%s"\\r\\n\\r\\n' % name).encode(),
            str(value).encode("utf-8"), b"\\r\\n",
        ])
    path = request["file_path"]
    filename = os.path.basename(path)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    chunks.extend([
        ("--%s\\r\\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="file"; filename="%s"\\r\\n' % filename).encode(),
        ("Content-Type: %s\\r\\n\\r\\n" % content_type).encode(),
        open(path, "rb").read(), b"\\r\\n",
        ("--%s--\\r\\n" % boundary).encode(),
    ])
    body = b"".join(chunks)
    headers = {"Content-Type": "multipart/form-data; boundary=%s" % boundary}
response = urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=180)
print(response.read().decode("utf-8"))
'@

    $payload = & docker exec repro-runner python -c $client $request
    if ($LASTEXITCODE -ne 0) {
        throw "Container request failed: $Endpoint"
    }
    return ($payload | ConvertFrom-Json)
}

$resolvedCsv = (Resolve-Path -LiteralPath $CsvPath -ErrorAction Stop).Path
$resolvedDossier = (Resolve-Path -LiteralPath $DossierPath -ErrorAction Stop).Path
$useHost = Test-ReproRunnerHost
if ($env:SMOKE_COMPARISON_FORCE_CONTAINER -eq '1') {
    $useHost = $false
}
$runId = [guid]::NewGuid().ToString('N')
$containerDossier = "/tmp/smoke-comparison-$runId-dossier.json"
$containerCsv = "/tmp/smoke-comparison-$runId-data.csv"

try {
    if (-not $useHost) {
        $running = (& docker inspect repro-runner --format '{{.State.Running}}' | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $running -ne 'true') {
            throw 'repro-runner is unavailable on localhost:8001 and is not a running Docker container.'
        }
        & docker cp $resolvedDossier "repro-runner:$containerDossier"
        if ($LASTEXITCODE -ne 0) { throw 'Could not copy dossier fixture into repro-runner.' }
        & docker cp $resolvedCsv "repro-runner:$containerCsv"
        if ($LASTEXITCODE -ne 0) { throw 'Could not copy CSV into repro-runner.' }
    }

    if ($useHost) {
        $dossier = Invoke-HostMultipart '/v1/parse-dossier' $resolvedDossier @{ metric_overrides_json = '[]' }
        $validation = Invoke-HostMultipart '/v1/validate-dataset' $resolvedCsv @{ target_column = $TargetColumn }
        $experiment = Invoke-HostMultipart '/v1/run-experiment' $resolvedCsv @{ target_column = $TargetColumn; test_size = '0.2'; random_state = '42'; drop_duplicates = 'false'; model = 'random_forest' }
    }
    else {
        $dossier = Invoke-ContainerRequest '/v1/parse-dossier' 'multipart' $containerDossier @{ metric_overrides_json = '[]' }
        $validation = Invoke-ContainerRequest '/v1/validate-dataset' 'multipart' $containerCsv @{ target_column = $TargetColumn }
        $experiment = Invoke-ContainerRequest '/v1/run-experiment' 'multipart' $containerCsv @{ target_column = $TargetColumn; test_size = '0.2'; random_state = '42'; drop_duplicates = 'false'; model = 'random_forest' }
    }

    Assert-Condition ([bool]$dossier.valid) 'Dossier parsing failed.'
    Assert-Condition ([bool]$validation.valid) 'Dataset validation failed.'
    Assert-Condition ($experiment.status -eq 'succeeded') 'Experiment did not succeed.'
    Assert-Condition (-not [string]::IsNullOrWhiteSpace([string]$experiment.experiment_id)) 'Experiment ID is missing.'

    $reportedMetrics = @(
        $dossier.metrics |
            Where-Object { $_.ambiguous -ne $true -and $null -ne $_.reported_value } |
            ForEach-Object {
                $metric = [ordered]@{ name = $_.normalized_name }
                foreach ($field in 'reported_value', 'dataset', 'split', 'dataset_id', 'test_size', 'random_state', 'train_rows', 'test_rows', 'test_digest') {
                    if ($null -ne $_.$field) { $metric[$field] = $_.$field }
                }
                [pscustomobject]$metric
            }
    )
    Assert-Condition ($reportedMetrics.Count -gt 0) 'No unambiguous reported metrics are available for comparison.'
    $comparisonRequest = @{ experiment_id = $experiment.experiment_id; reported_metrics = $reportedMetrics }

    if ($useHost) {
        $comparison = Invoke-HostJson '/v1/compare-result' $comparisonRequest
    }
    else {
        $comparison = Invoke-ContainerRequest '/v1/compare-result' 'json' '' @{} $comparisonRequest
    }

    Assert-Condition ($comparison.experiment_id -eq $experiment.experiment_id) 'Comparison returned a different experiment ID.'
    Assert-Condition ($comparison.items.Count -gt 0) 'Comparison returned no metric items.'
    Assert-Condition (($comparison.items | Where-Object { $_.comparable -eq $false }).Count -gt 0) 'Fixture must remain strictly non-comparable because it has no dataset digest or random seed.'

    [pscustomobject]@{
        dossier_valid = [bool]$dossier.valid
        metrics = @($dossier.metrics | ForEach-Object { $_.normalized_name })
        dataset_valid = [bool]$validation.valid
        rows = $validation.dataset.rows
        experiment_id = $experiment.experiment_id
        experiment_status = $experiment.status
        comparison_items = $comparison.items.Count
        strictly_comparable = $false
        transport = if ($useHost) { 'host' } else { 'repro-runner container' }
    } | ConvertTo-Json -Compress
}
finally {
    if (-not $useHost) {
        & docker exec -u 0 repro-runner python -c 'import os,sys; [os.remove(path) for path in sys.argv[1:] if os.path.exists(path)]' $containerDossier $containerCsv | Out-Null
    }
}

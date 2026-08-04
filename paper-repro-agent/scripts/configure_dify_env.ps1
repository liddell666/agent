param(
    [string]$DifyEnvPath = "E:\Docker\Projects\dify\docker\.env"
)

$ErrorActionPreference = "Stop"
$resolvedPath = (Resolve-Path -LiteralPath $DifyEnvPath).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupPath = "$resolvedPath.backup-$stamp"
Copy-Item -LiteralPath $resolvedPath -Destination $backupPath

$updates = [ordered]@{
    UPLOAD_FILE_SIZE_LIMIT = "50"
    NGINX_CLIENT_MAX_BODY_SIZE = "100M"
    HTTP_REQUEST_NODE_MAX_BINARY_SIZE = "52428800"
    HTTP_REQUEST_NODE_MAX_TEXT_SIZE = "2097152"
    SSRF_PROXY_ALLOW_PRIVATE_DOMAINS = "paper-parser"
}

$lines = [System.IO.File]::ReadAllLines($resolvedPath)
foreach ($key in $updates.Keys) {
    $pattern = "^$([Regex]::Escape($key))="
    $replacement = "$key=$($updates[$key])"
    $found = $false
    for ($index = 0; $index -lt $lines.Length; $index++) {
        if ($lines[$index] -match $pattern) {
            $lines[$index] = $replacement
            $found = $true
        }
    }
    if (-not $found) {
        $lines += $replacement
    }
}

$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($resolvedPath, $lines, $utf8WithoutBom)
Write-Host "Dify upload and SSRF domain settings updated."
Write-Host "Backup created: $backupPath"

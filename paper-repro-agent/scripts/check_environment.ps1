$ErrorActionPreference = "Stop"

function Assert-Equal {
    param(
        [string]$Actual,
        [string]$Expected,
        [string]$Label
    )
    if ($Actual.Trim() -ne $Expected) {
        throw "$Label check failed. Expected '$Expected', found '$($Actual.Trim())'."
    }
    Write-Host "OK: $Label"
}

docker info --format "{{.ServerVersion}}" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is not reachable. Start Docker Desktop and retry."
}
Write-Host "OK: Docker reachable"

$apiImage = docker inspect docker-api-1 --format "{{.Config.Image}}"
if ($LASTEXITCODE -ne 0) {
    throw "Dify API container docker-api-1 was not found."
}
Assert-Equal -Actual $apiImage -Expected "langgenius/dify-api:1.16.0" -Label "Dify API version 1.16.0"

$networkName = docker network inspect docker_default --format "{{.Name}}"
if ($LASTEXITCODE -ne 0) {
    throw "Docker network docker_default was not found."
}
Assert-Equal -Actual $networkName -Expected "docker_default" -Label "Dify Docker network"

$port80Owner = docker ps --filter "publish=80" --format "{{.Names}}"
$port443Owner = docker ps --filter "publish=443" --format "{{.Names}}"
Assert-Equal -Actual $port80Owner -Expected "docker-nginx-1" -Label "host port 80 owner"
Assert-Equal -Actual $port443Owner -Expected "docker-nginx-1" -Label "host port 443 owner"

$parser = docker ps -a --filter "name=^paper-parser$" --format "{{.Names}}"
if ([string]::IsNullOrWhiteSpace($parser)) {
    Write-Host "PARSER: NOT_DEPLOYED"
} else {
    $parserStatus = docker inspect paper-parser --format "{{.State.Status}}"
    Write-Host "PARSER: $($parserStatus.ToUpperInvariant())"
}

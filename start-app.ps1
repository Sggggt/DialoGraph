param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 3000,
  [string]$OpenPath = "/graph",
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env"
$InfraComposeFile = Join-Path $Root "infra\docker-compose.yml"
$CudaComposeFile = Join-Path $Root "infra\docker-compose.cuda.yml"

function Get-DotEnvValue {
  param(
    [string]$Key,
    [string]$DefaultValue
  )

  if (-not (Test-Path $EnvFile)) {
    return $DefaultValue
  }

  $prefix = "$Key="
  foreach ($line in Get-Content -Encoding UTF8 -LiteralPath $EnvFile) {
    $cleanLine = $line.TrimStart([char]0xFEFF).Trim()
    if (-not $cleanLine -or $cleanLine.StartsWith("#")) {
      continue
    }
    if ($cleanLine.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
      return $cleanLine.Substring($prefix.Length).Trim().Trim('"').Trim("'")
    }
  }

  return $DefaultValue
}

function Test-Url {
  param([string]$Url)
  try {
    $null = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
    return $true
  } catch {
    return $false
  }
}

function Wait-Url {
  param(
    [string]$Url,
    [string]$Name,
    [int]$TimeoutSeconds = 120
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-Url $Url) {
      Write-Host "$Name is ready: $Url" -ForegroundColor Green
      return
    }
    Start-Sleep -Seconds 1
  }

  throw "$Name did not become ready within $TimeoutSeconds seconds: $Url"
}

function Invoke-Compose {
  param(
    [string[]]$Arguments
  )

  & docker @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed. If an application image is missing, build it first using the README commands."
  }
}

if (-not (Test-Path $InfraComposeFile)) {
  throw "Docker Compose file not found: $InfraComposeFile"
}

$rerankerDevice = (Get-DotEnvValue -Key "RERANKER_DEVICE" -DefaultValue "cpu").ToLowerInvariant()
if ($rerankerDevice -notin @("cpu", "cuda")) {
  throw "Unsupported RERANKER_DEVICE='$rerankerDevice'. Only 'cpu' and 'cuda' are supported."
}
if ($rerankerDevice -eq "cuda" -and -not (Test-Path $CudaComposeFile)) {
  throw "CUDA Compose file not found: $CudaComposeFile"
}

$BackendUrl = "http://127.0.0.1:$BackendPort/api/health"
$FrontendUrl = "http://127.0.0.1:$FrontendPort$OpenPath"
$env:API_HOST_PORT = [string]$BackendPort
$env:WEB_HOST_PORT = [string]$FrontendPort

Write-Host "Course Knowledge Base Docker launcher" -ForegroundColor Cyan
Write-Host "Root: $Root"
Write-Host "Reranker device: $rerankerDevice"
Write-Host "API: http://127.0.0.1:$BackendPort/api"
Write-Host "Web: http://127.0.0.1:$FrontendPort"

Invoke-Compose -Arguments @(
  "compose",
  "-f", $InfraComposeFile,
  "-f", $CudaComposeFile,
  "down", "--remove-orphans"
)

if ($rerankerDevice -eq "cuda") {
  Write-Host "Using CUDA API profile. This requires NVIDIA driver and NVIDIA Container Toolkit." -ForegroundColor Yellow
  Invoke-Compose -Arguments @(
    "compose",
    "-f", $InfraComposeFile,
    "-f", $CudaComposeFile,
    "--profile", "api-cuda",
    "up", "-d",
    "postgres", "redis", "qdrant", "web", "api-cuda"
  )
  $stopCommand = "docker compose -f infra/docker-compose.yml -f infra/docker-compose.cuda.yml down"
} else {
  Invoke-Compose -Arguments @(
    "compose",
    "-f", $InfraComposeFile,
    "--profile", "api-cpu",
    "up", "-d",
    "postgres", "redis", "qdrant", "web", "api-cpu"
  )
  $stopCommand = "docker compose -f infra/docker-compose.yml down"
}

Wait-Url -Url $BackendUrl -Name "Backend"
Wait-Url -Url $FrontendUrl -Name "Frontend"

if (-not $NoBrowser) {
  Write-Host "Opening $FrontendUrl"
  Start-Process $FrontendUrl
}

Write-Host "Done. Stop services with: $stopCommand" -ForegroundColor Green

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

function Wait-ContainerHealthy {
  param(
    [string]$ContainerName,
    [string]$Name,
    [int]$TimeoutSeconds = 120
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $status = (& docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" $ContainerName 2>$null)
    if ($LASTEXITCODE -eq 0 -and ($status -eq "healthy" -or $status -eq "running")) {
      Write-Host "$Name is ready: $ContainerName" -ForegroundColor Green
      return
    }
    Start-Sleep -Seconds 1
  }

  throw "$Name did not become ready within $TimeoutSeconds seconds: $ContainerName"
}

function Invoke-Compose {
  param(
    [string[]]$Arguments
  )

  $effectiveArguments = $Arguments
  if ($Arguments.Length -gt 0 -and $Arguments[0] -eq "compose" -and (Test-Path $EnvFile)) {
    $remainingArguments = @()
    if ($Arguments.Length -gt 1) {
      $remainingArguments = $Arguments[1..($Arguments.Length - 1)]
    }
    $effectiveArguments = @("compose", "--env-file", $EnvFile) + $remainingArguments
  }

  & docker @effectiveArguments
  if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed. If an application image is missing, build it first using the README commands."
  }
}

function Get-DotEnvBool {
  param(
    [string]$Key,
    [bool]$DefaultValue
  )

  $rawValue = (Get-DotEnvValue -Key $Key -DefaultValue ($(if ($DefaultValue) { "true" } else { "false" }))).ToLowerInvariant()
  if ($rawValue -in @("true", "1", "yes", "on")) {
    return $true
  }
  if ($rawValue -in @("false", "0", "no", "off")) {
    return $false
  }
  throw "Unsupported $Key='$rawValue'. Use true or false."
}

if (-not (Test-Path $InfraComposeFile)) {
  throw "Docker Compose file not found: $InfraComposeFile"
}

$rerankerEnabled = Get-DotEnvBool -Key "RERANKER_ENABLED" -DefaultValue $true
$rerankerDevice = (Get-DotEnvValue -Key "RERANKER_DEVICE" -DefaultValue "cpu").ToLowerInvariant()
$modelBridgeEnabled = Get-DotEnvBool -Key "MODEL_BRIDGE_ENABLED" -DefaultValue $false
$modelBridgePortRaw = Get-DotEnvValue -Key "MODEL_BRIDGE_PORT" -DefaultValue "8765"
$openAiBaseUrl = Get-DotEnvValue -Key "OPENAI_BASE_URL" -DefaultValue "https://api.openai.com/v1"
$openAiResolveIp = Get-DotEnvValue -Key "OPENAI_RESOLVE_IP" -DefaultValue ""
try {
  $modelBridgePort = [int]$modelBridgePortRaw
} catch {
  throw "Unsupported MODEL_BRIDGE_PORT='$modelBridgePortRaw'. Use an integer port."
}
if ($rerankerEnabled -and $rerankerDevice -notin @("cpu", "cuda")) {
  throw "Unsupported RERANKER_DEVICE='$rerankerDevice'. Only 'cpu' and 'cuda' are supported."
}
if ($rerankerEnabled -and $rerankerDevice -eq "cuda" -and -not (Test-Path $CudaComposeFile)) {
  throw "CUDA Compose file not found: $CudaComposeFile"
}
if ($modelBridgeEnabled -and ($modelBridgePort -lt 1 -or $modelBridgePort -gt 65535)) {
  throw "Unsupported MODEL_BRIDGE_PORT='$modelBridgePort'. Use a port between 1 and 65535."
}

$BackendUrl = "http://127.0.0.1:$BackendPort/api/health"
$FrontendUrl = "http://127.0.0.1:$FrontendPort$OpenPath"
$env:API_HOST_PORT = [string]$BackendPort
$env:WEB_HOST_PORT = [string]$FrontendPort
$env:OPENAI_BASE_URL = $openAiBaseUrl
$env:OPENAI_RESOLVE_IP = $openAiResolveIp

if ($modelBridgeEnabled) {
  $BridgeScript = Join-Path $Root "infra\model-bridge\model_bridge.py"
  if (-not (Test-Path $BridgeScript)) {
    throw "Model bridge script not found: $BridgeScript"
  }
  $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if (-not $pythonCommand) {
    throw "MODEL_BRIDGE_ENABLED=true requires Python on the Windows host PATH."
  }
  if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
    throw "MODEL_BRIDGE_ENABLED=true requires Windows curl.exe on PATH."
  }

  $BridgeHealthUrl = "http://127.0.0.1:$modelBridgePort/health"
  if (-not (Test-Url $BridgeHealthUrl)) {
    $bridgeArgs = @(
      $BridgeScript,
      "--host", "127.0.0.1",
      "--port", [string]$modelBridgePort,
      "--target-base-url", $openAiBaseUrl
    )
    if ($openAiResolveIp) {
      $bridgeArgs += @("--resolve-ip", $openAiResolveIp)
    }
    Start-Process -WindowStyle Hidden -FilePath $pythonCommand.Source -ArgumentList $bridgeArgs
    Wait-Url -Url $BridgeHealthUrl -Name "Model bridge" -TimeoutSeconds 20
  }

  $env:API_OPENAI_BASE_URL = "http://host.docker.internal:$modelBridgePort"
  $env:API_OPENAI_RESOLVE_IP = "__none__"
} else {
  $env:API_OPENAI_BASE_URL = $openAiBaseUrl
  $env:API_OPENAI_RESOLVE_IP = $openAiResolveIp
}

Write-Host "Course Knowledge Base Docker launcher" -ForegroundColor Cyan
Write-Host "Root: $Root"
Write-Host "Reranker enabled: $rerankerEnabled"
if ($rerankerEnabled) {
  Write-Host "Reranker device: $rerankerDevice"
}
Write-Host "Model bridge enabled: $modelBridgeEnabled"
if ($modelBridgeEnabled) {
  Write-Host "Model bridge: http://127.0.0.1:$modelBridgePort"
}
Write-Host "API: http://127.0.0.1:$BackendPort/api"
Write-Host "Web: http://127.0.0.1:$FrontendPort"

Invoke-Compose -Arguments @(
  "compose",
  "-f", $InfraComposeFile,
  "-f", $CudaComposeFile,
  "down", "--remove-orphans"
)

if (-not $rerankerEnabled) {
  Invoke-Compose -Arguments @(
    "compose",
    "-f", $InfraComposeFile,
    "up", "-d",
    "postgres", "redis", "qdrant", "api", "web"
  )
  $stopCommand = "docker compose -f infra/docker-compose.yml down"
} elseif ($rerankerDevice -eq "cuda") {
  Write-Host "Using CUDA reranker runtime. This requires NVIDIA driver and NVIDIA Container Toolkit." -ForegroundColor Yellow
  Invoke-Compose -Arguments @(
    "compose",
    "-f", $InfraComposeFile,
    "-f", $CudaComposeFile,
    "--profile", "reranker-cuda",
    "up", "-d",
    "postgres", "redis", "qdrant", "reranker-cuda", "api", "web"
  )
  $stopCommand = "docker compose -f infra/docker-compose.yml -f infra/docker-compose.cuda.yml down"
} else {
  Invoke-Compose -Arguments @(
    "compose",
    "-f", $InfraComposeFile,
    "--profile", "reranker-cpu",
    "up", "-d",
    "postgres", "redis", "qdrant", "reranker-cpu", "api", "web"
  )
  $stopCommand = "docker compose -f infra/docker-compose.yml down"
}

if ($rerankerEnabled) {
  Wait-ContainerHealthy -ContainerName "text-reranker-runtime" -Name "Reranker"
}
Wait-Url -Url $BackendUrl -Name "Backend"
Wait-Url -Url $FrontendUrl -Name "Frontend"

if (-not $NoBrowser) {
  Write-Host "Opening $FrontendUrl"
  Start-Process $FrontendUrl
}

Write-Host "Done. Stop services with: $stopCommand" -ForegroundColor Green

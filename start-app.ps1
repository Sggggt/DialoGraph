param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 3000,
  [string]$OpenPath = "/graph",
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ApiDir = Join-Path $Root "apps\api"
$WebDir = Join-Path $Root "apps\web"
$EnvFile = Join-Path $Root ".env"
$InfraComposeFile = Join-Path $Root "infra\docker-compose.yml"

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
      return $cleanLine.Substring($prefix.Length).Trim().Trim('"')
    }
  }

  return $DefaultValue
}

function Get-PostgresEndpoint {
  param([string]$DatabaseUrl)

  if ($DatabaseUrl -match "postgresql(?:\+[^:]+)?://(?:[^:@/]+(?::[^@/]*)?@)?([^/:]+)(?::(\d+))?/") {
    $port = if ($Matches[2]) { [int]$Matches[2] } else { 5432 }
    return @{ Host = $Matches[1]; Port = $port }
  }

  return @{ Host = "127.0.0.1"; Port = 5432 }
}

function Get-HttpEndpoint {
  param(
    [string]$Url,
    [int]$DefaultPort
  )

  try {
    $uri = [System.Uri]$Url
    $port = if ($uri.IsDefaultPort) { $DefaultPort } else { $uri.Port }
    return @{ Host = $uri.Host; Port = $port; Url = $Url.TrimEnd("/") }
  } catch {
    return @{ Host = "127.0.0.1"; Port = $DefaultPort; Url = $Url.TrimEnd("/") }
  }
}

function Test-TcpPort {
  param(
    [string]$HostName,
    [int]$Port
  )

  try {
    $client = [System.Net.Sockets.TcpClient]::new()
    $task = $client.ConnectAsync($HostName, $Port)
    if (-not $task.Wait(2000)) {
      $client.Dispose()
      return $false
    }
    $client.Dispose()
    return $true
  } catch {
    return $false
  }
}

function Start-Infrastructure {
  $databaseUrl = Get-DotEnvValue -Key "DATABASE_URL" -DefaultValue "postgresql+psycopg://postgres:postgres@localhost:5432/course_kg"
  $qdrantUrl = Get-DotEnvValue -Key "QDRANT_URL" -DefaultValue "http://localhost:6333"
  $postgres = Get-PostgresEndpoint -DatabaseUrl $databaseUrl
  $qdrant = Get-HttpEndpoint -Url $qdrantUrl -DefaultPort 6333

  Write-Host "Starting infrastructure with Docker Compose:" -ForegroundColor Cyan
  Write-Host "  PostgreSQL, Redis, and Qdrant are managed by: $InfraComposeFile"
  Write-Host "  Expected PostgreSQL: $($postgres.Host):$($postgres.Port)"
  Write-Host "  Expected Qdrant: $($qdrant.Url)"

  if (-not (Test-Path $InfraComposeFile)) {
    throw "Docker Compose file not found: $InfraComposeFile"
  }

  & docker compose -f $InfraComposeFile up -d
  if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed to start infrastructure. Make sure Docker Desktop is running."
  }

  $deadline = (Get-Date).AddSeconds(90)
  do {
    $postgresOk = Test-TcpPort -HostName $postgres.Host -Port $postgres.Port
    $qdrantOk = Test-TcpPort -HostName $qdrant.Host -Port $qdrant.Port
    if ($postgresOk -and $qdrantOk) {
      Write-Host "Infrastructure is reachable." -ForegroundColor Green
      return
    }
    Start-Sleep -Seconds 1
  } while ((Get-Date) -lt $deadline)

  Write-Host ""
  Write-Host "Infrastructure did not become reachable:" -ForegroundColor Red
  if (-not $postgresOk) {
    Write-Host "  - PostgreSQL is not reachable at $($postgres.Host):$($postgres.Port)"
  }
  if (-not $qdrantOk) {
    Write-Host "  - Qdrant is not reachable at $($qdrant.Host):$($qdrant.Port)"
  }
  throw "Docker Compose started, but required infrastructure ports are not ready."
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
    [int]$TimeoutSeconds = 90
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

function Get-NodeVersion {
  param([string]$NodeExe)
  try {
    $version = & $NodeExe --version 2>$null
    if ($version -match '^v?(\d+)\.(\d+)\.(\d+)') {
      return [version]"$($Matches[1]).$($Matches[2]).$($Matches[3])"
    }
  } catch {
    return $null
  }
  return $null
}

function Find-Node {
  $candidates = New-Object System.Collections.Generic.List[string]

  $pathNode = Get-Command node.exe -ErrorAction SilentlyContinue
  if ($pathNode) {
    $candidates.Add($pathNode.Source)
  }

  $codexNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
  $candidates.Add($codexNode)
  $candidates.Add("C:\Program Files\nodejs\node.exe")

  foreach ($candidate in $candidates) {
    if (-not (Test-Path $candidate)) {
      continue
    }
    $version = Get-NodeVersion $candidate
    if ($version -and $version -ge [version]"20.9.0") {
      return $candidate
    }
  }

  throw "Node.js >= 20.9.0 was not found. Install Node 20+ or run this from Codex where the bundled runtime is available."
}

function Start-Window {
  param(
    [string]$Title,
    [string]$WorkingDirectory,
    [string]$Command
  )

  $wrapped = "`$Host.UI.RawUI.WindowTitle = '$Title'; $Command"
  Start-Process powershell.exe -WorkingDirectory $WorkingDirectory -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-Command", $wrapped
  )
}

$BackendUrl = "http://127.0.0.1:$BackendPort/api/courses/current/graph"
$FrontendUrl = "http://127.0.0.1:$FrontendPort$OpenPath"

Write-Host "Course Knowledge Base launcher" -ForegroundColor Cyan
Write-Host "Root: $Root"
Start-Infrastructure

if (-not (Test-Url $BackendUrl)) {
  $pythonExe = Join-Path $ApiDir ".venv\Scripts\python.exe"
  if (-not (Test-Path $pythonExe)) {
    $pythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $pythonCmd) {
      throw "Backend Python was not found. Expected $pythonExe or python.exe in PATH."
    }
    $pythonExe = $pythonCmd.Source
  }

  Write-Host "Starting backend on port $BackendPort ..."
  Start-Window `
    -Title "Knowledge Base API" `
    -WorkingDirectory $ApiDir `
    -Command "& '$pythonExe' -m uvicorn app.main:app --host 127.0.0.1 --port $BackendPort"
} else {
  Write-Host "Backend already running on port $BackendPort." -ForegroundColor Yellow
}

if (-not (Test-Url $FrontendUrl)) {
  $nodeExe = Find-Node
  $nextBin = Join-Path $WebDir "node_modules\next\dist\bin\next"
  if (-not (Test-Path $nextBin)) {
    throw "Next.js binary was not found at $nextBin. Run npm install in the repo root first."
  }

  Write-Host "Starting frontend on port $FrontendPort with $nodeExe ..."
  $apiBase = "http://127.0.0.1:$BackendPort/api"
  Start-Window `
    -Title "Knowledge Base Web" `
    -WorkingDirectory $WebDir `
    -Command "`$env:NEXT_PUBLIC_API_BASE_URL = '$apiBase'; & '$nodeExe' '$nextBin' dev --hostname 127.0.0.1 --port $FrontendPort"
} else {
  Write-Host "Frontend already running on port $FrontendPort." -ForegroundColor Yellow
}

Wait-Url -Url $BackendUrl -Name "Backend"
Wait-Url -Url $FrontendUrl -Name "Frontend"

if (-not $NoBrowser) {
  Write-Host "Opening $FrontendUrl"
  Start-Process $FrontendUrl
}

Write-Host "Done. Close the API/Web PowerShell windows to stop the services." -ForegroundColor Green

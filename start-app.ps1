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

param(
    [switch]$SkipInstall,
    [switch]$Check,
    [string]$HostOverride,
    [int]$PortOverride
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = $PSScriptRoot
$VenvDir = Join-Path $ScriptDir "venv"
$RequirementsFile = Join-Path $ScriptDir "requirements.txt"
$RequirementsHashFile = Join-Path $VenvDir ".requirements.sha256"
$ConfigFile = Join-Path $ScriptDir "config.yaml"
$ConfigExampleFile = Join-Path $ScriptDir "config.example.yaml"
$EnvFile = Join-Path $ScriptDir ".env"
$EnvExampleFile = Join-Path $ScriptDir ".env.example"

function Write-Info {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "OK  $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "WARN $Message" -ForegroundColor Yellow
}

function Stop-WithError {
    param([string]$Message)
    Write-Host "ERROR $Message" -ForegroundColor Red
    exit 1
}

function Get-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    return $null
}

function Get-FileSha256 {
    param([string]$Path)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Ensure-TemplateFile {
    param(
        [string]$Target,
        [string]$Template,
        [string]$Label
    )

    if (Test-Path -LiteralPath $Target) {
        return
    }
    if (Test-Path -LiteralPath $Template) {
        Copy-Item -LiteralPath $Template -Destination $Target
        Write-Warn "$Label was missing; created from $(Split-Path -Leaf $Template)"
    }
    else {
        Write-Warn "$Label is missing and no template was found"
    }
}

function Get-DotEnvValue {
    param([string]$Key)

    if (!(Test-Path -LiteralPath $EnvFile)) {
        return ""
    }

    $line = Get-Content -LiteralPath $EnvFile |
        Where-Object { $_ -match "^\s*$([regex]::Escape($Key))=" } |
        Select-Object -Last 1

    if (!$line) {
        return ""
    }

    return (($line -split "=", 2)[1]).Trim().Trim('"').Trim("'")
}

function Validate-Placeholders {
    if (!(Test-Path -LiteralPath $EnvFile)) {
        Write-Warn ".env is missing; Telegram credentials must come from the shell environment"
        return
    }

    $botToken = if ($env:BOT_TOKEN) { $env:BOT_TOKEN } else { Get-DotEnvValue "BOT_TOKEN" }
    $userApiId = if ($env:USER_API_ID) { $env:USER_API_ID } else { Get-DotEnvValue "USER_API_ID" }
    $userApiHash = if ($env:USER_API_HASH) { $env:USER_API_HASH } else { Get-DotEnvValue "USER_API_HASH" }

    if (!$botToken) { Write-Warn "BOT_TOKEN is empty" }
    if (!$userApiId) { Write-Warn "USER_API_ID is empty" }
    if (!$userApiHash) { Write-Warn "USER_API_HASH is empty" }

    $envText = Get-Content -Raw -LiteralPath $EnvFile
    if ($envText -match "你的|浣犵殑|your_") {
        Write-Warn ".env still appears to contain placeholder values"
    }
}

function Ensure-ProductionSafety {
    $appEnv = if ($env:APP_ENV) { $env:APP_ENV } else { Get-DotEnvValue "APP_ENV" }
    $webHost = if ($HostOverride) {
        $HostOverride
    }
    elseif ($env:WEB_HOST) {
        $env:WEB_HOST
    }
    else {
        Get-DotEnvValue "WEB_HOST"
    }
    $webApiKey = if ($env:WEB_API_KEY) { $env:WEB_API_KEY } else { Get-DotEnvValue "WEB_API_KEY" }

    if (($appEnv -in @("production", "prod")) -or ($webHost -eq "0.0.0.0")) {
        if (!$webApiKey) {
            Stop-WithError "WEB_API_KEY is required when APP_ENV=production or WEB_HOST=0.0.0.0"
        }
    }
}

function Install-DependenciesIfNeeded {
    if (!(Test-Path -LiteralPath $RequirementsFile)) {
        Stop-WithError "requirements.txt not found"
    }
    if ($SkipInstall) {
        Write-Warn "Skipping dependency installation"
        return
    }

    $currentHash = Get-FileSha256 $RequirementsFile
    $previousHash = ""
    if (Test-Path -LiteralPath $RequirementsHashFile) {
        $previousHash = (Get-Content -Raw -LiteralPath $RequirementsHashFile).Trim()
    }

    if ($currentHash -eq $previousHash) {
        Write-Ok "Dependencies are up to date"
        return
    }

    Write-Info "Installing dependencies"
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install -r $RequirementsFile
    Set-Content -LiteralPath $RequirementsHashFile -Value $currentHash -NoNewline
}

Write-Host "=================================================" -ForegroundColor Blue
Write-Host "Telegram Media Downloader startup" -ForegroundColor Green
Write-Host "=================================================" -ForegroundColor Blue
Write-Info "Project directory: $ScriptDir"

$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
if (!(Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = Join-Path $VenvDir "bin\python"
}

if (!(Test-Path -LiteralPath $PythonExe)) {
    $SystemPython = Get-PythonCommand
    if (!$SystemPython) {
        Stop-WithError "Python was not found. Install Python 3.11+ and add it to PATH."
    }
    Write-Ok "Found Python: $SystemPython"
    Write-Info "Creating virtual environment"
    & $SystemPython -m venv $VenvDir
    $PythonExe = Join-Path $VenvDir "Scripts\python.exe"
    if (!(Test-Path -LiteralPath $PythonExe)) {
        $PythonExe = Join-Path $VenvDir "bin\python"
    }
}

if (!(Test-Path -LiteralPath $PythonExe)) {
    Stop-WithError "Virtual environment Python not found: $PythonExe"
}
Write-Ok "Using virtual environment: $VenvDir"

Install-DependenciesIfNeeded
Ensure-TemplateFile $ConfigFile $ConfigExampleFile "config.yaml"
Ensure-TemplateFile $EnvFile $EnvExampleFile ".env"
Validate-Placeholders
Ensure-ProductionSafety


if ($HostOverride) {
    $env:WEB_HOST = $HostOverride
}
if ($PortOverride -gt 0) {
    $env:WEB_PORT = [string]$PortOverride
}

if ($Check) {
    Write-Ok "Environment check completed"
    exit 0
}

$displayHost = if ($env:WEB_HOST) { $env:WEB_HOST } else { "127.0.0.1" }
$displayPort = if ($env:WEB_PORT) { $env:WEB_PORT } else { "8000" }

Write-Info "Starting app"
Write-Info "Web console: http://${displayHost}:${displayPort}"
Set-Location -LiteralPath $ScriptDir
& $PythonExe (Join-Path $ScriptDir "main.py")

# Telegram Media Downloader - Windows Startup Script
# This script handles installation, configuration check, and startup

param(
    [switch]$SkipInstall,
    [switch]$SkipConfigCheck,
    [switch]$Help,
    [switch]$ShowLogs
)

# Colors for output (PowerShell)
$Colors = @{
    Red = [ConsoleColor]::Red
    Green = [ConsoleColor]::Green
    Yellow = [ConsoleColor]::Yellow
    Blue = [ConsoleColor]::Blue
    White = [ConsoleColor]::White
}

function Write-ColoredOutput {
    param([string]$Message, [ConsoleColor]$Color = [ConsoleColor]::White)
    Write-Host $Message -ForegroundColor $Color
}

function Write-Status { param([string]$Message) Write-ColoredOutput "[INFO] $Message" $Colors.Blue }
function Write-Success { param([string]$Message) Write-ColoredOutput "[SUCCESS] $Message" $Colors.Green }
function Write-Warning { param([string]$Message) Write-ColoredOutput "[WARNING] $Message" $Colors.Yellow }
function Write-Error { param([string]$Message) Write-ColoredOutput "[ERROR] $Message" $Colors.Red }

# Help function
function Show-Help {
    Write-Host "Telegram Media Downloader - Windows Startup Script" -ForegroundColor Cyan
    Write-Host "=================================================="
    Write-Host ""
    Write-Host "Usage: .\start.ps1 [options]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -SkipInstall      Skip dependency installation"
    Write-Host "  -SkipConfigCheck  Skip configuration validation"
    Write-Host "  -ShowLogs         Show application logs in real-time (keeps window open)"
    Write-Host "  -Help            Show this help message"
    Write-Host ""
    Write-Host "This script will:"
    Write-Host "  1. Check Node.js and npm installation"
    Write-Host "  2. Install dependencies (unless -SkipInstall)"
    Write-Host "  3. Validate configuration (unless -SkipConfigCheck)"
    Write-Host "  4. Start the application"
    Write-Host "  5. Show logs in real-time (if -ShowLogs is specified)"
    Write-Host ""
}

if ($Help) {
    Show-Help
    exit 0
}

Write-Host "🤖 Telegram Media Downloader - Windows Startup Script" -ForegroundColor Cyan
Write-Host "=================================================="
Write-Host ""

# Function to check Node.js
function Test-NodeJs {
    try {
        $nodeVersion = & node --version 2>$null
        if ($LASTEXITCODE -ne 0) { throw "Node.js not found" }

        $version = $nodeVersion -replace '^v', ''
        $majorVersion = [int]($version -split '\.')[0]

        if ($majorVersion -lt 18) {
            Write-Error "Node.js version $version is too old. Please upgrade to Node.js 18+"
            Write-Error "Download from: https://nodejs.org/"
            exit 1
        }

        Write-Success "Node.js version: $version"
        return $true
    }
    catch {
        Write-Error "Node.js is not installed or not in PATH."
        Write-Error "Please install Node.js 18+ from: https://nodejs.org/"
        exit 1
    }
}

# Function to check npm
function Test-Npm {
    try {
        $npmVersion = & npm --version 2>$null
        if ($LASTEXITCODE -ne 0) { throw "npm not found" }

        Write-Success "npm version: $npmVersion"
        return $true
    }
    catch {
        Write-Error "npm is not installed or not in PATH."
        Write-Error "npm should be included with Node.js installation."
        exit 1
    }
}

# Function to install dependencies
function Install-Dependencies {
    if ($SkipInstall) {
        Write-Status "Skipping dependency installation (-SkipInstall)"
        return
    }

    Write-Status "Installing dependencies..."

    if (!(Test-Path "package.json")) {
        Write-Error "package.json not found. Are you in the correct directory?"
        exit 1
    }

    try {
        & npm install
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Dependencies installed successfully"
        } else {
            Write-Error "Failed to install dependencies"
            exit 1
        }
    }
    catch {
        Write-Error "Failed to run npm install: $($_.Exception.Message)"
        exit 1
    }
}

# Function to check configuration
function Test-Configuration {
    if ($SkipConfigCheck) {
        Write-Status "Skipping configuration check (-SkipConfigCheck)"
        return
    }

    Write-Status "Checking configuration..."

    $configComplete = $true

    # Check .env file
    if (Test-Path ".env") {
        Write-Success ".env file exists"

        $envLines = Get-Content ".env"

        # Check required environment variables
        $requiredVars = @("BOT_TOKEN", "BOT_API_HOST", "PUBLIC_FILE_BASE_URL")

        foreach ($var in $requiredVars) {
            $found = $false
            foreach ($line in $envLines) {
                if ($line -match "^$var=") {
                    $found = $true
                    break
                }
            }
            if (-not $found) {
                Write-Warning "Environment variable ${var} not found in .env"
                $configComplete = $false
            }
        }

        # Check optional user API variables
        $userApiIdFound = $false
        $userApiHashFound = $false
        foreach ($line in $envLines) {
            if ($line -match "^USER_API_ID=") { $userApiIdFound = $true }
            if ($line -match "^USER_API_HASH=") { $userApiHashFound = $true }
        }
        if ($userApiIdFound -and $userApiHashFound) {
            Write-Success "User API configuration found (channel features enabled)"
        } else {
            Write-Warning "User API not configured (channel features disabled)"
        }
    } else {
        Write-Warning ".env file not found. Checking config.yaml..."

        if (!(Test-Path "config.yaml")) {
            Write-Error "Neither .env nor config.yaml found. Please create configuration file."
            $configComplete = $false
        } else {
            Write-Success "config.yaml file exists"
            $yamlContent = Get-Content "config.yaml" -Raw
            # Basic check for bot_token
            if ($yamlContent -notmatch "bot_token:") {
                Write-Warning "bot_token not found in config.yaml"
                $configComplete = $false
            }
        }
    }

    if ($configComplete) {
        Write-Success "Configuration check passed"
    } else {
        Write-Warning "Configuration incomplete. Please check your settings."
        Write-Host ""
        Write-Status "Required settings:"
        Write-Host "  - BOT_TOKEN (from @BotFather)"
        Write-Host "  - BOT_API_HOST (your Bot API server)"
        Write-Host "  - PUBLIC_FILE_BASE_URL (public file access URL)"
        Write-Host ""
        Write-Status "Optional (for channel features):"
        Write-Host "  - USER_API_ID and USER_API_HASH (from https://my.telegram.org)"
        Write-Host ""

        $continue = Read-Host "Continue anyway? (y/N)"
        if ($continue -notmatch "^[Yy]$") {
            exit 1
        }
    }
}

# Function to start the application
function Start-Application {
    Write-Status "Starting Telegram Media Downloader..."

    # Check if already running
    $existingProcess = Get-Process node -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -like "*src/index.js*"
    }

    if ($existingProcess) {
        Write-Warning "Application appears to be already running (PID: $($existingProcess.Id))"
        $kill = Read-Host "Kill existing process and start new one? (y/N)"
        if ($kill -match "^[Yy]$") {
            Stop-Process -Id $existingProcess.Id -Force
            Start-Sleep -Seconds 2
        } else {
            Write-Status "Exiting..."
            exit 0
        }
    }

    # Start the application
    try {
        if ($ShowLogs) {
            Write-Status "Starting application in foreground (showing logs)..."
            Write-Status "Press Ctrl+C to stop the application"
            Write-Host ""

            # Run in foreground to show logs
            & npm start
        } else {
            Write-Status "Starting application with: npm start"

            # Use Start-Process to run in background
            $process = Start-Process -FilePath "npm" -ArgumentList "start" -PassThru

            if ($process) {
                $processId = $process.Id
                Write-Success "Application started with PID: $processId"

                # Wait a moment and check if process is still running
                Start-Sleep -Seconds 2
                if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
                    Write-Success "Application is running successfully"
                    Write-Status "Use 'Stop-Process -Id $processId' to stop the application"
                    Write-Status "Check logs with: Get-Content combined.log -Tail 10 -Wait"
                    Write-Status "Or run with -ShowLogs to see logs in real-time"
                } else {
                    Write-Error "Application process exited immediately. Check logs for errors."
                    exit 1
                }
            } else {
                Write-Error "Failed to start application process"
                exit 1
            }
        }
    }
    catch {
        Write-Error "Failed to start application: $($_.Exception.Message)"
        exit 1
    }
}

# Main script execution
try {
    # Check prerequisites
    Test-NodeJs
    Test-Npm

    Write-Host ""

    # Install dependencies
    Install-Dependencies

    Write-Host ""

    # Check configuration
    Test-Configuration

    Write-Host ""

    # Start application
    Start-Application

    Write-Host ""
    if ($ShowLogs) {
        Write-Success "🎉 Application is running with logs displayed above."
        Write-Status "Press Ctrl+C to stop the application."
    } else {
        Write-Success "🎉 Setup complete! Telegram Media Downloader is now running."
        Write-Status "You can now send media files to your bot or use the commands."

        # Keep window open
        Write-Host ""
        Read-Host "Press Enter to exit"
    }
}
catch {
    Write-Error "Script execution failed: $($_.Exception.Message)"
    exit 1
}
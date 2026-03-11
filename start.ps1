# 设置 PowerShell 控制台输出编码为 UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 获取脚本所在目录
$SCRIPT_DIR = $PSScriptRoot
$VENV_PATH = Join-Path $SCRIPT_DIR "venv"

Write-Host ">>> Checking environment (dir: $SCRIPT_DIR)..." -ForegroundColor Green

# 1. 检查 Python
if (!(Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Python not found. Please install Python and add it to PATH." -ForegroundColor Red
    exit
}

# 2. 创建虚拟环境
if (!(Test-Path $VENV_PATH)) {
    Write-Host ">>> Creating virtual environment..." -ForegroundColor Green
    python -m venv $VENV_PATH
}

# Python路径
$PYTHON_EXE = Join-Path $VENV_PATH "Scripts\python.exe"
if (!(Test-Path $PYTHON_EXE)) {
    $PYTHON_EXE = Join-Path $VENV_PATH "bin/python"
}

# 3. 安装依赖
Write-Host ">>> Installing dependencies..." -ForegroundColor Green
& $PYTHON_EXE -m pip install --upgrade pip --quiet
& $PYTHON_EXE -m pip install -r "$SCRIPT_DIR\requirements.txt" --quiet

# 4. 配置文件
$CONFIG_FILE = Join-Path $SCRIPT_DIR "config.yaml"
if (!(Test-Path $CONFIG_FILE)) {

    $EXAMPLE = Join-Path $SCRIPT_DIR "config.yaml.example"

    if (Test-Path $EXAMPLE) {
        Write-Host ">>> Copying config.yaml.example..." -ForegroundColor Yellow
        Copy-Item $EXAMPLE $CONFIG_FILE
    }
    else {
        Write-Host "Warning: config.yaml not found." -ForegroundColor Yellow
    }
}

# 5. .env
$ENV_FILE = Join-Path $SCRIPT_DIR ".env"

if (!(Test-Path $ENV_FILE)) {

    $EXAMPLE = Join-Path $SCRIPT_DIR ".env.example"

    if (Test-Path $EXAMPLE) {
        Write-Host ">>> Copying .env.example..." -ForegroundColor Yellow
        Copy-Item $EXAMPLE $ENV_FILE
    }
}

Write-Host ">>> Starting bot..." -ForegroundColor Green

Set-Location "$SCRIPT_DIR"
& $PYTHON_EXE main.py
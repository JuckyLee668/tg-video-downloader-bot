#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
VENV_DIR="$ROOT_DIR/venv"
REQUIREMENTS_FILE="$ROOT_DIR/requirements.txt"
REQUIREMENTS_HASH_FILE="$VENV_DIR/.requirements.sha256"
CONFIG_FILE="$ROOT_DIR/config.yaml"
CONFIG_EXAMPLE_FILE="$ROOT_DIR/config.example.yaml"
ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE_FILE="$ROOT_DIR/.env.example"

SKIP_INSTALL=0
CHECK_ONLY=0
WEB_HOST_OVERRIDE=""
WEB_PORT_OVERRIDE=""

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { printf "%b\n" "${BLUE}==>${NC} $*"; }
ok() { printf "%b\n" "${GREEN}OK${NC} $*"; }
warn() { printf "%b\n" "${YELLOW}WARN${NC} $*"; }
fail() { printf "%b\n" "${RED}ERROR${NC} $*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
Usage: ./start.sh [options]

Options:
  --skip-install       Skip dependency installation
  --check              Check environment only, do not start the app
  --host HOST          Override WEB_HOST for this run
  --port PORT          Override WEB_PORT for this run
  -h, --help           Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-install)
            SKIP_INSTALL=1
            shift
            ;;
        --check)
            CHECK_ONLY=1
            shift
            ;;
        --host)
            [[ $# -ge 2 ]] || fail "--host requires a value"
            WEB_HOST_OVERRIDE="$2"
            shift 2
            ;;
        --port)
            [[ $# -ge 2 ]] || fail "--port requires a value"
            WEB_PORT_OVERRIDE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown option: $1"
            ;;
    esac
done

find_python() {
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return
    fi
    if command -v python >/dev/null 2>&1; then
        command -v python
        return
    fi
    return 1
}

hash_file() {
    local file="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$file" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$file" | awk '{print $1}'
    else
        "$PYTHON_EXE" -c "import hashlib, pathlib; print(hashlib.sha256(pathlib.Path('$file').read_bytes()).hexdigest())"
    fi
}

ensure_template_file() {
    local target="$1"
    local template="$2"
    local label="$3"

    if [[ -f "$target" ]]; then
        return
    fi
    if [[ -f "$template" ]]; then
        cp "$template" "$target"
        warn "$label was missing; created from $(basename "$template")"
    else
        warn "$label is missing and no template was found"
    fi
}

env_value() {
    local key="$1"
    if [[ ! -f "$ENV_FILE" ]]; then
        return 0
    fi
    grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '"' | tr -d "'"
}

validate_placeholders() {
    if [[ ! -f "$ENV_FILE" ]]; then
        warn ".env is missing; Telegram credentials must come from the shell environment"
        return
    fi

    local bot_token user_api_id user_api_hash
    bot_token="$(env_value BOT_TOKEN || true)"
    user_api_id="$(env_value USER_API_ID || true)"
    user_api_hash="$(env_value USER_API_HASH || true)"

    [[ -n "${BOT_TOKEN:-$bot_token}" ]] || warn "BOT_TOKEN is empty"
    [[ -n "${USER_API_ID:-$user_api_id}" ]] || warn "USER_API_ID is empty"
    [[ -n "${USER_API_HASH:-$user_api_hash}" ]] || warn "USER_API_HASH is empty"

    if grep -Eq "你的|浣犵殑|your_" "$ENV_FILE"; then
        warn ".env still appears to contain placeholder values"
    fi
}

ensure_production_safety() {
    local app_env="${APP_ENV:-}"
    local web_host="${WEB_HOST_OVERRIDE:-${WEB_HOST:-}}"
    local web_api_key="${WEB_API_KEY:-}"

    if [[ -z "$web_api_key" && -f "$ENV_FILE" ]]; then
        web_api_key="$(env_value WEB_API_KEY || true)"
    fi
    if [[ -z "$app_env" && -f "$ENV_FILE" ]]; then
        app_env="$(env_value APP_ENV || true)"
    fi
    if [[ -z "$web_host" && -f "$ENV_FILE" ]]; then
        web_host="$(env_value WEB_HOST || true)"
    fi

    if [[ "$app_env" == "production" || "$app_env" == "prod" || "$web_host" == "0.0.0.0" ]]; then
        [[ -n "$web_api_key" ]] || fail "WEB_API_KEY is required when APP_ENV=production or WEB_HOST=0.0.0.0"
    fi
}

install_dependencies_if_needed() {
    [[ -f "$REQUIREMENTS_FILE" ]] || fail "requirements.txt not found"
    [[ "$SKIP_INSTALL" -eq 0 ]] || { warn "Skipping dependency installation"; return; }

    local current_hash previous_hash
    current_hash="$(hash_file "$REQUIREMENTS_FILE")"
    previous_hash=""
    [[ -f "$REQUIREMENTS_HASH_FILE" ]] && previous_hash="$(cat "$REQUIREMENTS_HASH_FILE")"

    if [[ "$current_hash" == "$previous_hash" ]]; then
        ok "Dependencies are up to date"
        return
    fi

    info "Installing dependencies"
    "$PYTHON_EXE" -m pip install --upgrade pip
    "$PYTHON_EXE" -m pip install -r "$REQUIREMENTS_FILE"
    printf "%s" "$current_hash" > "$REQUIREMENTS_HASH_FILE"
}

printf "%b\n" "${BLUE}=================================================${NC}"
printf "%b\n" "${GREEN}Telegram Media Downloader startup${NC}"
printf "%b\n" "${BLUE}=================================================${NC}"
info "Project directory: $ROOT_DIR"

PYTHON_EXE="$VENV_DIR/bin/python"
if [[ ! -x "$PYTHON_EXE" ]]; then
    SYSTEM_PYTHON="$(find_python)" || fail "Python was not found. Install Python 3.11+ first."
    ok "Found Python: $SYSTEM_PYTHON"
    info "Creating virtual environment"
    "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
fi

[[ -x "$PYTHON_EXE" ]] || fail "Virtual environment Python not found: $PYTHON_EXE"
ok "Using virtual environment: $VENV_DIR"

install_dependencies_if_needed
ensure_template_file "$CONFIG_FILE" "$CONFIG_EXAMPLE_FILE" "config.yaml"
ensure_template_file "$ENV_FILE" "$ENV_EXAMPLE_FILE" ".env"
validate_placeholders
ensure_production_safety

if [[ -n "$WEB_HOST_OVERRIDE" ]]; then
    export WEB_HOST="$WEB_HOST_OVERRIDE"
fi
if [[ -n "$WEB_PORT_OVERRIDE" ]]; then
    export WEB_PORT="$WEB_PORT_OVERRIDE"
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    ok "Environment check completed"
    exit 0
fi

info "Starting app"
info "Web console: http://${WEB_HOST:-127.0.0.1}:${WEB_PORT:-8000}"
cd "$ROOT_DIR"
exec "$PYTHON_EXE" "$ROOT_DIR/main.py"

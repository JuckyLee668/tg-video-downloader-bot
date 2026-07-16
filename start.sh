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
PID_FILE="$ROOT_DIR/data/bot.pid"

SKIP_INSTALL=0
CHECK_ONLY=0
WEB_HOST_OVERRIDE=""
WEB_PORT_OVERRIDE=""
FORCE_RESTART=0

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { printf "%b\n" "${BLUE}==>${NC} $*"; }
ok()    { printf "%b\n" "   ${GREEN}✅${NC} $*"; }
warn()  { printf "%b\n" "   ${YELLOW}⚠️${NC} $*"; }
fail()  { printf "%b\n" "   ${RED}❌${NC} $*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
Usage: ./start.sh [options]

Options:
  --skip-install       Skip dependency installation
  --check              Check environment only, do not start the app
  --force              Kill existing instance and restart
  --host HOST          Override WEB_HOST for this run
  --port PORT          Override WEB_PORT for this run
  -h, --help           Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-install) SKIP_INSTALL=1; shift ;;
        --check)        CHECK_ONLY=1; shift ;;
        --force)        FORCE_RESTART=1; shift ;;
        --host)         [[ $# -ge 2 ]] || fail "--host requires a value"
                        WEB_HOST_OVERRIDE="$2"; shift 2 ;;
        --port)         [[ $# -ge 2 ]] || fail "--port requires a value"
                        WEB_PORT_OVERRIDE="$2"; shift 2 ;;
        -h|--help)      usage; exit 0 ;;
        *)              fail "Unknown option: $1" ;;
    esac
done

# ── Banner ───────────────────────────────────────────────────────────

echo ""
printf "%b\n" "${BLUE}╔══════════════════════════════════════════════╗${NC}"
printf "%b\n" "${BLUE}║${NC}   ${GREEN}Telegram Media Downloader${NC}                    ${BLUE}║${NC}"
printf "%b\n" "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── 1. System tools ──────────────────────────────────────────────────

info "检查系统工具..."

check_cmd() {
    if command -v "$1" &>/dev/null; then
        ok "$1 ($(command -v "$1"))"
        return 0
    else
        warn "$1 未安装"
        return 1
    fi
}

MISSING_TOOLS=()

check_cmd python3 || {
    # Maybe just 'python'
    command -v python &>/dev/null || MISSING_TOOLS+=("python3 (>=3.11)")
}

check_cmd wget || MISSING_TOOLS+=("wget")
check_cmd unzip || MISSING_TOOLS+=("unzip")

# ffmpeg — optional but highly recommended for yt-dlp
if check_cmd ffmpeg; then
    FFMPEG_VER=$(ffmpeg -version 2>&1 | head -1)
    ok "ffmpeg: $FFMPEG_VER"
else
    MISSING_TOOLS+=("ffmpeg (yt-dlp 合并视频需要)")
fi

# git — only needed for updates
check_cmd git || true

if [[ ${#MISSING_TOOLS[@]} -gt 0 ]]; then
    echo ""
    warn "缺少以下工具，请先安装："
    for t in "${MISSING_TOOLS[@]}"; do
        echo "       - $t"
    done
    echo ""
    echo "   Debian/Ubuntu: apt install python3 python3-venv wget unzip ffmpeg"
    echo "   CentOS/RHEL:   yum install python3 python3-pip wget unzip ffmpeg"
    echo "   macOS:         brew install python3 wget ffmpeg"
    echo ""
    # wget/unzip are hard requirements for aliyunpan install
    if [[ " ${MISSING_TOOLS[*]} " =~ " wget " ]] || [[ " ${MISSING_TOOLS[*]} " =~ " unzip " ]]; then
        fail "wget 和 unzip 是必需的依赖，请先安装"
    fi
fi

# ── 2. Python environment ────────────────────────────────────────────

info "检查 Python 环境..."

find_python() {
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
    elif command -v python >/dev/null 2>&1; then
        command -v python
    else
        return 1
    fi
}

SYSTEM_PYTHON="$(find_python)" || fail "未找到 Python，请安装 Python 3.11+"

PYTHON_VER=$("$SYSTEM_PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$("$SYSTEM_PYTHON" -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$("$SYSTEM_PYTHON" -c "import sys; print(sys.version_info.minor)")

if [[ "$PYTHON_MAJOR" -lt 3 ]] || { [[ "$PYTHON_MAJOR" -eq 3 ]] && [[ "$PYTHON_MINOR" -lt 10 ]]; }; then
    fail "需要 Python 3.10+，当前版本: $PYTHON_VER ($SYSTEM_PYTHON)"
fi
ok "Python $PYTHON_VER ($SYSTEM_PYTHON)"

# Virtual environment
PYTHON_EXE="$VENV_DIR/bin/python"
if [[ ! -x "$PYTHON_EXE" ]]; then
    info "创建虚拟环境: $VENV_DIR"
    "$SYSTEM_PYTHON" -m venv "$VENV_DIR" || fail "创建虚拟环境失败"
    ok "虚拟环境已创建"
fi
ok "虚拟环境: $VENV_DIR"

# ── 3. Dependencies ──────────────────────────────────────────────────

hash_file() {
    local file="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$file" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$file" | awk '{print $1}'
    else
        "$PYTHON_EXE" -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('$file').read_bytes()).hexdigest())"
    fi
}

install_dependencies_if_needed() {
    [[ -f "$REQUIREMENTS_FILE" ]] || fail "requirements.txt 不存在"
    [[ "$SKIP_INSTALL" -eq 0 ]] || { warn "跳过依赖安装 (--skip-install)"; return; }

    local current_hash previous_hash
    current_hash="$(hash_file "$REQUIREMENTS_FILE")"
    previous_hash=""
    [[ -f "$REQUIREMENTS_HASH_FILE" ]] && previous_hash="$(cat "$REQUIREMENTS_HASH_FILE")"

    if [[ "$current_hash" == "$previous_hash" ]]; then
        ok "依赖已是最新"
        return
    fi

    info "安装 Python 依赖..."
    "$PYTHON_EXE" -m pip install --upgrade pip -q
    "$PYTHON_EXE" -m pip install -r "$REQUIREMENTS_FILE" -q || fail "依赖安装失败"
    printf "%s" "$current_hash" > "$REQUIREMENTS_HASH_FILE"
    ok "依赖安装完成"
}

install_dependencies_if_needed

# ── 4. Critical package smoke test ───────────────────────────────────

info "检查关键依赖..."

SMOKE_CHECKS=(
    "telethon:Telethon"
    "yt_dlp:yt-dlp"
    "fastapi:FastAPI"
    "aiosqlite:aiosqlite"
    "loguru:loguru"
    "pydantic:pydantic"
)

for check in "${SMOKE_CHECKS[@]}"; do
    pkg="${check%%:*}"
    label="${check##*:}"
    if "$PYTHON_EXE" -c "import $pkg" 2>/dev/null; then
        ok "$label"
    else
        warn "$label — 尝试重新安装..."
        "$PYTHON_EXE" -m pip install "$pkg" -q || fail "$label 安装失败"
        ok "$label (已修复)"
    fi
done

# ── 5. Config files ──────────────────────────────────────────────────

info "检查配置文件..."

ensure_template() {
    local target="$1" template="$2" label="$3"
    if [[ -f "$target" ]]; then
        ok "$label 已存在"
        return
    fi
    if [[ -f "$template" ]]; then
        cp "$template" "$target"
        warn "$label 从模板创建 — 请编辑填入实际配置"
    else
        warn "$label 不存在，将使用默认配置"
    fi
}

ensure_template "$CONFIG_FILE" "$CONFIG_EXAMPLE_FILE" "config.yaml"
ensure_template "$ENV_FILE" "$ENV_EXAMPLE_FILE" ".env"

# Validate critical env vars
env_value() {
    local key="$1"
    [[ -f "$ENV_FILE" ]] || return 0
    grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'"
}

BOT_TOKEN="${BOT_TOKEN:-$(env_value BOT_TOKEN || true)}"
USER_API_ID="${USER_API_ID:-$(env_value USER_API_ID || true)}"
USER_API_HASH="${USER_API_HASH:-$(env_value USER_API_HASH || true)}"

[[ -n "$BOT_TOKEN" ]] || warn "BOT_TOKEN 未设置 — Bot 客户端无法启动"
[[ -n "$USER_API_ID" ]] || warn "USER_API_ID 未设置 — User 客户端无法启动"
[[ -n "$USER_API_HASH" ]] || warn "USER_API_HASH 未设置 — User 客户端无法启动"

if [[ -f "$ENV_FILE" ]] && grep -qE "你的|your_|change_me|xxxxxxxxxxxx" "$ENV_FILE" 2>/dev/null; then
    warn ".env 包含示例占位符，请编辑填入真实凭证"
fi

# ── 6. Production safety ─────────────────────────────────────────────

APP_ENV="${APP_ENV:-$(env_value APP_ENV || true)}"
WEB_HOST="${WEB_HOST_OVERRIDE:-${WEB_HOST:-$(env_value WEB_HOST || true)}}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_API_KEY="${WEB_API_KEY:-$(env_value WEB_API_KEY || true)}"

if [[ "$APP_ENV" == "production" || "$APP_ENV" == "prod" || "$WEB_HOST" == "0.0.0.0" ]]; then
    if [[ -z "$WEB_API_KEY" ]]; then
        fail "WEB_API_KEY 必须设置 (APP_ENV=$APP_ENV, WEB_HOST=$WEB_HOST)"
    fi
    ok "生产模式: WEB_API_KEY 已设置"
fi

# ── 7. Process singleton check ────────────────────────────────────────

info "检查运行实例..."

if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
        if [[ "$CHECK_ONLY" -eq 1 ]]; then
            ok "已有运行实例 (PID $OLD_PID)"
        elif [[ "$FORCE_RESTART" -eq 1 ]]; then
            warn "已有运行实例 (PID $OLD_PID)，--force 模式：强制停止旧进程"
            kill "$OLD_PID" 2>/dev/null || true
            sleep 2
            if kill -0 "$OLD_PID" 2>/dev/null; then
                kill -9 "$OLD_PID" 2>/dev/null || true
                sleep 1
            fi
            ok "旧进程已停止"
        else
            fail "已有运行实例 (PID $OLD_PID)。使用 --force 强制重启，或先停止旧进程"
        fi
    else
        ok "清理过期 PID 文件 (PID $OLD_PID 已不存在)"
        rm -f "$PID_FILE"
    fi
fi

# ── 8. AliyunDrive ───────────────────────────────────────────────────

setup_aliyundrive() {
    local enabled
    enabled=$("$PYTHON_EXE" -c "
import yaml
try:
    with open('$CONFIG_FILE') as f:
        cfg = yaml.safe_load(f) or {}
    print(str(cfg.get('aliyundrive_upload', {}).get('enabled', False)).lower())
except:
    print('false')
" 2>/dev/null) || true

    [[ "$enabled" == "true" ]] || return

    info "阿里云盘上传已启用，检查环境..."

    if command -v aliyunpan &>/dev/null; then
        ok "aliyunpan CLI: $(aliyunpan --version 2>&1 | head -1 || echo '已安装')"
    else
        warn "aliyunpan CLI 未安装，正在自动安装..."
        local ver="v0.3.9"
        local arch
        case "$(uname -m)" in
            x86_64|amd64) arch="amd64" ;;
            aarch64|arm64) arch="arm64" ;;
            armv7l|armv7)  arch="armv7" ;;
            armv5l|armv5)  arch="armv5" ;;
            *)             arch="amd64"; warn "未知架构 $(uname -m)，默认 amd64" ;;
        esac
        local zip_name="aliyunpan-${ver}-linux-${arch}.zip"
        local tmpdir="/tmp/.aliyunpan_install_$$"
        mkdir -p "$tmpdir"
        cd "$tmpdir"
        wget -q "https://github.com/tickstep/aliyunpan/releases/download/${ver}/${zip_name}" -O aliyunpan.zip || {
            cd /; rm -rf "$tmpdir"
            warn "下载 aliyunpan 失败，跳过（不影响 bot 核心功能）"
            return
        }
        unzip -q aliyunpan.zip || { cd /; rm -rf "$tmpdir"; warn "解压 aliyunpan 失败"; return; }
        find "$tmpdir" -name "aliyunpan" -type f -exec cp {} /usr/local/bin/aliyunpan \; 2>/dev/null || true
        chmod +x /usr/local/bin/aliyunpan 2>/dev/null || true
        cd /; rm -rf "$tmpdir"
        ok "aliyunpan CLI 已安装到 /usr/local/bin/aliyunpan"
    fi

    # 检查登录状态
    local who_out
    who_out=$(timeout 10 aliyunpan who 2>/dev/null) || true
    if echo "$who_out" | grep -q "当前账号"; then
        local user
        user=$(echo "$who_out" | grep -oP '当前账号：\K.*' || echo "未知")
        ok "阿里云盘已登录: $user"
    else
        echo ""
        warn "=============================================="
        warn "阿里云盘未登录，请在另一个终端执行："
        echo ""
        echo "    aliyunpan login"
        echo ""
        warn "按提示完成扫码登录后重新运行此脚本"
        warn "=============================================="
        echo ""
    fi
}

setup_aliyundrive

# ── 9. Data directory ────────────────────────────────────────────────

mkdir -p "$ROOT_DIR/data" "$ROOT_DIR/downloads"
ok "数据目录已就绪"

# ── 10. Launch ────────────────────────────────────────────────────────

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo ""
    ok "环境检查完成，一切正常 ✅"
    exit 0
fi

# Export overrides for main.py
[[ -n "$WEB_HOST_OVERRIDE" ]] && export WEB_HOST="$WEB_HOST_OVERRIDE"
[[ -n "$WEB_PORT_OVERRIDE" ]] && export WEB_PORT="$WEB_PORT_OVERRIDE"

echo ""
info "启动应用..."
info "Web 控制台: http://${WEB_HOST}:${WEB_PORT:-8000}"
echo ""

cd "$ROOT_DIR"
exec "$PYTHON_EXE" "$ROOT_DIR/main.py"

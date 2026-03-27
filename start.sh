#!/bin/bash

# 遇到错误立即退出
set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project root = script dir
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
VENV_DIR="$ROOT_DIR/venv"
REQUIREMENTS_FILE="$ROOT_DIR/requirements.txt"

echo -e "${BLUE}=================================================${NC}"
echo -e "${GREEN}>>> 正在从 $SCRIPT_DIR 初始化启动环境...${NC}"
echo -e "${BLUE}=================================================${NC}"

# 1) Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ 错误: 未找到 python3，请先安装 Python3。${NC}"
    exit 1
fi

# 2) Check/create venv
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}⚡ 正在创建虚拟环境 (venv)...${NC}"
    python3 -m venv "$VENV_DIR"
else
    echo -e "${GREEN}✅ 虚拟环境已存在${NC}"
fi

# Activate venv
source "$VENV_DIR/bin/activate"

# 3) Install/upgrade deps (Smart Check)
# 检查 requirements.txt 是否存在
if [ ! -f "$REQUIREMENTS_FILE" ]; then
    echo -e "${RED}❌ 错误: 找不到 $REQUIREMENTS_FILE，无法安装依赖。${NC}"
    exit 1
fi

echo -e "${YELLOW}⚡ 正在检查并同步依赖库...${NC}"
# 升级 pip 本身
pip install --upgrade pip -q

# 检查 uvicorn 是否已安装，作为一个示例检查
# 如果核心库缺失，则强制重新安装所有依赖
if ! python -c "import uvicorn" &> /dev/null; then
    echo -e "${GREEN}>>> 依赖缺失，正在执行完整安装...${NC}"
    pip install -r "$REQUIREMENTS_FILE" -q
else
    echo -e "${GREEN}>>> 核心依赖检查通过，跳过安装以加快速度。${NC}"
fi

# 4) Check config.yaml
if [ ! -f "$ROOT_DIR/config.yaml" ]; then
    echo -e "${RED}❌ 错误: 找不到 config.yaml，请确保配置文件位于项目根目录。${NC}"
    exit 1
fi

# 5) Ensure .env
if [ ! -f "$ROOT_DIR/.env" ]; then
    if [ -f "$ROOT_DIR/.env.example" ]; then
        echo -e "${YELLOW}⚡ 未找到 .env，正在从 .env.example 生成...${NC}"
        cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    else
        echo -e "${RED}⚠️ 警告: 未找到 .env 且无 .env.example 模板，请手动创建 .env。${NC}"
    fi
fi

echo -e "${BLUE}-------------------------------------------------${NC}"
echo -e "${GREEN}>>> 🚀 环境检查通过，正在启动主程序...${NC}"
echo -e "${BLUE}-------------------------------------------------${NC}"

cd "$SCRIPT_DIR"
python3 main.py

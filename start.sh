#!/bin/bash

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

# Project root = script dir
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"

echo -e "${GREEN}>>> 正在从 $SCRIPT_DIR 检查运行环境...${NC}"

# 1) Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}错误: 未找到 python3，请先安装 Python3。${NC}"
    exit 1
fi

# 2) Check/create venv in project root
if [ ! -d "$ROOT_DIR/venv" ]; then
    echo -e "${GREEN}>>> 正在根目录创建虚拟环境...${NC}"
    python3 -m venv "$ROOT_DIR/venv"
fi
source "$ROOT_DIR/venv/bin/activate"

# 3) Install/upgrade deps
echo -e "${GREEN}>>> 正在同步依赖库...${NC}"
pip install --upgrade pip -q
pip install -r "$ROOT_DIR/requirements.txt" -q

# 4) Check/create config.yaml in root
if [ ! -f "$ROOT_DIR/config.yaml" ]; then
    if [ -f "$ROOT_DIR/config.example.yaml" ]; then
        echo -e "${GREEN}>>> 未找到 config.yaml，正在从 config.example.yaml 复制...${NC}"
        cp "$ROOT_DIR/config.example.yaml" "$ROOT_DIR/config.yaml"
    else
        echo -e "${RED}错误: 找不到 config.yaml，请放在项目根目录。${NC}"
        exit 1
    fi
fi

# 5) Ensure .env in root (copy from .env.example if present)
if [ ! -f "$ROOT_DIR/.env" ]; then
    if [ -f "$ROOT_DIR/.env.example" ]; then
        echo -e "${GREEN}>>> 未找到 .env，正在从 .env.example 复制到 $ROOT_DIR/.env ...${NC}"
        cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    else
        echo -e "${RED}警告: 未找到 .env，也没有 .env.example。${NC}"
    fi
fi

echo -e "${GREEN}>>> 环境检查通过，正在启动...${NC}"
cd "$SCRIPT_DIR"
python3 main.py

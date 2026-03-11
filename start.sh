#!/bin/bash

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

# 获取脚本所在目录 (app 目录)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo -e "${GREEN}>>> 正在从 $SCRIPT_DIR 检查运行环境...${NC}"

# 1. 检查 Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}错误: 未找到 python3，请先安装 Python。${NC}"
    exit 1
fi

# 2. 检查/创建虚拟环境 (通常放在根目录)
if [ ! -d "$ROOT_DIR/venv" ]; then
    echo -e "${GREEN}>>> 正在根目录创建虚拟环境...${NC}"
    python3 -m venv "$ROOT_DIR/venv"
fi
source "$ROOT_DIR/venv/bin/activate"

# 3. 安装/更新依赖
echo -e "${GREEN}>>> 正在同步依赖库...${NC}"
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements.txt" -q

# 4. 检查配置文件 (在根目录或 app 目录)
if [ ! -f "$ROOT_DIR/config.yaml" ] && [ ! -f "$SCRIPT_DIR/config.yaml" ]; then
    echo -e "${RED}错误: 未找到 config.yaml。请确保它在根目录或 app 目录下。${NC}"
    exit 1
fi

# 5. 检查环境变量
if [ ! -f "$ROOT_DIR/.env" ]; then
    echo -e "${RED}警告: 未找到 .env 文件。${NC}"
fi

echo -e "${GREEN}>>> 环境检查通过，正在启动...${NC}"
# 切换到脚本所在目录启动
cd "$SCRIPT_DIR"
python3 main.py

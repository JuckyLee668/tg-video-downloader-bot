# Telegram Bot API 服务器编译部署指南

## 🚀 快速开始

### 1. 源码准备
**Fork 仓库**: [tdlib/telegram-bot-api](https://github.com/tdlib/telegram-bot-api)

### 2. 环境配置
使用官方构建配置工具生成编译参数：
[Telegram Bot API server build instructions generator](https://tdlib.github.io/telegram-bot-api/build.html)

### 3. GitHub Actions 自动化编译

#### 3.2 优化版工作流配置
创建文件 `.github/workflows/build.yml`：

```yaml
name: Build and Release

on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:

jobs:
  build-and-package:
    runs-on: ubuntu-22.04
    
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          submodules: recursive
          token: ${{ secrets.GITHUB_TOKEN }}
      
      - name: Install build deps
        run: |
          sudo apt-get update
          sudo apt-get install -y \
            build-essential \
            cmake \
            zlib1g-dev \
            libssl-dev \
            libreadline-dev \
            libffi-dev \
            gperf \
            ninja-build \
            pkg-config \
            clang-14 \
            libc++-14-dev \
            libc++abi-14-dev
      
      - name: Set version
        id: vars
        run: |
          if [[ "${GITHUB_REF}" == refs/tags/* ]]; then
            VERSION="${GITHUB_REF#refs/tags/}"
          else
            VERSION="git-${GITHUB_SHA::8}"
          fi
          echo "version=$VERSION" >> "$GITHUB_OUTPUT"
      
      - name: Configure & build
        run: |
          rm -rf build
          mkdir -p build
          cd build
          
          CXXFLAGS="-stdlib=libc++" \
          CC=/usr/bin/clang-14 \
          CXX=/usr/bin/clang++-14 \
          cmake -DCMAKE_BUILD_TYPE=Release ..
          
          cmake --build . --target telegram-bot-api
      
      - name: Verify binary exists
        run: |
          if [ -f "build/telegram-bot-api" ]; then
            echo "✅ Binary found"
            file build/telegram-bot-api
            ls -lh build/telegram-bot-api
          else
            echo "❌ Binary not found"
            find . -name "*telegram*" -type f 2>/dev/null
            exit 1
          fi
      
      - name: Create Release
        id: create_release
        uses: softprops/action-gh-release@v1
        if: startsWith(github.ref, 'refs/tags/')
        with:
          name: Release ${{ github.ref_name }}
          tag_name: ${{ github.ref_name }}
          body: |
            ## Telegram Bot API
            
            Version: ${{ github.ref_name }}
            
            Automated release built with GitHub Actions.
            
            ### Changes
            ${{ github.event.head_commit.message }}
            
            ### Build Info
            - Commit: ${{ github.sha }}
            - Built on: ${{ github.event.repository.updated_at }}
          draft: false
          prerelease: false
          generate_release_notes: true
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      
      - name: Upload binary to Release
        uses: actions/upload-release-asset@v1
        if: startsWith(github.ref, 'refs/tags/')
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          upload_url: ${{ steps.create_release.outputs.upload_url }}
          asset_path: ./build/telegram-bot-api
          asset_name: telegram-bot-api-${{ github.ref_name }}-linux-x86_64
          asset_content_type: application/octet-stream
      
      - name: Debug info
        if: always()
        run: |
          echo "Tag: ${{ github.ref }}"
          echo "Tag name: ${{ github.ref_name }}"
          echo "Is tag: ${{ startsWith(github.ref, 'refs/tags/') }}"
          echo "Release URL: ${{ steps.create_release.outputs.upload_url }}"
```

## 📱 申请 Telegram 应用

### 1. 访问申请页面
前往: [https://my.telegram.org/auth](https://my.telegram.org/auth)

### 2. 创建应用步骤
1. 使用手机号登录
2. 选择 "API Development Tools"
3. 填写应用信息：
   - **App title**: 您的应用名称
   - **Short name**: 应用简称（用于 URL）
   - **Platform**: 选择 "Desktop"
   - **Description**: 应用描述

### 3. 获取关键信息
申请成功后，获取并保存：
- **api_id**: 应用 ID
- **api_hash**: 应用哈希值

## 🏃 运行 Bot API 服务器

### 1.运行服务器
```bash
# 基础运行命令
./telegram-bot-api \
  --api-id=你的API_ID \
  --api-hash=你的API_HASH \
  --local \
  --http-port=8081 \
  --dir=/var/lib/telegram-bot-api \
  --log=/var/log/telegram-bot-api.log \
  --stats-dump-interval=60
```

2. ```bash
sudo -u www-data screen -dmS telegram-bot-api bash -c "telegram-bot-api   --api-id=30578955   --api-hash=db62a098851588b5fd97bbee6ddd1536   --local   --http-port=9081   --dir=/media/TGbot   --temp-dir=/tmp/telegram-bot-api   --log=/var/log/telegram-bot-api.log   --verbosity=2   --log-max-file-size=2000000000"
```

### 2.常用 Screen 命令

```bash
- 带账号启动
screen -dmS telegram-bot-api bash -c "telegram-bot-api   --api-id=   --api-hash=    --local   --http-port=8081   --dir=    ----temp-dir=    --log=  --verbosity=2   --log-max-file-size= "
- 附加到会话
screen -r telegram-bot-api
- 停止会话
screen -S telegram-bot-api -X quit
```
### 3. 使用 systemd 管理（推荐）
创建服务文件

`sudo nano /etc/systemd/system/telegram-bot-api.service`

```ini
[Unit]
Description=Telegram Bot API
After=network.target

[Service]
Type=simple
WorkingDirectory=/media/botToken
ExecStart=/usr/local/bin/telegram-bot-api \
    --api-id=YOUR_API_ID \
    --api-hash=YOUR_API_HASH \
    --local
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=telegram-bot-api

[Install]
WantedBy=multi-user.target

```

管理服务：
```bash
# 重载 systemd
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start telegram-bot-api

# 开机自启
sudo systemctl enable telegram-bot-api

# 查看状态
sudo systemctl status telegram-bot-api

# 查看日志
sudo journalctl -u telegram-bot-api -f
```

## 🔧 调试与监控

### 1. 基础调试命令
```bash
# 检查端口占用
sudo lsof -i:8081
sudo netstat -tlnp | grep 8081
ss -tlnp | grep 8081

# 检查进程
pgrep -l telegram-bot-api
ps aux | grep telegram-bot-api

# 检查目录权限
ls -la /var/lib/telegram-bot-api/
```

### 2. API 测试
```bash
# 获取机器人信息
curl  "http://localhost:8081/botYOUR_BOT_TOKEN/getMe"
# 发送测试消息
curl -s -X POST "http://localhost:8081/botYOUR_BOT_TOKEN/sendMessage" \
  -H "Content-Type: application/json" \
  -d '{"chat_id": YOUR_CHAT_ID, "text": "测试消息"}'
# 获取 Webhook 信息
curl "http://localhost:8081/botYOUR_BOT_TOKEN/getWebhookInfo"
```

### 3. 日志监控
```bash
# 实时查看日志
tail -f /var/log/telegram-bot-api.log

# 查看错误日志
grep -i error /var/log/telegram-bot-api.log

# 监控请求
tail -f /var/log/telegram-bot-api.log | grep -E "(request|response)"

# 使用 journalctl 查看
sudo journalctl -u telegram-bot-api --since "5 minutes ago"
```

### 4. 性能监控
```bash
# 查看进程资源使用
top -p $(pgrep telegram-bot-api)
htop -p $(pgrep telegram-bot-api)

# 内存使用
pmap $(pgrep telegram-bot-api) | tail -1

# 网络连接
ss -t -a | grep 8081
netstat -an | grep 8081
```

## 🔐 安全建议

### 1. 防火墙配置
```bash
# 仅允许特定 IP 访问
sudo ufw allow from 192.168.1.0/24 to any port 8081
sudo ufw allow from 10.0.0.0/8 to any port 8081

# 或者使用 nftables
sudo nft add rule inet filter input tcp dport 8081 ip saddr { 允许的IP列表 } accept
```

### 2. 使用反向代理（推荐）
```nginx
# Nginx 配置示例
server {
    listen 443 ssl http2;
    server_name bot-api.your-domain.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        # 增加超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # 限制请求大小
        client_max_body_size 50M;
    }
    
    # 访问限制
    location /bot {
        limit_req zone=api burst=20 nodelay;
        limit_conn api_conn 100;
    }
}
```

## 🚨 故障排除

### 常见问题及解决方案

1. **端口被占用**
```bash
# 查找占用进程
sudo lsof -i :8081
# 或者
sudo netstat -tulpn | grep 8081

# 杀死进程（谨慎操作）
sudo kill -9 $(sudo lsof -t -i:8081)
```

2. **权限问题**
```bash
# 创建专用用户
sudo useradd -r -s /bin/false telegram

# 设置目录权限
sudo mkdir -p /var/lib/telegram-bot-api /var/log/telegram-bot-api
sudo chown -R telegram:telegram /var/lib/telegram-bot-api /var/log/telegram-bot-api
sudo chmod 750 /var/lib/telegram-bot-api
```

3. **API 密钥错误**
- 确认 `api_id` 和 `api_hash` 正确
- 检查是否有特殊字符需要转义
- 重新申请 API 密钥

4. **内存不足**
```bash
# 查看内存使用
free -h
top -o %MEM

# 调整虚拟内存
sudo dd if=/dev/zero of=/swapfile bs=1G count=4
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

5. **连接超时**
```bash
# 增加文件描述符限制
echo "* soft nofile 65535" | sudo tee -a /etc/security/limits.conf
echo "* hard nofile 65535" | sudo tee -a /etc/security/limits.conf

# 立即生效
ulimit -n 65535
```

## 📈 性能优化

### 1.  数据库优化
```bash
# 定期清理旧数据
./telegram-bot-api --clean-db --dir=/var/lib/telegram-bot-api

# 备份数据库
cp -r /var/lib/telegram-bot-api /backup/telegram-bot-api-$(date +%Y%m%d)
```


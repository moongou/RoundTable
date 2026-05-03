#!/bin/bash
# ============================================================
# 圆桌思辨 — 阿里云 ECS 一键部署脚本
# 适用: Ubuntu 24.04 64位
# 用法: ssh root@39.97.253.10 'bash -s' < deploy/deploy_aliyun.sh
#       或在服务器上直接: chmod +x deploy_aliyun.sh && ./deploy_aliyun.sh
# ============================================================
set -euo pipefail

# ---- 配置变量（按需修改）----
DOMAIN="${DOMAIN:-rainforgrain.top}"          # 域名
APP_DIR="/opt/roundtable"
REPO_URL="${REPO_URL:-}"                    # 留空则不自动clone，手动上传代码
PYTHON_BIN="python3.12"                     # Ubuntu 24.04 自带 3.12
VENV_DIR="$APP_DIR/backend/.venv"
NGINX_CONF="/etc/nginx/sites-available/roundtable"
BACKEND_USER="${BACKEND_USER:-www-data}"
OPS_PANEL_TOKEN="${OPS_PANEL_TOKEN:-change-this-ops-token}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo "=========================================="
echo "  圆桌思辨 阿里云部署脚本"
echo "  目标: $DOMAIN"
echo "=========================================="
echo ""

if [ "$OPS_PANEL_TOKEN" = "change-this-ops-token" ]; then
    warn "OPS_PANEL_TOKEN 正在使用默认值。上线前请自定义强口令，例如: OPS_PANEL_TOKEN='your-long-token'"
fi

# ---- 1. 系统环境 ----
log "更新系统包..."
apt-get update -qq && apt-get upgrade -y -qq

log "安装系统依赖..."
apt-get install -y -qq \
    $PYTHON_BIN $PYTHON_BIN-venv $PYTHON_BIN-dev \
    nginx certbot python3-certbot-nginx \
    git curl build-essential nodejs \
    ffmpeg

# 确保 python3 和 pip 可用
if ! command -v python3 &>/dev/null; then
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/$PYTHON_BIN 1
fi
$PYTHON_BIN -m ensurepip --upgrade 2>/dev/null || true

# ---- 2. 拉取代码 ----
if [ ! -d "$APP_DIR" ]; then
    mkdir -p "$APP_DIR"
    if [ -n "$REPO_URL" ]; then
        log "克隆仓库: $REPO_URL"
        git clone "$REPO_URL" "$APP_DIR"
    else
        warn "未设置 REPO_URL，请手动上传代码到 $APP_DIR"
        warn "  rsync -avz ./ root@$DOMAIN:$APP_DIR/"
    fi
fi

cd "$APP_DIR"

# ---- 3. Python 虚拟环境 ----
log "配置 Python 虚拟环境..."
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON_BIN -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q

if [ -f "$APP_DIR/backend/pyproject.toml" ]; then
    log "安装 Python 依赖..."
    pip install -e "$APP_DIR/backend[dev]" -q
else
    err "未找到 backend/pyproject.toml，请确认代码目录结构正确"
fi

# ---- 4. 环境变量配置 ----
if [ ! -f "$APP_DIR/backend/.env" ]; then
    log "生成 .env 配置文件..."
    cat > "$APP_DIR/backend/.env" << 'ENVEOF'
# ---- LLM ----
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

# ---- ASR ----
ASR_PROVIDER=siliconflow_asr
SILICONFLOW_API_KEY=sk-xxx
SILICONFLOW_ASR_MODEL=FunAudioLLM/SenseVoiceSmall
SILICONFLOW_ASR_BASE_URL=https://api.siliconflow.cn/v1

# ---- TTS ----
TTS_PROVIDER=siliconflow_tts
SILICONFLOW_TTS_MODEL=FunAudioLLM/CosyVoice2-0.5B
SILICONFLOW_TTS_VOICE=FunAudioLLM/CosyVoice2-0.5B:alex
SILICONFLOW_TTS_BASE_URL=https://api.siliconflow.cn/v1

# ---- 服务器 ----
HOST=0.0.0.0
PORT=8001
DEBUG=false
CORS_ALLOWED_ORIGINS=["http://rainforgrain.top","https://rainforgrain.top","http://39.97.253.10","https://39.97.253.10"]

# ---- 讨论 ----
MAX_TURNS=30
HUMAN_TURN_TIMEOUT=15
PUSH_TO_TALK=true

# ---- 管理 ----
MANAGEMENT_API_TOKEN=roundtable-admin-2026
MANAGEMENT_AUTH_ENFORCED=false

# ---- Web Search (需要 Tavily API Key) ----
TAVILY_API_KEY=
WEB_SEARCH_ENABLED=false
ENVEOF
    warn "请编辑 $APP_DIR/backend/.env 填入真实的 API 密钥"
fi

# ---- 5. 前端静态文件 ----
if [ ! -f "$APP_DIR/backend/static/index.html" ]; then
    warn "前端未构建。在本地执行 build_web.sh 并重新上传:"
    warn "  cd frontend && flutter build web --release && rsync -av build/web/ root@$DOMAIN:$APP_DIR/backend/static/"
fi

# ---- 6. systemd 服务 ----
log "配置 systemd 服务..."
cat > /etc/systemd/system/roundtable.service << SERVICE
[Unit]
Description=RoundTable Backend
After=network.target

[Service]
Type=simple
User=$BACKEND_USER
WorkingDirectory=$APP_DIR/backend
ExecStart=$VENV_DIR/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 2
Restart=always
RestartSec=5
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=$APP_DIR/backend/.env

[Install]
WantedBy=multi-user.target
SERVICE

cat > /etc/systemd/system/roundtable-devpanel.service << SERVICE
[Unit]
Description=RoundTable Dev Panel
After=network.target roundtable.service
Wants=roundtable.service

[Service]
Type=simple
User=$BACKEND_USER
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/node $APP_DIR/devpanel.js
Restart=always
RestartSec=3
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable roundtable
systemctl restart roundtable
log "RoundTable 后端服务已启动"

if [ -f "$APP_DIR/devpanel.js" ]; then
    systemctl enable roundtable-devpanel
    systemctl restart roundtable-devpanel
    log "RoundTable 开发面板服务已启动 (127.0.0.1:8888)"
else
    warn "未找到 $APP_DIR/devpanel.js，已跳过 roundtable-devpanel 服务"
fi

if [ -f "$APP_DIR/deploy/rotate_ops_token.sh" ]; then
    chmod +x "$APP_DIR/deploy/rotate_ops_token.sh"
fi

# ---- 7. Nginx 配置 ----
log "配置 Nginx..."
cat > "$NGINX_CONF" << NGINX
# RoundTable Nginx 配置
# WebSocket 升级映射
map \$http_upgrade \$connection_upgrade {
    default upgrade;
    ''      close;
}

map \$http_x_ops_token \$ops_token_header_ok {
    default 0;
    "$OPS_PANEL_TOKEN" 1;
}

map \$arg_token \$ops_token_query_ok {
    default 0;
    "$OPS_PANEL_TOKEN" 1;
}

map "\$ops_token_header_ok\$ops_token_query_ok" \$ops_token_ok {
    default 0;
    ~1 1;
}

server {
    listen 80;
    server_name $DOMAIN;

    # 上传限制
    client_max_body_size 50M;

    # 后端 API + 静态文件（单端口架构）
    location = /admin {
        return 301 /admin/;
    }

    # 管理后台（显式放行，避免后续规则调整误伤）
    location ^~ /admin/ {
        proxy_pass http://127.0.0.1:8001/admin/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # 受保护的运维面板入口（转发到本机 devpanel :8888）
    location = /ops {
        return 302 /ops/;
    }

    location ^~ /ops/ {
        if (\$ops_token_ok = 0) {
            return 403;
        }
        proxy_pass http://127.0.0.1:8888/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
    }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # WebSocket 长连接
    location /api/v1/ws/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
NGINX

ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/roundtable
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx
log "Nginx 配置完成"

# ---- 8. 防火墙 ----
log "配置防火墙..."
if command -v ufw &>/dev/null; then
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw allow 22/tcp
    ufw --force enable 2>/dev/null || true
fi

# ---- 9. 完成 ----
IP=$(curl -s ifconfig.me 2>/dev/null || echo "$DOMAIN")
echo ""
echo "=========================================="
echo "  部署完成!"
echo "=========================================="
echo ""
echo "  网站:     http://$IP"
echo "  管理后台: http://$IP/admin/"
echo "  运维面板: http://$IP/ops/  (需令牌；支持 Header: X-Ops-Token 或 ?token=...)"
echo "  开发面板(服务器本机): http://127.0.0.1:8888"
echo "  远程访问开发面板(SSH 隧道): ssh -L 8888:127.0.0.1:8888 root@$IP"
echo "  API 文档: http://$IP:8001/docs"
echo ""
echo "  检查服务状态:"
echo "    systemctl status roundtable"
echo "    systemctl status roundtable-devpanel"
echo "    systemctl status nginx"
echo "    journalctl -u roundtable -f"
echo "    journalctl -u roundtable-devpanel -f"
echo ""
echo "  运维面板验收示例:"
echo "    curl -H 'X-Ops-Token: <OPS_PANEL_TOKEN>' http://$IP/ops/api/status"
echo "    curl -H 'X-Ops-Token: <OPS_PANEL_TOKEN>' http://$IP/ops/api/admin/stats"
echo ""
echo "  一键轮换 /ops token:"
echo "    OPS_PANEL_TOKEN='<NEW_TOKEN>' $APP_DIR/deploy/rotate_ops_token.sh"
echo ""
echo "  配置 SSL (有域名后):"
echo "    certbot --nginx -d your-domain.com"
echo ""
echo "  后续更新:"
echo "    1. 本地 build_web.sh 构建前端"
echo "    2. rsync -avz build/web/ root@$IP:$APP_DIR/backend/static/"
echo "    3. ssh root@$IP 'systemctl restart roundtable'"
echo ""

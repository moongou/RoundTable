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
APP_NAME="${APP_NAME:-roundtable}"
APP_DIR="${APP_DIR:-/opt/$APP_NAME}"
REPO_URL="${REPO_URL:-}"                    # 留空则不自动clone，手动上传代码
PYTHON_BIN="python3.12"                     # Ubuntu 24.04 自带 3.12
VENV_DIR="$APP_DIR/backend/.venv"
BACKEND_PORT="${BACKEND_PORT:-18421}"
DEVPANEL_PORT="${DEVPANEL_PORT:-8921}"
PUBLIC_PORT="${PUBLIC_PORT:-8421}"
EXPOSE_PUBLIC_PORT="${EXPOSE_PUBLIC_PORT:-true}"
ENABLE_STANDARD_HTTP="${ENABLE_STANDARD_HTTP:-false}"
ENABLE_PUBLIC_TLS="${ENABLE_PUBLIC_TLS:-false}"
TLS_CERT_DOMAIN="${TLS_CERT_DOMAIN:-$DOMAIN}"
TLS_CERT_DIR="${TLS_CERT_DIR:-/etc/letsencrypt/live/$TLS_CERT_DOMAIN}"
TLS_CERT_FILE="${TLS_CERT_FILE:-$TLS_CERT_DIR/fullchain.pem}"
TLS_KEY_FILE="${TLS_KEY_FILE:-$TLS_CERT_DIR/privkey.pem}"
TLS_OPTIONS_FILE="${TLS_OPTIONS_FILE:-/etc/letsencrypt/options-ssl-nginx.conf}"
TLS_DHPARAM_FILE="${TLS_DHPARAM_FILE:-/etc/letsencrypt/ssl-dhparams.pem}"
CONFIG_ONLY="${CONFIG_ONLY:-false}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
DEVPANEL_SERVICE_NAME="${DEVPANEL_SERVICE_NAME:-$APP_NAME-devpanel}"
NGINX_SITE_NAME="${NGINX_SITE_NAME:-$APP_NAME}"
NGINX_CONF="/etc/nginx/sites-available/$NGINX_SITE_NAME"
BACKEND_USER="${BACKEND_USER:-www-data}"
OPS_PANEL_TOKEN="${OPS_PANEL_TOKEN:-change-this-ops-token}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

PUBLIC_TLS_ACTIVE="false"

is_true() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

extract_existing_ops_token() {
    local nginx_conf_path="$1"
    if [ ! -f "$nginx_conf_path" ]; then
        return 0
    fi
    awk '/map \$http_x_ops_token \$ops_token_header_ok \{/ {capture=1; next} capture && /".*" 1;/ {gsub(/^\s*"|" 1;\s*$/, "", $0); print; exit}' "$nginx_conf_path"
}

primary_public_url() {
    local scheme="http"
    if [ "$PUBLIC_TLS_ACTIVE" = "true" ]; then
        scheme="https"
    fi
    if is_true "$EXPOSE_PUBLIC_PORT"; then
        echo "$scheme://$DOMAIN:$PUBLIC_PORT"
    else
        echo "$scheme://$DOMAIN"
    fi
}

configure_public_tls() {
    if ! is_true "$ENABLE_PUBLIC_TLS"; then
        PUBLIC_TLS_ACTIVE="false"
        return
    fi
    [ -f "$TLS_CERT_FILE" ] || err "已启用 ENABLE_PUBLIC_TLS，但未找到证书文件: $TLS_CERT_FILE"
    [ -f "$TLS_KEY_FILE" ] || err "已启用 ENABLE_PUBLIC_TLS，但未找到私钥文件: $TLS_KEY_FILE"
    PUBLIC_TLS_ACTIVE="true"
}

write_nginx_server_block() {
    local listen_port="$1"
    local enable_tls="${2:-false}"
    cat >> "$NGINX_CONF" <<NGINX

server {
    listen $listen_port$( [ "$enable_tls" = "true" ] && printf ' ssl' );
    server_name $DOMAIN;

NGINX

    if [ "$enable_tls" = "true" ]; then
        cat >> "$NGINX_CONF" <<NGINX
    ssl_certificate $TLS_CERT_FILE;
    ssl_certificate_key $TLS_KEY_FILE;
NGINX
        if [ -f "$TLS_OPTIONS_FILE" ]; then
            cat >> "$NGINX_CONF" <<NGINX
    include $TLS_OPTIONS_FILE;
NGINX
        fi
        if [ -f "$TLS_DHPARAM_FILE" ]; then
            cat >> "$NGINX_CONF" <<NGINX
    ssl_dhparam $TLS_DHPARAM_FILE;
NGINX
        fi
    fi

    cat >> "$NGINX_CONF" <<NGINX

    client_max_body_size 50M;

    location = /admin {
        return 301 /admin/;
    }

    location ^~ /admin/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT/admin/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location = /ops {
        return 302 /ops/;
    }

    location ^~ /ops/ {
        if (\$ops_token_ok = 0) {
            return 403;
        }
        proxy_pass http://127.0.0.1:$DEVPANEL_PORT/;
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
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /api/v1/ws/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT;
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
}

write_nginx_redirect_server_block() {
    local listen_port="$1"
    cat >> "$NGINX_CONF" <<NGINX

server {
    listen $listen_port;
    server_name $DOMAIN;

    location / {
        return 301 https://\$host:$PUBLIC_PORT\$request_uri;
    }
}
NGINX
}

echo ""
echo "=========================================="
echo "  圆桌思辨 阿里云部署脚本"
echo "  目标: $DOMAIN"
echo "=========================================="
echo ""

if [ "$OPS_PANEL_TOKEN" = "change-this-ops-token" ]; then
    existing_ops_token="$(extract_existing_ops_token "$NGINX_CONF" || true)"
    if [ -n "$existing_ops_token" ]; then
        OPS_PANEL_TOKEN="$existing_ops_token"
        log "沿用现有 OPS_PANEL_TOKEN（未覆盖）"
    fi
fi

if [ "$OPS_PANEL_TOKEN" = "change-this-ops-token" ]; then
    warn "OPS_PANEL_TOKEN 正在使用默认值。上线前请自定义强口令，例如: OPS_PANEL_TOKEN='your-long-token'"
fi

configure_public_tls

if [ "$PUBLIC_TLS_ACTIVE" = "true" ]; then
    log "公网入口将启用 HTTPS（端口 $PUBLIC_PORT，证书域名 $TLS_CERT_DOMAIN）"
fi

if is_true "$CONFIG_ONLY"; then
    log "CONFIG_ONLY=true，仅刷新 .env 模板、systemd、Nginx 与防火墙配置"
else
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
fi

cd "$APP_DIR"

# ---- 4. 环境变量配置 ----
if [ ! -f "$APP_DIR/backend/.env" ]; then
    log "生成 .env 配置文件..."
    cat > "$APP_DIR/backend/.env" << ENVEOF
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
PORT=$BACKEND_PORT
DEBUG=false
CORS_ALLOWED_ORIGINS=["http://$DOMAIN","https://$DOMAIN","http://$DOMAIN:$PUBLIC_PORT","https://$DOMAIN:$PUBLIC_PORT","http://39.97.253.10","https://39.97.253.10","http://39.97.253.10:$PUBLIC_PORT","https://39.97.253.10:$PUBLIC_PORT"]

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
cat > "/etc/systemd/system/$SERVICE_NAME.service" << SERVICE
[Unit]
Description=RoundTable Backend
After=network.target

[Service]
Type=simple
User=$BACKEND_USER
WorkingDirectory=$APP_DIR/backend
ExecStart=$VENV_DIR/bin/uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT --workers 2
Restart=always
RestartSec=5
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=$APP_DIR/backend/.env

[Install]
WantedBy=multi-user.target
SERVICE

cat > "/etc/systemd/system/$DEVPANEL_SERVICE_NAME.service" << SERVICE
[Unit]
Description=RoundTable Dev Panel
After=network.target $SERVICE_NAME.service
Wants=$SERVICE_NAME.service

[Service]
Type=simple
User=$BACKEND_USER
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/node $APP_DIR/devpanel.js
Restart=always
RestartSec=3
Environment=NODE_ENV=production
Environment=ROUNDTABLE_BACKEND_PORT=$BACKEND_PORT
Environment=ROUNDTABLE_DEVPANEL_PORT=$DEVPANEL_PORT

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
log "RoundTable 后端服务已启动"

if [ -f "$APP_DIR/devpanel.js" ]; then
    systemctl enable "$DEVPANEL_SERVICE_NAME"
    systemctl restart "$DEVPANEL_SERVICE_NAME"
    log "RoundTable 开发面板服务已启动 (127.0.0.1:$DEVPANEL_PORT)"
else
    warn "未找到 $APP_DIR/devpanel.js，已跳过 $DEVPANEL_SERVICE_NAME 服务"
fi

if [ -f "$APP_DIR/deploy/rotate_ops_token.sh" ]; then
    chmod +x "$APP_DIR/deploy/rotate_ops_token.sh"
fi

# ---- 7. Nginx 配置 ----
log "配置 Nginx..."
if ! is_true "$ENABLE_STANDARD_HTTP" && ! is_true "$EXPOSE_PUBLIC_PORT"; then
    err "ENABLE_STANDARD_HTTP 与 EXPOSE_PUBLIC_PORT 不能同时为 false"
fi

if [ "$PUBLIC_TLS_ACTIVE" = "true" ] && ! is_true "$EXPOSE_PUBLIC_PORT"; then
    err "启用 ENABLE_PUBLIC_TLS 时必须同时启用 EXPOSE_PUBLIC_PORT"
fi

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
NGINX

if is_true "$ENABLE_STANDARD_HTTP"; then
    if [ "$PUBLIC_TLS_ACTIVE" = "true" ]; then
        write_nginx_redirect_server_block 80
    else
        write_nginx_server_block 80 false
    fi
fi

if is_true "$EXPOSE_PUBLIC_PORT"; then
    if [ "$PUBLIC_TLS_ACTIVE" = "true" ]; then
        write_nginx_server_block "$PUBLIC_PORT" true
    else
        write_nginx_server_block "$PUBLIC_PORT" false
    fi
fi

ln -sf "$NGINX_CONF" "/etc/nginx/sites-enabled/$NGINX_SITE_NAME"
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx
log "Nginx 配置完成"

# ---- 8. 防火墙 ----
log "配置防火墙..."
if command -v ufw &>/dev/null; then
    if is_true "$ENABLE_STANDARD_HTTP"; then
        ufw allow 80/tcp
        ufw allow 443/tcp
    fi
    if is_true "$EXPOSE_PUBLIC_PORT"; then
        ufw allow "$PUBLIC_PORT/tcp"
    fi
    ufw allow 22/tcp
    ufw --force enable 2>/dev/null || true
fi

# ---- 9. 完成 ----
IP=$(curl -s ifconfig.me 2>/dev/null || echo "$DOMAIN")
PUBLIC_BASE_URL="$(primary_public_url)"
echo ""
echo "=========================================="
echo "  部署完成!"
echo "=========================================="
echo ""
echo "  后端监听: http://127.0.0.1:$BACKEND_PORT"
if is_true "$ENABLE_STANDARD_HTTP"; then
    echo "  标准入口: http://$IP"
fi
if is_true "$EXPOSE_PUBLIC_PORT"; then
    if [ "$PUBLIC_TLS_ACTIVE" = "true" ]; then
        echo "  项目入口: https://$IP:$PUBLIC_PORT"
    else
        echo "  项目入口: http://$IP:$PUBLIC_PORT"
    fi
fi
echo "  管理后台: $PUBLIC_BASE_URL/admin/"
echo "  运维面板: $PUBLIC_BASE_URL/ops/  (需令牌；支持 Header: X-Ops-Token 或 ?token=...)"
echo "  开发面板(服务器本机): http://127.0.0.1:$DEVPANEL_PORT"
echo "  远程访问开发面板(SSH 隧道): ssh -L $DEVPANEL_PORT:127.0.0.1:$DEVPANEL_PORT root@$IP"
echo "  API 文档: $PUBLIC_BASE_URL/docs"
echo ""
echo "  检查服务状态:"
echo "    systemctl status $SERVICE_NAME"
echo "    systemctl status $DEVPANEL_SERVICE_NAME"
echo "    systemctl status nginx"
echo "    journalctl -u $SERVICE_NAME -f"
echo "    journalctl -u $DEVPANEL_SERVICE_NAME -f"
echo ""
echo "  运维面板验收示例:"
echo "    curl -H 'X-Ops-Token: <OPS_PANEL_TOKEN>' $PUBLIC_BASE_URL/ops/api/status"
echo "    curl -H 'X-Ops-Token: <OPS_PANEL_TOKEN>' $PUBLIC_BASE_URL/ops/api/admin/stats"
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
echo "    3. ssh root@$IP 'systemctl restart $SERVICE_NAME'"
echo ""

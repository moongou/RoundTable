#!/bin/bash
# ============================================================
# 圆桌思辨 — 快速部署脚本（本地构建 + 推送至阿里云 ECS）
# 用法: ./deploy/deploy.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ---- 配置（按需修改）----
SERVER="${SERVER:-root@39.97.253.10}"
APP_NAME="${APP_NAME:-roundtable}"
APP_DIR="${APP_DIR:-/opt/$APP_NAME}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
DEVPANEL_SERVICE_NAME="${DEVPANEL_SERVICE_NAME:-$APP_NAME-devpanel}"
BACKEND_PORT="${BACKEND_PORT:-18421}"
DEVPANEL_PORT="${DEVPANEL_PORT:-8921}"
PUBLIC_PORT="${PUBLIC_PORT:-8421}"
EXPOSE_PUBLIC_PORT="${EXPOSE_PUBLIC_PORT:-true}"
ENABLE_STANDARD_HTTP="${ENABLE_STANDARD_HTTP:-false}"
ENABLE_PUBLIC_TLS="${ENABLE_PUBLIC_TLS:-true}"
DOMAIN="${DOMAIN:-rainforgrain.top}"
SKIP_WEB_BUILD="${SKIP_WEB_BUILD:-true}"
SSH_PORT="${SSH_PORT:-22}"
CLEAN_STALE_TOP_LEVEL="${CLEAN_STALE_TOP_LEVEL:-true}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[1/4]${NC} $1"; }
log2() { echo -e "${GREEN}[2/4]${NC} $1"; }
log3() { echo -e "${GREEN}[3/4]${NC} $1"; }
log4() { echo -e "${GREEN}[4/4]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

is_true() {
    case "${1:-}" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

public_scheme() {
    if is_true "$ENABLE_PUBLIC_TLS"; then
        echo "https"
    else
        echo "http"
    fi
}

echo ""
echo "=========================================="
echo "  圆桌思辨 快速部署"
echo "  目标: $SERVER"
echo "=========================================="
echo ""

# ---- Step 1: 构建前端 ----
if is_true "$SKIP_WEB_BUILD"; then
    log "跳过 Flutter Web 构建（本次仅部署 ECS 端口/服务配置）..."
else
    log "构建 Flutter Web 前端..."
    cd "$PROJECT_DIR"
    bash "$PROJECT_DIR/build_web.sh" --force || err "前端构建失败"
fi

# ---- Step 2: 测试后端配置 ----
log "验证后端配置..."
cd "$PROJECT_DIR/backend"
if ! source .venv/bin/activate 2>/dev/null; then
    err "未找到虚拟环境，请先在 backend/ 下执行: python3.12 -m venv .venv && source .venv/bin/activate && pip install -e .[dev]"
fi
python -c "from app.config import settings; print(f'  LLM={settings.llm_provider} ASR={settings.asr_provider} TTS={settings.tts_provider}')" || err "配置加载失败"

# ---- Step 3: 推送代码到服务器 ----
log2 "推送代码 (rsync)..."
rsync -avz --delete \
    -e "ssh -p $SSH_PORT" \
    --exclude '.venv/' \
    --exclude 'runtime/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude 'node_modules/' \
    "$PROJECT_DIR/backend/" \
    "$SERVER:$APP_DIR/backend/" || err "rsync 失败"

rsync -avz -e "ssh -p $SSH_PORT" \
    "$PROJECT_DIR/devpanel.js" \
    "$SERVER:$APP_DIR/devpanel.js" || err "devpanel.js 推送失败"

ssh -p "$SSH_PORT" "$SERVER" "mkdir -p $APP_DIR/deploy" >/dev/null
rsync -avz -e "ssh -p $SSH_PORT" \
    "$PROJECT_DIR/deploy/rotate_ops_token.sh" \
    "$SERVER:$APP_DIR/deploy/rotate_ops_token.sh" || err "rotate_ops_token.sh 推送失败"

rsync -avz -e "ssh -p $SSH_PORT" \
    "$PROJECT_DIR/deploy/deploy_aliyun.sh" \
    "$SERVER:$APP_DIR/deploy/deploy_aliyun.sh" || err "deploy_aliyun.sh 推送失败"

log3 "推送 .env 配置（如存在）..."
if [ -f "$PROJECT_DIR/backend/.env" ]; then
    rsync -avz -e "ssh -p $SSH_PORT" \
        "$PROJECT_DIR/backend/.env" \
        "$SERVER:$APP_DIR/backend/.env" || err ".env 推送失败"
else
    warn="true"
fi

# ---- Step 4: 重启后端服务 ----
log4 "重启后端服务..."
ssh -p "$SSH_PORT" "$SERVER" <<REMOTE
    set -e
    cd "$APP_DIR/backend"
    if [ -f .venv/bin/activate ]; then
        source .venv/bin/activate
        pip install -e .[dev] -q 2>&1 | tail -1
    fi
    # 确保 runtime 目录权限
    mkdir -p runtime
    chown -R www-data:www-data runtime 2>/dev/null || true
    [ -f "$APP_DIR/deploy/rotate_ops_token.sh" ] && chmod +x "$APP_DIR/deploy/rotate_ops_token.sh" || true
    [ -f "$APP_DIR/deploy/deploy_aliyun.sh" ] && chmod +x "$APP_DIR/deploy/deploy_aliyun.sh" || true
    DOMAIN="$DOMAIN" \
    APP_NAME="$APP_NAME" \
    APP_DIR="$APP_DIR" \
    BACKEND_PORT="$BACKEND_PORT" \
    DEVPANEL_PORT="$DEVPANEL_PORT" \
    PUBLIC_PORT="$PUBLIC_PORT" \
    EXPOSE_PUBLIC_PORT="$EXPOSE_PUBLIC_PORT" \
    ENABLE_STANDARD_HTTP="$ENABLE_STANDARD_HTTP" \
    ENABLE_PUBLIC_TLS="$ENABLE_PUBLIC_TLS" \
    CLEAN_STALE_TOP_LEVEL="$CLEAN_STALE_TOP_LEVEL" \
    CONFIG_ONLY=true \
    SERVICE_NAME="$SERVICE_NAME" \
    DEVPANEL_SERVICE_NAME="$DEVPANEL_SERVICE_NAME" \
    NGINX_SITE_NAME="$APP_NAME" \
    BACKEND_USER="www-data" \
    bash "$APP_DIR/deploy/deploy_aliyun.sh"
    if systemctl list-unit-files | grep -q '^$DEVPANEL_SERVICE_NAME.service'; then
        systemctl is-active --quiet "$DEVPANEL_SERVICE_NAME" && echo "  $DEVPANEL_SERVICE_NAME 服务运行中" || echo "  [WARN] $DEVPANEL_SERVICE_NAME 服务未正常启动，请检查日志"
    else
        echo "  [WARN] 未发现 $DEVPANEL_SERVICE_NAME.service（可先执行 deploy_aliyun.sh 初始化）"
    fi
    sleep 2
    systemctl is-active --quiet "$SERVICE_NAME" && echo "  $SERVICE_NAME 服务运行中" || echo "  [WARN] $SERVICE_NAME 服务未正常启动，请检查日志"
REMOTE

echo ""
echo "=========================================="
echo "  部署完成!"
echo "=========================================="
echo ""
echo "  ECS 站点域名: $DOMAIN"
echo "  预期后端内网端口: $BACKEND_PORT"
echo "  预期开发面板端口: $DEVPANEL_PORT"
echo "  预期公网访问端口: $PUBLIC_PORT"
echo "  预期公网协议: $(public_scheme)"
echo "  如需刷新 Nginx/systemd 端口配置，请在服务器执行:"
echo "    BACKEND_PORT=$BACKEND_PORT DEVPANEL_PORT=$DEVPANEL_PORT PUBLIC_PORT=$PUBLIC_PORT EXPOSE_PUBLIC_PORT=$EXPOSE_PUBLIC_PORT ENABLE_STANDARD_HTTP=$ENABLE_STANDARD_HTTP ENABLE_PUBLIC_TLS=$ENABLE_PUBLIC_TLS DOMAIN=$DOMAIN bash $APP_DIR/deploy/deploy_aliyun.sh"
echo "  开发面板(服务器本机): http://127.0.0.1:$DEVPANEL_PORT"
echo "  远程访问开发面板: ssh -L $DEVPANEL_PORT:127.0.0.1:$DEVPANEL_PORT $SERVER"
echo ""
echo "  检查服务:"
echo "    ssh $SERVER 'systemctl status $SERVICE_NAME'"
echo "    ssh $SERVER 'systemctl status $DEVPANEL_SERVICE_NAME'"
echo "    ssh $SERVER 'journalctl -u $SERVICE_NAME -f'"
echo "    ssh $SERVER 'journalctl -u $DEVPANEL_SERVICE_NAME -f'"
echo ""
echo "  运维面板验收示例:"
echo "    curl -H 'X-Ops-Token: <OPS_PANEL_TOKEN>' $(public_scheme)://$DOMAIN:$PUBLIC_PORT/ops/api/status"
echo ""
echo "  一键轮换 /ops token:"
echo "    ssh $SERVER \"OPS_PANEL_TOKEN='<NEW_TOKEN>' $APP_DIR/deploy/rotate_ops_token.sh\""
echo ""

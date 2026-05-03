#!/bin/bash
# ============================================================
# 圆桌思辨 — 快速部署脚本（本地构建 + 推送至阿里云 ECS）
# 用法: ./deploy/deploy.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ---- 配置（按需修改）----
SERVER="root@39.97.253.10"
APP_DIR="/opt/roundtable"
SSH_PORT="${SSH_PORT:-22}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[1/4]${NC} $1"; }
log2() { echo -e "${GREEN}[2/4]${NC} $1"; }
log3() { echo -e "${GREEN}[3/4]${NC} $1"; }
log4() { echo -e "${GREEN}[4/4]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo "=========================================="
echo "  圆桌思辨 快速部署"
echo "  目标: $SERVER"
echo "=========================================="
echo ""

# ---- Step 1: 构建前端 ----
log "构建 Flutter Web 前端..."
cd "$PROJECT_DIR"
bash "$PROJECT_DIR/build_web.sh" --force || err "前端构建失败"

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
ssh -p "$SSH_PORT" "$SERVER" << 'REMOTE'
    set -e
    cd /opt/roundtable/backend
    if [ -f .venv/bin/activate ]; then
        source .venv/bin/activate
        pip install -e .[dev] -q 2>&1 | tail -1
    fi
    # 确保 runtime 目录权限
    mkdir -p runtime
    chown -R www-data:www-data runtime 2>/dev/null || true
    [ -f /opt/roundtable/deploy/rotate_ops_token.sh ] && chmod +x /opt/roundtable/deploy/rotate_ops_token.sh || true
    systemctl restart roundtable
    if systemctl list-unit-files | grep -q '^roundtable-devpanel.service'; then
        systemctl restart roundtable-devpanel
        systemctl is-active --quiet roundtable-devpanel && echo "  roundtable-devpanel 服务运行中" || echo "  [WARN] roundtable-devpanel 服务未正常启动，请检查日志"
    else
        echo "  [WARN] 未发现 roundtable-devpanel.service（可先执行 deploy_aliyun.sh 初始化）"
    fi
    sleep 2
    systemctl is-active --quiet roundtable && echo "  roundtable 服务运行中" || echo "  [WARN] roundtable 服务未正常启动，请检查日志"
REMOTE

echo ""
echo "=========================================="
echo "  部署完成!"
echo "=========================================="
echo ""
echo "  网站: https://rainforgrain.top"
echo "  管理: https://rainforgrain.top/admin/"
echo "  运维面板: https://rainforgrain.top/ops/ (需 X-Ops-Token 或 ?token=...)"
echo "  开发面板(服务器本机): http://127.0.0.1:8888"
echo "  远程访问开发面板: ssh -L 8888:127.0.0.1:8888 $SERVER"
echo ""
echo "  检查服务:"
echo "    ssh $SERVER 'systemctl status roundtable'"
echo "    ssh $SERVER 'systemctl status roundtable-devpanel'"
echo "    ssh $SERVER 'journalctl -u roundtable -f'"
echo "    ssh $SERVER 'journalctl -u roundtable-devpanel -f'"
echo ""
echo "  运维面板验收示例:"
echo "    curl -H 'X-Ops-Token: <OPS_PANEL_TOKEN>' https://rainforgrain.top/ops/api/status"
echo ""
echo "  一键轮换 /ops token:"
echo "    ssh $SERVER \"OPS_PANEL_TOKEN='<NEW_TOKEN>' $APP_DIR/deploy/rotate_ops_token.sh\""
echo ""

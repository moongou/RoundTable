#!/bin/bash
# ============================================================
# RoundTable /ops token 一键轮换脚本
# 用法:
#   OPS_PANEL_TOKEN='<NEW_TOKEN>' ./deploy/rotate_ops_token.sh
#   ./deploy/rotate_ops_token.sh '<NEW_TOKEN>'
#
# 可选环境变量:
#   NGINX_CONF=/etc/nginx/sites-available/roundtable
#   NGINX_RELOAD=1   # 1=校验并重载 nginx, 0=仅改文件不重载
# ============================================================
set -euo pipefail

NGINX_CONF="${NGINX_CONF:-/etc/nginx/sites-available/roundtable}"
NGINX_RELOAD="${NGINX_RELOAD:-1}"
NEW_TOKEN="${OPS_PANEL_TOKEN:-${1:-}}"

if [[ -z "$NEW_TOKEN" ]]; then
    echo "[ERROR] 请输入新 token。"
    echo "示例: OPS_PANEL_TOKEN='your-long-token' ./deploy/rotate_ops_token.sh"
    exit 1
fi

# 限制 token 字符集，避免破坏 Nginx 双引号字符串
if [[ ! "$NEW_TOKEN" =~ ^[A-Za-z0-9._~-]{16,128}$ ]]; then
    echo "[ERROR] token 格式不合法。"
    echo "仅允许字符: A-Z a-z 0-9 . _ ~ -，长度 16-128"
    exit 1
fi

if [[ ! -f "$NGINX_CONF" ]]; then
    echo "[ERROR] 未找到 Nginx 配置文件: $NGINX_CONF"
    exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
    if [[ "$NGINX_RELOAD" == "0" && -w "$NGINX_CONF" ]]; then
        echo "[WARN] 非 root 模式：仅用于本地文件演练，不会执行 nginx reload。"
    else
        echo "[ERROR] 需要 root 权限运行。请使用 sudo 或 root 用户执行。"
        exit 1
    fi
fi

TMP_CONF="$(mktemp)"
BACKUP_FILE="${NGINX_CONF}.bak.$(date +%Y%m%d%H%M%S)"
trap 'rm -f "$TMP_CONF"' EXIT

cp "$NGINX_CONF" "$BACKUP_FILE"

if ! awk -v token="$NEW_TOKEN" '
    {
        if ($0 ~ /map \$http_x_ops_token \$ops_token_header_ok \{/) {
            in_header = 1
            print
            next
        }
        if ($0 ~ /map \$arg_token \$ops_token_query_ok \{/) {
            in_query = 1
            print
            next
        }

        if ((in_header || in_query) && $0 ~ /"[^"]*" 1;/) {
            sub(/"[^"]*" 1;/, "\"" token "\" 1;")
            if (in_header) header_changed++
            if (in_query) query_changed++
        }

        if ((in_header || in_query) && $0 ~ /^}/) {
            if (in_header) in_header = 0
            if (in_query) in_query = 0
        }

        print
    }
    END {
        if (header_changed != 1 || query_changed != 1) {
            exit 42
        }
    }
' "$NGINX_CONF" > "$TMP_CONF"; then
    echo "[ERROR] 轮换失败：未正确匹配 Nginx token 映射块。"
    echo "已保留备份: $BACKUP_FILE"
    exit 1
fi

cp "$TMP_CONF" "$NGINX_CONF"

if [[ "$NGINX_RELOAD" == "1" ]]; then
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx
    else
        cp "$BACKUP_FILE" "$NGINX_CONF"
        echo "[ERROR] nginx -t 失败，已自动回滚配置。"
        nginx -t || true
        exit 1
    fi
else
    echo "[WARN] NGINX_RELOAD=0，仅更新文件，未执行 nginx -t / reload。"
fi

masked_token=$(printf '%s' "$NEW_TOKEN" | sed -E 's/^(.{4}).*(.{4})$/\1****\2/')
echo "[OK] /ops token 已轮换: $masked_token"
echo "[OK] 配置备份: $BACKUP_FILE"

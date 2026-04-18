#!/bin/bash
# 构建 Flutter Web 前端并部署到后端 static 目录
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
BACKEND_STATIC_DIR="$SCRIPT_DIR/backend/static"

# 设置国内镜像（如需要可取消注释）
# export PUB_HOSTED_URL=https://pub.flutter-io.cn
# export FLUTTER_STORAGE_BASE_URL=https://storage.flutter-io.cn

echo "=== RoundTable Web 构建脚本 ==="

# 检查 Flutter
if ! command -v flutter &> /dev/null; then
    export PATH="/Users/m3max/flutter/bin:$PATH"
fi

echo "[1/3] 安装依赖..."
cd "$FRONTEND_DIR"
flutter pub get

echo "[2/3] 构建 Web..."
flutter build web --release

echo "[3/3] 部署到后端 static 目录..."
rm -rf "$BACKEND_STATIC_DIR"
cp -r "$FRONTEND_DIR/build/web" "$BACKEND_STATIC_DIR"

echo "=== 构建完成！==="
echo "静态文件已部署到: $BACKEND_STATIC_DIR"
echo "启动后端即可访问: http://localhost:8001"
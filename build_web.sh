#!/bin/bash
# 构建 Flutter Web 前端并部署到后端 static 目录（支持按需构建）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
BACKEND_STATIC_DIR="$SCRIPT_DIR/backend/static"
STAMP_FILE="$BACKEND_STATIC_DIR/.frontend_source.sha256"

# 设置国内镜像（如需要可取消注释）
# export PUB_HOSTED_URL=https://pub.flutter-io.cn
# export FLUTTER_STORAGE_BASE_URL=https://storage.flutter-io.cn

FORCE_BUILD=0
if [[ "${1:-}" == "--force" ]]; then
    FORCE_BUILD=1
fi

echo "=== RoundTable Web 构建脚本 ==="

BUILD_ARGS=()
FLUTTER_BIN="${FLUTTER_BIN:-flutter}"

if ! command -v "$FLUTTER_BIN" >/dev/null 2>&1; then
    if [[ -d "$HOME/flutter/bin" ]]; then
        export PATH="$HOME/flutter/bin:$PATH"
        FLUTTER_BIN="flutter"
    else
        echo "未找到 flutter。请先安装 Flutter，或通过 FLUTTER_BIN 指定可执行路径。"
        exit 1
    fi
fi

calc_source_fingerprint() {
    local -a files=()
    local entry
    local path
    local rel
    local digest

    for path in "$FRONTEND_DIR/lib" "$FRONTEND_DIR/web" "$FRONTEND_DIR/pubspec.yaml" "$FRONTEND_DIR/pubspec.lock"; do
        if [[ -d "$path" ]]; then
            while IFS= read -r entry; do
                files+=("$entry")
            done < <(find "$path" -type f | LC_ALL=C sort)
        elif [[ -f "$path" ]]; then
            files+=("$path")
        fi
    done

    if [[ ${#files[@]} -eq 0 ]]; then
        echo ""
        return
    fi

    {
        for entry in "${files[@]}"; do
            digest="$(shasum -a 256 "$entry" | awk '{print $1}')"
            rel="${entry#$FRONTEND_DIR/}"
            printf '%s  %s\n' "$digest" "$rel"
        done
    } | shasum -a 256 | awk '{print $1}'
}

SOURCE_FINGERPRINT="$(calc_source_fingerprint)"
PREV_FINGERPRINT=""
if [[ -f "$STAMP_FILE" ]]; then
    PREV_FINGERPRINT="$(cat "$STAMP_FILE" 2>/dev/null || true)"
fi

if [[ $FORCE_BUILD -eq 0 ]] && [[ -n "$SOURCE_FINGERPRINT" ]] && [[ "$SOURCE_FINGERPRINT" == "$PREV_FINGERPRINT" ]] && [[ -f "$BACKEND_STATIC_DIR/index.html" ]]; then
    echo "源码无变更，跳过 Flutter Web 构建。"
    echo "如需强制重建，请执行: ./build_web.sh --force"
    exit 0
fi

echo "[1/3] 安装依赖..."
cd "$FRONTEND_DIR"
if "$FLUTTER_BIN" pub get; then
    echo "依赖安装完成"
elif [[ -f ".dart_tool/package_config.json" ]]; then
    echo "依赖安装失败，改用本地缓存继续构建 (--no-pub)"
    BUILD_ARGS+=(--no-pub)
else
    echo "依赖安装失败，且未找到本地缓存的 package_config.json"
    exit 1
fi

echo "[2/3] 构建 Web..."
"$FLUTTER_BIN" build web --release --pwa-strategy=none --no-tree-shake-icons "${BUILD_ARGS[@]}"

echo "[3/3] 同步到后端 static 目录..."
mkdir -p "$BACKEND_STATIC_DIR"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$FRONTEND_DIR/build/web/" "$BACKEND_STATIC_DIR/"
else
    rm -rf "$BACKEND_STATIC_DIR"
    cp -r "$FRONTEND_DIR/build/web" "$BACKEND_STATIC_DIR"
fi

if [[ -n "$SOURCE_FINGERPRINT" ]]; then
    echo "$SOURCE_FINGERPRINT" > "$STAMP_FILE"
fi

echo "=== 构建完成！==="
echo "静态文件已部署到: $BACKEND_STATIC_DIR"
echo "启动后端即可访问: http://localhost:8001"
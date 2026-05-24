#!/bin/bash
# ============================================================
# 圆桌思辨 HarmonyOS 构建脚本
# 用法:
#   ./build_harmonyos.sh              # 构建 release HAP
#   ./build_harmonyos.sh debug        # 构建 debug HAP
#   ./build_harmonyos.sh sync-version # 仅同步版本号
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VERSION_FILE="$SCRIPT_DIR/version.json"
BUILD_MODE="${1:-release}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ---- 步骤1: 读取版本号 ----
sync_version() {
  log_info "同步版本号..."
  local version
  version=$(python3 -c "import json; print(json.load(open('$VERSION_FILE'))['version'])")
  local version_code
  version_code=$(python3 -c "import json; print(json.load(open('$VERSION_FILE'))['versionCode'])")

  log_info "  版本: $version (code: $version_code)"

  # 使用 python 更新 json/json5 文件中的版本号
  python3 -c "
import json, re

version = '$version'
version_code = $version_code

# 更新 AppScope/app.json5 (json5格式兼容json)
with open('$SCRIPT_DIR/AppScope/app.json5', 'r') as f:
    content = f.read()
content = re.sub(r'\"versionCode\": \d+', f'\"versionCode\": {version_code}', content)
content = re.sub(r'\"versionName\": \"[^\"]+\"', f'\"versionName\": \"{version}\"', content)
with open('$SCRIPT_DIR/AppScope/app.json5', 'w') as f:
    f.write(content)

# 更新 oh-package.json5
with open('$SCRIPT_DIR/oh-package.json5', 'r') as f:
    content = f.read()
content = re.sub(r'\"version\": \"[^\"]+\"', f'\"version\": \"{version}\"', content)
with open('$SCRIPT_DIR/oh-package.json5', 'w') as f:
    f.write(content)

print('版本号同步完成')
"
  log_info "版本号同步完成"
}

# ---- 步骤2: 构建Flutter Web ----
build_flutter_web() {
  log_info "构建Flutter Web..."
  cd "$PROJECT_DIR/frontend"

  if ! command -v flutter &> /dev/null; then
    log_error "Flutter SDK未安装"
    exit 1
  fi

  flutter build web --release --base-href /
  log_info "Flutter Web构建完成"
}

# ---- 步骤3: 复制Web资源到rawfile ----
sync_web_assets() {
  log_info "复制Web资源到rawfile..."
  local rawfile_dir="$SCRIPT_DIR/entry/src/main/resources/rawfile"
  local web_build_dir="$PROJECT_DIR/frontend/build/web"

  if [ ! -d "$web_build_dir" ]; then
    log_warn "Flutter Web未构建，正在构建..."
    build_flutter_web
  fi

  mkdir -p "$rawfile_dir"

  # 清空旧资源
  rm -rf "$rawfile_dir"/*

  # 复制所有web资源（排除Flutter默认的favicon，保留自定义的）
  cp -r "$web_build_dir"/* "$rawfile_dir/"

  local file_count
  file_count=$(find "$rawfile_dir" -type f | wc -l | tr -d ' ')
  log_info "已复制 $file_count 个文件到 rawfile"
}

# ---- 步骤4: 构建HarmonyOS HAP ----
build_hap() {
  log_info "构建HarmonyOS HAP ($BUILD_MODE)..."
  cd "$SCRIPT_DIR"

  # 检查DevEco Studio SDK
  if [ ! -d "/Applications/DevEco-Studio.app" ]; then
    log_error "DevEco Studio未安装在默认路径"
    log_info "请修改 local.properties 中的 sdk.dir"
    exit 1
  fi

  if [ "$BUILD_MODE" = "debug" ]; then
    hvigorw assembleHap -p buildMode=debug
  else
    hvigorw assembleHap -p buildMode=release
  fi

  log_info "HAP构建完成!"
  log_info "输出位置: $SCRIPT_DIR/entry/build/default/outputs/default/"
  ls -la "$SCRIPT_DIR/entry/build/default/outputs/default/"*.hap 2>/dev/null || \
    log_warn "未找到 .hap 文件，请检查构建日志"
}

# ---- 主流程 ----
echo ""
echo "=========================================="
echo "  圆桌思辨 HarmonyOS 构建工具"
echo "=========================================="
echo ""

case "$BUILD_MODE" in
  sync-version)
    sync_version
    log_info "完成! 版本号已同步到所有配置文件"
    ;;
  debug)
    sync_version
    sync_web_assets
    build_hap
    log_info "完成! Debug HAP已构建"
    ;;
  release|*)
    sync_version
    sync_web_assets
    build_hap
    log_info "完成! Release HAP已构建"
    ;;
esac

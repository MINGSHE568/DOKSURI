#!/usr/bin/env bash
# =====================================================================
# DOKSURI DMG 安装包生成脚本（在 Mac 终端运行：bash make_dmg.sh）
# 需先运行 build.sh 生成 dist/DOKSURI3A.app
# 产物：dist/DOKSURI3A-<版本>-<架构>.dmg（挂载后拖拽安装，含 Applications 快捷方式）
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")"

APP="dist/DOKSURI3A.app"
VER="3.0.0"
ARCH="$(uname -m)"
DMG="dist/DOKSURI3A-${VER}-${ARCH}.dmg"

[ -d "$APP" ] || { echo "未找到 $APP，请先运行 bash build.sh"; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/DOKSURI3A.app"
ln -s /Applications "$STAGE/Applications"    # 拖拽安装的 Applications 快捷方式

rm -f "$DMG"
hdiutil create -volname DOKSURI -srcfolder "$STAGE" -ov -format UDZO "$DMG"

echo "完成：$DMG"
echo "分发方式：双击挂载 → 把 DOKSURI3A.app 拖入 Applications 文件夹 → 弹出磁盘映像"

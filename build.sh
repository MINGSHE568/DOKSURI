#!/usr/bin/env bash
# =====================================================================
# DOKSURI macOS 一键打包脚本（在 Mac 终端运行：bash build.sh）
# 产物：dist/DOKSURI3A.app（内置中英翻译 + 中文 OCR 基础模型，开箱离线可用）
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="DOKSURI3A"

echo "==> [1/5] 检查环境（架构 $(uname -m) / macOS $(sw_vers -productVersion)）"
command -v python3 >/dev/null 2>&1 || {
    echo "缺少 python3：请先运行 xcode-select --install，或安装 Homebrew Python"; exit 1; }

echo "==> [2/5] 创建虚拟环境并安装依赖（首次约数分钟）"
python3 -m venv .venv-mac
source .venv-mac/bin/activate
python -m pip install -U pip wheel
pip install -r requirements.txt
pip install pyinstaller

echo "==> [3/5] 生成 macOS 图标（doksuri.icns / doksuri.png）"
python - <<'PY'
from PIL import Image
im = Image.open("doksuri.ico").convert("RGBA")
side = max(im.size)
if side < 1024:
    canvas = Image.new("RGBA", (max(side, 1024), max(side, 1024)), (0, 0, 0, 0))
    canvas.paste(im, ((max(side, 1024) - im.size[0]) // 2,
                      (max(side, 1024) - im.size[1]) // 2))
    im = canvas
im.resize((1024, 1024), Image.LANCZOS).save("doksuri.png")   # 窗口图标回退
print("PNG 生成完成:", im.size)
PY
ICONSET="doksuri.iconset"; rm -rf "$ICONSET"; mkdir -p "$ICONSET"
for s in 16 32 64 128 256 512; do
    sips -z $s $s doksuri.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    d=$((s*2)); sips -z $d $d doksuri.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o doksuri.icns && rm -rf "$ICONSET"
echo "icns 生成完成"

echo "==> [4/5] PyInstaller 打包（约 5-15 分钟，取决于机器）"
pyinstaller --clean --noconfirm doksuri_mac.spec

echo "==> [5/5] 校验产物"
[ -d "dist/${APP_NAME}.app" ] || { echo "打包失败：未生成 ${APP_NAME}.app"; exit 1; }
du -sh "dist/${APP_NAME}.app"
echo "完成：dist/${APP_NAME}.app（双击即可运行，首次启动需在系统设置中授予屏幕录制权限后重启）"
echo "可选：运行 bash make_dmg.sh 生成 DMG 安装包"

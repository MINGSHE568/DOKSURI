# -*- mode: python ; coding: utf-8 -*-
"""DOKSURI macOS 打包配置（build.sh 调用：pyinstaller --clean --noconfirm doksuri_mac.spec）

- onedir + BUNDLE：生成标准 macOS 应用包 dist/DOKSURI3A.app（无控制台，等价 --windowed）
- Bundle ID：com.doksuri.app；版本 3.0.0；Retina 高分屏（NSHighResolutionCapable）
- Info.plist 含屏幕录制权限说明（NSScreenCaptureUsageDescription）
- 内置基础模型 _models（中英双向翻译 + 中文 OCR），首次启动自动复制到
  ~/Library/Application Support/DOKSURI/models，无需联网下载
- 架构：跟随构建机（M 系列 → arm64 原生；Intel → x86_64 原生）
"""
from PyInstaller.utils.hooks import collect_all

datas = [
    ("_models", "_models"),          # 内置基础模型（首次启动复制到 ~/Library/.../DOKSURI/models）
    ("doksuri.ico", "."),            # 原始图标
    ("models_index.json", "."),      # 其余语对模型的下载索引
]
binaries = []
hiddenimports = []

# 大依赖全量收集：paddle 推理引擎、OCR、翻译引擎及其资源文件/动态库
for pkg in ("paddle", "paddleocr", "cv2", "argostranslate", "stanza",
            "langdetect", "ctranslate2", "sentencepiece"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# build.sh 由 doksuri.ico 生成的 macOS 图标；运行时窗口图标按 doksuri.icns/.png 查找
datas += [("doksuri.icns", "."), ("doksuri.png", ".")]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,       # onedir：二进制交由 COLLECT 归档
    name="DOKSURI3A",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,               # 等价 --windowed：无控制台黑框
    target_arch=None,            # 构建机原生架构（如需指定：arm64 / x86_64 / universal2）
    codesign_identity="-",       # ad-hoc 签名，降低 Gatekeeper 阻拦
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DOKSURI3A",
)

APP_VERSION = "3.0.0"
bundle = BUNDLE(
    coll,
    name="DOKSURI3A.app",
    icon="doksuri.icns",
    bundle_identifier="com.doksuri.app",
    version=APP_VERSION,
    info_plist={
        "CFBundleName": "DOKSURI",
        "CFBundleDisplayName": "DOKSURI",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "NSHighResolutionCapable": True,   # Retina 视网膜屏高清渲染
        "LSMinimumSystemVersion": "11.0",  # Big Sur 起，M 系列/Intel 均可
        "NSScreenCaptureUsageDescription": (
            "DOKSURI 需要「屏幕录制」权限来读取您框选的屏幕区域，"
            "以便在本机完成截图文字识别与翻译。屏幕画面仅本地处理，不会上传或联网。"
        ),
        "NSHumanReadableCopyright": "© 2026 DOKSURI",
    },
)

# -*- mode: python ; coding: utf-8 -*-
"""DOKSURI 单文件打包配置（由 build.bat 调用：pyinstaller --clean --noconfirm doksuri.spec）

- 单文件模式（onefile）：全部依赖与资源打进一个 DOKSURI3.exe
- 内置基础模型：_models 目录（中英双向翻译 + 中文 OCR），随 exe 分发
- 窗口模式（无控制台），使用 doksuri.ico 图标
"""
from PyInstaller.utils.hooks import collect_all

datas = [
    ("_models", "_models"),          # 内置基础模型（首次启动复制到 %LOCALAPPDATA%/DOKSURI/models）
    ("doksuri.ico", "."),            # 软件图标（运行时窗口/任务栏加载）
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

# onefile：没有 COLLECT 段，a.binaries/a.datas 直接进 EXE
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DOKSURI3",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="doksuri.ico",
)

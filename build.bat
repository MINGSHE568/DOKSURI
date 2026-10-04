@echo off
chcp 65001 >nul
rem ============================================================
rem  DOKSURI 一键打包脚本（PyInstaller 单文件模式）
rem
rem  用法：双击运行，或在命令行执行 build.bat
rem  产物：dist\DOKSURI3.exe（单文件，内置中英翻译模型 + 中文OCR模型）
rem
rem  打包说明：
rem   1. 前提：项目目录下已有 .venv 虚拟环境并装好全部依赖
rem      （PyQt6 / torch / ctranslate2 / paddlepaddle / paddleocr /
rem        argostranslate / stanza / langdetect / pymupdf / python-docx / openpyxl）
rem   2. 内置模型放在 _models 目录（缺失时脚本会报错提示）：
rem        _models\argos\packages\translate-en_zh-1_9   英语→中文
rem        _models\argos\packages\translate-zh_en-1_9   中文→英语
rem        _models\paddleocr\whl\det\ch                 中文 OCR 检测模型
rem        _models\paddleocr\whl\rec\ch                 中文 OCR 识别模型
rem   3. 打包耗时约 10-30 分钟，产物约 2 GB；
rem      单文件 exe 首次启动需解压到临时目录，请耐心等待 1-3 分钟
rem   4. 首次启动后基础模型自动复制到 %LOCALAPPDATA%\DOKSURI\models，
rem      之后完全离线可用；其余语对模型在「模型管理」中按需下载
rem ============================================================
setlocal
cd /d "%~dp0"

set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
    echo [错误] 未找到虚拟环境 %PY%，请先创建并安装全部依赖。
    pause
    exit /b 1
)

echo [1/3] 检查 PyInstaller ...
%PY% -m pip show pyinstaller >nul 2>&1
if errorlevel 1 %PY% -m pip install pyinstaller
if errorlevel 1 goto :fail

echo [2/3] 检查内置模型资源 _models ...
if not exist "_models\argos\packages\translate-en_zh-1_9" goto :no_models
if not exist "_models\argos\packages\translate-zh_en-1_9" goto :no_models
if not exist "_models\paddleocr\whl\det\ch" goto :no_models
if not exist "_models\paddleocr\whl\rec\ch" goto :no_models

echo [3/3] 开始打包（单文件模式，请耐心等待 10-30 分钟）...
%PY% -m PyInstaller --clean --noconfirm doksuri.spec
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  打包完成！
echo  输出文件: %cd%\dist\DOKSURI3.exe
echo  双击即可运行；首次启动解压约需 1-3 分钟。
echo ============================================================
pause
exit /b 0

:no_models
echo [错误] 缺少内置模型 _models，请先按 build.bat 头部说明准备模型资源。
pause
exit /b 1

:fail
echo [错误] 打包失败，请检查上方日志。
pause
exit /b 1

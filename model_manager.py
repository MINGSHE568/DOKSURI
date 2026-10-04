"""DOKSURI 模型管理 - 翻译/OCR 语言包枚举、下载、删除与缓存位置配置

模型全部存储在本地：
- Argos 翻译模型包：默认 ~/.local/share/argos-translate/packages，
  设置 cache_root 后重定向到 <root>/argos/packages
  （启动时经 ARGOS_PACKAGES_DIR 环境变量生效，须在 import argostranslate 前调用）。
- PaddleOCR 模型：默认 ~/.paddleocr，设置 cache_root 后重定向到 <root>/paddleocr
  （实例化 OCR 管线前写入 paddleocr.BASE_DIR 生效）。

打包版（PyInstaller frozen）差异：
- exe 内置 _models 基础模型（中英翻译 + 中文 OCR），首次启动复制到
  平台数据目录下的 DOKSURI/models 后从该处加载，无需联网下载；
- 配置文件与默认缓存位置都在平台数据目录的 DOKSURI 下。

平台数据目录（_platform_data_dir）：
- Windows：%LOCALAPPDATA%/DOKSURI
- macOS：~/Library/Application Support/DOKSURI
- Linux：~/.local/share/DOKSURI（遵循 XDG_DATA_HOME）

注意：缓存位置变更写入 model_settings.json，当前会话的部分设置需重启软件完全生效。
"""
import json
import os
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _platform_data_dir():
    """平台应用数据根目录（配置与默认模型缓存位置）。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "DOKSURI"
    if sys.platform == "linux":
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        return Path(base) / "DOKSURI"
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "DOKSURI"


# 数据目录（配置 + 默认模型缓存）：
# - 打包版（所有平台）：平台数据目录；
# - macOS：开发/打包统一走平台数据目录（~/Library/Application Support/DOKSURI），
#   避免把配置/模型写进程序目录（macOS 惯例应存用户数据目录）；
# - Windows 开发版：沿用项目目录（历史行为不变）。
if getattr(sys, "frozen", False) or sys.platform == "darwin":
    _DATA_DIR = _platform_data_dir()
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH = _DATA_DIR / "model_settings.json"
    APP_DATA_ROOT = _DATA_DIR / "models"
else:
    SETTINGS_PATH = BASE_DIR / "model_settings.json"
    APP_DATA_ROOT = None   # Windows 开发版无打包数据根，沿用各库默认位置

OCR_LANG_NAMES = {"ch": "中文+英文", "en": "英语", "japan": "日语", "korean": "韩语"}


# ---------------------------------------------------------------- 配置

def load_settings():
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_settings(cfg):
    SETTINGS_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_cache_root():
    """自定义缓存根目录；None 表示使用默认位置。"""
    return load_settings().get("cache_root")


def get_bundled_models_dir():
    """exe 内置模型目录（_MEIPASS/_models）；仅打包版存在，否则 None。"""
    if not getattr(sys, "frozen", False):
        return None
    p = Path(getattr(sys, "_MEIPASS", "")) / "_models"
    return p if p.is_dir() else None


def _copy_missing(src, dst):
    """目录级增量复制：目标已存在的条目跳过（保证用户删除的内容不被复活）。"""
    if not src.is_dir():
        return
    for child in src.iterdir():
        target = dst / child.name
        try:
            if child.is_dir():
                if not _dir_nonempty(target):
                    shutil.copytree(child, target, dirs_exist_ok=True)
            elif not target.exists():
                shutil.copy2(child, target)
        except OSError:
            pass


def _provision_bundled(src):
    """首次启动把 exe 内置的基础模型复制到平台数据目录的 models/ 下。

    之后写 .bundled 标记不再复制：用户在模型管理里删除内置模型后不会复活。
    返回数据根目录。"""
    dst = APP_DATA_ROOT
    (dst / "argos" / "packages").mkdir(parents=True, exist_ok=True)
    (dst / "paddleocr").mkdir(parents=True, exist_ok=True)
    if not (dst / ".bundled").exists():
        _copy_missing(src / "argos" / "packages", dst / "argos" / "packages")
        _copy_missing(src / "paddleocr", dst / "paddleocr")
        (dst / ".bundled").write_text("bundled", encoding="utf-8")
    return dst


def apply_startup():
    """应用缓存目录与内置模型预置（需在 import argostranslate / 实例化 OCR 管线之前调用）。"""
    root = get_cache_root()
    if root is None:
        bundled = get_bundled_models_dir()
        if bundled is not None:
            root = _provision_bundled(bundled)
        elif APP_DATA_ROOT is not None:
            root = str(APP_DATA_ROOT)   # 打包版/macOS 默认缓存位置（须为可写目录）
    if root:
        # 预建缓存子目录（幂等），保证首次下载/安装模型时目录已就绪
        (Path(root) / "argos" / "packages").mkdir(parents=True, exist_ok=True)
        (Path(root) / "paddleocr").mkdir(parents=True, exist_ok=True)
        os.environ["ARGOS_PACKAGES_DIR"] = str(Path(root) / "argos" / "packages")
        os.environ["DOKSURI_OCR_BASE"] = str(Path(root) / "paddleocr")


def minisbd_dir():
    """断句模型（minisbd）缓存目录：跟随缓存根目录；
    无自定义根时与 argostranslate 默认数据目录保持一致（兼容既有缓存）。"""
    root = get_cache_root()
    if root:
        return Path(root) / "minisbd"
    if APP_DATA_ROOT is not None:
        return APP_DATA_ROOT / "minisbd"
    return Path(os.path.expanduser("~/.local/share/argos-translate/minisbd"))


def argos_packages_dir():
    env = os.environ.get("ARGOS_PACKAGES_DIR")
    if env:
        return Path(env)
    root = get_cache_root()
    if root:
        return Path(root) / "argos" / "packages"
    if APP_DATA_ROOT is not None:
        return APP_DATA_ROOT / "argos" / "packages"
    return Path(os.path.expanduser("~/.local/share/argos-translate/packages"))


def ocr_base_dir():
    env = os.environ.get("DOKSURI_OCR_BASE")
    if env:
        return Path(env)
    root = get_cache_root()
    if root:
        return Path(root) / "paddleocr"
    if APP_DATA_ROOT is not None:
        return APP_DATA_ROOT / "paddleocr"
    return Path(os.path.expanduser("~/.paddleocr"))


# ---------------------------------------------------------------- 工具

def dir_size(path):
    total = 0
    for p in Path(path).rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def human_size(n):
    if n is None:
        return "—"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def _dir_nonempty(p):
    try:
        return p.is_dir() and any(p.iterdir())
    except OSError:
        return False


def _ocr_model_dirs(lang, base=None):
    """某 OCR 语言用到的模型目录（en/ch 共用中文检测模型，japan/korean 共享 multilingual）。"""
    base = base or ocr_base_dir()
    det = "ch" if lang in ("ch", "en") else "ml"
    return [base / "whl" / "det" / det, base / "whl" / "rec" / lang]


# ---------------------------------------------------------------- 列表

def list_models():
    """枚举全部模型（Argos 语言对 + OCR 语言）。

    返回 dict 列表：kind/ key / name / installed / size / path
    """
    from translator import CODE_NAMES, _load_index

    models = []
    installed = {}
    try:
        from argostranslate import package as _pkg
        for p in _pkg.get_installed_packages():
            installed[(p.from_code, p.to_code)] = Path(p.package_path)
    except Exception:
        pass
    pkgs_root = argos_packages_dir()
    for entry in sorted(_load_index(), key=lambda e: (e["from_code"], e["to_code"])):
        f, t = entry["from_code"], entry["to_code"]
        pair = (f, t)
        path = installed.get(pair)
        models.append({
            "kind": "argos",
            "key": pair,
            "name": f"{CODE_NAMES[f]} → {CODE_NAMES[t]}",
            "installed": path is not None,
            "size": dir_size(path) if path else None,
            "path": str(path) if path else str(pkgs_root),
        })

    base = ocr_base_dir()
    for lang in OCR_LANG_NAMES:
        ins = all(_dir_nonempty(d) for d in _ocr_model_dirs(lang, base))
        dirs = [d for d in _ocr_model_dirs(lang, base) if _dir_nonempty(d)]
        models.append({
            "kind": "ocr",
            "key": lang,
            "name": f"OCR {OCR_LANG_NAMES[lang]}（{lang}）",
            "installed": ins,
            "size": sum(dir_size(d) for d in dirs) if ins else None,
            "path": "; ".join(str(d) for d in dirs) if dirs else str(base / "whl"),
        })
    return models


# ---------------------------------------------------------------- 下载

def download_argos(f, t, progress_cb=None, cancel_check=None):
    import translator
    translator.download_and_install(
        f, t, progress_cb=progress_cb, cancel_check=cancel_check
    )


def download_ocr(lang):
    import ocr_engine
    ocr_engine.download_model(lang)


# ---------------------------------------------------------------- 删除

def uninstall_argos(f, t):
    from argostranslate import package as _pkg
    for p in _pkg.get_installed_packages():
        if p.from_code == f and p.to_code == t:
            _pkg.uninstall(p)   # 删除包目录并刷新语言缓存
            return True
    return False


def uninstall_ocr(lang):
    base = ocr_base_dir()
    targets = [base / "whl" / "rec" / lang]
    if lang == "ch":
        targets.append(base / "whl" / "det" / "ch")
    for t in targets:
        shutil.rmtree(t, ignore_errors=True)
    # japan/korean 共享的 multilingual 检测模型：两者都删后才清理
    if lang in ("japan", "korean"):
        other = "korean" if lang == "japan" else "japan"
        if not _dir_nonempty(base / "whl" / "rec" / other):
            shutil.rmtree(base / "whl" / "det" / "ml", ignore_errors=True)
    try:
        import ocr_engine
        ocr_engine._instances.pop(lang, None)   # 移除已加载管线，避免引用已删模型
    except Exception:
        pass
    return True


# ---------------------------------------------------------------- 缓存位置

def migrate_to(root, progress_cb=None):
    """把当前生效位置的模型复制到新的自定义根目录（不删除原文件）。

    须在修改环境变量之前调用（此时 argos_packages_dir()/ocr_base_dir()
    仍指向旧位置）。
    """
    jobs = []
    new_root = Path(root)
    src_pkgs = argos_packages_dir()
    if src_pkgs.is_dir() and src_pkgs != new_root / "argos" / "packages":
        jobs.append((src_pkgs, new_root / "argos" / "packages"))
    src_ocr = ocr_base_dir()
    if src_ocr.is_dir() and src_ocr != new_root / "paddleocr":
        jobs.append((src_ocr, new_root / "paddleocr"))
    total = len(jobs)
    for i, (src, dst) in enumerate(jobs, 1):
        if progress_cb:
            progress_cb(i - 1, total, f"复制 {src.name} → {dst}")
        shutil.copytree(src, dst, dirs_exist_ok=True)
    if progress_cb:
        progress_cb(total, total, "迁移完成")


def set_cache_root(root, migrate=False, progress_cb=None):
    """设置缓存根目录（None 恢复默认）。写入配置并同步当前进程环境变量；
    argostranslate 的目录绑定在 import 时确定，完全生效需重启软件。"""
    cfg = load_settings()
    if root:
        if migrate:
            migrate_to(root, progress_cb)   # 先迁移（env 仍指向旧位置）
        cfg["cache_root"] = str(Path(root))
        save_settings(cfg)
        os.environ["ARGOS_PACKAGES_DIR"] = str(Path(root) / "argos" / "packages")
        os.environ["DOKSURI_OCR_BASE"] = str(Path(root) / "paddleocr")
    else:
        cfg.pop("cache_root", None)
        save_settings(cfg)
        os.environ.pop("ARGOS_PACKAGES_DIR", None)
        os.environ.pop("DOKSURI_OCR_BASE", None)

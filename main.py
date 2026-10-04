"""DOKSURI 离线翻译 - 主程序"""
import ctypes
import os
import sys
import tempfile
from pathlib import Path

# DLL 加载顺序兼容（仅 Windows 存在此约束；其他平台按此顺序导入无副作用）：
# ctranslate2/torch 与 paddle 各自捆绑 OpenMP/MKL，若 paddle 先加载、torch 后
# 加载会报 [WinError 127]。必须在任何翻译/OCR 之前按此顺序预载。
import ctranslate2  # noqa: F401
import torch  # noqa: F401

from PyQt6.QtCore import QPoint, QRect, QUrl, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QProgressBar,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import doc_engine
import history_db
import hotkeys
import model_manager
import ocr_engine
import translator
from translator import CODE_NAMES

# 应用自定义模型缓存目录（须在 argostranslate / OCR 管线首次加载前）
model_manager.apply_startup()

SOURCE_LANGS = [("自动检测", None)] + [(name, code) for code, name in CODE_NAMES.items()]
TARGET_LANGS = [(name, code) for code, name in CODE_NAMES.items()]

MAX_TEXT_CHARS = 50000   # 文本翻译支持的最大字数
SEGMENT_CHARS = 500      # 超过该字数自动分段（500 字/段兼顾取消响应与句子连贯）


def setup_high_dpi():
    """高分屏 / Retina 适配，必须在 QApplication 创建之前调用。

    - Windows：per-monitor DPI 感知 + 缩放直通，保证字体清晰不模糊；
    - macOS：Qt6 Cocoa 后端原生支持 Retina 渲染（自动 2x 绘制与高清字体），
      无需额外声明，仅统一缩放取整策略保持各平台一致。
    """
    if sys.platform == "win32":
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )


def apply_app_font(app):
    """统一界面字体：各平台选择高清渲染效果最好的系统字体（均含中文）。"""
    if sys.platform == "win32":
        app.setFont(QFont("Microsoft YaHei UI", 9))
    elif sys.platform == "darwin":
        # Retina 屏使用系统苹方，标准字重在 2x 渲染下清晰不发虚
        app.setFont(QFont("PingFang SC", 13))
    # Linux 等其他平台沿用 Qt 默认字体


def app_icon():
    """软件图标：打包版在 _MEIPASS，开发版在项目根目录。macOS 优先 .icns/.png。"""
    root = Path(__file__).resolve().parent
    names = ("doksuri.icns", "doksuri.png", "doksuri.ico") \
        if sys.platform == "darwin" else ("doksuri.ico",)
    for base in (Path(getattr(sys, "_MEIPASS", "")), root):
        for name in names:
            p = base / name
            if p.is_file():
                return QIcon(str(p))
    return QIcon()


def set_windows_app_identity():
    """设置 Windows 应用用户模型 ID，让任务栏正确显示图标与分组。"""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "DOKSURI.Application")
        except Exception:
            pass


# macOS「系统设置 → 隐私与安全性 → 屏幕录制」面板的 scheme 直达链接
_MAC_SCREEN_CAPTURE_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)


def _mac_screen_capture_allowed():
    """macOS 屏幕录制权限预检（非 macOS 恒为 True）。

    用 CoreGraphics 公开 API 静默预检、不触发系统弹窗；未授权时系统会剔除
    其他窗口内容（抓屏只能拿到壁纸），故必须先确认权限再进入截图流程。
    """
    if sys.platform != "darwin":
        return True
    try:
        cg = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGPreflightScreenCaptureAccess())
    except Exception:
        return True   # 判定失败时不阻塞，由实际抓屏结果兜底


def _ensure_screen_capture_permission(parent):
    """确保屏幕录制权限（macOS 专有限制）。未授权时弹窗指引授权并返回 False。"""
    if _mac_screen_capture_allowed():
        return True
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("需要屏幕录制权限")
    box.setText(
        "截图翻译需要「屏幕录制」权限，macOS 出于隐私保护要求手动授权：\n\n"
        "1. 点击下方按钮，打开「系统设置 → 隐私与安全性 → 屏幕录制」\n"
        "2. 在列表中开启 DOKSURI（或运行它的终端 / IDE）\n"
        "3. 退出并重新启动本软件后即可使用\n\n"
        "屏幕画面仅用于本地文字识别，全程不会上传或联网。"
    )
    open_btn = box.addButton("打开系统设置", QMessageBox.ButtonRole.ActionRole)
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is open_btn:
        QDesktopServices.openUrl(QUrl(_MAC_SCREEN_CAPTURE_SETTINGS_URL))
    return False


def _split_sentences(para):
    """按中英文句末标点切句，标点保留在句尾。"""
    sents, cur = [], ""
    for ch in para:
        cur += ch
        if ch in "。！？；!?;":
            sents.append(cur)
            cur = ""
    if cur.strip():
        sents.append(cur)
    return sents


def split_text(text, max_len=SEGMENT_CHARS):
    """把长文本切成不超过 max_len 的段：优先在换行/句子边界断开。

    无任何标点的超长句在 max_len 处硬切，保证每段都不超过 max_len。
    """
    segments = []
    cur = ""
    for para in text.splitlines():
        para = para.strip()
        if not para:
            continue
        pieces = _split_sentences(para) if len(para) > max_len else [para]
        for piece in pieces:
            while len(piece) > max_len:      # 无标点的超长句硬切
                if cur:
                    segments.append(cur)
                    cur = ""
                segments.append(piece[:max_len])
                piece = piece[max_len:]
            if not piece:
                continue
            if cur and len(cur) + len(piece) > max_len:
                segments.append(cur)
                cur = ""
            cur += piece
    if cur:
        segments.append(cur)
    return segments


class TranslateWorker(QThread):
    """后台翻译线程：长文本自动分段逐段推理，避免卡住界面，支持随时取消。"""

    finished_ok = pyqtSignal(str, str)          # 译文, 实际源语言
    failed = pyqtSignal(str)
    models_missing = pyqtSignal(list, object)   # 缺失模型 [(f, t)], 总大小MB或None
    progress = pyqtSignal(int, int)             # 已完成段数, 总段数
    cancelled = pyqtSignal()

    def __init__(self, text, f_code, t_code):
        super().__init__()
        self.text = text
        self.f_code = f_code
        self.t_code = t_code
        self._cancelled = False

    def cancel(self):
        """请求取消：当前分段翻译完即中止（单段推理不可打断，最长约数秒）。"""
        self._cancelled = True

    def run(self):
        try:
            f = self.f_code
            if f is None:
                f = translator.detect_language(self.text)
                if f is None:
                    self.failed.emit("无法自动识别源语言（或该语言不受支持），请手动选择源语言。")
                    return
            missing = translator.missing_models(f, self.t_code)
            if missing:
                self.models_missing.emit(missing, translator.total_size_mb(missing))
                return
            segments = split_text(self.text)
            total = len(segments)
            self.progress.emit(0, total)
            # 翻译路径/语言对象/断句检查整任务只解析一次，逐段复用
            session = translator.TranslationSession(f, self.t_code)
            parts = []
            for i, seg in enumerate(segments):
                if self._cancelled:
                    self.cancelled.emit()
                    return
                parts.append(session.translate(seg))
                self.progress.emit(i + 1, total)
            if self._cancelled:
                self.cancelled.emit()
                return
            self.finished_ok.emit("\n".join(parts), f)
        except Exception as e:
            self.failed.emit(f"翻译失败：{e}")


class DownloadWorker(QThread):
    """模型包下载线程。"""

    progress = pyqtSignal(int, int)   # done_bytes, total_bytes（当前包）
    status = pyqtSignal(str)
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, pairs):
        super().__init__()
        self.pairs = pairs
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            for i, (f, t) in enumerate(self.pairs, 1):
                if self._cancelled:
                    return
                name = f"{translator.CODE_NAMES[f]} → {translator.CODE_NAMES[t]}"
                self.status.emit(f"正在下载模型（{i}/{len(self.pairs)}）：{name}")
                translator.download_and_install(
                    f, t,
                    progress_cb=lambda d, tot: self.progress.emit(d, tot),
                    cancel_check=lambda: self._cancelled,
                )
            self.finished_ok.emit()
        except UserWarning:
            return
        except Exception as e:
            self.failed.emit(f"模型下载失败：{e}")


class OcrWorker(QThread):
    """本地 OCR 识别线程，避免推理时卡住界面。"""

    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, image_path):
        super().__init__()
        self.image_path = image_path

    def run(self):
        try:
            self.done.emit(ocr_engine.recognize(self.image_path))
        except Exception as e:
            self.failed.emit(f"本地识别失败：{e}")


class DocExtractWorker(QThread):
    """文档提取线程：解析文档内容，扫描版 PDF 页面走本地 OCR。"""

    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(object)   # DocDocument
    failed = pyqtSignal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            doc = doc_engine.extract(
                self.path, progress_cb=lambda d, t: self.progress.emit(d, t)
            )
            if not doc.units:
                self.failed.emit("未在文档中找到可翻译的文本内容。")
                return
            self.finished_ok.emit(doc)
        except Exception as e:
            self.failed.emit(f"读取文档失败：{e}")


class DocTranslateWorker(QThread):
    """文档翻译线程：自动检测源语言，逐段调用本地 Argos 模型翻译。"""

    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(object)   # DocDocument
    failed = pyqtSignal(str)
    models_missing = pyqtSignal(list, object)

    def __init__(self, doc, t_code):
        super().__init__()
        self.doc = doc
        self.t_code = t_code

    def run(self):
        try:
            doc = self.doc
            sample = "\n".join(doc.units)[:600]
            f = translator.detect_language(sample)
            if f is None:
                self.failed.emit("无法自动识别文档语言，请先用「文本翻译」粘贴一段原文确认语言受支持。")
                return
            missing = translator.missing_models(f, self.t_code)
            if missing:
                self.models_missing.emit(missing, translator.total_size_mb(missing))
                return
            doc_engine.translate_all(
                doc, f, self.t_code,
                progress_cb=lambda d, t: self.progress.emit(d, t),
            )
            self.finished_ok.emit(doc)
        except Exception as e:
            self.failed.emit(f"文档翻译失败：{e}")


class DropArea(QLabel):
    """文档拖拽区：支持拖入文件或点击选择。"""

    file_dropped = pyqtSignal(str)
    clicked = pyqtSignal()

    def __init__(self):
        super().__init__(
            "将文档拖拽到此处，或点击选择文件\n\n"
            "支持 .txt / .docx / .pdf / .xlsx（扫描版 PDF 自动本地 OCR）\n"
            "文档全程本地处理，不上传任何内容"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(130)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_highlight(False)

    def _set_highlight(self, on):
        if on:
            self.setStyleSheet(
                "QLabel { border: 2px dashed #00aaff; border-radius: 8px;"
                " background: rgba(0, 170, 255, 30); color: #0088cc; }"
            )
        else:
            self.setStyleSheet(
                "QLabel { border: 2px dashed #aaaaaa; border-radius: 8px;"
                " background: rgba(0, 0, 0, 10); color: #888888; }"
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_highlight(True)

    def dragLeaveEvent(self, event):
        self._set_highlight(False)

    def dropEvent(self, event):
        self._set_highlight(False)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.file_dropped.emit(path)
                break


class ModelOpWorker(QThread):
    """模型包下载线程（Argos / OCR 通用）。"""

    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(object)   # model dict
    failed = pyqtSignal(str)

    def __init__(self, model):
        super().__init__()
        self.model = model

    def run(self):
        try:
            m = self.model
            if m["kind"] == "argos":
                model_manager.download_argos(
                    m["key"][0], m["key"][1],
                    progress_cb=lambda d, t: self.progress.emit(d, t),
                )
            else:
                model_manager.download_ocr(m["key"])
            self.finished_ok.emit(m)
        except Exception as e:
            self.failed.emit(str(e))


class MigrateWorker(QThread):
    """模型缓存目录迁移线程（复制现有模型文件到新位置）。"""

    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, root, migrate):
        super().__init__()
        self.root = root
        self.migrate = migrate

    def run(self):
        try:
            model_manager.set_cache_root(self.root, migrate=self.migrate)
            self.finished_ok.emit(self.root)
        except Exception as e:
            self.failed.emit(str(e))


class ModelManagerDialog(QDialog):
    """模型管理窗口：语言包列表 / 下载 / 删除 / 缓存位置设置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("模型管理")
        self.resize(880, 560)
        self.op_worker = None
        self.migrate_worker = None
        self._busy_key = None
        self._busy_name = ""

        v = QVBoxLayout(self)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["模型", "状态", "大小", "本地路径", "操作"])
        header = self.tree.header()
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, 230)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 90)
        self.tree.setColumnWidth(4, 90)
        v.addWidget(self.tree, stretch=1)

        cache_row = QHBoxLayout()
        cache_row.addWidget(QLabel("模型缓存位置："))
        self.cache_label = QLabel()
        self.cache_label.setWordWrap(True)
        self.btn_reset_cache = QPushButton("恢复默认")
        btn_change = QPushButton("修改位置…")
        cache_row.addWidget(self.cache_label, stretch=1)
        cache_row.addWidget(btn_change)
        cache_row.addWidget(self.btn_reset_cache)
        v.addLayout(cache_row)

        bottom = QHBoxLayout()
        self.status_label = QLabel("模型全部保存在本地，翻译与识别不依赖网络。")
        btn_close = QPushButton("关闭")
        bottom.addWidget(self.status_label, stretch=1)
        bottom.addWidget(btn_close)
        v.addLayout(bottom)

        btn_change.clicked.connect(self.on_change_cache)
        self.btn_reset_cache.clicked.connect(self.on_reset_cache)
        btn_close.clicked.connect(self.reject)

        self._refresh()

    # ---------- 列表 ----------

    def _refresh(self):
        self.tree.clear()
        models = model_manager.list_models()
        groups = {
            "argos": QTreeWidgetItem(["翻译语言包（Argos）", "", "", "", ""]),
            "ocr": QTreeWidgetItem(["OCR 语言包（PaddleOCR）", "", "", "", ""]),
        }
        for g in groups.values():
            self.tree.addTopLevelItem(g)
        n_installed = 0
        for m in models:
            g = groups[m["kind"]]
            status = "已下载" if m["installed"] else "未下载"
            item = QTreeWidgetItem([
                m["name"], status, model_manager.human_size(m["size"]), m["path"], "",
            ])
            item.setToolTip(3, m["path"])
            item.setForeground(1, QColor("#2e8b57") if m["installed"] else QColor("#b06060"))
            g.addChild(item)
            self.tree.setItemWidget(item, 4, self._make_action_cell(m))
            if m["installed"]:
                n_installed += 1
        for g in groups.values():
            g.setExpanded(True)
        self.status_label.setText(f"共 {len(models)} 个模型包，已下载 {n_installed} 个。")
        self._update_cache_label()

    def _make_action_cell(self, m):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 0, 4, 0)
        if self._busy_key == m["key"]:
            btn = QPushButton("下载中…")
            btn.setEnabled(False)
        else:
            btn = QPushButton("删除" if m["installed"] else "下载")
            btn.clicked.connect(lambda checked=False, mm=m: self._on_action(mm))
        h.addWidget(btn)
        btn_open = QPushButton("文件夹")
        btn_open.setToolTip("在访达（macOS）/资源管理器（Windows）中打开模型所在文件夹")
        btn_open.clicked.connect(lambda checked=False, mm=m: self._open_model_folder(mm))
        h.addWidget(btn_open)
        h.addStretch(1)
        return w

    def _open_model_folder(self, m):
        """打开该模型所在文件夹：macOS 用 Finder、Windows 用资源管理器
        （统一走 QDesktopServices，无需平台分支）。"""
        # OCR 模型可能分布在多个目录（det/rec 共享），取第一个存在的目录
        for part in str(m["path"]).split("; "):
            p = Path(part)
            folder = p if p.is_dir() else p.parent
            if folder.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
                return
        QMessageBox.information(
            self, "模型管理", f"模型目录尚未创建，请先下载该模型：\n{m['path']}"
        )

    def _update_cache_label(self):
        root = model_manager.get_cache_root()
        if root:
            self.cache_label.setText(f"{root}（自定义）")
        else:
            self.cache_label.setText(
                f"默认：{model_manager.argos_packages_dir().parent}；{model_manager.ocr_base_dir()}"
            )
        self.btn_reset_cache.setEnabled(root is not None)

    # ---------- 下载 / 删除 ----------

    def _on_action(self, m):
        if self.op_worker is not None and self.op_worker.isRunning():
            QMessageBox.information(self, "模型管理", "当前有下载任务进行中，请稍候。")
            return
        if m["installed"]:
            ret = QMessageBox.question(
                self, "删除模型",
                f"确定删除「{m['name']}」的本地模型文件吗？\n删除后可随时重新下载。",
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
            try:
                if m["kind"] == "argos":
                    model_manager.uninstall_argos(*m["key"])
                else:
                    model_manager.uninstall_ocr(m["key"])
                self.status_label.setText(f"已删除：{m['name']}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"删除失败：{e}")
            self._refresh()
        else:
            self._start_download(m)

    def _start_download(self, m):
        self._busy_key = m["key"]
        self._busy_name = m["name"]
        self.status_label.setText(f"正在下载：{m['name']}…")
        self._refresh()
        worker = ModelOpWorker(m)
        worker.progress.connect(self._on_dl_progress)
        worker.finished_ok.connect(self._on_dl_done)
        worker.failed.connect(self._on_dl_failed)
        self.op_worker = worker
        worker.start()

    def _on_dl_progress(self, done, total):
        if total:
            self.status_label.setText(f"正在下载 {self._busy_name}… {done * 100 // total}%")

    def _on_dl_done(self, m):
        self._busy_key = None
        self.status_label.setText(f"下载完成：{m['name']}")
        self._refresh()

    def _on_dl_failed(self, msg):
        self._busy_key = None
        self.status_label.setText("下载失败")
        self._refresh()
        QMessageBox.warning(self, "模型管理", f"下载失败：{msg}")

    # ---------- 缓存位置 ----------

    def on_change_cache(self):
        current = model_manager.get_cache_root() or str(Path.home())
        root = QFileDialog.getExistingDirectory(self, "选择模型缓存根目录", current)
        if not root:
            return
        ret = QMessageBox.question(
            self, "修改缓存位置",
            f"将把模型缓存根目录设置为：\n{root}\n\n"
            "是否同时把现有模型文件复制到新目录？\n"
            "（复制完成并重启软件后完全生效，原目录文件保留）",
        )
        migrate = ret == QMessageBox.StandardButton.Yes
        self.status_label.setText("正在设置缓存位置…" if not migrate else "正在复制模型文件，请稍候…")
        worker = MigrateWorker(root, migrate)
        worker.finished_ok.connect(self._on_migrate_done)
        worker.failed.connect(self._on_migrate_failed)
        self.migrate_worker = worker
        worker.start()

    def _on_migrate_done(self, root):
        self._update_cache_label()
        self.status_label.setText(f"已设置缓存位置：{root}（重启软件后完全生效）")

    def _on_migrate_failed(self, msg):
        self.status_label.setText("缓存位置设置失败")
        QMessageBox.critical(self, "错误", f"设置失败：{msg}")

    def on_reset_cache(self):
        ret = QMessageBox.question(
            self, "恢复默认",
            "将恢复使用默认模型缓存位置（重启软件后完全生效）。\n"
            "自定义目录中的文件不会被删除。继续吗？",
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        model_manager.set_cache_root(None)
        self._refresh()
        self.status_label.setText("已恢复默认缓存位置（重启软件后完全生效）")

    # ---------- 关闭保护 ----------

    def _busy(self):
        return ((self.op_worker is not None and self.op_worker.isRunning())
                or (self.migrate_worker is not None and self.migrate_worker.isRunning()))

    def reject(self):
        if self._busy():
            QMessageBox.information(self, "模型管理", "当前有任务进行中，请等待完成后再关闭。")
            return
        super().reject()


class RegionSelector(QWidget):
    """全屏半透明遮罩，鼠标框选屏幕区域。

    松开鼠标发出 selected(QRect 全局坐标)；按 Esc 发出 cancelled()。
    """
    selected = pyqtSignal(object)
    cancelled = pyqtSignal()

    MIN_SIZE = 10

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self._origin = None   # QPoint 全局坐标
        self._cur = None

        desktop = QRect()
        for s in QApplication.screens():
            desktop = desktop.united(s.virtualGeometry())
        self._desktop = desktop
        self.setGeometry(desktop)

    # ---- 坐标工具 ----

    def _selection(self):
        """当前选区（全局坐标，normalized）；未框选返回 None。"""
        if self._origin is None or self._cur is None:
            return None
        return QRect(self._origin, self._cur).normalized()

    # ---- 事件 ----

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.globalPosition().toPoint()
            self._cur = self._origin
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            # macOS 触控板默认「双指轻点=右键」，提供与 Esc 等效的取消手势
            self._close()
            self.cancelled.emit()

    def mouseMoveEvent(self, event):
        if self._origin is not None:
            self._cur = event.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        sel = self._selection()
        self._close()
        if sel is None or sel.width() < self.MIN_SIZE or sel.height() < self.MIN_SIZE:
            self.cancelled.emit()
        else:
            self.selected.emit(sel)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._close()
            self.cancelled.emit()

    def _close(self):
        self.hide()
        self.deleteLater()

    # ---- 绘制 ----

    def paintEvent(self, event):
        p = QPainter(self)
        # 全屏压暗
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        sel = self._selection()
        if sel is not None:
            local = sel.translated(-self._desktop.x(), -self._desktop.y())
            # 选区镂空，透出真实屏幕
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(local, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = p.pen()
            pen.setColor(QColor(0, 170, 255))
            pen.setWidth(2)
            p.setPen(pen)
            p.drawRect(local)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DOKSURI 离线翻译")
        self.resize(900, 600)
        self.worker = None
        self.download_worker = None
        self.ocr_worker = None
        self.doc_extract_worker = None
        self.doc_translate_worker = None
        self.selector = None
        self._ocr_temp_file = None
        self._doc_path = None
        self.doc = None
        self._download_target = "text"   # 模型下载完成后回到哪个流程：text / doc
        self._cur_source_text = ""       # 本次翻译的原文（写入历史用，避免翻译期间被编辑）
        self._init_ui()
        self._setup_hotkeys()
        self._fit_to_screen()

    def _fit_to_screen(self):
        """把初始窗口限制在屏幕可用区域内并居中，避免 MacBook 等小屏尺寸异常。"""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        w = min(self.width(), int(geo.width() * 0.92))
        h = min(self.height(), int(geo.height() * 0.92))
        self.resize(w, h)
        self.move(geo.x() + (geo.width() - w) // 2,
                  geo.y() + (geo.height() - h) // 2)

    def _setup_hotkeys(self):
        """注册系统级全局快捷键（应用在后台也可响应）。

        修饰键用平台标准按键：Windows Ctrl / macOS Command（hotkeys.ACCEL_MOD）。
        """
        self.hotkeys = hotkeys.GlobalHotkeys()
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self.hotkeys)
        ok_t = self.hotkeys.add(
            hotkeys.HK_QUICK_TRANSLATE, hotkeys.ACCEL_MOD, hotkeys.VK_T,
            self.quick_translate,
        )
        ok_o = self.hotkeys.add(
            hotkeys.HK_QUICK_SCREENSHOT, hotkeys.ACCEL_MOD | hotkeys.MOD_SHIFT,
            hotkeys.VK_O, self.quick_screenshot,
        )
        if not (ok_t and ok_o):
            self.status_label.setText(
                "部分全局快捷键注册失败（可能被其他程序占用），手动操作不受影响"
            )

    def _init_ui(self):
        self._build_menu_bar()
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, stretch=1)
        self.tabs.addTab(self._build_text_tab(), "文本翻译")
        self.tabs.addTab(self._build_doc_tab(), "文档翻译")
        self.tabs.addTab(self._build_history_tab(), "翻译历史")
        # 窗口级拖拽：文档文件可拖到窗口任意位置导入（拖到拖拽区也可）
        self.setAcceptDrops(True)

    def _build_menu_bar(self):
        """构建菜单栏，整合到 macOS 屏幕顶部系统菜单栏。

        Qt 会自动把 QMenuBar 映射到 macOS 系统菜单栏；带 MenuRole 的动作
        （设置/关于/退出）自动归入应用菜单（DOKSURI → 设置… Cmd+, 等），
        其余动作显示在「工具」菜单。Windows 上则原样显示为窗口内菜单栏。
        """
        mbar = self.menuBar()
        tools = mbar.addMenu("工具")

        act_model = QAction("模型管理…", self)
        act_model.triggered.connect(self.open_model_manager)
        tools.addAction(act_model)

        act_hist = QAction("翻译历史", self)
        act_hist.triggered.connect(self.open_history_tab)
        tools.addAction(act_hist)

        act_pref = QAction("设置…", self)
        act_pref.setMenuRole(QAction.MenuRole.PreferencesRole)
        act_pref.setShortcut(QKeySequence.StandardKey.Preferences)   # macOS Cmd+,
        act_pref.triggered.connect(self.open_settings)
        tools.addAction(act_pref)

        act_about = QAction("关于 DOKSURI", self)
        act_about.setMenuRole(QAction.MenuRole.AboutRole)
        act_about.triggered.connect(self.show_about)
        tools.addAction(act_about)

        act_quit = QAction("退出", self)
        act_quit.setMenuRole(QAction.MenuRole.QuitRole)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)          # macOS Cmd+Q
        act_quit.triggered.connect(self.close)
        tools.addAction(act_quit)

    def open_model_manager(self):
        dlg = ModelManagerDialog(self)
        dlg.exec()

    def open_history_tab(self):
        """菜单/系统菜单栏入口：切到「翻译历史」标签页。"""
        self.tabs.setCurrentIndex(2)

    def open_settings(self):
        """设置入口（macOS 应用菜单「设置…」Cmd+,）：打开模型与缓存设置。"""
        self.open_model_manager()

    def show_about(self):
        """关于对话框（macOS 应用菜单「关于 DOKSURI」）。"""
        QMessageBox.about(
            self, "关于 DOKSURI",
            "DOKSURI 离线翻译\n\n"
            "完全离线的本地翻译与 OCR 工具：\n"
            "文本翻译 · 文档翻译 · 截图/图片翻译 · 翻译历史 · 模型管理\n\n"
            "所有翻译与识别运算全部在本机完成，文本与文档不经过网络。",
        )

    # ---- 窗口级拖拽导入（macOS Finder / Windows 资源管理器通用） ----

    def dragEnterEvent(self, event):
        """拖入文档文件时接受，切换到文档翻译标签页。"""
        if event.mimeData().hasUrls() and any(
            Path(u.toLocalFile()).suffix.lower() in doc_engine.SUPPORTED_EXTS
            for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event):
        """文档文件拖到窗口任意位置即导入：自动切换到文档翻译并读取。"""
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in doc_engine.SUPPORTED_EXTS:
                self.tabs.setCurrentIndex(1)
                self.on_doc_file_chosen(path)
                event.acceptProposedAction()
                return

    # ---------- 标签页一：文本翻译 ----------

    def _build_text_tab(self):
        page = QWidget()
        root = QVBoxLayout(page)

        # 顶部：源语言、交换按钮、目标语言
        top_bar = QHBoxLayout()
        self.source_lang = QComboBox()
        for name, code in SOURCE_LANGS:
            self.source_lang.addItem(name, code)
        self.source_lang.setCurrentIndex(0)  # 默认自动检测
        self.swap_btn = QPushButton("交换语言")
        self.target_lang = QComboBox()
        for name, code in TARGET_LANGS:
            self.target_lang.addItem(name, code)
        self.target_lang.setCurrentIndex(1)  # 默认英语
        top_bar.addWidget(self.source_lang)
        top_bar.addWidget(self.swap_btn)
        top_bar.addWidget(self.target_lang)
        root.addLayout(top_bar)

        # 中间：左右分栏（原文 / 译文）
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.source_edit = QPlainTextEdit()
        self.source_edit.setPlaceholderText("请输入要翻译的原文…")
        self.target_edit = QPlainTextEdit()
        self.target_edit.setPlaceholderText("译文将显示在这里…")
        self.target_edit.setReadOnly(True)
        self.splitter.addWidget(self.source_edit)
        self.splitter.addWidget(self.target_edit)
        self.splitter.setSizes([450, 450])
        root.addWidget(self.splitter, stretch=1)

        # 复制按钮行：原文左、译文右
        copy_row = QHBoxLayout()
        self.copy_src_btn = QPushButton("复制原文")
        self.copy_src_btn.setToolTip("复制左侧原文到剪贴板（框内也可直接 Ctrl+C）")
        self.copy_tgt_btn = QPushButton("复制译文")
        self.copy_tgt_btn.setToolTip("复制右侧译文到剪贴板（框内也可直接 Ctrl+C）")
        copy_row.addWidget(self.copy_src_btn)
        copy_row.addStretch(1)
        copy_row.addWidget(self.copy_tgt_btn)
        root.addLayout(copy_row)

        # 底部：OCR 按钮 + 状态提示 + 进度条 + 翻译、清空按钮
        bottom_bar = QHBoxLayout()
        self.screenshot_btn = QPushButton("截图翻译")
        self.import_btn = QPushButton("导入图片翻译")
        self.status_label = QLabel("就绪")
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumWidth(140)
        self.progress_bar.setMaximumWidth(220)
        self.progress_bar.setTextVisible(False)
        self.translate_btn = QPushButton("翻译")
        self.clear_btn = QPushButton("清空")
        bottom_bar.addWidget(self.screenshot_btn)
        bottom_bar.addWidget(self.import_btn)
        bottom_bar.addWidget(self.status_label, stretch=1)
        bottom_bar.addWidget(self.progress_bar)
        bottom_bar.addWidget(self.translate_btn)
        bottom_bar.addWidget(self.clear_btn)
        root.addLayout(bottom_bar)

        self.screenshot_btn.clicked.connect(self.on_screenshot_translate)
        self.import_btn.clicked.connect(self.on_import_image)
        self.translate_btn.clicked.connect(self.on_translate)
        self.clear_btn.clicked.connect(self.on_clear)
        self.swap_btn.clicked.connect(self.on_swap)
        self.copy_src_btn.clicked.connect(self.on_copy_source)
        self.copy_tgt_btn.clicked.connect(self.on_copy_target)
        return page

    # ---------- 标签页二：文档翻译 ----------

    def _build_doc_tab(self):
        page = QWidget()
        root = QVBoxLayout(page)

        self.drop_area = DropArea()
        self.drop_area.file_dropped.connect(self.on_doc_file_chosen)
        self.drop_area.clicked.connect(self.on_doc_browse)
        root.addWidget(self.drop_area)

        self.doc_file_label = QLabel("未选择文件")
        self.doc_file_label.setWordWrap(True)
        root.addWidget(self.doc_file_label)

        row = QHBoxLayout()
        row.addWidget(QLabel("目标语言："))
        self.doc_target_lang = QComboBox()
        for name, code in TARGET_LANGS:
            self.doc_target_lang.addItem(name, code)
        self.doc_translate_btn = QPushButton("开始翻译")
        self.doc_translate_btn.setEnabled(False)
        self.doc_export_btn = QPushButton("导出译文")
        self.doc_export_btn.setEnabled(False)
        row.addWidget(self.doc_target_lang)
        row.addWidget(self.doc_translate_btn)
        row.addWidget(self.doc_export_btn)
        row.addStretch(1)
        root.addLayout(row)

        self.doc_progress = QProgressBar()
        self.doc_progress.setVisible(False)
        root.addWidget(self.doc_progress)

        self.doc_status = QLabel("文档全程本地处理，不会上传任何内容")
        root.addWidget(self.doc_status)

        self.doc_preview = QPlainTextEdit()
        self.doc_preview.setReadOnly(True)
        self.doc_preview.setPlaceholderText("导入文档后，此处预览提取到的原文…")
        root.addWidget(self.doc_preview, stretch=1)

        self.doc_translate_btn.clicked.connect(self.on_doc_translate)
        self.doc_export_btn.clicked.connect(self.on_doc_export)
        return page

    # ---------- 标签页三：翻译历史 ----------

    @staticmethod
    def _ellipsize(text, n=60):
        text = text.replace("\n", " ")
        return text if len(text) <= n else text[:n] + "…"

    def _build_history_tab(self):
        page = QWidget()
        root = QVBoxLayout(page)

        bar = QHBoxLayout()
        self.hist_search = QLineEdit()
        self.hist_search.setPlaceholderText("搜索历史记录（按原文或译文包含的字词过滤）…")
        self.hist_search.setClearButtonEnabled(True)
        self.hist_search.textChanged.connect(self.refresh_history)
        self.hist_reuse_btn = QPushButton("复用到原文框")
        self.hist_delete_btn = QPushButton("删除选中")
        self.hist_clear_btn = QPushButton("清空全部")
        self.hist_reuse_btn.clicked.connect(self.on_history_reuse)
        self.hist_delete_btn.clicked.connect(self.on_history_delete)
        self.hist_clear_btn.clicked.connect(self.on_history_clear)
        bar.addWidget(QLabel("搜索："))
        bar.addWidget(self.hist_search, stretch=1)
        bar.addWidget(self.hist_reuse_btn)
        bar.addWidget(self.hist_delete_btn)
        bar.addWidget(self.hist_clear_btn)
        root.addLayout(bar)

        self.hist_tree = QTreeWidget()
        self.hist_tree.setColumnCount(4)
        self.hist_tree.setHeaderLabels(["时间", "方向", "原文", "译文"])
        hist_header = self.hist_tree.header()
        hist_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hist_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.hist_tree.setColumnWidth(0, 150)
        self.hist_tree.setColumnWidth(1, 160)
        self.hist_tree.setRootIsDecorated(False)
        self.hist_tree.setAllColumnsShowFocus(True)
        self.hist_tree.itemSelectionChanged.connect(self._on_history_select)
        self.hist_tree.itemDoubleClicked.connect(lambda *_: self.on_history_reuse())
        root.addWidget(self.hist_tree, stretch=2)

        self.hist_detail = QPlainTextEdit()
        self.hist_detail.setReadOnly(True)
        self.hist_detail.setPlaceholderText("点击上方记录查看完整内容；双击记录可复用到原文框")
        root.addWidget(self.hist_detail, stretch=1)

        self.hist_status = QLabel("历史记录保存在本机（history.db），不上传任何内容")
        root.addWidget(self.hist_status)

        self.refresh_history()
        return page

    def refresh_history(self):
        search = self.hist_search.text().strip() or None
        entries = history_db.list_entries(search)
        self.hist_tree.clear()
        for e in entries:
            f_name = (translator.CODE_NAMES.get(e["f_code"], e["f_code"] or "自动检测")
                      if e["f_code"] else "自动检测")
            t_name = translator.CODE_NAMES.get(e["t_code"], e["t_code"])
            item = QTreeWidgetItem([
                e["ts"], f"{f_name} → {t_name}",
                self._ellipsize(e["source"]), self._ellipsize(e["translated"]),
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, e["id"])
            item.setToolTip(2, e["source"])
            item.setToolTip(3, e["translated"])
            self.hist_tree.addTopLevelItem(item)
        total = history_db.count()
        shown = f"共 {total} 条记录，当前显示 {len(entries)} 条"
        self.hist_status.setText(shown + "；记录保存在本机（history.db），不上传任何内容")
        self.hist_detail.clear()

    def _selected_history_id(self):
        items = self.hist_tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole)

    def _on_history_select(self):
        entry_id = self._selected_history_id()
        if entry_id is None:
            return
        e = history_db.get_entry(entry_id)
        if not e:
            return
        self.hist_detail.setPlainText(
            f"【原文】\n{e['source']}\n\n【译文】\n{e['translated']}"
        )

    def on_history_reuse(self):
        entry_id = self._selected_history_id()
        if entry_id is None:
            self.hist_status.setText("请先在列表中选择一条历史记录")
            return
        e = history_db.get_entry(entry_id)
        if not e:
            return
        self.tabs.setCurrentIndex(0)
        self.source_edit.setPlainText(e["source"])
        idx = self.target_lang.findData(e["t_code"])
        if idx >= 0:
            self.target_lang.setCurrentIndex(idx)
        if e["f_code"]:
            idx = self.source_lang.findData(e["f_code"])
            if idx >= 0:
                self.source_lang.setCurrentIndex(idx)
        self.status_label.setText("已复用历史记录，可点击「翻译」重新翻译")

    def on_history_delete(self):
        entry_id = self._selected_history_id()
        if entry_id is None:
            self.hist_status.setText("请先选择要删除的记录")
            return
        ret = QMessageBox.question(
            self, "删除历史", "确定删除选中的这条历史记录吗？"
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        history_db.delete_entry(entry_id)
        self.refresh_history()

    def on_history_clear(self):
        if history_db.count() == 0:
            self.hist_status.setText("暂无历史记录")
            return
        ret = QMessageBox.question(
            self, "清空历史",
            f"确定清空全部 {history_db.count()} 条历史记录吗？\n该操作不可恢复。",
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        history_db.clear_all()
        self.refresh_history()

    # ---------- 交互 ----------

    def on_translate(self):
        # 翻译进行中再次点击 = 取消
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.status_label.setText("正在取消…（等待当前分段完成，最长数秒）")
            return
        text = self.source_edit.toPlainText().strip()
        if not text:
            self.status_label.setText("请先输入要翻译的内容")
            return
        if len(text) > MAX_TEXT_CHARS:
            QMessageBox.warning(
                self, "文本翻译",
                f"长文本最多支持 {MAX_TEXT_CHARS} 字，当前约 {len(text)} 字，请分段翻译。",
            )
            return
        self.translate_btn.setText("取消翻译")
        self.status_label.setText("正在加载本地模型并翻译（首次使用需数秒）…")
        self._cur_source_text = text
        self.progress_bar.setRange(0, 0)   # 语言检测/模型加载阶段用忙碌指示
        self.progress_bar.setVisible(True)
        worker = TranslateWorker(
            text,
            self.source_lang.currentData(),
            self.target_lang.currentData(),
        )
        worker.progress.connect(self.on_translate_progress)
        worker.finished_ok.connect(self.on_translate_done)
        worker.failed.connect(self.on_translate_failed)
        worker.cancelled.connect(self.on_translate_cancelled)
        worker.models_missing.connect(self.on_models_missing)
        self.worker = worker
        worker.start()

    def on_translate_progress(self, done, total):
        if total > 1:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)
            pct = done * 100 // total
            self.status_label.setText(f"正在翻译（{done}/{total} 段，{pct}%，本地模型推理中）…")

    def _reset_translate_ui(self):
        """翻译结束/失败/取消后恢复按钮与进度条。"""
        self.translate_btn.setText("翻译")
        self.progress_bar.setVisible(False)

    def on_translate_cancelled(self):
        self._reset_translate_ui()
        self.status_label.setText("已取消翻译")

    def on_translate_done(self, result, detected):
        self._reset_translate_ui()
        self.target_edit.setPlainText(result)
        if self.source_lang.currentData() is None:
            self.status_label.setText(f"翻译完成（检测到源语言：{translator.CODE_NAMES[detected]}）")
        else:
            self.status_label.setText("翻译完成")
        try:
            history_db.add_entry(
                self._cur_source_text, result,
                detected, self.target_lang.currentData(),
            )
            self.refresh_history()
        except Exception:
            pass   # 历史记录失败不影响翻译本身

    def on_copy_source(self):
        text = self.source_edit.toPlainText()
        if not text.strip():
            self.status_label.setText("原文为空，没有可复制的内容")
            return
        QApplication.clipboard().setText(text)
        self.status_label.setText("已复制原文到剪贴板")

    def on_copy_target(self):
        text = self.target_edit.toPlainText()
        if not text.strip():
            self.status_label.setText("暂无译文，请先翻译")
            return
        QApplication.clipboard().setText(text)
        self.status_label.setText("已复制译文到剪贴板")

    # ---------- 全局快捷键 ----------

    def quick_translate(self):
        """快速翻译（全局，macOS Cmd+T / Windows Ctrl+T）。

        置前窗口；原文为空时取剪贴板内容，自动开始翻译。
        """
        self.tabs.setCurrentIndex(0)
        self._restore_window()
        if self.worker is not None and self.worker.isRunning():
            self.status_label.setText("翻译进行中…")
            return
        if not self.source_edit.toPlainText().strip():
            clip = QApplication.clipboard().text().strip()
            if clip:
                self.source_edit.setPlainText(clip[:MAX_TEXT_CHARS])
                self.status_label.setText("已填入剪贴板内容，正在翻译…")
        self.on_translate()

    def quick_screenshot(self):
        """截图翻译（全局，macOS Cmd+Shift+O / Windows Ctrl+Shift+O）。"""
        self.tabs.setCurrentIndex(0)
        self.on_screenshot_translate()

    def on_translate_failed(self, msg):
        self._reset_translate_ui()
        self.status_label.setText("翻译失败")
        QMessageBox.critical(self, "错误", msg)

    def on_models_missing(self, missing, size_mb):
        self._reset_translate_ui()
        self.status_label.setText("缺少语言模型")
        if self._ask_download(missing, size_mb):
            self._download_target = "text"
            self.start_download(missing)
        else:
            self.status_label.setText("已取消模型下载，未进行翻译")

    def _ask_download(self, missing, size_mb):
        """弹窗询问是否下载缺失的语言模型包。返回是否同意。"""
        f, t = missing[0][0], missing[-1][1]
        direction = f"{translator.CODE_NAMES[f]} → {translator.CODE_NAMES[t]}"
        size_text = f"（共约 {size_mb} MB，仅需下载一次）" if size_mb else "（仅需下载一次）"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("需要下载语言模型")
        box.setText(f"首次使用「{direction}」翻译，需要下载对应的离线语言模型包{size_text}。\n"
                    "下载完成后翻译即可完全离线使用，您的文本不会被发送到网络。")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("立即下载")
        box.button(QMessageBox.StandardButton.No).setText("取消")
        return box.exec() == QMessageBox.StandardButton.Yes

    def start_download(self, missing):
        n = len(missing)
        self.progress_dlg = QProgressDialog("正在下载语言模型…", "取消", 0, 100, self)
        self.progress_dlg.setWindowTitle("下载语言模型")
        self.progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dlg.setMinimumDuration(0)

        def on_progress(done, total):
            # 单个模型包进度折算到总体进度（按包数均分）
            percent = int(min(done / total, 1.0) * 100 / n)
            self.progress_dlg.setValue(min(percent, 99))

        worker = DownloadWorker(missing)
        worker.status.connect(lambda s: self.status_label.setText(s))
        worker.progress.connect(on_progress)
        worker.finished_ok.connect(self.on_download_done)
        worker.failed.connect(self.on_download_failed)
        self.progress_dlg.canceled.connect(worker.cancel)
        self.download_worker = worker
        worker.start()

    def on_download_done(self):
        self.progress_dlg.setValue(100)
        self.progress_dlg.close()
        if self._download_target == "doc":
            self.doc_status.setText("模型安装完成，继续翻译文档…")
            self.on_doc_translate()
        else:
            self.status_label.setText("模型安装完成，开始翻译…")
            self.on_translate()

    def on_download_failed(self, msg):
        self.progress_dlg.close()
        self.status_label.setText("模型下载失败")
        QMessageBox.critical(self, "错误", msg)

    def on_clear(self):
        self.source_edit.clear()
        self.target_edit.clear()
        self.status_label.setText("就绪")

    def on_swap(self):
        src_code = self.source_lang.currentData()
        tgt_index = self.target_lang.currentIndex()
        if src_code is None:
            # 自动检测无法直接作为目标语言，交换后目标语言设为英语
            self.source_lang.setCurrentIndex(tgt_index + 1)
            self.target_lang.setCurrentIndex(1)
        else:
            self.source_lang.setCurrentIndex(tgt_index + 1)
            self.target_lang.setCurrentIndex(
                next(i for i, (_, c) in enumerate(SOURCE_LANGS) if c == src_code) - 1
            )

    # ---------- 文档翻译 ----------

    DOC_FILE_FILTER = "支持的文档 (*.txt *.docx *.pdf *.xlsx);;所有文件 (*)"

    def on_doc_browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文档", "", self.DOC_FILE_FILTER)
        if path:
            self.on_doc_file_chosen(path)

    def on_doc_file_chosen(self, path):
        if self.doc_extract_worker is not None and self.doc_extract_worker.isRunning():
            QMessageBox.warning(self, "文档翻译", "当前正在读取文档，请稍候。")
            return
        if self.doc_translate_worker is not None and self.doc_translate_worker.isRunning():
            QMessageBox.warning(self, "文档翻译", "当前文档正在翻译，请等待完成后再导入新文件。")
            return
        ext = Path(path).suffix.lower()
        if ext not in doc_engine.SUPPORTED_EXTS:
            QMessageBox.warning(
                self, "文档翻译",
                "暂不支持该格式，请选择 .txt / .docx / .pdf / .xlsx 文件。",
            )
            return
        self._doc_path = path
        self.doc = None
        self.doc_translate_btn.setEnabled(False)
        self.doc_export_btn.setEnabled(False)
        self.doc_file_label.setText(path)
        self.doc_preview.setPlainText("")
        self.doc_status.setText("正在读取文档…")
        self.doc_progress.setRange(0, 0)   # 窗口指示器模式（读取阶段步数未知）
        self.doc_progress.setVisible(True)
        worker = DocExtractWorker(path)
        worker.progress.connect(self._on_doc_extract_progress)
        worker.finished_ok.connect(self._on_doc_extract_done)
        worker.failed.connect(self._on_doc_failed)
        self.doc_extract_worker = worker
        worker.start()

    def _on_doc_extract_progress(self, done, total):
        if total:
            self.doc_progress.setRange(0, total)
            self.doc_progress.setValue(done)
            self.doc_status.setText(f"正在读取文档（第 {done}/{total} 页，扫描页本地 OCR 中）…")

    def _on_doc_extract_done(self, doc):
        self.doc = doc
        self.doc_progress.setVisible(False)
        n = len(doc.units)
        self.doc_file_label.setText(f"{self._doc_path}（共 {n} 段可翻译内容）")
        self.doc_preview.setPlainText("\n".join(doc.units)[:3000])
        self.doc_translate_btn.setEnabled(True)
        self.doc_status.setText("读取完成，请选择目标语言后开始翻译")

    def on_doc_translate(self):
        if self.doc is None:
            return
        t_code = self.doc_target_lang.currentData()
        self.doc_translate_btn.setEnabled(False)
        self.doc_export_btn.setEnabled(False)
        self.doc_status.setText("正在准备翻译（加载本地模型并检测源语言）…")
        self.doc_progress.setRange(0, 0)
        self.doc_progress.setVisible(True)
        worker = DocTranslateWorker(self.doc, t_code)
        worker.progress.connect(self._on_doc_translate_progress)
        worker.finished_ok.connect(self._on_doc_translate_done)
        worker.failed.connect(self._on_doc_failed)
        worker.models_missing.connect(self._on_doc_models_missing)
        self.doc_translate_worker = worker
        worker.start()

    def _on_doc_translate_progress(self, done, total):
        if total:
            self.doc_progress.setRange(0, total)
            self.doc_progress.setValue(done)
            self.doc_status.setText(f"正在翻译（{done}/{total} 段，本地模型推理中）…")

    def _on_doc_translate_done(self, doc):
        self.doc_progress.setVisible(False)
        self.doc_export_btn.setEnabled(True)
        self.doc_translate_btn.setEnabled(True)
        src_name = translator.CODE_NAMES.get(doc.source_lang, doc.source_lang)
        self.doc_status.setText(f"翻译完成（源语言：{src_name}），可点击「导出译文」保存")
        self.doc_preview.setPlainText("\n".join(doc.translations)[:3000])

    def _on_doc_models_missing(self, missing, size_mb):
        self.doc_progress.setVisible(False)
        self.doc_status.setText("缺少语言模型")
        if self._ask_download(missing, size_mb):
            self._download_target = "doc"
            self.start_download(missing)
        else:
            self.doc_translate_btn.setEnabled(True)
            self.doc_status.setText("已取消模型下载，未进行翻译")

    def _on_doc_failed(self, msg):
        self.doc_progress.setVisible(False)
        self.doc_translate_btn.setEnabled(self.doc is not None)
        self.doc_status.setText("处理失败")
        QMessageBox.warning(self, "文档翻译", msg)

    def on_doc_export(self):
        doc = self.doc
        if doc is None or doc.translations is None:
            return
        filters = {
            "txt": "文本文件 (*.txt)",
            "docx": "Word 文档 (*.docx)",
            "pdf": "PDF 文档 (*.pdf)",
            "xlsx": "Excel 表格 (*.xlsx)",
        }
        stem = Path(self._doc_path).stem if self._doc_path else "译文"
        default = str(Path(self._doc_path).with_name(f"{stem}_译文")) if self._doc_path else "译文"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出译文", default, filters[doc.fmt]
        )
        if not path:
            return
        try:
            doc_engine.export(doc, path)
            self.doc_status.setText(f"已导出：{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出失败：{e}")

    # ---------- OCR ----------

    def on_screenshot_translate(self):
        """截图翻译：权限检查 → 隐藏窗口 → 全屏遮罩框选 → 本地识别 → 自动翻译。

        想截取其他应用的菜单栏时，先 Cmd+Tab 切到目标应用再按全局快捷键，
        此时菜单栏显示的是目标应用（本应用窗口隐藏不影响全局热键）。
        """
        if self.ocr_worker is not None and self.ocr_worker.isRunning():
            self.status_label.setText("正在识别，请稍候…")
            return
        if not _ensure_screen_capture_permission(self):
            self.status_label.setText("未授予屏幕录制权限，无法截图翻译")
            return
        self.status_label.setText("请框选要翻译的屏幕区域（Esc 或右键取消）")
        self.hide()
        # macOS 窗口隐藏带动画且耗时更长，等窗口完全消失后再弹遮罩，
        # 避免遮罩被本窗口的残留画面遮挡/拍到本窗口
        delay = 450 if sys.platform == "darwin" else 300
        QTimer.singleShot(delay, self._show_selector)

    def _show_selector(self):
        self.selector = RegionSelector()
        self.selector.selected.connect(self._on_region_selected)
        self.selector.cancelled.connect(self._on_selector_cancelled)
        self.selector.show()
        # macOS 上新建 Tool 窗口不自动成为 key window，
        # 必须显式置前并激活，否则键盘 Esc 取消与鼠标框选可能无响应
        self.selector.raise_()
        self.selector.activateWindow()

    def _on_selector_cancelled(self):
        self._restore_window()
        self.status_label.setText("已取消截图")

    def _on_region_selected(self, rect):
        # 等遮罩从屏幕上消失后再抓屏，否则会把遮罩拍进去
        QTimer.singleShot(150, lambda: self._capture_region(rect))

    def _capture_region(self, rect):
        try:
            if sys.platform == "darwin":
                # macOS：grabWindow(0) 返回所在屏幕的内容（Retina 下为 2x 像素），
                # 先定位选区所在屏幕，再相对该屏换算，多显示器时才不会错位
                screen = (QApplication.screenAt(rect.center())
                          or QApplication.primaryScreen())
                base = screen.geometry()
            else:
                # Windows：grabWindow(0) 抓取整个虚拟桌面
                screen = QApplication.primaryScreen()
                base = QRect()
                for s in QApplication.screens():
                    base = base.united(s.virtualGeometry())
            pm = screen.grabWindow(0)
            sx = pm.width() / base.width()
            sy = pm.height() / base.height()
            crop_rect = QRect(
                round((rect.x() - base.x()) * sx),
                round((rect.y() - base.y()) * sy),
                max(1, round(rect.width() * sx)),
                max(1, round(rect.height() * sy)),
            )
            crop = pm.copy(crop_rect)
            fd, path = tempfile.mkstemp(suffix=".png", prefix="doksuri_ocr_")
            os.close(fd)
            if not crop.save(path, "PNG"):
                raise RuntimeError("无法保存截图")
            self._ocr_temp_file = path
            self._start_ocr(path)
        except Exception as e:
            self._restore_window()
            self.status_label.setText("截图失败")
            QMessageBox.warning(self, "截图翻译", f"截图失败：{e}")

    def on_import_image(self):
        """导入图片翻译：选择本地图片，本地识别后自动翻译。

        Qt 在 macOS 默认使用原生 NSOpenPanel（无需额外设置），
        过滤器会自动映射为原生对话框的格式选择；图片全程本地处理。
        """
        if self.ocr_worker is not None and self.ocr_worker.isRunning():
            self.status_label.setText("正在识别，请稍候…")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片 (*.jpg *.jpeg *.png)"
        )
        if not path:
            return
        self._start_ocr(path)

    def _start_ocr(self, image_path):
        self.status_label.setText("正在本地识别图片文字（首次使用需加载模型）…")
        self._set_ocr_buttons_enabled(False)
        worker = OcrWorker(image_path)
        worker.done.connect(self._on_ocr_done)
        worker.failed.connect(self._on_ocr_failed)
        self.ocr_worker = worker
        worker.start()

    def _on_ocr_done(self, text):
        self._cleanup_ocr_temp()
        self._set_ocr_buttons_enabled(True)
        self._restore_window()
        text = text.strip()
        if not text:
            self.status_label.setText("未识别到文字，请重试或调整图片/截图区域")
            return
        self.source_edit.setPlainText(text)
        self.on_translate()

    def _on_ocr_failed(self, msg):
        self._cleanup_ocr_temp()
        self._set_ocr_buttons_enabled(True)
        self._restore_window()
        self.status_label.setText("识别失败")
        QMessageBox.warning(self, "截图翻译", msg)

    def _set_ocr_buttons_enabled(self, enabled):
        self.screenshot_btn.setEnabled(enabled)
        self.import_btn.setEnabled(enabled)

    def _restore_window(self):
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def _cleanup_ocr_temp(self):
        if self._ocr_temp_file:
            try:
                os.remove(self._ocr_temp_file)
            except OSError:
                pass
            self._ocr_temp_file = None

    def closeEvent(self, event):
        if getattr(self, "hotkeys", None) is not None:
            try:
                app = QApplication.instance()
                if app is not None:
                    app.removeNativeEventFilter(self.hotkeys)
                self.hotkeys.unregister_all()
            except Exception:
                pass
        for w in (self.worker, self.download_worker, self.ocr_worker,
                  self.doc_extract_worker, self.doc_translate_worker):
            if w is not None and w.isRunning():
                w.wait(2000)
        event.accept()


if __name__ == "__main__":
    setup_high_dpi()   # 须在 QApplication 创建前
    set_windows_app_identity()
    app = QApplication(sys.argv)
    # 软件名称保持 DOKSURI（各平台应用元数据；macOS 程序坞/菜单栏显示）
    app.setApplicationName("DOKSURI")
    if sys.platform == "darwin":
        app.setApplicationDisplayName("DOKSURI")
    apply_app_font(app)
    app.setWindowIcon(app_icon())
    window = MainWindow()   # 窗口标题保持 DOKSURI
    window.show()
    sys.exit(app.exec())

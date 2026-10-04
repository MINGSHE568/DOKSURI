"""DOKSURI 本地 OCR 引擎 - 基于 PaddleOCR，全程离线推理，图片不发送网络。

语种支持：en（英语）、ch（中文+英文）、japan（日语）、korean（韩语）。
识别时全部可用管线自动尝试，按 Σ(置信度×字符数) 择优，无需手动选择语种。
模型在首次使用时安装到本地缓存目录（macOS 为
~/Library/Application Support/DOKSURI/models/paddleocr），之后完全离线加载。
macOS（Apple Silicon / Intel）固定 CPU 推理。
"""
import os
import sys
import tempfile
import threading

# 降低 paddle/GLOG 日志噪音；KMP 兼容开关（与 torch 共存时避免 OpenMP 重复初始化）
os.environ.setdefault("GLOG_minloglevel", "3")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

OCR_LANGS = ("en", "ch", "japan", "korean")

_instance_lock = threading.Lock()   # 保护管线实例创建
_infer_lock = threading.Lock()      # paddle predictor 非线程安全，推理全局串行
_instances = {}


def _get_pipeline(lang):
    """惰性加载某语言的 OCR 管线（仅读本地模型，缺失时由调用方提示）。"""
    with _instance_lock:
        if lang not in _instances:
            from paddleocr import PaddleOCR
            from model_manager import ocr_base_dir
            if sys.platform == "darwin":
                # M 系列/Intel Mac：Paddle 在 macOS 仅提供 CPU 后端，显式固定 CPU 推理
                import paddle
                paddle.set_device("cpu")
            # 应用自定义模型缓存目录（model_settings.json），须在实例化前写入
            from paddleocr import paddleocr as _pp
            _pp.BASE_DIR = str(ocr_base_dir())
            # rec_image_shape 放宽宽度，避免 CJK 长句被压缩导致误识别；
            # 关闭角度分类器：屏幕/文档文字均为正向，cls 反而会误判旋转导致乱识别
            _instances[lang] = PaddleOCR(
                lang=lang,
                use_angle_cls=False,
                show_log=False,
                rec_image_shape="3,48,640",
            )
        return _instances[lang]


def _run_pipeline(lang, image):
    """跑单条管线，返回 [(text, confidence), ...]。"""
    result = _get_pipeline(lang).ocr(image, cls=False)
    lines = []
    for page in result or []:
        if not page:
            continue
        for item in page:
            try:
                text, conf = item[1]
            except (TypeError, ValueError, IndexError):
                continue
            lines.append((str(text), float(conf)))
    return lines


def _upscale_small_image(image):
    """小图智能放大：短边过小的截图/图片放大 2 倍后再识别，显著提升小字号
    密排文本的检测与识别效果（检测模型内部会把长边压到 960，先放大可保证
    实际参与推理的有效分辨率更高）。返回新图片路径；无需放大返回原路径。"""
    try:
        from PIL import Image
        img = Image.open(image)
        w, h = img.size
        if max(w, h) >= 1000:
            return image
        fd, upscaled = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        img.convert("RGB").resize((w * 2, h * 2), Image.LANCZOS).save(upscaled)
        return upscaled
    except Exception:
        return image


def _merge_line_breaks(lines):
    """合并英文行尾连字符断词（如 govern- / ment → government），
    避免断词连着换行符一起进入翻译导致译文质量下降。"""
    merged = []
    for ln in lines:
        if merged and merged[-1].endswith("-") and ln[:1].isascii() and ln[:1].islower():
            merged[-1] = merged[-1][:-1] + ln
        else:
            merged.append(ln)
    return merged


def recognize(image, langs=OCR_LANGS):
    """识别图片内文字：各语言管线全部尝试，按 Σ(置信度×字符数) 择优，换行拼接。

    字符数加权可避免「只识别出少量字符但置信度高」的结果胜出。
    小图先放大 2 倍再识别（见 _upscale_small_image）。
    image: 图片文件路径（str）。
    返回识别文本（可能为空字符串）。
    """
    upscaled = _upscale_small_image(image)
    try:
        with _infer_lock:
            best_lines, best_score = None, -1.0
            for lang in langs:
                try:
                    lines = _run_pipeline(lang, upscaled)
                except Exception:
                    continue
                if not lines:
                    continue
                score = sum(conf * len(t) for t, conf in lines)
                if score > best_score:
                    best_score, best_lines = score, lines
    finally:
        if upscaled != image:
            try:
                os.remove(upscaled)
            except OSError:
                pass
    if not best_lines:
        return ""
    texts = [t.strip() for t, _ in best_lines if t.strip()]
    return "\n".join(_merge_line_breaks(texts))


def preheat(langs=OCR_LANGS):
    """预加载全部管线（首次联网时装模型用，也用于离线自检）。"""
    for lang in langs:
        _get_pipeline(lang)


def download_model(lang):
    """确保某语言 OCR 管线可用；缺失的检测/识别模型自动从官方源下载到本地缓存。

    仅下载模型文件，不发送任何用户数据。需要网络时由调用方保证。
    """
    _get_pipeline(lang)

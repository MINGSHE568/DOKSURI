"""DOKSURI 文档翻译引擎 - 纯本地提取与导出（txt / docx / pdf / xlsx）

提取、翻译、导出全部在本地完成，不向网络发送任何文档内容。
扫描版 PDF 页面（无可提取文本）自动回落到本地 PaddleOCR 识别（ocr_engine）。

统一中间结构：units 为待翻译文本列表；导出时按原格式把译文写回，
尽量保留原文档的基础格式（docx 段落/标题/表格与 run 格式、
xlsx 单元格位置、PDF 页面尺寸等）。
"""
import os
import tempfile
from pathlib import Path

SUPPORTED_EXTS = (".txt", ".docx", ".pdf", ".xlsx")

# 判定 PDF 页面为扫描页的最少可提取字符数（低于该值则走本地 OCR）
PDF_MIN_TEXT_CHARS = 16


class UnsupportedFormatError(Exception):
    pass


class DocDocument:
    """统一中间结构。

    units        待翻译文本列表（已过滤空白段）
    ctx          格式相关上下文对象（导出时使用）
    translations 与 units 对齐的译文列表
    source_lang  翻译时检测到的源语言码
    """

    def __init__(self, fmt, units, ctx):
        self.fmt = fmt
        self.units = units
        self.ctx = ctx
        self.translations = None
        self.source_lang = None


# ---------------------------------------------------------------- 提取

def extract(path, progress_cb=None):
    """提取文档为统一中间结构。progress_cb(done, total) 用于 PDF 分页进度。"""
    ext = Path(path).suffix.lower()
    if ext == ".txt":
        return _extract_txt(path)
    if ext == ".docx":
        return _extract_docx(path)
    if ext == ".pdf":
        return _extract_pdf(path, progress_cb)
    if ext == ".xlsx":
        return _extract_xlsx(path)
    raise UnsupportedFormatError(f"暂不支持的格式：{ext}（支持 {' '.join(SUPPORTED_EXTS)}）")


def _extract_txt(path):
    raw = Path(path).read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    units = [ln for ln in lines if ln.strip()]
    ctx = {"lines": lines, "trailing_nl": text.endswith(("\n", "\r"))}
    return DocDocument("txt", units, ctx)


def _iter_docx_paras(doc):
    """按文档顺序遍历正文段落（含表格单元格内段落，合并单元格会重复出现）。"""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            for row in table.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        yield p


def _extract_docx(path):
    from docx import Document as DocxDocument

    d = DocxDocument(path)
    paras = [p for p in _iter_docx_paras(d) if p.text.strip()]
    units = [p.text for p in paras]
    return DocDocument("docx", units, {"doc": d})


def _extract_pdf(path, progress_cb=None):
    import fitz

    pdf = fitz.open(path)
    pages = []   # [(页索引, 是否OCR), ...] 与 units 对齐
    units = []
    total = pdf.page_count
    for i, page in enumerate(pdf):
        text = ""
        try:
            text = page.get_text().strip()
        except Exception:
            text = ""
        if len(text) >= PDF_MIN_TEXT_CHARS:
            pages.append((i, False))
            units.append(text)
        else:
            # 扫描页：渲染为图片后本地 OCR 识别
            pm = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
            fd, png = tempfile.mkstemp(suffix=".png", prefix="doksuri_pdf_")
            os.close(fd)
            try:
                pm.save(png)
                import ocr_engine
                ocr_text = ocr_engine.recognize(png)
            finally:
                try:
                    os.remove(png)
                except OSError:
                    pass
            pages.append((i, True))
            units.append(ocr_text.strip())
        if progress_cb:
            progress_cb(i + 1, total)
    if not units or not any(u.strip() for u in units):
        pdf.close()
        raise ValueError("未在 PDF 中找到可翻译的文本（包括 OCR 识别结果）。")
    return DocDocument("pdf", units, {"pdf": pdf, "pages": pages})


def _extract_xlsx(path):
    from openpyxl import load_workbook

    wb = load_workbook(path)
    refs = []    # [(工作表名, 行, 列), ...] 与 units 对齐
    units = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                # 只翻译文本单元格；公式（=开头）与数值保持原样
                if isinstance(v, str) and v.strip() and not v.startswith("="):
                    refs.append((ws.title, cell.row, cell.column))
                    units.append(v)
    return DocDocument("xlsx", units, {"wb": wb, "refs": refs})


# ---------------------------------------------------------------- 翻译

def translate_all(doc, f_code, t_code, progress_cb=None):
    """逐段调用本地 Argos 模型翻译，结果写入 doc.translations。

    单段翻译失败时回退保留原文，保证整体任务不中断。
    """
    from translator import translate

    doc.source_lang = f_code
    total = len(doc.units)
    if f_code == t_code:
        doc.translations = list(doc.units)
        if progress_cb:
            progress_cb(total, total)
        return doc
    doc.translations = []
    for i, unit in enumerate(doc.units):
        try:
            translated = translate(unit, f_code, t_code)
        except Exception:
            translated = unit
        doc.translations.append(translated)
        if progress_cb:
            progress_cb(i + 1, total)
    return doc


# ---------------------------------------------------------------- 导出

def export(doc, out_path):
    """把译文按原格式导出。"""
    if doc.translations is None:
        raise RuntimeError("文档尚未翻译，无法导出")
    if doc.fmt == "txt":
        _export_txt(doc, out_path)
    elif doc.fmt == "docx":
        _export_docx(doc, out_path)
    elif doc.fmt == "pdf":
        _export_pdf(doc, out_path)
    elif doc.fmt == "xlsx":
        _export_xlsx(doc, out_path)
    else:
        raise UnsupportedFormatError(doc.fmt)


def _export_txt(doc, out_path):
    lines = list(doc.ctx["lines"])
    it = iter(doc.translations)
    for i, ln in enumerate(lines):
        if ln.strip():
            lines[i] = next(it)
    text = "\n".join(lines)
    if doc.ctx.get("trailing_nl") and text:
        text += "\n"
    Path(out_path).write_text(text, encoding="utf-8-sig")


def _set_para_text(p, text):
    """把译文写入段落：保留第一个非空 run 的字符格式，清空其余 run。"""
    target = None
    for r in p.runs:
        if target is None and r.text.strip():
            target = r
        else:
            r.text = ""
    if target is not None:
        target.text = text
    elif p.runs:
        p.runs[0].text = text
    else:
        p.add_run(text)


def _export_docx(doc, out_path):
    d = doc.ctx["doc"]
    paras = [p for p in _iter_docx_paras(d) if p.text.strip()]
    for p, text in zip(paras, doc.translations):
        _set_para_text(p, text)
    d.save(out_path)


def _cjk_font():
    """内置通用字体（Droid Sans Fallback），覆盖中日韩/西里尔/拉丁字符。"""
    import fitz

    try:
        return fitz.Font("cjk")
    except Exception:
        return fitz.Font(ordering=0)


def _tokenize(text):
    """断行用分词：CJK 逐字拆分，其余（拉丁/西里尔等）按连续串整词保留。"""
    segments = []
    buf = ""
    prev_cjk = None
    for ch in text:
        is_cjk = ord(ch) > 0x2E80
        if prev_cjk is None or is_cjk == prev_cjk:
            buf += ch
        else:
            segments.append(buf)
            buf = ch
        prev_cjk = is_cjk
    if buf:
        segments.append(buf)
    tokens = []
    for seg in segments:
        if all(ord(c) > 0x2E80 for c in seg):
            tokens.extend(seg)
        else:
            tokens.append(seg)
    return tokens


def _wrap_text(text, font, fontsize, max_width):
    """按字体实测宽度贪心断行（保留显式换行）。"""
    out = []
    for raw in text.splitlines() or [""]:
        if not raw.strip():
            out.append("")
            continue
        cur = ""
        for token in _tokenize(raw):
            trial = cur + token
            if not cur.strip() or font.text_length(trial, fontsize) <= max_width:
                cur = trial
            else:
                out.append(cur.rstrip())
                cur = token.lstrip()
        out.append(cur.rstrip())
    return out


def _export_pdf(doc, out_path):
    import fitz

    src = doc.ctx["pdf"]
    out = fitz.open()
    font = _cjk_font()
    fontsize = 11.0
    leading = fontsize * 1.5
    margin = 56.0

    for (page_idx, _ocr), text in zip(doc.ctx["pages"], doc.translations):
        spage = src[page_idx]
        page = out.new_page(width=spage.rect.width, height=spage.rect.height)
        if not text.strip():
            continue
        rect = fitz.Rect(margin, margin, page.rect.width - margin, page.rect.height - margin)
        tw = fitz.TextWriter(page.rect)
        y = rect.y0
        for line in _wrap_text(text, font, fontsize, rect.width):
            if y + leading > rect.y1 + 0.5:
                # 单页写不下则另起一页续写，避免截断内容
                page = out.new_page(width=spage.rect.width, height=spage.rect.height)
                tw = fitz.TextWriter(page.rect)
                y = margin
            tw.append(fitz.Point(rect.x0, y + fontsize), line, font=font, fontsize=fontsize)
            y += leading
        tw.write_text(page)

    out.save(out_path, garbage=3, deflate=True)
    out.close()


def _export_xlsx(doc, out_path):
    wb = doc.ctx["wb"]
    for (title, row, col), text in zip(doc.ctx["refs"], doc.translations):
        wb[title].cell(row=row, column=col).value = text
    wb.save(out_path)

"""DOKSURI 本地离线翻译引擎（基于 Argos Translate）

所有翻译推理均在本地完成；仅首次下载语言模型包时需要联网，
且只下载模型文件，不向网络发送任何用户文本。
"""
import json
import os
import tempfile
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INDEX_PATH = BASE_DIR / "models_index.json"

# 支持的语种（Argos 语言码），顺序即界面显示顺序
SUPPORTED_CODES = ["zh", "en", "ja", "ko", "fr", "de", "ru", "es"]

CODE_NAMES = {
    "zh": "中文（简体）",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "ru": "俄语",
    "es": "西班牙语",
}


class ModelMissingError(Exception):
    """翻译所需的语言模型未安装。missing 为缺失的 (from_code, to_code) 列表。"""

    def __init__(self, missing):
        self.missing = missing
        super().__init__(str(missing))


class UnsupportedLanguageError(Exception):
    pass


def get_installed_languages():
    """获取（并刷新）已安装语言列表。首次调用需加载本地库，耗时数秒。"""
    from argostranslate import translate as _at
    return _at.get_installed_languages()


def _lang_by_code(code):
    for lang in get_installed_languages():
        if lang.code == code:
            return lang
    return None


def _load_index():
    with open(INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


# minisbd 断句模型的语言码映射（与 argostranslate 内部逻辑一致）
_MINISBD_LANG_MAP = {
    "zt": "zh-hant",
    "zh": "zh-hans",
    "pb": "pt",
    "az": "tr",
    "bn": "hi",
    "eo": "en",
    "ms": "en",
    "tl": "en",
}

_patched = False


def _apply_offline_patches():
    """离线补丁：语言包内已自带 stanza 断句模型与 resources.json，
    屏蔽 stanza.Pipeline 初始化时的联网检查，确保推理阶段零网络访问。"""
    global _patched
    if _patched:
        return
    try:
        from stanza.pipeline.core import DownloadMethod
        import argostranslate.sbd as _argo_sbd
        _orig_pipeline = _argo_sbd.stanza.Pipeline

        def _offline_pipeline(*args, **kwargs):
            kwargs.setdefault("download_method", DownloadMethod.NONE)
            return _orig_pipeline(*args, **kwargs)

        _argo_sbd.stanza.Pipeline = _offline_pipeline
    except Exception:
        pass

    # 兼容补丁：语言包内置的旧版断句模型缺少新版 stanza 的
    # feat_dropout 超参数与 lexicon 字段，加载时补默认值
    try:
        import torch
        from stanza.models.tokenization import trainer as _tok_trainer

        def _compat_load(self, filename):
            checkpoint = torch.load(filename, lambda storage, loc: storage, weights_only=True)
            self.args = checkpoint["config"]
            if self.args.get("use_mwt", None) is None:
                # 旧模型均带 mwt 层，沿用 stanza 自身的兼容逻辑
                self.args["use_mwt"] = True
            self.args.setdefault("feat_dropout", 0.0)
            self.model = _tok_trainer.Tokenizer(
                self.args,
                self.args["vocab_size"],
                self.args["emb_dim"],
                self.args["hidden_dim"],
                dropout=self.args["dropout"],
                feat_dropout=self.args["feat_dropout"],
            )
            self.model.load_state_dict(checkpoint["model"])
            self.vocab = _tok_trainer.Vocab.load_state_dict(checkpoint["vocab"])
            self.lexicon = checkpoint.get("lexicon")
            if self.lexicon is not None:
                self.lexicon = set(self.lexicon)
                self.dictionary = _tok_trainer.create_dictionary(self.lexicon)
            else:
                self.dictionary = None

        _tok_trainer.Trainer.load = _compat_load
    except Exception:
        pass

    # 兼容补丁：旧版断句模型使用 stanza 已移除的 all_caps 特征函数，
    # 且旧配置缺少 use_dictionary 字段；此处恢复该特征并做缺省容错
    try:
        import numpy as np
        from stanza.models.tokenization.data import TokenizationDataset, NUMERIC_RE

        def _compat_para_to_sentences(self, para):
            res = []
            funcs = []
            for feat_func in self.args["feat_funcs"]:
                if feat_func in ("end_of_para", "start_of_para"):
                    continue
                if feat_func == "space_before":
                    func = lambda x: 1 if x.startswith(" ") else 0
                elif feat_func == "capitalized":
                    func = lambda x: 1 if x[0].isupper() else 0
                elif feat_func == "numeric":
                    func = lambda x: 1 if (NUMERIC_RE.match(x) is not None) else 0
                elif feat_func == "all_caps":
                    func = lambda x: 1 if (len(x) > 1 and x.isupper()) else 0
                else:
                    raise ValueError(f'Feature function "{feat_func}" is undefined.')
                funcs.append(func)
            composite_func = lambda x: [f(x) for f in funcs]

            def process_sentence(sent_units, sent_labels, sent_feats):
                return (np.array([self.vocab.unit2id(y) for y in sent_units]),
                        np.array(sent_labels),
                        np.array(sent_feats),
                        list(sent_units))

            use_end_of_para = "end_of_para" in self.args["feat_funcs"]
            use_start_of_para = "start_of_para" in self.args["feat_funcs"]
            use_dictionary = self.args.get("use_dictionary", False)
            current_units, current_labels, current_feats = [], [], []
            for i, (unit, label) in enumerate(para):
                feats = composite_func(unit)
                if use_end_of_para:
                    feats.append(1 if i == len(para) - 1 else 0)
                if use_start_of_para:
                    feats.append(1 if i == 0 else 0)
                if use_dictionary:
                    feats = feats + self.extract_dict_feat(para, i)
                current_units.append(unit)
                current_labels.append(label)
                current_feats.append(feats)
                if not self.eval and (label == 2 or label == 4):
                    if len(current_units) <= self.args["max_seqlen"]:
                        res.append(process_sentence(current_units, current_labels, current_feats))
                    current_units.clear()
                    current_labels.clear()
                    current_feats.clear()
            if len(current_units) > 0:
                if self.eval or len(current_units) <= self.args["max_seqlen"]:
                    res.append(process_sentence(current_units, current_labels, current_feats))
            return res

        TokenizationDataset.para_to_sentences = _compat_para_to_sentences
    except Exception:
        pass
    _patched = True


def _fix_stanza_resources(pkg):
    """修正语言包内置的精简版 stanza resources.json：
    补充缺失的 packages 字段（stanza 1.10 的 add_mwt 需要它），
    并移除未随包打包的 mwt 模型引用，使断句模型可完全离线加载。"""
    res_path = Path(pkg.package_path) / "stanza" / "resources.json"
    if not res_path.exists():
        return
    try:
        data = json.loads(res_path.read_text(encoding="utf-8"))
    except Exception:
        return
    changed = False
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        if "packages" not in entry:
            entry["packages"] = {}
            changed = True
        # stanza 将裸 "tokenize" 解析为 "default" 包名，
        # 这里把它映射到包内实际自带的模型包
        default_tokenize = None
        dp = entry.get("default_processors")
        if isinstance(dp, dict):
            default_tokenize = dp.get("tokenize")
        if default_tokenize and "default" not in entry["packages"]:
            entry["packages"]["default"] = {"tokenize": default_tokenize}
            changed = True
        if "mwt" in entry:
            entry.pop("mwt")
            changed = True
    if changed:
        res_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _ensure_sbd_local(f_code):
    """确保源语言的断句模型已本地化：包内自带 stanza 模型即可离线使用；
    否则预取 minisbd 断句模型到本地缓存（仅下载模型文件，不涉及用户文本）。"""
    from argostranslate import package as _pkg, settings as _settings
    pkg = None
    for p in _pkg.get_installed_packages():
        if p.from_code == f_code:
            pkg = p
            break
    if pkg is None:
        return
    if (Path(pkg.package_path) / "stanza" / "resources.json").exists():
        _fix_stanza_resources(pkg)
        return
    import minisbd.models as _mm
    # 断句模型缓存跟随 DOKSURI 数据目录（跨平台；失败时回落 argos 默认位置）
    try:
        import model_manager
        _mm.cache_dir = str(model_manager.minisbd_dir())
    except Exception:
        _mm.cache_dir = str(_settings.data_dir / "minisbd")
    lang = _MINISBD_LANG_MAP.get(f_code, f_code)
    if lang not in _mm.list_models():
        lang = "en"
    fname = _mm.MODELS.get(lang)
    if fname and (Path(_mm.cache_dir) / fname).exists():
        return
    os.makedirs(_mm.cache_dir, exist_ok=True)
    last_error = None
    for base in (_mm.REPO_URL, "https://gh-proxy.com/" + _mm.REPO_URL):
        try:
            _mm.REPO_URL = base
            _mm.get_model_file(lang)
            return
        except Exception as e:
            last_error = e
    raise RuntimeError(f"断句模型下载失败（{lang}）：{last_error}")


def is_pair_installed(f, t):
    lang_f, lang_t = _lang_by_code(f), _lang_by_code(t)
    if lang_f is None or lang_t is None:
        return False
    try:
        return lang_f.get_translation(lang_t) is not None
    except Exception:
        return False


def resolve_path(f, t):
    """解析翻译路径：优先直达语向，否则经英语中转。返回 [(from, to), ...]。"""
    if f == t:
        return [(f, t)]
    if f == "en" or t == "en" or is_pair_installed(f, t):
        return [(f, t)]
    return [(f, "en"), ("en", t)]


def missing_models(f, t):
    """返回翻译 f -> t 尚未安装的模型列表 [(from, to), ...]。"""
    if f == t:
        return []
    return [pair for pair in resolve_path(f, t) if not is_pair_installed(*pair)]


def total_size_mb(pairs):
    """查询模型包总大小（MB）；获取失败时返回 None。仅访问模型文件的 HTTP 头。"""
    index = _load_index()
    url_map = {(p["from_code"], p["to_code"]): p["url"] for p in index}
    total = 0
    for pair in pairs:
        url = url_map.get(pair)
        if not url:
            return None
        try:
            req = urllib.request.Request(url, method="HEAD",
                                         headers={"User-Agent": "DOKSURI/0.2"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                total += int(resp.headers.get("Content-Length") or 0)
        except Exception:
            return None
    return round(total / 1024 / 1024)


def _is_pure_chinese(text, max_len=10):
    """是否为纯中文短句：去除空白后长度少于 max_len，且每个字符都是汉字。

    含数字、字母、标点符号或其他语言字符的混合文本一律返回 False，
    继续走 langdetect 自动检测。
    """
    chars = [ch for ch in text if not ch.isspace()]
    if not chars or len(chars) >= max_len:
        return False
    return all(
        "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf"   # 基本区 + 扩展A
        for ch in chars
    )


def detect_language(text):
    """自动检测语种，返回受支持的语种代码；无法识别时返回 None。纯本地计算。

    纯中文短句（去空白后少于 10 字且全部为汉字）直接判定为中文，
    规避 langdetect 对短文本的误判（如把短中文判成 ko/ja）。
    """
    if _is_pure_chinese(text):
        return "zh"
    try:
        from langdetect import DetectorFactory, detect
        DetectorFactory.seed = 0
        code = (detect(text) or "").lower()
    except Exception:
        return None
    if code.startswith("zh"):
        code = "zh"
    return code if code in SUPPORTED_CODES else None


def download_and_install(f, t, progress_cb=None, cancel_check=None):
    """下载并安装 f -> t 语言模型包（模型文件缓存到临时目录后本地安装）。

    progress_cb(done_bytes, total_bytes)：下载进度回调。
    cancel_check()：返回 True 时中止下载。
    """
    if is_pair_installed(f, t):
        _ensure_sbd_local(f)
        return
    pkg = None
    for p in _load_index():
        if p["from_code"] == f and p["to_code"] == t:
            pkg = p
            break
    if pkg is None:
        raise UnsupportedLanguageError(f"模型索引中没有 {f} -> {t} 的语言包")

    req = urllib.request.Request(pkg["url"], headers={"User-Agent": "DOKSURI/0.2"})
    tmp_path = None
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, \
                tempfile.NamedTemporaryFile(suffix=".argosmodel", delete=False) as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancel_check and cancel_check():
                    raise UserWarning("下载已取消")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
        tmp_path = out.name
        from argostranslate import package
        package.install_from_path(tmp_path)
        _ensure_sbd_local(f)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def translate(text, f, t):
    """本地离线翻译。模型缺失时抛出 ModelMissingError，不访问网络。"""
    if f == t:
        return text  # 同语种无需翻译
    _apply_offline_patches()
    missing = missing_models(f, t)
    if missing:
        raise ModelMissingError(missing)
    for a, _ in resolve_path(f, t):
        _ensure_sbd_local(a)
    result = text
    for a, b in resolve_path(f, t):
        lang_a, lang_b = _lang_by_code(a), _lang_by_code(b)
        result = lang_a.get_translation(lang_b).translate(result)
    return result


class TranslationSession:
    """长文本批量翻译会话：翻译路径解析、语言对象查找、断句模型本地化检查
    在会话创建时只做一次，之后逐段复用，降低多段翻译的每段固定开销。

    用法：
        session = TranslationSession(f, t)   # 模型缺失时抛 ModelMissingError
        for seg in segments:
            session.translate(seg)
    """

    def __init__(self, f, t):
        self._pairs = []          # [(lang_a, lang_b), ...]
        if f == t:
            return
        _apply_offline_patches()
        missing = missing_models(f, t)
        if missing:
            raise ModelMissingError(missing)
        for a, _ in resolve_path(f, t):
            _ensure_sbd_local(a)
        self._pairs = [
            (_lang_by_code(a), _lang_by_code(b)) for a, b in resolve_path(f, t)
        ]

    def translate(self, text):
        """翻译一个分段（须为不超过分段上限的短文本）。"""
        if not self._pairs:
            return text   # 同语种
        result = text
        for lang_a, lang_b in self._pairs:
            result = lang_a.get_translation(lang_b).translate(result)
        return result

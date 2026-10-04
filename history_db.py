"""DOKSURI 翻译历史 - 本地 SQLite 存储。

仅保存在程序目录 history.db（可用环境变量 DOKSURI_HISTORY_DB 覆盖），
全程离线，不上传任何内容。
每个接口独立开/关连接，天然线程安全；写入量小，GUI 线程直接调用即可。
"""
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _platform_data_dir():
    """平台应用数据根目录（打包版数据位置，与 model_manager 保持一致）。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "DOKSURI"
    if sys.platform == "linux":
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        return Path(base) / "DOKSURI"
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "DOKSURI"


# 数据目录：打包版（所有平台）与 macOS（含开发模式）用平台数据目录；
# Windows 开发版沿用项目目录（历史行为不变）。
# macOS 历史库按标准布局存放在
# ~/Library/Application Support/DOKSURI/history/history.db
if getattr(sys, "frozen", False) or sys.platform == "darwin":
    _DATA_DIR = _platform_data_dir()
    if sys.platform == "darwin":
        _DATA_DIR = _DATA_DIR / "history"   # macOS 专属 history 子目录
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH = Path(os.environ.get("DOKSURI_HISTORY_DB", _DATA_DIR / "history.db"))
else:
    DB_PATH = Path(os.environ.get("DOKSURI_HISTORY_DB", BASE_DIR / "history.db"))

_COLS = ("id", "ts", "f_code", "t_code", "source", "translated")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS history (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ts TEXT NOT NULL,
               f_code TEXT,
               t_code TEXT NOT NULL,
               source TEXT NOT NULL,
               translated TEXT NOT NULL
           )"""
    )
    return conn


def _query(sql, params=(), fetch=False):
    conn = _conn()
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows
    finally:
        conn.close()


def add_entry(source, translated, f_code, t_code):
    """新增一条翻译历史，返回记录 id；空文本不入库。"""
    if not source or not translated:
        return None
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO history (ts, f_code, t_code, source, translated)"
            " VALUES (?, ?, ?, ?, ?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             f_code, t_code, source, translated),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_entries(search=None, limit=500):
    """按时间倒序列出历史；search 时按原文/译文模糊过滤。"""
    base = f"SELECT {', '.join(_COLS)} FROM history"
    if search:
        like = f"%{search}%"
        rows = _query(
            base + " WHERE source LIKE ? OR translated LIKE ? ORDER BY id DESC LIMIT ?",
            (like, like, limit), fetch=True,
        )
    else:
        rows = _query(base + " ORDER BY id DESC LIMIT ?", (limit,), fetch=True)
    return [dict(zip(_COLS, r)) for r in rows]


def get_entry(entry_id):
    """按 id 取单条记录，不存在返回 None。"""
    rows = _query(
        f"SELECT {', '.join(_COLS)} FROM history WHERE id = ?", (entry_id,), fetch=True
    )
    return dict(zip(_COLS, rows[0])) if rows else None


def delete_entry(entry_id):
    _query("DELETE FROM history WHERE id = ?", (entry_id,))


def clear_all():
    _query("DELETE FROM history")


def count():
    rows = _query("SELECT COUNT(*) FROM history", fetch=True)
    return rows[0][0] if rows else 0

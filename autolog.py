"""自动上传记录：给「最近自动上传」那张表用。

和 journal.py 的分工：
  · journal 是**去重台账** —— 键是站点三元组，只回答「这条传过没有」，
    满了按时间淘汰，用户看不到；
  · 这份是**给人看的历史** —— 什么时候、哪一局、为什么值得传、结果如何，
    关掉程序也还在，用户能自己核对这功能干了什么。

两份互不影响：台账丢了最多多传一次（站点会去重），历史丢了只是少一段记录。

位置：``<数据目录>\\auto-uploads.json``，跟着数据目录一起删。
"""

import json
import os
import tempfile
import threading
import time

import paths

VERSION = 1
MAX_ITEMS = 100           # 一条约 200 字节，100 条 20 KB，够了
FIELDS = ("t", "level_cn", "time", "bv", "why", "result", "ok", "path")

_lock = threading.RLock()
_cache = None


def _path():
    return os.path.join(paths.data_dir(), "auto-uploads.json")


def load(force=False):
    """返回 {"version": 1, "items": [...]}，读不出来就当空的。"""
    global _cache
    with _lock:
        if _cache is not None and not force:
            return _cache
        data = {"version": VERSION, "items": []}
        try:
            with open(_path(), "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict) and isinstance(raw.get("items"), list):
                data = {"version": VERSION,
                        "items": [x for x in raw["items"] if isinstance(x, dict)]}
        except Exception:
            pass
        _cache = data
        return _cache


def items():
    """最近的在前。"""
    return list(load()["items"])


def add(entry):
    """追加一条（最新的排最前），超出上限丢最旧的。"""
    if not isinstance(entry, dict):
        return False
    item = {k: entry.get(k) for k in FIELDS if entry.get(k) is not None}
    item.setdefault("t", time.strftime("%m-%d %H:%M"))
    with _lock:
        data = load()
        data["items"].insert(0, item)
        del data["items"][MAX_ITEMS:]
        return _flush(data)


def clear():
    global _cache
    with _lock:
        _cache = {"version": VERSION, "items": []}
        try:
            os.remove(_path())
        except OSError:
            pass


def _flush(data):
    path = _path()
    tmp = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".auto-", suffix=".tmp",
                                   dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        tmp = None
        return True
    except OSError:
        return False
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass

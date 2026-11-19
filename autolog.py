"""自动上传记录：给「最近自动上传」那张表用。

和 journal.py 的分工：
  · journal 是**去重台账** —— 键是站点三元组，只回答「这条传过没有」，
    满了按时间淘汰，用户看不到；
  · 这份是**给人看的历史** —— 什么时候、哪一局、为什么值得传、结果如何，
    关掉程序也还在，用户能自己核对这功能干了什么。

两份互不影响：台账丢了最多多传一次（站点会去重），历史丢了只是少一段记录。

位置：``<数据目录>\\auto-uploads.json``，跟着数据目录一起删。
"""

import atexit
import json
import os
import tempfile
import threading
import time

import paths

VERSION = 1
MAX_ITEMS = 100           # 一条约 200 字节，100 条 20 KB，够了
FLUSH_GAP = 0.05          # 连着写很多条时，落盘最多每 50 ms 一次
FIELDS = ("t", "level_cn", "time", "bv", "why", "result", "ok", "path")

_lock = threading.RLock()
_cache = None
_dirty = False            # 内存里有还没写盘的记录
_last_flush = 0.0


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
    """追加一条（最新的排最前），超出上限丢最旧的。

    落盘按时间合并：连着写很多条时不必每条都重写整个文件。内存里的
    记录立刻是最新的，``items()`` 读到的永远不会少一条。
    """
    global _dirty
    if not isinstance(entry, dict):
        return False
    item = {k: entry.get(k) for k in FIELDS if entry.get(k) is not None}
    item.setdefault("t", time.strftime("%m-%d %H:%M"))
    with _lock:
        data = load()
        data["items"].insert(0, item)
        del data["items"][MAX_ITEMS:]
        _dirty = True
        return _flush_due(data)


def flush():
    """立刻把内存里的记录写盘（退出前、清空前用）。"""
    with _lock:
        if not _dirty:
            return True
        return _flush_due(load(), force=True)


def clear():
    global _cache, _dirty
    with _lock:
        _cache = {"version": VERSION, "items": []}
        _dirty = False
        try:
            os.remove(_path())
        except OSError:
            pass


def _flush_due(data, force=False):
    """距上次落盘不到 FLUSH_GAP 就先攒着；force 时无条件写。"""
    global _dirty, _last_flush
    now = time.monotonic()
    if not force and now - _last_flush < FLUSH_GAP:
        return True
    ok = _flush(data)
    if ok:
        _dirty = False
        _last_flush = now
    return ok


atexit.register(lambda: flush())


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

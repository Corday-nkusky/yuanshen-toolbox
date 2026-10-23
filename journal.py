"""上传台账：记住「这条已经传上去了」，用来做断点续传。

为什么要本地记一份，而不是只信服务器：
  · 传了 80 条，第 81 条断网，关掉程序 —— 下次启动要先联网拉一遍网站列表才知道
    前 80 条已经在了；网络还没恢复时，界面会把它们全标成「未上传」；
  · 用户重新点「上传全部未传」也不会重复上传（站点按 timems+bv+file_size 去重，
    返回 collision），但每一条都要重新传一遍完整的录像文件，白等一轮。

台账只是**加速与离线兜底**，不是权威：每次登录后仍会用服务器列表校正
（`_rebuild_up_rows` 里服务器的信息优先）。

位置：`<数据目录>\\uploads.json`，跟着数据目录一起删。
键用站点自己的去重三元组 `timems/bv/file_size`，与服务器判定口径一致。
"""

import json
import os
import tempfile
import threading
import time

import paths

VERSION = 1
MAX_ITEMS = 8000          # 单条录像约 5~40 KB，8000 条也就几百 KB，够用且不会无限长

# RLock：mark() 持锁时会再调 load()，普通 Lock 会自锁死
_lock = threading.RLock()
_cache = None


def _path():
    return os.path.join(paths.data_dir(), "uploads.json")


def _key_str(key):
    if isinstance(key, str):
        return key
    try:
        timems, bv, size = key
        return "%d/%d/%d" % (int(timems), int(bv), int(size))
    except (TypeError, ValueError):
        return ""


def load(force=False):
    """返回 {key: {t, state}}。读不出来就当空表，不打断任何流程。"""
    global _cache
    with _lock:
        if _cache is not None and not force:
            return _cache
        data = {"version": VERSION, "items": {}}
        try:
            with open(_path(), "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
                data = raw
            elif isinstance(raw, dict):
                data["items"] = raw          # 容错：早期可能只存了裸字典
        except Exception:
            pass
        _cache = data
        return _cache


def _flush(data):
    """先写临时文件再 os.replace，避免中途断电留下半个 JSON。"""
    path = _path()
    tmp = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".uploads-", suffix=".tmp",
                                   dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
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


def mark(key, state=None, when=None):
    """记录一条已上传。key 可以是 (timems, bv, size) 或 "t/b/s"。"""
    ks = _key_str(key)
    if not ks:
        return False
    with _lock:
        data = load()
        items = data["items"]
        items[ks] = {"t": when or time.strftime("%Y-%m-%d %H:%M"),
                     "state": state or ""}
        if len(items) > MAX_ITEMS:      # 超出上限就丢掉最早的一批
            keep = sorted(items.items(), key=lambda kv: kv[1].get("t", ""))[-MAX_ITEMS:]
            data["items"] = dict(keep)
        return _flush(data)


def has(key):
    ks = _key_str(key)
    return bool(ks) and ks in load()["items"]


def get(key):
    ks = _key_str(key)
    return load()["items"].get(ks) if ks else None


def count():
    return len(load()["items"])


def clear():
    global _cache
    with _lock:
        _cache = {"version": VERSION, "items": {}}
        try:
            os.remove(_path())
        except OSError:
            pass


def summary():
    data = load()
    return "本地台账 %d 条（%s）" % (len(data["items"]), _path())

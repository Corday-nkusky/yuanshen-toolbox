"""录像解析结果缓存（sqlite，数据目录里的 `scan-cache.db`）。

为什么需要：一次深度扫描的开销几乎全在「打开文件 + 让 ms-toollib 重演」上。
本机实测每局 0.8~8 ms（热缓存 0.8 ms，冷盘 7 ms 以上），2 万个录像就是几分钟，
而这些文件下次打开时绝大多数一个字节都没变。所以按 (mtime, size) 判断过期，
没变就直接拿上次解析出来的指标。

缓存的是 `core.read_video()` 的返回值：rce / bv / 时间 / 完成与否 / 标识 /
审核码。仍然要每局做的判断（NF/FL、PB、汇总）成本可以忽略，不在缓存范围。

失效规则只有一条：文件 mtime 或大小变了就当没缓存。改名、移动会换 path，
等于重新解析一次（阿比特的时间/BV 本来就以内容为准，无所谓）。

刻意**不做**「删掉已不存在的文件对应的行」：用户可能这次只扫了一个目录，
按「本次没见到」去删会把别的目录的缓存清掉。表里多留几行没有功能影响。
"""

import os
import sqlite3
import threading

import paths

_FILE = "scan-cache.db"
_lock = threading.RLock()
_conn = None
_ram = {}            # path -> (mtime_ns, size, dict)
_dirty = {}          # path -> (mtime_ns, size, dict)
_sha_dirty = {}      # path -> (mtime_ns, size, sha)
_loaded = False
_hits = 0
_misses = 0


def db_path():
    return os.path.join(paths.data_dir(), _FILE)


def _db():
    global _conn
    if _conn is not None:
        return _conn
    p = db_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        _conn = sqlite3.connect(p, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS video("
            "path TEXT PRIMARY KEY, mtime INTEGER, size INTEGER, data TEXT)")
        # 判重用的文件哈希只对「候选组」算（约 6%），单独一张表，不影响主表结构
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS file_sha("
            "path TEXT PRIMARY KEY, mtime INTEGER, size INTEGER, sha TEXT)")
    except sqlite3.Error:
        _conn = None
    return _conn


def _load():
    """一次性把缓存读进内存（几万行也就几十毫秒），扫描时不再查库。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    db = _db()
    if db is None:
        return
    try:
        import json
        for path, mt, size, data in db.execute(
                "SELECT path, mtime, size, data FROM video"):
            try:
                _ram[path] = (mt, size, json.loads(data))
            except ValueError:
                continue
    except sqlite3.Error:
        pass


def begin():
    """扫描开始：清空本次命中统计。"""
    global _hits, _misses
    with _lock:
        _load()
        _hits = _misses = 0


def get(path, mtime_ns, size):
    """命中返回解析结果，否则 None。"""
    global _hits, _misses
    with _lock:
        _load()
        row = _ram.get(path)
        if row and row[0] == int(mtime_ns) and row[1] == int(size):
            _hits += 1
            return dict(row[2])
        _misses += 1
        return None


def put(path, mtime_ns, size, data):
    with _lock:
        item = (int(mtime_ns), int(size), dict(data))
        _ram[path] = item
        _dirty[path] = item


def sha_get(path, mtime_ns, size):
    """取上次算过的文件哈希；文件没变才有意义（mtime / size 都要对得上）。"""
    db = _db()
    if db is None:
        return None
    with _lock:
        try:
            row = db.execute("SELECT mtime, size, sha FROM file_sha WHERE path=?",
                             (path,)).fetchone()
        except sqlite3.Error:
            return None
    if row and row[0] == int(mtime_ns) and row[1] == int(size):
        return row[2]
    return None


def sha_put(path, mtime_ns, size, sha):
    with _lock:
        _sha_dirty[path] = (int(mtime_ns), int(size), sha)


def commit():
    """把本次新解析的写回磁盘（一个事务，几万行也就一两百毫秒）。"""
    with _lock:
        db = _db()
        if db is None or not (_dirty or _sha_dirty):
            return 0
        import json
        rows = [(p, it[0], it[1], json.dumps(it[2], ensure_ascii=False))
                for p, it in _dirty.items()]
        sha_rows = [(p, it[0], it[1], it[2]) for p, it in _sha_dirty.items()]
        try:
            with db:
                if rows:
                    db.executemany(
                        "INSERT INTO video(path, mtime, size, data) VALUES(?,?,?,?) "
                        "ON CONFLICT(path) DO UPDATE SET "
                        "mtime=excluded.mtime, size=excluded.size, data=excluded.data",
                        rows)
                if sha_rows:
                    db.executemany(
                        "INSERT INTO file_sha(path, mtime, size, sha) VALUES(?,?,?,?) "
                        "ON CONFLICT(path) DO UPDATE SET "
                        "mtime=excluded.mtime, size=excluded.size, sha=excluded.sha",
                        sha_rows)
        except sqlite3.Error:
            return 0
        n = len(rows)
        _dirty.clear()
        _sha_dirty.clear()
        return n


def stats():
    return {"hits": _hits, "misses": _misses, "rows": len(_ram)}


def clear():
    global _loaded
    with _lock:
        _ram.clear()
        _dirty.clear()
        _sha_dirty.clear()
        _loaded = False
        db = _db()
        if db is not None:
            try:
                with db:
                    db.execute("DELETE FROM video")
                    db.execute("DELETE FROM file_sha")
            except sqlite3.Error:
                return
            try:                      # 把文件缩回去，否则磁盘上还是几 MB
                old = db.isolation_level
                db.isolation_level = None
                db.execute("VACUUM")
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                db.isolation_level = old
            except sqlite3.Error:
                pass


def size_on_disk():
    try:
        return os.path.getsize(db_path())
    except OSError:
        return 0

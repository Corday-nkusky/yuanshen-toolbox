"""元扫雷（Metasweeper）成绩统计核心。

只读 .evf 录像目录，不修改任何文件。

录像文件名格式（实测外推，来自 b/i/e 三级共 35 个样本）::

    b_0_1.774_9_5.073_PlayerName(Region).evf
    │ │  │     │  │      └ 玩家名
    │ │  │     │  └ bvs（完成局 = bv/time；文件名保留 3 位小数）
    │ │  │     └ bv（3BV）
    │ │  └ time（rtime，从 0 秒起算）
    │ └ 第二字段，样本全为 0，含义未确认（疑似 flag 数 / 模式变体）
    └ b / i / e = 初级 / 中级 / 高级

失败局以 ``_fail`` 结尾，本工具直接跳过非完成局（用户口径）。

NF / FL 判定用 ms-toollib（eee555/ms-toollib）重演录像得到 rce：
**rce = 右键有效点击数**，只有「第一次给雷格插旗」才 +1（同一格反复插拔不重复计，
插在非雷格上也不计）。``rce == 0`` 即 NF（无旗），否则 FL（有旗）。
"""

import hashlib
import heapq
import math
import os
from datetime import datetime, timezone

import cache as _cache

LEVELS = ("b", "i", "e")
LEVEL_CN = {"b": "初级", "i": "中级", "e": "高级"}
LEVEL_EN = {"b": "Beginner", "i": "Intermediate", "e": "Expert"}
# stnb 常数：官方原文（openms.top/docs/guide/minesweeper/stat）
STNB_CONST = {"b": 36, "i": 162, "e": 435}

# 官方评级的 3BV 门槛：初级 ≥10、中级 ≥30、高级 ≥100。
# 低于门槛的盘面随机性太小，成绩不参与标准评级。
BV_MIN_DEFAULT = {"b": 10, "i": 30, "e": 100}

SKIP_DIRS = {
    "__macosx", ".git", ".svn", "node_modules", "$recycle.bin",
    "system volume information", "bv_viewer", "bv_records", "metasweeper-pb",
}

# 递归扫描的目录数上限。实测本机 os.walk 约 4000 目录/秒（Defender 开着）：
# 20000 个目录 ≈ 5 秒，够覆盖任何「replay 下按年份/级别分文件夹」的存档，
# 又不会让误选 C:\ 的扫描跑上几分钟。
MAX_SCAN_DIRS = 20000

# ms-toollib 里的 level 编码：3=初级 4=中级 5=高级 6=自定义
VIDEO_LEVEL = {3: "b", 4: "i", 5: "e"}

# 模式（mode）：0 = 标准。其余都是变体玩法 —— 1 upk、4 win7、5 竞速无猜、
# 6 强无猜、7 弱无猜、8 准无猜、9 强可猜、10 弱可猜、11 标准递归。
# 口径出自站点源码 back_end/saolei/config/text_choices.py 的 Mode 枚举，
# 与元扫雷自己写进文件名的那个数字同源（见 Metasweeper 的 cal_evf_filename）。
# 本工具只统计标准模式：别的玩法难度不可比，混进 PB 就是把账算错。
STD_MODE = 0

# 自定义盘面在元扫雷录像文件名里的级别前缀（b_ / i_ / e_ 之外的那个）
CUSTOM_PREFIX = "c_"

# 录像来源：按扩展名判定（站点也按扩展名分派解析器，见 openms 站点源码 utils/parser.py）
VIDEO_SRC = {".evf": "ms", ".avf": "arb"}
SRC_CN = {"ms": "元扫雷", "arb": "阿比特"}

STYLE_CN = {"nf": "NF", "fl": "FL"}
STYLE_NAME = {"nf": "无旗（No Flag）", "fl": "有旗（Flag）"}

# PB 只有两张榜：全PB（不分风格）与 NF PB（rce = 0）。
# FL 不是独立榜，只是「全PB 的获胜局恰好用了旗」这个事实。
PB_CATEGORY_CN = {"all": "全PB", "nf": "NF PB"}
PB_SOURCE_CN = {"local": "本地", "site": "网站", "both": "同速"}
# Top5 来源：both 表示同一条录像在本地与网站都进了前五，不是「同速」。
TOP_SOURCE_CN = {"local": "本地", "site": "网站", "both": "两边"}


def _load_lib():
    """延迟加载 ms-toollib；不可用时返回 None（统计降级为仅按文件名）。"""
    try:
        import ms_toollib as ms
    except Exception:                     # noqa: BLE001
        return None
    return ms


MSLIB = _load_lib()

# 录像文件的头部特征。ms-toollib 拿到垃圾内容不是抛异常，而是**直接让进程消失**
# （实测：64 字节全零的 .evf，parse() 里崩在 C++ 层，Python 侧抓不到）。
# 所以交给它之前先自己核一眼。本机全量核过：270 个 .evf 首字节都是 0x04、第三个
# 字节都是 0x40；7198 个 .avf 首字节都是 0x34，其余字节不固定。
_MAGIC = {"ms": (0, 0x04), "arb": (0, 0x34)}


def looks_like_video(path, src=None):
    """头部对不对得上（对不上就是垃圾 / 空文件 / 别的格式）。

    返回 False 的文件一律不送进 ms-toollib —— 宁可这一局算「读不出」，
    也不能让一个坏文件把整个工具箱带下去。
    """
    src = src or video_src(path)
    if src is None:
        return False
    try:
        size = os.path.getsize(path)
        if size < 32:
            return False
        with open(path, "rb") as fh:
            head = fh.read(3)
    except OSError:
        return False
    if len(head) < 3:
        return False
    pos, val = _MAGIC[src]
    if head[pos] != val:
        return False
    return src != "ms" or head[2] == 0x40


def _wall_epoch(us, src):
    """录像内时间戳（微秒）→ 本机时区的真 epoch 秒；取不到返回 None。

    两种格式的语义**不一样**，这是全模块最容易静默出错的地方：

    · ``.evf``（元扫雷）存的是真 UTC，``fromtimestamp`` 直接得到本地墙钟；
    · ``.avf``（阿比特）存的是**当时的本地墙钟被当成 UTC 编码**。

    对 .avf 若直接用 ``fromtimestamp``，会整整偏 +8 小时（东八区）：21 点以后
    打的局被算到第二天、0–8 点打的局被算到前一天，而界面上看不出任何异常，
    只是「某天局数多了一倍、某天少了一半」。所以 .avf 先按 UTC 读回 naive
    墙钟，再让本机时区把它解释成真 epoch —— 上层从此只用
    ``datetime.fromtimestamp(t0)``，不必知道录像是什么格式。

    实测 23532 个样本（23183 avf + 349 evf）全部合法，与文件 mtime 的差
    中位数 avf 0.03 s、evf 0.07 s。
    """
    try:
        us = float(us)
    except (TypeError, ValueError):
        return None
    if us <= 0:
        return None
    try:
        if src == "arb":
            dt = datetime.fromtimestamp(us / 1e6, timezone.utc).replace(tzinfo=None)
        else:
            dt = datetime.fromtimestamp(us / 1e6)
    except (OverflowError, OSError, ValueError):
        return None
    return dt.timestamp()


def day_text(t0):
    """epoch 秒 → 本地日期 ``YYYY-MM-DD``（一天 = 本地 00:00–24:00）。"""
    if not t0 or float(t0) <= 0:
        return ""
    try:
        return datetime.fromtimestamp(float(t0)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def clock_text(t0):
    """epoch 秒 → ``HH:MM:SS``。"""
    if not t0 or float(t0) <= 0:
        return ""
    try:
        return datetime.fromtimestamp(float(t0)).strftime("%H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def read_video(path):
    """用 ms-toollib 重演一局录像，返回权威指标；失败返回 None。

    注意：``analyse()`` 之后各 getter 读的是「当前播放位置」的累计值，而
    ``current_event_id`` 停在 0，所以直接取 ``v.rce`` 永远是 0。必须取
    ``v.events[-1].key_dynamic_params`` 才是整局累计值——这一点很容易踩坑。
    """
    if MSLIB is None:
        return None
    src = video_src(path)
    if src is None or not looks_like_video(path, src):
        return None
    try:
        v = (MSLIB.AvfVideo if src == "arb" else MSLIB.EvfVideo)(path)
        v.parse()
        v.analyse()
        kp = v.events[-1].key_dynamic_params
        # 标识与审核码：上传前预检用（站点就是拿这两个字段判「进不进榜」）
        try:
            ident = str(v.player_identifier or "")
        except BaseException:                  # noqa: BLE001
            ident = ""
        try:
            valid = int(v.is_valid())
        except BaseException:                  # noqa: BLE001
            valid = None
        try:
            # 站点去重键用的是整数毫秒，自己用 round(秒*1000) 算会有 ±1ms 的
            # 浮点误差，导致「网站已有」对不上又重复上传（传上去会被判 collision）
            tms = int(v.rtime_ms)
        except BaseException:                  # noqa: BLE001
            tms = int(round(float(v.rtime) * 1000))
        try:
            mode = int(v.mode)
        except BaseException:                  # noqa: BLE001
            mode = -1
        # 录制时刻：t0 是开始、t1 是结束（真本地 epoch 秒）。读不到给 None，
        # 由 scan() 退回文件时间，不让一个坏录像带崩整个扫描。
        t0 = _wall_epoch(getattr(v, "start_time", None), src)
        t1 = _wall_epoch(getattr(v, "end_time", None), src)
        return {
            "rce": int(kp.rce),
            "left": int(kp.left),
            "flag": int(kp.flag),
            "solved": int(kp.bbbv_solved),
            "bv": int(v.bbbv),
            "time": float(v.rtime),
            "timems": tms,
            "level": VIDEO_LEVEL.get(int(v.level)),
            "mode": mode,
            "completed": bool(v.is_completed),
            "ident": ident,
            "valid": valid,
            "src": src,
            "t0": t0,
            "t1": t1,
        }
    except BaseException:                 # noqa: BLE001  (rust panic 也是 BaseException)
        return None


def stnb_of(bv, time, level):
    """stnb = sqrt(completion) * C / qg，qg = time^1.7 / bv。

    完成局 completion = 1，化简为 stnb = C * bv / time^1.7。
    """
    if bv <= 0 or time <= 0:
        return 0.0
    return STNB_CONST[level] * bv / (time ** 1.7)


def parse_filename(fn):
    """解析单个文件名，失败返回 None。"""
    if not fn.lower().endswith(".evf"):
        return None
    stem = fn[:-4]
    parts = stem.split("_", 5)
    if len(parts) < 6:
        return None
    lv, flag2, raw_t, raw_bv, raw_bvs, rest = parts
    lv = lv.strip().lower()
    if lv not in LEVELS:
        return None
    try:
        time = float(raw_t)
        bv = int(raw_bv)
        bvs_file = float(raw_bvs)
    except ValueError:
        return None
    fail = rest.endswith("_fail")
    if fail:
        rest = rest[: -len("_fail")]
    return {
        "level": lv,
        "flag2": flag2,
        "mode": flag2,          # 第二字段就是 mode，标准模式是 "0"
        "time": time,
        "bv": bv,
        "bvs_file": bvs_file,
        "player": rest,
        "fail": fail,
    }


def video_src(name):
    """按扩展名判断录像来源：'.evf' → ms（元扫雷），'.avf' → arb（阿比特）。"""
    return VIDEO_SRC.get(os.path.splitext(name)[1].lower())


_AVF_RE = None


def parse_avf_filename(fn):
    """阿比特文件名兜底解析（读不出内容时用，精度不如内容里的字段）。

    实测到的两种写法::

        HI-SCORE Beg_1.00_3BV=6_3BVs=6_Anonymous.avf
        Beg_NF_1.06_3BV=5_3BVs=4.71_Anonymous.avf
        #Exp_4.73_3BV=191_3BVs=4.01_Anonymous.avf

    还有 ``Ju Ze En_Exp_26.1(3bv123).avf`` 这种玩家名在前的写法，正则认不出，
    只能靠 ms-toollib 读内容。
    """
    global _AVF_RE
    if _AVF_RE is None:
        import re
        _AVF_RE = re.compile(
            r"(Beg|Int|Exp)[_ ](?:(NF|FL)_)?([0-9]+(?:\.[0-9]+)?)_3BV=(\d+)_3BVs=([0-9.]+)_(.+)$",
            re.IGNORECASE)
    stem = os.path.splitext(fn)[0]
    m = _AVF_RE.search(stem)
    if not m:
        return None
    lv = {"beg": "b", "int": "i", "exp": "e"}[m.group(1).lower()]
    try:
        return {
            "level": lv,
            "flag2": (m.group(2) or "avf").lower(),
            "time": float(m.group(3)),
            "bv": int(m.group(4)),
            "bvs_file": float(m.group(5)),
            "player": m.group(6).strip(),
            "fail": False,
        }
    except ValueError:
        return None


HISCORE_PREFIX = "hi-score"


def _hash_file(path, mtime_ns=0, size=0, chunk=1 << 20):
    """整文件 md5；读不出来返回 None（这时宁可不判重，也不误删一局）。

    结果按 (path, mtime_ns, size) 记在缓存库里，所以第二次扫描不再碰磁盘。
    """
    if mtime_ns:
        hit = _cache.sha_get(path, mtime_ns, size)
        if hit:
            return hit
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(chunk), b""):
                h.update(block)
    except OSError:
        return None
    digest = h.hexdigest()
    if mtime_ns:
        _cache.sha_put(path, mtime_ns, size, digest)
    return digest


def _keep_rank(rec):
    """同一局存了多份时，谁留下：正本 > 副本。

    1. 文件名不是 ``HI-SCORE *`` 的优先 —— 阿比特破纪录时会额外导出一份，
       正本在它自己的 replays 目录里。
    2. 目录层级浅的优先 —— 用户自己的 replay 目录胜过 gsc\\27415gsc16 这类
       下载下来的归档子目录。
    3. 路径字典序兜底，保证换机器、换扫描顺序结果都一样（不用 mtime）。
    """
    name = rec["file"].lower()
    return (name.startswith(HISCORE_PREFIX), rec["path"].count(os.sep),
            rec["path"].lower())


def dedupe_games(games):
    """同一局存了多份时只留一份，返回 (保留的 games, 去掉的份数)。

    判定只能用**文件字节**：另存/改名的副本与正本逐字节相同，但指标组合撞不起
    区分作用 —— 阿比特录像的 rtime 只有 10 ms 精度，同级别同 BV 同时间的**不同**
    局大量存在（实测 ``(b, 3, 970)`` 就有三份，其中两份 bv/rce/solved 全同、
    left 与点击流完全不同）。所以不做指标级去重。

    字节相同 ⇒ size / level / bv / timems 必然也相同，因此只对这四项都撞上的
    候选组算哈希（实测占全量 6.4%，0.5 秒），不必读全部文件。
    """
    if len(games) < 2:
        return games, 0

    groups = {}
    for rec in games:
        key = (rec.get("size"), rec.get("level"), rec.get("bv"), rec.get("timems"))
        groups.setdefault(key, []).append(rec)

    drop = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        by_hash = {}
        for rec in members:
            digest = _hash_file(rec["path"], rec.get("mtime_ns") or 0,
                                rec.get("size") or 0)
            if digest is None:
                by_hash[("unreadable", rec["path"])] = [rec]
            else:
                by_hash.setdefault(digest, []).append(rec)
        for copies in by_hash.values():
            if len(copies) < 2:
                continue
            copies.sort(key=_keep_rank)
            drop.update(rec["path"] for rec in copies[1:])

    if not drop:
        return games, 0
    return [rec for rec in games if rec["path"] not in drop], len(drop)


def _walk_one_root(root):
    """深度优先遍历一个根目录，产出 (dirpath, [文件 DirEntry], 目录 mtime_ns)。

    为什么不用 ``os.walk``：Windows 上 ``os.scandir`` 返回的 DirEntry 已经把
    stat 信息（大小、mtime、是否目录）一并拿回来了，再 ``os.stat(path)`` 一次
    等于把同一个查询重做一遍。实测 2.2 万个文件：os.walk + os.stat 0.49 s，
    scandir + DirEntry.stat 0.04 s。

    顺带把每个目录的 mtime 也带出去：界面的「自动刷新」要拿它当基线，
    有了它就不必在扫描结束后再走一遍树。

    顺序与 os.walk 一致（父目录先于子目录，同层按系统返回顺序），
    子目录过滤规则（SKIP_DIRS）也一致，所以扫描结果不会因为换实现而变。
    """
    stack = [(root, None)]
    while stack:
        dirpath, mtime_ns = stack.pop()
        if mtime_ns is None:
            try:
                mtime_ns = os.stat(dirpath).st_mtime_ns
            except OSError:
                mtime_ns = 0
        try:
            with os.scandir(dirpath) as it:
                entries = list(it)
        except OSError:                 # 权限不足 / 扫描期间被删
            yield dirpath, [], mtime_ns
            continue
        files, subs = [], []
        for ent in entries:
            try:
                if ent.is_dir(follow_symlinks=False):
                    if ent.name.lower() not in SKIP_DIRS:
                        try:
                            sub_m = ent.stat().st_mtime_ns
                        except OSError:
                            sub_m = None
                        subs.append((ent.path, sub_m))
                    continue
            except OSError:
                continue
            files.append(ent)
        yield dirpath, files, mtime_ns
        stack.extend(reversed(subs))    # 反着压栈，出栈就还是原来的顺序


def count_videos(roots, max_dirs=MAX_SCAN_DIRS):
    """快速清点 scan() 会看到的录像文件个数，给进度条当分母。

    只读目录项、不 stat 也不解析（命中 Windows 目录项缓存），实测 2.2 万个文件
    约 0.2 秒 —— 相比整轮扫描（首扫上百秒）可以忽略。目录数上限、跳过目录、
    后缀判定都与 scan() 保持一致，分子分母才不会对不上。
    """
    if isinstance(roots, (str, os.PathLike)):
        roots = [roots]
    total = 0
    dirs = 0
    for root in [os.path.abspath(r) for r in roots if r]:
        if not os.path.isdir(root):
            continue
        for _dirpath, dirnames, filenames in os.walk(root):
            dirs += 1
            if max_dirs and dirs > max_dirs:
                return total
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
            for fn in filenames:
                if video_src(fn):
                    total += 1
    return total


def scan(roots, progress=None, deep=True, max_dirs=MAX_SCAN_DIRS, dedupe=True):
    """递归扫描一个或多个目录下的 .evf / .avf，返回 (games, info)。

    roots 可以是单个路径字符串，也可以是路径列表（元扫雷 + 阿比特 + 任意多个存档文件夹）。
    每条 game 带 ``src``：``ms``（元扫雷 .evf）或 ``arb``（阿比特 .avf）。

    games 只含完成局；info 记录文件数、跳过数、耗时等审计信息。
    deep=True 时用 ms-toollib 重演每局，得到 rce 与 NF/FL 判定。
    dedupe=True 时把「同一局存了多份」压成一局（见 dedupe_games）。

    progress 回调收到「已经读过的录像文件个数」（0 起，每 200 个一次），
    除以 count_videos() 的结果就是进度。它跑在调用方的线程里，不能碰界面。

    递归是为了容许「选上一级目录」和「按年份/级别分子文件夹」的存档习惯，
    但用户的根目录一旦点错（C:\\ 、整个用户目录），无上限的 os.walk 会把界面
    卡在「正在扫描」上几十秒到几分钟。所以给一个目录数上限：到顶就带着已找到的
    结果返回，并把 info["truncated"] 置 True，让界面提示「指到 replay 本身」。
    """
    import time as _time

    if isinstance(roots, (str, os.PathLike)):
        roots = [roots]
    roots = [os.path.abspath(r) for r in roots if r]

    t0 = _time.perf_counter()
    games = []
    seen_paths = set()
    _cache.begin()
    info = {
        "root": roots[0] if roots else "",
        "roots": roots,
        "files": 0,
        "dirs_visited": 0,
        "truncated": False,
        "unparsed": [],
        "fail_skipped": 0,
        "duplicates": 0,
        "dup_removed": 0,
        "rce_failed": 0,
        "retimed": 0,
        "refilled": 0,
        "tampered": [],
        "level_conflict": [],
        "non_std": 0,
        "dirs": set(),
        "by_src": {"ms": 0, "arb": 0},
    }

    if progress:
        progress(0)                   # 立刻亮一下：让进度条从 0 开始，而不是停在上一轮的位置

    newest = 0                          # 最新一局的 mtime（界面判断「真的有没有变」用）
    newest_ms = 0                       # 只算元扫雷录像：record.ini 对账要拿它比新旧
    wdirs = {}                          # 目录 -> mtime_ns（自动刷新的监控基线）
    for root in roots:
        for dirpath, files, dir_mtime in _walk_one_root(root):
            info["dirs_visited"] += 1
            if max_dirs and info["dirs_visited"] > max_dirs:
                info["truncated"] = True
                break
            if dir_mtime:
                wdirs[dirpath] = dir_mtime
            hit = False
            for ent in files:
                fn = ent.name
                src = video_src(fn)
                if src is None:
                    continue
                hit = True
                info["files"] += 1
                # 进度每 200 个文件报一次（放在文件循环里；放目录循环里会因为
                # 「一个目录几百个文件」而几百次才跳一格，进度条看着像卡住）
                if progress and info["files"] % 200 == 0:
                    progress(info["files"])
                path = ent.path
                if path in seen_paths:      # 两个根目录互相覆盖（选了 replay 和它的上一级）
                    info["duplicates"] += 1
                    continue

                # 先取 stat：缓存要靠它判断录像有没有变。
                # ent.stat() 读的是 scandir 已经拿到的属性，不再发起系统调用。
                try:
                    st = ent.stat()
                    mt, size = st.st_mtime, st.st_size
                    mt_ns = st.st_mtime_ns
                except OSError:
                    mt = size = mt_ns = 0
                if mt_ns > newest:
                    newest = mt_ns
                if src == "ms" and mt_ns > newest_ms:
                    newest_ms = mt_ns

                vid = None
                if deep:
                    vid = _cache.get(path, mt_ns, size)
                    stale = vid is not None and "t0" not in vid
                    if stale:
                        # 这一版以前的缓存里没有录像内时间（那时「日期」其实来自
                        # 文件 mtime）。就地重解析一次写回，之后永不复发：
                        # 2 万局热盘约 40 秒，只在第一次升级时付这笔钱。
                        vid = None
                    if vid is None:
                        vid = read_video(path)
                        if vid is not None:
                            _cache.put(path, mt_ns, size, vid)
                            if stale:
                                info["refilled"] += 1

                if src == "ms":
                    if fn[:2].lower() == CUSTOM_PREFIX:
                        info["non_std"] += 1     # 自定义盘面：级别对不上任何一档
                        continue
                    rec = parse_filename(fn)
                    if rec is None:
                        info["unparsed"].append(fn)
                        continue
                    if rec["fail"]:
                        info["fail_skipped"] += 1
                        continue
                    if rec["mode"] != str(STD_MODE):
                        info["non_std"] += 1     # 无猜、可猜、win7 这类变体玩法
                        continue
                else:
                    # 阿比特：能读内容就以内容为准，读不出才退回文件名。
                    # 内容里已经有 level 时不必再跑文件名正则 —— 2 万局实测省
                    # 0.23 s，而文件名里的 level/time/bv 后面本来就会被内容覆盖。
                    lvl = vid.get("level") if vid is not None else None
                    if vid is not None:
                        # level 6 = 自定义盘面；mode 非 0 = 变体玩法。两种都不算成绩。
                        #
                        # 老缓存里没有 mode 字段（这一版才加的）。这里**不**为了补它
                        # 重新解析：阿比特（Arbiter）根本没有无猜玩法，实测 23183 条
                        # 录像的 mode 全是 0，而重解析 2 万条要十几秒 —— 打开工具箱
                        # 卡在那儿等十几秒，比放行一条不存在的无猜局更糟。缺字段就按
                        # 标准放行；新录像解析时自然带上 mode，从下一版起逐条补齐。
                        # 元扫雷的 mode 来自文件名，任何时候都准，不受这条影响。
                        mode = vid.get("mode")
                        if lvl is None or (mode is not None
                                           and int(mode) != STD_MODE):
                            info["non_std"] += 1
                            continue
                    if lvl:
                        rec = {"level": lvl, "flag2": "avf", "time": 0.0, "bv": 0,
                               "bvs_file": 0.0, "player": "", "fail": False}
                    else:
                        rec = parse_avf_filename(fn) or {
                            "level": "", "flag2": "avf", "time": 0.0, "bv": 0,
                            "bvs_file": 0.0, "player": "", "fail": False}
                    if vid is None and not rec["level"]:
                        info["unparsed"].append(fn)
                        continue

                if deep and vid is None:
                    info["rce_failed"] += 1
                if vid is not None:
                    rec["timems"] = vid.get("timems")
                    if not vid["completed"]:
                        info["fail_skipped"] += 1
                        continue
                    if src == "arb":
                        # 文件名里的时间/BV 只是「当时的」记录，一律用录像内容覆盖
                        rec["level"] = vid["level"] or rec["level"]
                        rec["time"] = vid["time"] or rec["time"]
                        rec["bv"] = vid["bv"] or rec["bv"]
                        rec["player"] = vid.get("ident") or rec["player"]

                if rec["level"] not in LEVELS or rec["time"] <= 0 or rec["bv"] <= 0:
                    info["unparsed"].append(fn)
                    continue

                seen_paths.add(path)
                # 录制时刻以录像内的时间戳为准（见 _wall_epoch）；读不到才退回
                # 文件 mtime，并在 info["retimed"] 里记账。日期跟着缓存走：
                # 缓存键是 (路径, mtime)，文件不变日期就不会变。
                # 变量名别叫 t0/t1：外层那个 t0 是 scan() 的计时起点。
                rec_t0 = (vid or {}).get("t0")
                if not rec_t0:
                    rec_t0 = float(mt) if mt else 0.0
                    info["retimed"] += 1
                rec_t1 = (vid or {}).get("t1") or (rec_t0 + float(rec["time"]))
                dtxt = day_text(rec_t0)
                rec.update(
                    file=fn,
                    path=path,
                    src=src,
                    mtime=mt,
                    mtime_ns=mt_ns,
                    size=size,
                    date=dtxt,
                    t0=rec_t0,
                    t1=rec_t1,
                    bvs=rec["bv"] / rec["time"] if rec["time"] > 0 else 0.0,
                    stnb=stnb_of(rec["bv"], rec["time"], rec["level"]),
                )

                rec["rce"] = None
                rec["nf"] = None
                rec["style"] = None
                rec["tampered"] = False
                if vid is not None:
                    rec["rce"] = vid["rce"]
                    rec["nf"] = vid["rce"] == 0
                    rec["style"] = "nf" if rec["nf"] else "fl"
                    rec["solved"] = vid["solved"]
                    rec["vc_bv"] = vid["bv"]
                    rec["vc_time"] = vid["time"]
                    rec["ident"] = vid.get("ident") or ""
                    rec["valid"] = vid.get("valid")
                    if src == "ms":
                        # 文件名与录像内不一致 → 可能被改名改数据。
                        # 阿比特的文件名是另一个程序生成的、小数位不统一，不做这个比对。
                        if vid["bv"] != rec["bv"] or abs(vid["time"] - rec["time"]) > 0.0015:
                            rec["tampered"] = True
                            info["tampered"].append(fn)
                        if vid["level"] and vid["level"] != rec["level"]:
                            info["level_conflict"].append(fn)

                info["by_src"][src] += 1
                games.append(rec)
            if hit:
                info["dirs"].add(dirpath)

    info["dirs"] = sorted(info["dirs"])
    info["newest"] = newest             # 最新的一个录像的 mtime_ns，0 表示一个都没读到
    info["newest_ms"] = newest_ms       # 最新一局「元扫雷」录像的 mtime_ns（record.ini 对账用）
    info["dirs_mtime"] = wdirs          # 走过的每个目录的 mtime_ns（界面监控基线）
    if dedupe:
        games, info["dup_removed"] = dedupe_games(games)
        info["by_src"] = {"ms": 0, "arb": 0}
        for rec in games:
            info["by_src"][rec["src"]] = info["by_src"].get(rec["src"], 0) + 1
    if deep:
        info["cache_written"] = _cache.commit()
        info["cache"] = _cache.stats()
    else:
        info["cache_written"] = 0
        info["cache"] = {"hits": 0, "misses": 0, "rows": 0}
    info["elapsed"] = _time.perf_counter() - t0
    info["unique"] = len(games)
    info["with_rce"] = sum(1 for g in games if g.get("nf") is not None)
    info["nf_count"] = sum(1 for g in games if g.get("nf") is True)
    info["fl_count"] = sum(1 for g in games if g.get("nf") is False)
    return games, info


def build_pb(games, style=None):
    """每个 (level, bv) 的最快完成局。

    style: None=全PB（不分风格）；'nf'=NF PB（只看 rce=0 的局）。
    FL 不是独立榜，只是全PB 获胜局的风格标签。
    """
    pool = games if style is None else [g for g in games if g.get("style") == style]
    best = {}
    for g in pool:
        key = (g["level"], g["bv"])
        cur = best.get(key)
        if cur is None or g["time"] < cur["time"]:
            best[key] = g
    rows = []
    for key, g in best.items():
        lv, bv = key[0], key[1]
        rows.append({
            "level": lv,
            "bv": bv,
            "time": g["time"],
            "timems": (g.get("timems") if g.get("timems") is not None
                       else int(round(float(g["time"]) * 1000))),
            "bvs": g["bvs"],
            "stnb": g["stnb"],
            "date": g["date"],
            "file": g["file"],
            "path": g["path"],
            "style": g.get("style"),
            "style_cn": STYLE_CN.get(g.get("style"), "?"),
            "nf": g.get("nf"),
            "rce": g.get("rce"),
        })
    rows.sort(key=lambda r: (LEVELS.index(r["level"]), r["bv"],
                             0 if r["style"] == "nf" else 1))
    return rows


def build_pb_rows(games):
    """返回两张正式 PB 榜：category='all'（全PB）和 'nf'（NF PB）。

    两榜各自在子集内取最快，互不比较：全PB 可能比 NF PB 快，也可能相等；
    NF PB 即使更慢也保留，因为它是受限玩法（禁用插旗）的独立成绩。
    全PB 行的 ``style`` 只表示获胜局实际是 NF 还是 FL，不是第三个榜。
    """
    rows = []
    for row in build_pb(games):
        row = dict(row)
        row["category"] = "all"
        rows.append(row)
    for row in build_pb(games, style="nf"):
        row = dict(row)
        row["category"] = "nf"
        rows.append(row)
    rows.sort(key=lambda r: (LEVELS.index(r["level"]), r["bv"],
                             0 if r["category"] == "all" else 1))
    return rows


def _pb_ms(row):
    """PB 行的整数毫秒。站点按 ms 存，本地也统一成 ms 比较。"""
    ms = row.get("timems")
    if ms is None:
        ms = int(round(float(row.get("time") or 0) * 1000))
    return int(ms)


def _site_pb_row(rec):
    """站点 PB 记录 -> 本地 PB 行（时间/3BV/s/stnb 由 timems 算）。"""
    lv = str(rec.get("level") or "").lower()
    bv = int(rec.get("bv") or 0)
    ms = int(rec.get("timems") or 0)
    sec = ms / 1000.0
    return {
        "level": lv,
        "bv": bv,
        "time": sec,
        "timems": ms,
        "bvs": bv / sec if sec > 0 else 0.0,
        "stnb": stnb_of(bv, sec, lv),
        "date": "",
        "file": "网站 #%s" % (rec.get("video_id") or "?"),
        "path": "",
        "style": None,
        "style_cn": "—",
        "nf": None,
        "rce": None,
        "site_video_id": rec.get("video_id"),
        "site_rank": rec.get("rank"),
    }


def site_pb_best(site_records):
    """网站 PB 记录 -> {(级别, BV, 榜): 整数毫秒}。

    每个（级别 × BV × 榜）只保留最快的一条，口径与 merge_site_pb 一致：
    ``nf=True`` 进 NF PB，其余进全PB；非法记录直接忽略。
    """
    best = {}
    for rec in site_records or []:
        if not isinstance(rec, dict):
            continue
        lv = str(rec.get("level") or "").lower()
        try:
            bv = int(rec.get("bv") or 0)
            ms = int(rec.get("timems") or 0)
        except (TypeError, ValueError):
            continue
        if lv not in LEVELS or bv <= 0 or ms <= 0:
            continue
        key = (lv, bv, "nf" if rec.get("nf") else "all")
        if key not in best or ms < best[key]:
            best[key] = ms
    return best


def merge_site_pb(local_rows, site_records):
    """把 openms 的 PB 记录并进本地 PB 行，返回合并后的行。

    口径与站点一致：
      * ``nf=False`` -> 全PB（category='all'）
      * ``nf=True``  -> NF PB（category='nf'）

    两榜独立取各自最快，不拿全PB 去盖 NF PB。全PB 行额外标出获胜局的
    实际风格：``std < nf`` 必定是 FL，``std == nf`` 且同一条录像则是 NF，
    时间相同但录像不同则只能写「同速」。只有 std 说明该 BV 没有 NF 记录，
    按 FL 处理。这个标签只用于说明，不产生第三个榜。

    比较用整数毫秒；同速优先本地行，因为本地有文件路径、日期和 rce。
    站点行没有本地文件，``file`` 写成 ``网站 #录像 id``，``path`` 为空。
    """
    local = {}
    for row in local_rows or []:
        if not isinstance(row, dict):
            continue
        lv = row.get("level")
        try:
            bv = int(row.get("bv") or 0)
        except (TypeError, ValueError):
            continue
        if lv not in LEVELS or bv <= 0:
            continue
        cat = row.get("category") or "all"
        key = (lv, bv, cat)
        row = dict(row, category=cat)
        row.setdefault("timems", _pb_ms(row))
        cur = local.get(key)
        if cur is None or _pb_ms(row) < _pb_ms(cur):
            local[key] = row

    site_all, site_nf = {}, {}
    for rec in site_records or []:
        if not isinstance(rec, dict):
            continue
        lv = str(rec.get("level") or "").lower()
        try:
            bv = int(rec.get("bv") or 0)
            ms = int(rec.get("timems") or 0)
        except (TypeError, ValueError):
            continue
        if lv not in LEVELS or bv <= 0 or ms <= 0:
            continue
        item = _site_pb_row(rec)
        bucket = site_nf if rec.get("nf") else site_all
        key = (lv, bv)
        cur = bucket.get(key)
        if cur is None or ms < cur["timems"]:
            bucket[key] = item

    site = {}
    for (lv, bv), item in site_all.items():
        item = dict(item)
        nf_item = site_nf.get((lv, bv))
        if nf_item is not None and item["timems"] < nf_item["timems"]:
            item.update(style="fl", style_cn="FL", nf=False, rce=None)
        elif nf_item is not None and item["timems"] == nf_item["timems"]:
            if item.get("site_video_id") == nf_item.get("site_video_id"):
                item.update(style="nf", style_cn="NF", nf=True, rce=0)
            else:
                # 时间相同但录像不同：无法从 PB 接口判断 std 那条是 NF 还是 FL
                item.update(style=None, style_cn="同速", nf=None, rce=None)
        elif nf_item is None:
            # 该 BV 没有 NF 记录，全PB 只能来自 FL
            item.update(style="fl", style_cn="FL", nf=False, rce=None)
        else:
            # 站点数据不一致（全PB 慢于 NF），不猜风格
            item.update(style=None, style_cn="—", nf=None, rce=None)
        item["category"] = "all"
        site[(lv, bv, "all")] = item

    for (lv, bv), item in site_nf.items():
        item = dict(item)
        item.update(category="nf", style="nf", style_cn="NF", nf=True, rce=0)
        site[(lv, bv, "nf")] = item

    out = []
    for key in set(local) | set(site):
        lrow = local.get(key)
        srow = site.get(key)
        if lrow and srow:
            lm, sm = _pb_ms(lrow), srow["timems"]
            if lm <= sm:
                row = dict(lrow)
                row["source"] = "both" if lm == sm else "local"
                row["site_timems"] = sm
                row["site_video_id"] = srow.get("site_video_id")
                row["site_rank"] = srow.get("site_rank")
                row["local_timems"] = lm
            else:
                row = dict(srow)
                row["source"] = "site"
                row["local_timems"] = lm
        elif lrow:
            row = dict(lrow)
            row["source"] = "local"
            row["site_timems"] = None
            row["site_video_id"] = None
            row["site_rank"] = None
            row["local_timems"] = _pb_ms(lrow)
        else:
            row = dict(srow)
            row["source"] = "site"
            row["local_timems"] = None
        row["category"] = key[2]
        row.setdefault("timems", _pb_ms(row))
        row.setdefault("rce", None)
        row.setdefault("nf", None)
        row.setdefault("style", None)
        row.setdefault("style_cn", STYLE_CN.get(row.get("style"), "—"))
        out.append(row)

    out.sort(key=lambda r: (LEVELS.index(r["level"]), r["bv"],
                             0 if r["category"] == "all" else 1))
    return out


def count_by_bv(games, style=None):
    cnt = {}
    for g in games:
        if style is not None and g.get("style") != style:
            continue
        cnt[(g["level"], g["bv"])] = cnt.get((g["level"], g["bv"]), 0) + 1
    return cnt


def top5(games, level, metric, bv_min=0, style=None):
    """某级别某指标的前 5 局。

    metric: 'time'（升序）| 'bvs'（降序）| 'stnb'（降序）
    style:  None=全部 | 'nf' | 'fl'
    """
    pool = [g for g in games
            if g["level"] == level and g["bv"] >= bv_min
            and (style is None or g.get("style") == style)]
    if metric == "time":
        pool.sort(key=lambda g: (g["time"], -g["bv"]))
    else:
        pool.sort(key=lambda g: (-g[metric], g["time"]))
    return pool[:5]


# 站点 Top5 字段：std:bt / nf:es 等，t=time(ms)、b=3BV/s、s=iqg。
TOP_FIELD_METRICS = {"t": "time", "b": "bvs", "s": "stnb"}


def _parse_top_field(field):
    """``std:bt`` -> ('all', 'b', 'time')；``nf:es`` -> ('nf', 'e', 'stnb')。"""
    if not isinstance(field, str):
        return None
    mode, sep, code = field.partition(":")
    if not sep or mode not in ("std", "nf") or len(code) != 2:
        return None
    level, stat = code[0], code[1]
    metric = TOP_FIELD_METRICS.get(stat)
    if level not in LEVELS or metric is None:
        return None
    return ("nf" if mode == "nf" else "all"), level, metric


def _number(value):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _top_sort_key(row, metric):
    """站点/合并 Top5 的排序：time 升序，bvs/stnb 降序，缺值放最后。"""
    value = row.get(metric)
    if metric == "time":
        return (value is None, value if value is not None else float("inf"),
                -(row.get("bv") or 0))
    return (value is None, -(value if value is not None else 0),
            row.get("time") if row.get("time") is not None else float("inf"))


def site_top_video_ids(records):
    """站点 Top5 里出现的录像 id（去重），用于一次批量取解析明细。"""
    ids, seen = [], set()
    if not isinstance(records, dict):
        return ids
    for field, record in records.items():
        if _parse_top_field(field) is None or not isinstance(record, dict):
            continue
        for item in record.get("videos") or []:
            if not isinstance(item, dict):
                continue
            vid = _integer(item.get("video_id"))
            if vid and vid > 0 and vid not in seen:
                seen.add(vid)
                ids.append(vid)
    return ids


def site_top_rows(records, details=None):
    """站点 Top5 记录 -> {(榜, 级别, 指标): [Top5 行, ...]}。

    ``榜`` 为 'all' / 'nf'；指标为 'time' / 'bvs' / 'stnb'。站点 ``s`` 榜
    存的是 iqg，显示与比较时乘级别常数换成 stnb。字段值本身决定该榜名次，
    ``details``（staticdetailbatch 的 {录像 id: 明细}）只用来补 BV、时间、
    风格和日期；缺明细时该行仍保留，只是其他列为空。
    """
    out = {}
    if not isinstance(records, dict):
        return out
    details = details if isinstance(details, dict) else {}

    for field, record in records.items():
        parsed = _parse_top_field(field)
        if parsed is None or not isinstance(record, dict):
            continue
        category, level, metric = parsed
        videos = record.get("videos")
        if not isinstance(videos, list):
            continue

        rows, seen = [], set()
        for rank, item in enumerate(videos, 1):
            if not isinstance(item, dict):
                continue
            vid = _integer(item.get("video_id"))
            value = _number(item.get("value"))
            if not vid or vid <= 0 or value is None or vid in seen:
                continue
            if metric == "time" and value <= 0:   # 0 ms 不是有效完成局
                continue
            seen.add(vid)

            det = details.get(vid) or details.get(str(vid)) or {}
            if not isinstance(det, dict):
                det = {}
            d_ms = _integer(det.get("timems"))
            d_bv = _integer(det.get("bv"))
            if d_ms is not None and d_ms <= 0:
                d_ms = None
            if d_bv is not None and d_bv <= 0:
                d_bv = None
            size = _integer(det.get("file_size")) or 0

            timems = d_ms
            if metric == "time":
                timems = int(round(value))
            sec = timems / 1000.0 if timems else None
            bvs = d_bv / sec if d_bv and sec else None
            stnb = stnb_of(d_bv, sec, level) if d_bv and sec else None
            if metric == "bvs":
                bvs = value
            elif metric == "stnb":
                stnb = value * STNB_CONST[level]

            style = None
            if category == "nf":
                style = "nf"
            else:
                right_ce = _integer(det.get("right_ce"))
                if right_ce is not None:
                    style = "nf" if right_ce == 0 else "fl"

            end_time = det.get("end_time")
            date = end_time[:10] if isinstance(end_time, str) and len(end_time) >= 10 else ""
            rows.append({
                "level": level,
                "metric": metric,
                "time": sec,
                "timems": timems,
                "bv": d_bv,
                "bvs": bvs,
                "stnb": stnb,
                "date": date,
                "file": "网站 #%d" % vid,
                "path": "",
                "style": style,
                "style_cn": STYLE_CN.get(style, "—"),
                "nf": True if style == "nf" else (False if style == "fl" else None),
                "rce": 0 if style == "nf" else None,
                "size": size,
                "source": "site",
                "source_cn": TOP_SOURCE_CN["site"],
                "site_video_id": vid,
                "site_rank": rank,
            })

        rows.sort(key=lambda r: _top_sort_key(r, metric))
        out[(category, level, metric)] = rows[:5]
    return out


def site_top_cutoffs(records):
    """站点各 Top5 小榜的第五名成绩 -> {(榜, 级别, 指标): 阈值}。

    只有满 5 条才有阈值：不满五条时本地任何成绩都能补位，不该被排除。
    time 阈值是整数毫秒；bvs/stnb 是显示值（stnb 已乘级别常数）。
    """
    cutoffs = {}
    for (category, level, metric), rows in site_top_rows(records).items():
        if len(rows) < 5:
            continue
        row = rows[4]
        if metric == "time":
            cutoffs[(category, level, metric)] = row.get("timems")
        else:
            cutoffs[(category, level, metric)] = row.get(metric)
    return {k: v for k, v in cutoffs.items() if v is not None}


def top_beats(game, metric, cutoff):
    """本地局能否挤进网站已满五条的 Top5。同值不算（站点同分不替换末项）。"""
    if cutoff is None:
        return True
    if metric == "time":
        try:
            ms = game.get("timems")
            if ms is None:
                ms = int(round(float(game.get("time") or 0) * 1000))
            return int(ms) < int(cutoff)
        except (TypeError, ValueError):
            return True
    value = _number(game.get(metric))
    if value is None:
        return True
    return value > float(cutoff) * (1 + 1e-9)


def top_match_key(game):
    """本地/站点录像的同一性：timems + BV + 文件大小（站点去重三元组）。"""
    try:
        ms = game.get("timems")
        if ms is None:
            ms = int(round(float(game.get("time") or 0) * 1000))
        bv = int(game.get("bv") or 0)
        size = int(game.get("size") or 0)
    except (TypeError, ValueError):
        return None
    if ms <= 0 or bv <= 0 or size <= 0:
        return None
    return (ms, bv, size)


def merge_top_rows(local_rows, site_rows, metric, limit=5):
    """把本地 Top5 与站点 Top5 合成一张榜。

    同一条录像（三元组相同）只留本地行，来源标「两边」；没在本地出现
    的站点成绩也要显示，来源标「网站」。按指标排序后取前五。
    """
    site_by_key = {}
    site_unkeyed = []
    for row in site_rows or []:
        key = top_match_key(row)
        if key:
            site_by_key[key] = row
        else:
            site_unkeyed.append(row)   # 缺明细时无从去重，但成绩本身仍要显示

    merged = []
    for game in local_rows or []:
        item = dict(game)
        item["source"] = "local"
        key = top_match_key(item)
        site = site_by_key.pop(key, None) if key else None
        if site is not None:
            item["source"] = "both"
            item["site_video_id"] = site.get("site_video_id")
            item["site_rank"] = site.get("site_rank")
            # 同一录像以站点榜单值为准；时间榜同时同步毫秒字段
            item[metric] = site.get(metric, item.get(metric))
            if metric == "time":
                item["timems"] = site.get("timems", item.get("timems"))
                item["time"] = site.get("time", item.get("time"))
        item["source_cn"] = TOP_SOURCE_CN[item["source"]]
        merged.append(item)

    for site in site_unkeyed + list(site_by_key.values()):
        item = dict(site)
        item["source"] = "site"
        item["source_cn"] = TOP_SOURCE_CN["site"]
        merged.append(item)

    merged.sort(key=lambda r: _top_sort_key(r, metric))
    return merged[:max(1, int(limit))]


# ------------------------------------------------------------------ LSF
# 口径：openms.top/docs/zh/guide/top-lsf。18 个 top5 小榜 → 按锚点归一成
# 单项软实力 → 级别加权 6/14/20 → 普通 0.97 + NF 0.03。
LSF_TOP = 5
LSF_MISS_TIMEMS = 999999
LSF_SOFT_W = {"b": {"t": 32, "b": 48, "s": 20},
              "i": {"t": 36, "b": 36, "s": 28},
              "e": {"t": 32, "b": 32, "s": 36}}
LSF_LEVEL_W = {"b": 6, "i": 14, "e": 20}
LSF_MODE_W = {"std": 0.97, "nf": 0.03}
LSF_GRADE_NODES = (0, 1 / 100, 1 / 60, 1 / 55, 1 / 50, 1 / 45,
                   1 / 40, 1 / 35, 1 / 30, 1 / 25)
LSF_GRADE_CN = ("新手", "工兵", "小雷神", "大雷神", "小雷圣",
                "大雷圣", "小雷帝", "大雷帝", "外星人")


def anchor_value(anchors, field):
    """锚点两种形态都吃：``{field: 值}`` 与 ``{field: {"value": 值}}``。"""
    item = (anchors or {}).get(field)
    if isinstance(item, dict):
        item = item.get("value")
    try:
        return float(item) if item is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def lsf_radius(mean, anchor, stat):
    """单项软实力：Time 用 A/V，Bvs 用 V/A，Stnb 用 (V/A)^(1/1.7)。

    1 表示刚好达到该项锚点。锚点缺失时按 0 处理（站点同样不着色不排名）。
    """
    if anchor <= 0:
        return 0.0
    if stat == "t":
        return 100.0 * anchor / (mean if mean > 0 else LSF_MISS_TIMEMS)
    if stat == "s":
        return 100.0 * (mean / anchor) ** (1 / 1.7) if mean > 0 else 0.0
    return 100.0 * mean / anchor


def lsf_row_value(row, level, metric):
    """一局在 LSF 榜单里的取值：Time 毫秒、Bvs 3BV/s、Stnb 除以级别常数（iqg）。"""
    if metric == "time":
        ms = row.get("timems")
        if not ms and row.get("time"):
            try:
                ms = float(row["time"]) * 1000.0
            except (TypeError, ValueError):
                ms = 0.0
        try:
            return float(ms) if ms else 0.0
        except (TypeError, ValueError):
            return 0.0
    if metric == "bvs":
        try:
            return float(row.get("bvs") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(row.get("stnb") or 0.0) / STNB_CONST[level]
    except (TypeError, ValueError):
        return 0.0


def _lsf_mean(rows, level, metric):
    """前五均值。Time 缺项补 999999 毫秒，Bvs/Stnb 补 0，始终除以五。"""
    vals = [v for v in (lsf_row_value(r, level, metric)
                        for r in (rows or [])[:LSF_TOP]) if v > 0]
    miss = LSF_TOP - len(vals)
    if metric == "time":
        return (sum(vals) + miss * LSF_MISS_TIMEMS) / LSF_TOP
    return sum(vals) / LSF_TOP


def lsf_counts(rows, level, metric):
    """某一项实际有几局进了前五；不足的部分按最差补满，所以会压低均值。"""
    return sum(1 for r in (rows or [])[:LSF_TOP]
               if lsf_row_value(r, level, metric) > 0)


def _lsf_push(keep, seq, game, bv_min):
    """把一局塞进它该进的几个「前五」小顶堆，返回新的序号。

    序号只用来在同分时给堆一个稳定的先后，必须单调递增 —— 撞号会让
    heapq 退化到比较第三项（局本身），那是个 dict，直接 TypeError。
    """
    level = game.get("level")
    if level not in LEVELS or (game.get("bv") or 0) < bv_min.get(level, 0):
        return seq
    modes = ("std", "nf") if game.get("nf") is True else ("std",)
    for mode in modes:
        for metric in ("time", "bvs", "stnb"):
            val = game.get(metric)
            if not val:
                continue
            # 统一成「越小越好」再取前五，省掉全表排序
            score = val if metric == "time" else -val
            heap = keep.setdefault((mode, level, metric), [])
            seq += 1
            item = (-score, seq, game)
            if len(heap) < LSF_TOP:
                heapq.heappush(heap, item)
            elif score < -heap[0][0]:
                heapq.heapreplace(heap, item)
    return seq


def _lsf_heaps(games, bv_min):
    """每项维护一个「前五」小顶堆。堆顶是当前最差的一局（-score 最小）。

    返回 ``(keep, seq)``；seq 是最后用掉的序号，接着往里塞局时要往后接。
    """
    keep = {}
    seq = 0
    for game in games or ():
        seq = _lsf_push(keep, seq, game, bv_min)
    return keep, seq


def _lsf_means_of(keep):
    """18 项前五均值（顺序无关，前五这个集合定了，均值就定了）。"""
    out = {}
    for mode in ("std", "nf"):
        for level in LEVELS:
            for metric in ("time", "bvs", "stnb"):
                rows = [item[2] for item in sorted(keep.get((mode, level, metric)) or [])]
                out[(mode, level, metric)] = _lsf_mean(rows, level, metric)
    return out


def _lsf_total(keep, anchors):
    return lsf_soft(_lsf_means_of(keep), anchors)["lsf"]


def lsf_gains(games, anchors, rows, bv_min=None):
    """``rows`` 里每一局在「它打出来的那一刻」涨了多少分。

    涨分与 PB 无关：没破 PB 的局，只要挤进某一项前五，一样涨分。
    起点状态由 rows 之前的所有局决定，而前五只看集合、与顺序无关，
    所以先一遍扫出起点，再按时间顺序把 rows 一局局加进去。
    返回 ``{path: 涨分}``；算不出来（没锚点 / 没 path）就返回空。
    """
    bv_min = bv_min or BV_MIN_DEFAULT
    target = [g for g in (rows or ()) if g.get("path")]
    if not target or not anchors:
        return {}
    cut = min((g.get("mtime") or 0) for g in target)
    keep, seq = _lsf_heaps([g for g in (games or ())
                            if (g.get("mtime") or 0) < cut], bv_min)
    out = {}
    for g in sorted(target, key=lambda x: ((x.get("mtime") or 0), x.get("path") or "")):
        before = _lsf_total(keep, anchors)
        seq = _lsf_push(keep, seq, g, bv_min)
        out[g["path"]] = _lsf_total(keep, anchors) - before
    return out


def lsf_top5(games, site_top=None, site_details=None, bv_min=None):
    """18 项各自的前五局（本地 + 网站合并，按指标排好序，好的在前）。

    返回 ``{(mode, level, metric): [局, ...]}``；每行带 ``source`` 标来源。
    准入与站点一致：官方 3BV 门槛（不受页面上「BV 下限」输入框影响）。
    """
    bv_min = bv_min or BV_MIN_DEFAULT
    site_map = site_top_rows(site_top, site_details) if site_top else {}
    keep, _seq = _lsf_heaps(games, bv_min)
    tops = {}
    for mode in ("std", "nf"):
        cat = "nf" if mode == "nf" else "all"
        for level in LEVELS:
            for metric in ("time", "bvs", "stnb"):
                heap = keep.get((mode, level, metric)) or []
                local_rows = [item[2] for item in sorted(heap)]
                site_rows = [r for r in site_map.get((cat, level, metric), [])
                             if r.get("bv") is None
                             or r["bv"] >= bv_min.get(level, 0)]
                tops[(mode, level, metric)] = merge_top_rows(
                    local_rows, site_rows, metric)
    return tops


def lsf_means(games, site_top=None, site_details=None, bv_min=None):
    """18 项前五均值。Time 单位毫秒，Bvs 为 3BV/s，Stnb 为 iqg。"""
    tops = lsf_top5(games, site_top, site_details, bv_min)
    return {key: _lsf_mean(rows, key[1], key[2]) for key, rows in tops.items()}


def lsf_soft(means, anchors):
    """9 项软实力 + LSF。返回 ``{'std:b', 'std:sum', 'nf:e', 'lsf', ...}``。"""
    vals = {}
    for mode in ("std", "nf"):
        level_sum = 0.0
        for level in LEVELS:
            total = 0.0
            for stat, metric in (("t", "time"), ("b", "bvs"), ("s", "stnb")):
                anchor = anchor_value(anchors, "%s:%s%s" % (mode, level, stat))
                mean = (means or {}).get((mode, level, metric), 0.0)
                total += LSF_SOFT_W[level][stat] * lsf_radius(mean, anchor, stat) / 100.0
            vals["%s:%s" % (mode, level)] = total
            level_sum += LSF_LEVEL_W[level] * total
        vals[mode + ":sum"] = level_sum
    vals["lsf"] = (vals["std:sum"] * LSF_MODE_W["std"]
                   + vals["nf:sum"] * LSF_MODE_W["nf"])
    return vals


def lsf_et(soft, anchor_et_ms, baseline=4000.0):
    """等价 et（秒）。锚点缺失或软实力为 0 时返回 None。"""
    if not anchor_et_ms or anchor_et_ms <= 0 or not soft:
        return None
    return anchor_et_ms / (1000.0 * soft / baseline)


def lsf_grade(soft, anchor_et_ms, baseline=4000.0):
    """定阶：返回 ``(称号, 阶数)``；缺锚点返回 None。

    阶数按软实力线性插值，区间宽度取固定节点，允许超过十阶。
    """
    if not anchor_et_ms or anchor_et_ms <= 0:
        return None
    strength = soft * 1000.0 / (baseline * anchor_et_ms)
    idx = 0
    while idx < len(LSF_GRADE_NODES) - 2 and strength >= LSF_GRADE_NODES[idx + 1]:
        idx += 1
    lo, hi = LSF_GRADE_NODES[idx], LSF_GRADE_NODES[idx + 1]
    pos = max(0.0, (strength - lo) / (hi - lo))
    return LSF_GRADE_CN[idx], 10.0 * pos


def lsf_report(games, site_top=None, site_details=None, anchors=None):
    """一次算全：18 项前五（含每局明细）、9 项软实力、等价 et、定阶。

    ``games`` 传空列表就是「只按网站成绩算」，与站点显示的分数同口径；
    ``tops`` 是明细对话框的数据源，``levels`` 给每级的 (软实力, et, 定阶)。
    """
    tops = lsf_top5(games, site_top, site_details)
    means = {key: _lsf_mean(rows, key[1], key[2]) for key, rows in tops.items()}
    soft = lsf_soft(means, anchors)
    et_std = anchor_value(anchors, "std:et")
    et_nf = anchor_value(anchors, "nf:et")
    levels, nf_levels = {}, {}
    for lv in LEVELS:
        one = soft["std:" + lv]
        nf = soft["nf:" + lv]
        # 单级别以 100 为基准，综合以 4000 为基准，定阶才落在同一把尺子上
        levels[lv] = {"soft": one, "et": lsf_et(one, et_std, 100.0),
                      "grade": lsf_grade(one, et_std, 100.0)}
        nf_levels[lv] = {"soft": nf, "et": lsf_et(nf, et_nf, 100.0),
                         "grade": lsf_grade(nf, et_nf, 100.0)}
    return {
        "means": means,
        "tops": tops,
        "anchors": anchors or {},
        "soft": soft,
        "et": lsf_et(soft["lsf"], et_std),
        "grade": lsf_grade(soft["lsf"], et_std),
        "levels": levels,
        "nf_levels": nf_levels,
        "et_std": lsf_et(soft["std:sum"], et_std),
        "et_nf": lsf_et(soft["nf:sum"], et_nf),
    }


def summary(games, style=None):
    """每级汇总。"""
    out = {}
    for lv in LEVELS:
        sub = [g for g in games
               if g["level"] == lv and (style is None or g.get("style") == style)]
        if not sub:
            out[lv] = {"games": 0, "nf": 0, "fl": 0}
            continue
        best_time = min(sub, key=lambda g: g["time"])
        best_bvs = max(sub, key=lambda g: g["bvs"])
        best_stnb = max(sub, key=lambda g: g["stnb"])
        out[lv] = {
            "games": len(sub),
            "bestTime": best_time,
            "bestBvs": best_bvs,
            "bestStnb": best_stnb,
            "nf": sum(1 for g in sub if g.get("nf") is True),
            "fl": sum(1 for g in sub if g.get("nf") is False),
        }
    return out


def daily_rows(games, level=None):
    """按「打的那一天」聚合，返回按日期倒序的行。

    口径（写死，别处不要另算一遍）：
      · 一天 = 本地时区 00:00–24:00，按**开始时刻**归日。跨午夜的局算在开始
        那天（本机去重后 22723 局里只有 15 局跨午夜，占 0.07%）。
      · 起 = 当天最早一局的开始；止 = 当天最晚一局的**结束**。
      · 平均是**算术平均**，不是中位数 —— 与 Arbiter 的 Daily Review 同口径，
        否则同一批录像两边算出的数对不上，看的人只会觉得这边算错了。
      · 不按 3BV 门槛过滤：小盘刷出来的最好成绩照样进「最快 / 最高」，
        那才是「当天真实发生了什么」。
      · 只聚合传进来的 games（scan() 已滤掉非完成局、非标准玩法），这里不再筛。

    level=None 时三级混在一起；一般每次都只给一个级别 —— 三级的 BV 分布
    完全不同，混着算的「平均时间」没有意义。

    返回的每行：date / games / t_from / t_till / span / best / avg / worst /
    bvs_max / bvs_avg / bvs_min / stnb_max / stnb_avg / stnb_min / nf /
    best_path。时间值都是秒（float），t_from / t_till 是 epoch 秒，
    由界面格式化成 HH:MM:SS。stnb 越大越好（与 time 相反）。
    """
    days = {}
    for g in games:
        if level is not None and g.get("level") != level:
            continue
        t0 = g.get("t0") or g.get("mtime") or 0.0
        if t0 <= 0:
            continue
        day = day_text(t0)
        if not day:
            continue
        t = float(g.get("time") or 0.0)
        bvs = float(g.get("bvs") or 0.0)
        stnb = float(g.get("stnb") or 0.0)
        t1 = g.get("t1") or (t0 + t)
        r = days.get(day)
        if r is None:
            days[day] = {
                "date": day, "games": 1,
                "t_from": t0, "t_till": t1,
                "best": t, "worst": t, "t_sum": t,
                "bvs_max": bvs, "bvs_min": bvs, "bvs_sum": bvs,
                "stnb_max": stnb, "stnb_min": stnb, "stnb_sum": stnb,
                "nf": 1 if g.get("nf") is True else 0,
                "best_path": g.get("path") or "",
            }
            continue
        r["games"] += 1
        if t0 < r["t_from"]:
            r["t_from"] = t0
        if t1 > r["t_till"]:
            r["t_till"] = t1
        if t < r["best"]:
            r["best"] = t
            r["best_path"] = g.get("path") or ""
        if t > r["worst"]:
            r["worst"] = t
        r["t_sum"] += t
        if bvs > r["bvs_max"]:
            r["bvs_max"] = bvs
        if bvs < r["bvs_min"]:
            r["bvs_min"] = bvs
        r["bvs_sum"] += bvs
        if stnb > r["stnb_max"]:
            r["stnb_max"] = stnb
        if stnb < r["stnb_min"]:
            r["stnb_min"] = stnb
        r["stnb_sum"] += stnb
        if g.get("nf") is True:
            r["nf"] += 1

    rows = []
    for r in days.values():
        n = r["games"]
        rows.append({
            "date": r["date"],
            "games": n,
            "t_from": r["t_from"],
            "t_till": r["t_till"],
            "span": max(0.0, r["t_till"] - r["t_from"]),
            "best": r["best"],
            "avg": r["t_sum"] / n,
            "worst": r["worst"],
            "bvs_max": r["bvs_max"],
            "bvs_avg": r["bvs_sum"] / n,
            "bvs_min": r["bvs_min"],
            "stnb_max": r["stnb_max"],
            "stnb_avg": r["stnb_sum"] / n,
            "stnb_min": r["stnb_min"],
            "nf": r["nf"],
            "best_path": r["best_path"],
        })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def daily_games(games, day, level=None):
    """某一天（YYYY-MM-DD）的局，按开始时刻倒序 —— 给当天明细用。

    归日规则与 daily_rows 完全一致（按开始时刻），所以两处列出的局数必然相等。
    """
    out = []
    for g in games:
        if level is not None and g.get("level") != level:
            continue
        t0 = g.get("t0") or g.get("mtime") or 0.0
        if not t0 or day_text(t0) != day:
            continue
        item = dict(g)
        item["clock"] = clock_text(t0)
        item["t0"] = t0
        out.append(item)
    out.sort(key=lambda r: r["t0"], reverse=True)
    return out


def read_record_ini(path):
    """读元扫雷 record.ini 里 [BEGINNER]/[INTERMEDIATE]/[EXPERT] 的 PB 表。

    返回 {(level, bv): time}，用于与录像统计交叉校验。
    """
    sec_map = {"BEGINNER": "b", "INTERMEDIATE": "i", "EXPERT": "e"}
    out = {}
    cur = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("[") and line.endswith("]"):
                    cur = sec_map.get(line[1:-1].upper())
                    continue
                if cur and "=" in line:
                    k, _, v = line.partition("=")
                    try:
                        bv = int(k.strip())
                        t = float(v.strip())
                    except ValueError:
                        continue
                    if t < 999:      # 999.999 是「无记录」哨兵
                        out[(cur, bv)] = t
    except OSError:
        return {}
    return out


def cross_check(pb_rows, ini_pb):
    """与 record.ini 对账，返回差异列表。"""
    mine = {(r["level"], r["bv"]): r["time"] for r in pb_rows}
    diffs = []
    keys = set(mine) | set(ini_pb)
    for k in sorted(keys):
        a, b = mine.get(k), ini_pb.get(k)
        if a is None:
            diffs.append({"level": k[0], "bv": k[1], "mine": None, "ini": b})
        elif b is None:
            diffs.append({"level": k[0], "bv": k[1], "mine": a, "ini": None})
        elif abs(a - b) > 1e-6:
            diffs.append({"level": k[0], "bv": k[1], "mine": a, "ini": b})
    return diffs


def record_ini_stale(ini_mtime, newest_ms_ns):
    """record.ini 是否落后于最新一局录像（毫秒/纳秒都按秒口径传）。

    元扫雷**只在退出时**才把成绩写进 record.ini，所以它开着的时候这个文件必然
    比录像旧。拿它当真值去对账，会把「刚破了 PB」报成「数值冲突」。
    ini_mtime 是秒（os.path.getmtime），newest_ms_ns 是纳秒（st_mtime_ns）。
    """
    if not ini_mtime:
        return False
    return (newest_ms_ns or 0) > ini_mtime * 1e9


def judge_record_diffs(diffs, n_keys, stale=False):
    """把 cross_check 的差异分成「该报警的」与「必然的正常现象」。

    stale（record.ini 落后）时，本地更快是必然的：ini 里只留着上次退出时的成绩。
    真异常只剩两种：本地比 ini 慢（ini 记着一条我们扫不出来的更快成绩）、
    ini 有而录像里没有（录像被删或移走了）。
    """
    only_mine = sum(1 for d in diffs if d["ini"] is None)
    only_ini = sum(1 for d in diffs if d["mine"] is None)
    both = [d for d in diffs if d["mine"] is not None and d["ini"] is not None]
    faster = [d for d in both if d["mine"] < d["ini"]] if stale else []
    return {
        "only_mine": only_mine,     # 只有录像有：新 PB / 新 BV，正常
        "only_ini": only_ini,       # 只有 ini 有：录像不见了，要报
        "conflict": len(both) - len(faster),   # 数值冲突：落后时只留下「本地更慢」
        "faster": len(faster),      # 本地更快：落后时属正常，不报
        "same": n_keys - only_mine - len(both),
    }


def has_evf(path):
    try:
        with os.scandir(path) as it:
            return any(e.name.lower().endswith(".evf") for e in it if e.is_file())
    except OSError:
        return False


def peek_dir(root, max_dirs=3000):
    """只数文件、不解析内容——给「录像目录」对话框显示每个目录有几局。

    有上限：用户可能把整个 C:\\ 加进来，数文件也会卡住对话框。
    """
    out = {"ms": 0, "arb": 0, "truncated": False}
    if not root or not os.path.isdir(root):
        return out
    visited = 0
    for dirpath, dirnames, filenames in os.walk(root):
        visited += 1
        if visited > max_dirs:
            out["truncated"] = True
            break
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        for fn in filenames:
            s = video_src(fn)
            if s:
                out[s] += 1
    return out


def replay_dirs():
    """%APPDATA% / %LOCALAPPDATA% 下所有 ``Metasweeper*\\replay``（有 .evf 的）。

    元扫雷的存档目录带版本号（Metasweeper3.3.4、Metasweeper3.4 …），版本一变
    路径就变，所以这里按前缀通配而不是写死某个版本。
    """
    out = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base or not os.path.isdir(base):
            continue
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            if not name.lower().startswith("metasweeper"):
                continue
            cand = os.path.join(base, name, "replay")
            if has_evf(cand):
                out.append(cand)
    return out


def default_roots():
    """首次运行时的默认目录：**只有元扫雷的 replay**，按录像新旧排序。

    阿比特的目录（``<安装目录>\\replays``）故意不放进这里：用户很可能会把录像
    下载到阿比特的安装目录里观摩，自动收进来就会把别人的成绩当成自己的。
    阿比特目录一律由用户在「路径…」里手动添加，添加前会提醒：里面的录像会全部计入成绩统计。
    """
    cands = replay_dirs()

    def newest(d):
        try:
            return max(os.path.getmtime(os.path.join(d, e.name))
                       for e in os.scandir(d)
                       if e.is_file() and e.name.lower().endswith(".evf"))
        except (OSError, ValueError):
            return 0.0

    cands.sort(key=newest, reverse=True)
    return cands


def autodetect_root():
    """在用户漫游目录里找元扫雷的录像目录；多个版本并存时取录像最新的那个。"""
    cands = default_roots()
    return cands[0] if cands else None


def discover_root(start):
    """尝试自动认出元扫雷的录像目录；认不出返回 None。

    顺序：自身含 .evf → 自身/replay → 往上三层找 replay
    → 兜底扫 %APPDATA% / %LOCALAPPDATA% 下的 Metasweeper*\\replay。
    只认 ``.evf``：阿比特的目录不会被自动当成录像目录（见 default_roots）。
    """
    cur = os.path.abspath(start)
    for _ in range(4):
        if has_evf(cur):
            return cur
        cand = os.path.join(cur, "replay")
        if has_evf(cand):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return autodetect_root()


def find_record_ini(root):
    """record.ini 可能在录像目录里，也可能在它上一级（元扫雷安装目录）。"""
    for cand in (
        os.path.join(root, "record.ini"),
        os.path.join(os.path.dirname(os.path.normpath(root)), "record.ini"),
    ):
        if os.path.isfile(cand):
            return cand
    return None

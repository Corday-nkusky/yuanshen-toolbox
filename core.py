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
import os
from datetime import datetime

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

# 录像来源：按扩展名判定（站点也按扩展名分派解析器，见 openms 站点源码 utils/parser.py）
VIDEO_SRC = {".evf": "ms", ".avf": "arb"}
SRC_CN = {"ms": "元扫雷", "arb": "阿比特"}

STYLE_CN = {"nf": "NF", "fl": "FL"}
STYLE_NAME = {"nf": "无旗（No Flag）", "fl": "有旗（Flag）"}


def _load_lib():
    """延迟加载 ms-toollib；不可用时返回 None（统计降级为仅按文件名）。"""
    try:
        import ms_toollib as ms
    except Exception:                     # noqa: BLE001
        return None
    return ms


MSLIB = _load_lib()


def read_video(path):
    """用 ms-toollib 重演一局录像，返回权威指标；失败返回 None。

    注意：``analyse()`` 之后各 getter 读的是「当前播放位置」的累计值，而
    ``current_event_id`` 停在 0，所以直接取 ``v.rce`` 永远是 0。必须取
    ``v.events[-1].key_dynamic_params`` 才是整局累计值——这一点很容易踩坑。
    """
    if MSLIB is None:
        return None
    src = video_src(path)
    if src is None:
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
        return {
            "rce": int(kp.rce),
            "left": int(kp.left),
            "flag": int(kp.flag),
            "solved": int(kp.bbbv_solved),
            "bv": int(v.bbbv),
            "time": float(v.rtime),
            "timems": tms,
            "level": VIDEO_LEVEL.get(int(v.level)),
            "completed": bool(v.is_completed),
            "ident": ident,
            "valid": valid,
            "src": src,
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
        "tampered": [],
        "level_conflict": [],
        "dirs": set(),
        "by_src": {"ms": 0, "arb": 0},
    }

    if progress:
        progress(0)                   # 立刻亮一下：让进度条从 0 开始，而不是停在上一轮的位置

    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            info["dirs_visited"] += 1
            if max_dirs and info["dirs_visited"] > max_dirs:
                info["truncated"] = True
                break
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
            hit = False
            for fn in filenames:
                src = video_src(fn)
                if src is None:
                    continue
                hit = True
                info["files"] += 1
                # 进度每 200 个文件报一次（放在文件循环里；放目录循环里会因为
                # 「一个目录几百个文件」而几百次才跳一格，进度条看着像卡住）
                if progress and info["files"] % 200 == 0:
                    progress(info["files"])
                path = os.path.join(dirpath, fn)
                if path in seen_paths:      # 两个根目录互相覆盖（选了 replay 和它的上一级）
                    info["duplicates"] += 1
                    continue

                # 先取 stat：缓存要靠它判断录像有没有变
                try:
                    st = os.stat(path)
                    mt, size = st.st_mtime, st.st_size
                    mt_ns = st.st_mtime_ns
                except OSError:
                    mt = size = mt_ns = 0

                vid = None
                if deep:
                    vid = _cache.get(path, mt_ns, size)
                    if vid is None:
                        vid = read_video(path)
                        if vid is not None:
                            _cache.put(path, mt_ns, size, vid)

                if src == "ms":
                    rec = parse_filename(fn)
                    if rec is None:
                        info["unparsed"].append(fn)
                        continue
                    if rec["fail"]:
                        info["fail_skipped"] += 1
                        continue
                else:
                    # 阿比特：能读内容就以内容为准，读不出才退回文件名
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
                rec.update(
                    file=fn,
                    path=path,
                    src=src,
                    mtime=mt,
                    mtime_ns=mt_ns,
                    size=size,
                    date=datetime.fromtimestamp(mt).strftime("%Y-%m-%d") if mt else "",
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


def build_pb(games, style=None, split_style=False):
    """每个 (level, bv) 的最快完成局。

    style:        None=不分风格；'nf'/'fl' = 只看该风格
    split_style:  True 时把 NF 与 FL 拆成两行（官方评级就是两张独立榜，
                  否则 FL 的最好成绩会被同 BV 更快的 NF 局盖掉）
    """
    pool = games if style is None else [g for g in games if g.get("style") == style]
    best = {}
    for g in pool:
        key = (g["level"], g["bv"], g.get("style")) if split_style else (g["level"], g["bv"])
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


def summary(games, style=None):
    """每级汇总。"""
    out = {}
    for lv in LEVELS:
        sub = [g for g in games
               if g["level"] == lv and (style is None or g.get("style") == style)]
        if not sub:
            out[lv] = {"games": 0, "bvCount": 0, "nf": 0, "fl": 0}
            continue
        best_time = min(sub, key=lambda g: g["time"])
        best_bvs = max(sub, key=lambda g: g["bvs"])
        best_stnb = max(sub, key=lambda g: g["stnb"])
        bvs = [g["bv"] for g in sub]
        out[lv] = {
            "games": len(sub),
            "bvCount": len({g["bv"] for g in sub}),
            "bvMin": min(bvs),
            "bvMax": max(bvs),
            "bestTime": best_time,
            "bestBvs": best_bvs,
            "bestStnb": best_stnb,
            "nf": sum(1 for g in sub if g.get("nf") is True),
            "fl": sum(1 for g in sub if g.get("nf") is False),
        }
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
    阿比特目录一律由用户在「路径…」里手动添加，加之前会确认「全是自己打的」。
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

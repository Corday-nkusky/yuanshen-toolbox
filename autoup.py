"""自动上传的判定：哪些新局值得传，为什么值得传。

纯逻辑：不 import tkinter、不发网络请求、不读录像内容。

判定口径**不在这里重写** —— 直接调 ``openms.scope_paths()``，也就是上传页
「范围」用的那一套（对照网站 PB、前端 Top5 第五名、同值不替换、BV 门槛、
NF 榜独立）。本模块只做三件事：

  1. ``Tracker``：认出「这次扫描新出现的、并且已经写稳的」录像；
  2. ``judge()``：把新录像过滤成「值得传」的（范围 ∩ 未传 ∩ 预检通过）；
  3. ``explain()``：给每条写一句人能看懂的理由，供「最近自动上传」那张表用。

筛选只认 ``scope_paths``，``explain`` 只负责解释。两者分开的原因是：
理由算不出来时退回范围标签，绝不能因为解释不出来就漏传。

``all``（全部新增完成局）不参与任何成绩对照：它要的就是候选池里所有新局。
这条路径单独写、不走 ``scope_paths`` —— 那个函数对未知范围是「返回全部」的
兜底，靠兜底实现功能，等哪天它加了校验就会静默变成一条不传。

为什么理由要单独算：用户得知道这功能到底干了什么。
「涨分 · 中级 3BV/s 第 2 名（6.53）」比「已上传」有用得多。
"""

import math
import os
import re
import time

import core
import openms

# 按成绩筛的三个范围。'all'（全部完成局）单独走，见上面 docstring。
AUTO_SCOPES = ("top", "top_nf", "pb", "pb_nf")

# 「所有新局」：新出现的完成局一律传，不看 PB / Top5。它没有「为什么是这局」
# 可言 —— 每一局的答案都一样，所以不往备注里写东西。
ALL = "all"

# 界面上的三个开关 -> 范围集合。涨分局 = 前端 std + 前端 NF，两项绑在一起：
# 用户口径里它们是同一件事（排行榜上的显示值变好）。
GROUPS = (
    ("rank", ("top", "top_nf"), "涨分局"),
    ("pb", ("pb",), "全PB"),
    ("pb_nf", ("pb_nf",), "NF PB"),
)

SCOPE_CN = {
    "top": "涨分",
    "top_nf": "涨分·NF",
    "pb": "全PB",
    "pb_nf": "NF PB",
    "all": "所有新局",
}

METRIC_CN = {"time": "time", "bvs": "3BV/s", "stnb": "stnb"}


def scopes_of(flags):
    """三个界面开关 -> 范围元组。"""
    flags = flags or {}
    out = []
    for key, scopes, _label in GROUPS:
        if flags.get(key):
            out.extend(scopes)
    return tuple(s for s in AUTO_SCOPES if s in out)


def flags_of(scopes):
    """范围元组 -> 三个开关的布尔值（读配置用）。all 不算在三个开关里。"""
    have = set(scopes or ())
    return {key: all(s in have for s in sc) if sc else False
            for key, sc, _label in GROUPS}


def normalize_scopes(scopes):
    """范围规范化：all 单独成立（它覆盖其它），不认识的丢掉。

    丢掉未知值是必须的 —— 否则一个拼错的范围名会落进「按成绩筛」的
    那套计算里，算出来的集合还可能不空。
    """
    have = set(scopes or ())
    if ALL in have:
        return (ALL,)
    return tuple(s for s in AUTO_SCOPES if s in have)


def pool(games, include_arb=True):
    """自动上传的候选池，跟上传页的「含阿比特录像」用同一个开关。

    不勾的时候阿比特录像根本不进判定 —— 只是「不采信它的成绩」，不是删数据。
    """
    if include_arb:
        return list(games or ())
    return [g for g in (games or ()) if (g.get("src") or "ms") != "arb"]


def ready(scopes, site_pb=None, site_top=None, pb_ok=None, top_ok=None):
    """判定需要的**成绩对照数据**齐了没有；账号与录像列表不归这里管。

    涨分局缺网站 Top5 就**不能**判：``top_beats`` 在没有阈值时一律返回 True，
    会把「本地前五」全当成「新上榜」一口气传一堆。PB 两榜缺网站 PB 只是可能
    传了比网站慢的，但既然读 PB 只要一个请求，就一并要求，省额度。

    「齐」= 读成功，**不是非空**：新账号网站成绩合法为空，用真值判断会把
    自动上传永久卡死。调用方传 pb_ok / top_ok（读成功且无 err）；不传就退回
    真值判断（老调用方 / 单测）。

    全量不对照任何成绩，所以这里恒为可判。它要等的是「当前账号的录像列表
    已到齐」，那是 App 的状态（``oms_loaded`` / ``oms_synced`` / 请求代次），
    由 ``App._auto_ready_reason()`` 挡 —— 全量唯一的去重依据就是那份列表。
    """
    scopes = set(normalize_scopes(scopes))
    if ALL in scopes:
        return True
    if scopes & {"top", "top_nf"}:
        ok = bool(site_top) if top_ok is None else top_ok
        if not ok:
            return False
    if scopes & {"pb", "pb_nf"}:
        ok = bool(site_pb) if pb_ok is None else pb_ok
        if not ok:
            return False
    return True


# ---------------------------------------------------------------- 判定
def _ms(game):
    ms = game.get("timems")
    if ms is None:
        try:
            ms = int(round(float(game.get("time") or 0) * 1000))
        except (TypeError, ValueError):
            ms = 0
    return int(ms)


def uploaded_before(game, keys):
    """和站点同一套去重键（timems + bv + file_size）。"""
    try:
        k = openms.game_key(game)
    except OSError:
        return False
    return bool(k) and k in (keys or ())


def blocked(game, idents=None):
    """本地就能判定的「传了也白传」。返回原因，None 表示可以传。

    · valid == 1：ms-toollib 判定不合法，站点会冻结（state=b），占额度不进榜。
    · 标识不在账号里：站点收下但不进任何榜（state=d），要先去网站登记标识。
    idents 为空表示还没读到账号标识，此时不判断（不能凭猜测拦）。
    """
    if game.get("valid") == 1:
        return "会被冻结"
    if idents:
        ident = (game.get("ident") or "").strip()
        if ident and ident not in set(idents):
            return "标识不在账号里"
    return None


def judge(games, scopes, new_paths, site_pb=None, site_top=None):
    """新局里哪些值得传。

    new_paths: ``Tracker`` 放行的路径集合（已确认写稳的新局）。
    返回 ``[{"path", "game", "why", "brief"}]``；不在返回里的就是不传。

    ``games`` 是候选池（通常已过 ``pool()``，阿比特按用户勾选处理过）。
    全量范围就是「池里所有新局」—— 不复算一次成绩范围，也不再按 PB / Top5
    排除。模式与完成状态的过滤在扫描层，这里不重写一套。
    """
    scopes = normalize_scopes(scopes)
    if not scopes or not new_paths:
        return []

    if scopes == (ALL,):
        # 就绪由 App 保证：当前账号的录像列表已到齐，去重才有依据。
        # 理由留空：这一档「为什么传」的答案对每一局都一样（因为它是新局），
        # 写进备注就是整列重复同一句话，没有信息。
        return [{"path": g["path"], "game": g, "why": [], "brief": []}
                for g in games if g.get("path") in new_paths]

    want = set()
    for s in scopes:
        want |= openms.scope_paths(games, s, site_pb=site_pb or [],
                                   site_top=site_top or {})
    if not want:
        return []

    best = core.site_pb_best(site_pb or [])
    cut = (core.site_top_cutoffs(site_top or {})
           if set(scopes) & {"top", "top_nf"} else {})
    # 前端榜的完整排名表：explain 要写「第几名」，光有第五名阈值算不出来。
    board = (core.site_top_rows(site_top or {})
             if set(scopes) & {"top", "top_nf"} else {})

    out = []
    for g in games:
        p = g.get("path")
        if p not in new_paths or p not in want:
            continue
        pairs = _explain(g, games, scopes, best, cut, board)
        if not pairs:
            # 理论上不该发生（want 就是按同一口径算出来的）。真出现时宁可
            # 少说一句，也不能漏传 —— 用范围标签兜底。
            label = SCOPE_CN.get(scopes[0], "符合上传范围")
            pairs = [(label, label)]
        out.append({"path": p, "game": g,
                    "why": [full for _short, full in pairs],
                    "brief": [short for short, _full in pairs]})
    return out


def explain(game, games, scopes, best=None, cut=None, board=None):
    """这条局为什么值得传。返回中文短句列表，空表示解释不出来。

    board: 网站前端榜的完整排名 {(榜, 级别, 指标): [行]}，用来写「第几名」。
    只给 cut（第五名阈值）能判该不该传，但算不出具体名次。
    """
    return [full for _short, full in _explain(game, games, scopes, best, cut, board)]


def _explain(game, games, scopes, best=None, cut=None, board=None):
    """理由的长短两版：``(短写, 完整)``。

    短写进表格那一格（列宽有限，一局可能有三四条理由），完整版在表格下面的
    详情条里 —— 一行一条，写全。
    """
    best = best if best is not None else {}
    cut = cut if cut is not None else {}
    board = board if board is not None else {}
    if ALL in (scopes or ()):
        return []                       # 全量没有「为什么是这局」，见 judge
    lv = game.get("level")
    bv = int(game.get("bv") or 0)
    ms = _ms(game)
    sec = ms / 1000.0
    lv_cn = core.LEVEL_CN.get(lv, lv or "?")
    out = []

    for s in scopes:
        if s not in ("pb", "pb_nf"):
            continue
        if s == "pb_nf" and game.get("style") != "nf":
            continue                    # NF PB 榜只收 NF 局
        cat = "nf" if s == "pb_nf" else "all"
        name = "NF PB" if cat == "nf" else "全PB"
        tag = "nf pb" if cat == "nf" else "pb"
        cur = best.get((lv, bv, cat))
        if cur is None:
            out.append((tag + " 首次",
                        "%s · %s %d 首次（%.3f 秒）" % (name, lv_cn, bv, sec)))
        elif ms <= cur:
            out.append((tag + " " + _sec_delta((ms - cur) / 1000.0),
                        "%s · %s %d %.3f 秒（原 %.3f 秒）"
                        % (name, lv_cn, bv, sec, cur / 1000.0)))

    if set(scopes) & {"top", "top_nf"}:
        floor = core.BV_MIN_DEFAULT.get(lv, 0)
        for s in scopes:
            if s not in ("top", "top_nf"):
                continue
            cat = "nf" if s == "top_nf" else "all"
            style = None if cat == "all" else "nf"
            prefix = "涨分·NF" if cat == "nf" else "涨分"
            for metric in ("time", "bvs", "stnb"):
                rows = core.top5(games, lv, metric, floor, style=style)
                if not any(r.get("path") == game.get("path") for r in rows):
                    continue            # 本地前五都进不去，不算涨分
                c = cut.get((cat, lv, metric))
                if not core.top_beats(game, metric, c):
                    continue
                rank = _site_rank(board.get((cat, lv, metric), []),
                                  metric, game, ms)
                edge = cut.get((cat, lv, metric))
                short = "%s %s" % (prefix, METRIC_CN[metric])
                if rank:
                    short += " 第%d" % rank
                out.append((short,
                            _top_text(prefix, lv_cn, metric, game, ms,
                                      rank, edge)))
    return out


def _sec_delta(sec):
    """PB 快了多少秒。四舍五入后归零的不冒 -0.000 这种自相矛盾的符号。"""
    return ("%+.3fs" % sec) if abs(sec) >= 0.0005 else "0s"


# 老记录（v0.31.x 及更早写的）只存了长句，没有短写。表格那一格要的是短写，
# 而当时的数据（网站第五名、原 PB）现在未必还在，重算会算出不一样的数 ——
# 所以从存下来的长句里抠，抠不出来就原样返回，宁可长也不瞎编。
_BRIEF_TAG = {"全PB": "pb", "NF PB": "nf pb", "涨分": "涨分", "涨分·NF": "涨分·NF"}
_BRIEF_METRIC = ("3BV/s", "stnb", "time")


def briefs(why):
    """长句（可能多条，用「；」连着）→ 每条的短写。"""
    parts = [s.strip() for s in (why or "").split("；")
             if s.strip() and s.strip() != "—"]
    return [_brief_one(s) for s in parts]


def brief_text(why):
    """长句 → 一行短写（几条用「 · 」连着）。"""
    return " · ".join(briefs(why)) or "—"


def _brief_one(s):
    head, _sep, rest = s.partition(" · ")
    tag = _BRIEF_TAG.get(head)
    if tag is None:
        return s
    if tag.endswith("pb"):
        if "首次" in rest:
            return tag + " 首次"
        m = re.search(r"([\d.]+)\s*秒（原\s*([\d.]+)\s*秒）", rest)
        if not m:
            return s
        return "%s %s" % (tag, _sec_delta(float(m.group(1)) - float(m.group(2))))
    metric = next((m for m in _BRIEF_METRIC if m in rest), None)
    if metric is None:
        return s
    m = re.search(r"第\s*(\d+)\s*名", rest) or re.search(r"网站第\s*(\d+)", rest)
    return "%s %s%s" % (tag, metric, (" 第" + m.group(1)) if m else "")


def _site_rank(rows, metric, game, ms):
    """这局插进网站榜后排第几名。它已经通过了 top_beats，必然进前五。

    网站行按指标从好到坏排好（time 升序，bvs/stnb 降序）。数有几行比它更好，
    +1 就是名次。缺明细的行没有对应指标值，不算「比它好」——否则会把没成绩
    的行当成压在前面的名次，虚报一个靠后的排名。
    """
    rank = 1
    if not rows:
        return None             # 这个榜没读到，不虚报名次
    if metric == "time":
        for r in rows:
            v = r.get("timems")
            if v is not None and int(v) < ms:
                rank += 1
    else:
        val = _ms_value(game.get(metric))
        if val is None:
            return None
        for r in rows:
            v = _ms_value(r.get(metric))
            if v is not None and v > val:
                rank += 1
    return rank


def _ms_value(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _val_text(metric, v):
    """指标值按习惯写：time 带秒，3BV/s 两位、stnb 一位。"""
    if metric == "time":
        return "%.3f 秒" % (float(v) / 1000.0)
    if metric == "bvs":
        return "%.2f" % float(v)
    return "%.1f" % float(v)


def _top_text(prefix, lv_cn, metric, game, ms, rank, edge=None):
    """一条涨分理由：这局多少、插进网站榜第几、第五名（边缘）是多少。

    边缘 = 网站这个榜现在第五名的成绩，是「还能不能挤进去」的那条线。
    """
    val = _val_text(metric, {"time": ms,
                             "bvs": game.get("bvs") or 0,
                             "stnb": game.get("stnb") or 0}[metric])
    bits = []
    if rank:
        bits.append("第 %d 名" % rank)
    if edge is not None:
        bits.append("边缘 " + _val_text(metric, edge))
    tail = ("（%s）" % " · ".join(bits)) if bits else ""
    return "%s · %s %s %s%s" % (prefix, lv_cn, METRIC_CN[metric], val, tail)


# ---------------------------------------------------------------- 强无猜
# 强无猜（mode=6）在站点只有 strict_ng / strict_ng_nf 两个大榜，每榜六格
# （初/中/高各自的最快 time + 最高 3BV/s）+ 总和，没有逐 BV 的 PB 榜。
# 所以「全PB」落不了地，改成「进榜才传」：新局能刷新自己某一格的最好成绩
# （或填上空格）才传。规则与标准大榜同口径（前端 SpeedRanking 的 saolei 条）：
# 初级 time 需 3BV≥2、初级 3BV/s 需 ≥4，中级 ≥30，高级 ≥100。
NG_ALL = "all"
NG_TOP = "top"
NG_BOARDS = ("strict_ng", "strict_ng_nf")
# (级别, 指标) -> (榜内字段, 最低 3BV)
NG_SLOTS = {
    ("b", "time"): ("bt", 2), ("b", "bvs"): ("bb", 4),
    ("i", "time"): ("it", 30), ("i", "bvs"): ("ib", 30),
    ("e", "time"): ("et", 100), ("e", "bvs"): ("eb", 100),
}


def judge_ng(games, new_paths, scope, site_ng=None):
    """强无猜新局里哪些值得传。scope: 'all' 全传 / 'top' 进榜才传。

    返回 ``[{"path", "game", "why", "brief"}]``，结构与 ``judge`` 一致。
    进榜才传：新局严格优于自己该格当前最好（同值不算 —— 站点同分优先更早
    上传），或该格还空着。NF 局额外比 strict_ng_nf 榜。
    """
    if not new_paths or scope not in (NG_ALL, NG_TOP):
        return []
    games = [g for g in (games or ()) if g.get("path") in new_paths]
    if scope == NG_ALL:
        return [{"path": g["path"], "game": g,
                 "why": ["强无猜 · 新局全部上传"], "brief": ["强无猜"]}
                for g in games]
    site_ng = site_ng or {}
    out = []
    for g in games:
        pairs = _ng_explain(g, site_ng)
        if pairs:
            out.append({"path": g["path"], "game": g,
                        "why": [full for _s, full in pairs],
                        "brief": [short for short, _f in pairs]})
    return out


def _ng_explain(game, site_ng):
    """进榜才传的理由：刷新了哪一格、原成绩多少。空列表 = 不值得传。"""
    lv = game.get("level")
    bv = int(game.get("bv") or 0)
    ms = _ms(game)
    bvs = _ms_value(game.get("bvs")) or 0.0
    nf = bool(game.get("nf"))
    lv_cn = core.LEVEL_CN.get(lv, lv or "?")
    boards = ("strict_ng", "strict_ng_nf") if nf else ("strict_ng",)
    out = []
    for metric in ("time", "bvs"):
        slot = NG_SLOTS.get((lv, metric))
        if not slot:
            continue
        field, min_bv = slot
        if bv < min_bv:
            continue
        val = ms if metric == "time" else bvs
        # 普通榜优先；只有 NF 榜能进时标「强无猜·NF」
        hit_board, cur = None, None
        for board in boards:
            c = (site_ng.get(board) or {}).get(field)
            better = c is None or (val < c if metric == "time" else val > c)
            if better:
                hit_board, cur = board, c
                break
        if hit_board is None:
            continue
        prefix = "强无猜·NF" if hit_board == "strict_ng_nf" else "强无猜"
        m_cn = METRIC_CN[metric]
        v_txt = _val_text(metric, val)
        if cur is None:
            out.append(("%s %s 首次" % (prefix, m_cn),
                        "%s · %s %s 首次进榜（%s）" % (prefix, lv_cn, m_cn, v_txt)))
        else:
            out.append(("%s %s" % (prefix, m_cn),
                        "%s · %s %s %s（原 %s）"
                        % (prefix, lv_cn, m_cn, v_txt, _val_text(metric, cur))))
    return out


# ---------------------------------------------------------------- 新局识别
class Tracker:
    """记住见过哪些录像，只放行「新出现且已经写稳」的。

    两个用途：
      · 本次会话内：路径差集，精确；
      · 跨启动：水位（上次见到的最大 mtime 纳秒）。重启后把「关机期间打的局」
        补进来，但历史成绩一律不动 —— 否则第一次打开开关会一口气传几十条旧 PB。

    「写稳」= 连续两次扫描看到同样的 (size, mtime_ns)，且间隔不小于 settle。
    元扫雷写完一局的瞬间，ms-toollib 可能读到半截文件；这条规则挡的就是它。
    """

    def __init__(self, watermark=0, settle_s=1.5):
        self.watermark = max(0, int(watermark or 0))
        self.settle = max(0.0, float(settle_s))
        self._known = None          # None = 还没建立基线
        self._seen = {}             # path -> (size, mtime_ns, 首次看到的时间)

    @property
    def primed(self):
        return self._known is not None

    def prime(self, games, now=None):
        """第一次扫描：建基线。

        水位为 0（第一次启用）时全部算旧局 —— 不补传历史。水位大于 0 时，
        mtime 比它新的算「关机期间打的」，进候选池。

        这些局的首次计时直接往前推 —— 它们的文件早就写完了，不存在「还在写」，
        没必要再等一个 settle 周期（等的意义在于挡住元扫雷正在写的文件）。
        """
        now = time.monotonic() if now is None else now
        self._known, self._seen = set(), {}
        for g in games or ():
            p = g.get("path")
            if not p:
                continue
            mt = int(g.get("mtime_ns") or 0)
            if self.watermark and mt > self.watermark:
                self._seen[p] = (int(g.get("size") or 0), mt,
                                 now - self.settle - 1.0)
            else:
                self._known.add(p)
        return self

    def observe(self, games, now=None):
        """返回这一拍可以判定 / 上传的路径（新出现 + 已写稳）。

        幂等：同一条局在 ``commit()`` 之前每拍都会被再返回一次，调用方判定完
        再统一 commit。这样「网站成绩还没读到、这拍不判」不会把候选弄丢。
        """
        now = time.monotonic() if now is None else now
        games = list(games or ())
        if self._known is None:
            self.prime(games, now)
            # 建完基线继续往下走，不 return：关机期间打的局这一拍就该拿到
        ready, alive = [], set()
        for g in games:
            p = g.get("path")
            if not p:
                continue
            alive.add(p)
            if p in self._known:
                continue
            size = int(g.get("size") or 0)
            mt = int(g.get("mtime_ns") or 0)
            prev = self._seen.get(p)
            if prev is None:
                self._seen[p] = (size, mt, now)
                continue
            if prev[0] != size or prev[1] != mt:
                self._seen[p] = (size, mt, now)     # 还在写，重新计时
                continue
            if now - prev[2] >= self.settle:
                ready.append(p)
        for p in list(self._seen):        # 文件被删 / 移走：别再等它
            if p not in alive:
                self._seen.pop(p, None)
        return ready

    def commit(self, paths):
        """这一批已经处理完（传了、或确认不值得传），以后不再算新局。"""
        for p in paths or ():
            self._seen.pop(p, None)
            if self._known is not None:
                self._known.add(p)

    def requeue(self, path):
        """上传临时失败、值得再试：放回候选池。

        只把它从「见过」里拿掉，下一次 observe 会当成新局重新计时 —— 等它再稳一次
        才放行，顺手也给了重试的天然间隔。
        """
        if path and self._known is not None:
            self._known.discard(path)

    def newest(self, games=None):
        """当前见过的最大 mtime_ns，作为下次启动的水位。"""
        mt = self.watermark
        for g in games or ():
            mt = max(mt, int(g.get("mtime_ns") or 0))
        return mt

    def pending(self):
        """还在等稳定的路径（诊断显示用）。"""
        return list(self._seen)

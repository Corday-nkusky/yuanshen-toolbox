"""自动上传的判定：哪些新局值得传，为什么值得传。

纯逻辑：不 import tkinter、不发网络请求、不读录像内容。

判定口径**不在这里重写** —— 直接调 ``openms.scope_paths()``，也就是上传页
「范围」用的那一套（对照网站 PB、前端 Top5 第五名、同值不替换、BV 门槛、
NF 榜独立）。本模块只做三件事：

  1. ``Tracker``：认出「这次扫描新出现的、并且已经写稳的」录像；
  2. ``judge()``：取新录像与上传范围的交集（预检和去重由 App 负责）；
  3. ``explain()``：给每条写一句人能看懂的理由，供「最近自动上传」那张表用。

筛选只认 ``scope_paths``，``explain`` 只负责解释。两者分开的原因是：
理由算不出来时退回范围标签，绝不能因为解释不出来就漏传。
额外的 ``all`` 范围走 ``scope_paths(..., "all")``：只放行扫描层已确认的标准
完成局，不做任何 PB / Top5 对照，理由固定为「新增完成局」。

为什么理由要单独算：用户得知道这功能到底干了什么。
「涨分 · 3BV/s 6.53（网站第五 6.12）」比「已上传」有用得多。
"""

import os
import time

import core
import openms

# 三项偏好与独立的全量开关分别保存。
AUTO_SCOPES = ("top", "top_nf", "pb", "pb_nf")

ALL_SCOPE = "all"
ALL_WHY = "新增完成局"

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
    "all": ALL_WHY,
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
    """范围元组 -> 三项偏好，不包含独立的全量开关。"""
    have = set(scopes or ())
    return {key: all(s in have for s in sc) if sc else False
            for key, sc, _label in GROUPS}


def normalize_scopes(scopes):
    """全量优先；未知范围忽略，避免进入 scope_paths() 的全量兜底。"""
    have = set(scopes or ())
    if ALL_SCOPE in have:
        return (ALL_SCOPE,)
    return tuple(s for s in AUTO_SCOPES if s in have)


def pool(games, include_arb=True):
    """自动上传的候选池，跟上传页的「含阿比特录像」用同一个开关。

    不勾的时候阿比特录像根本不进判定 —— 只是「不采信它的成绩」，不是删数据。
    """
    if include_arb:
        return list(games or ())
    return [g for g in (games or ()) if (g.get("src") or "ms") != "arb"]


def ready(scopes, site_pb=None, site_top=None):
    """判定需要的**范围成绩数据**齐了没有；账号 / 录像列表不归这里管。

    涨分局缺网站 Top5 就**不能**判：``top_beats`` 在没有阈值时一律返回 True，
    会把「本地前五」全当成「新上榜」一口气传一堆。PB 两榜缺网站 PB 只是可能
    传了比网站慢的，但既然读 PB 只要一个请求，就一并要求，省额度。

    全量范围不对照 PB / Top5，这两个接口失败、空榜或还在加载都不该挡它，
    所以恒为 true；账号与网站录像列表的门控在 ``App._auto_ready_reason()``。
    """
    scopes = set(normalize_scopes(scopes))
    if ALL_SCOPE in scopes:
        return True
    if scopes & {"top", "top_nf"} and not site_top:
        return False
    if scopes & {"pb", "pb_nf"} and not site_pb:
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
    返回 ``[{"path", "game", "why": [str]}]``；不在返回里的就是不传。
    ``games`` 必须是 ``core.scan()`` 的输出：模式 / 完成状态的过滤在扫描层，
    判定层不重新实现一套文件名或录像内容检查。
    """
    scopes = normalize_scopes(scopes)
    if not scopes or not new_paths:
        return []

    if scopes == (ALL_SCOPE,):
        # 全量不计算排名；预检与去重仍由 App 负责。
        want = openms.scope_paths(games, ALL_SCOPE)
        return [{"path": g["path"], "game": g, "why": [ALL_WHY]}
                for g in games if g["path"] in new_paths and g["path"] in want]

    want = set()
    for s in scopes:
        want |= openms.scope_paths(games, s, site_pb=site_pb or [],
                                   site_top=site_top or {})
    if not want:
        return []

    best = core.site_pb_best(site_pb or [])
    cut = (core.site_top_cutoffs(site_top or {})
           if set(scopes) & {"top", "top_nf"} else {})

    out = []
    for g in games:
        p = g.get("path")
        if p not in new_paths or p not in want:
            continue
        reasons = explain(g, games, scopes, best, cut)
        if not reasons:
            # 理论上不该发生（want 就是按同一口径算出来的）。真出现时宁可
            # 少说一句，也不能漏传 —— 用范围标签兜底。
            reasons = [SCOPE_CN.get(scopes[0], "符合上传范围")]
        out.append({"path": p, "game": g, "why": reasons})
    return out


def explain(game, games, scopes, best=None, cut=None):
    """这条局为什么值得传。返回中文短句列表，空表示解释不出来。"""
    if ALL_SCOPE in (scopes or ()):
        return [ALL_WHY]
    best = best if best is not None else {}
    cut = cut if cut is not None else {}
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
        cur = best.get((lv, bv, cat))
        if cur is None:
            out.append("%s · %s %d 首次（%.3f 秒）" % (name, lv_cn, bv, sec))
        elif ms <= cur:
            out.append("%s · %s %d %.3f 秒（原 %.3f 秒）"
                       % (name, lv_cn, bv, sec, cur / 1000.0))

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
                out.append(_top_text(prefix, lv_cn, metric, game, ms, c))
    return out


def _top_text(prefix, lv_cn, metric, game, ms, cutoff):
    if metric == "time":
        val = "%.3f 秒" % (ms / 1000.0)
        old = ("网站第五 %.3f 秒" % (cutoff / 1000.0)) if cutoff else "新上榜"
    elif metric == "bvs":
        val = "%.2f" % float(game.get("bvs") or 0)
        old = ("网站第五 %.2f" % cutoff) if cutoff else "新上榜"
    else:
        val = "%.1f" % float(game.get("stnb") or 0)
        old = ("网站第五 %.1f" % cutoff) if cutoff else "新上榜"
    return "%s · %s %s %s（%s）" % (prefix, lv_cn, METRIC_CN[metric], val, old)


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

"""把一天（或今天）的记录画成可以直接发出去的图。

为什么单独一个文件：这段逻辑「每日」页的右键菜单和「概览」页的今日总结
按钮都要用，两处各写一遍出图，迟早出现一张卡是 37 局、另一张是 36 局这种事。

口径：总结卡全部来自 core.daily_rows()（一天 = 本地 00:00–24:00，按开始时刻
归日；平均是算术平均）。详细版是复盘卡，围绕 time / 3BV/s / stnb 三指标，
对照窗口 = 近 WINDOW_DAYS 个扫雷日（见 levels_detail_png 与它上面的纯函数）：
参考线 = 窗口内同 BV 中位，PB 线 = 今天之前同 BV 全期最快，状态表整表 ≥门槛 BV，
PB / BV 纪录 = 全期口径。
这里只排版与算这些口径，不碰扫描与缓存。

图片为什么用 PIL 画而不是截窗口：截图带窗口边框、跟着 DPI 走、窗口被挡住
就废；而且发到群里要的是干净的一张卡片。字体用程序自带的 MiSans（与界面
同一套），别人机器上也不会掉字。
"""

import ctypes
import io
import os
import sys
from ctypes import wintypes
from datetime import datetime

import core

# 与 app.LEVEL_ACCENT 一致（test_summary 会比对，改一处忘另一处会红）。
LEVEL_ACCENT = {
    "b": ("#ff6f9c", "#ffe4ee"),
    "i": ("#3fa9dd", "#e0f3fd"),
    "e": ("#8f6ce8", "#ebe4fd"),
}

PAGE_BG = "#faf3fa"
CARD_BG = "#ffffff"
C_INK = "#3c3352"
C_DIM = "#9b8aae"
C_LINE = "#f2e2ef"
C_RULE = "#f6ecf3"
C_PANEL = "#fcf8fb"

SCALE = 2          # 2 倍像素渲染：发到手机上放大也不糊


# ------------------------------------------------- 时间与完成方式的小工具
def _clock(t):
    return core.clock_text(t)


def _meta_text(t_from, t_till, fl=None, nf=None, brief=False):
    """卡片上那行小字：起止 + FL / NF 各多少局。

    只有这三样。不再顺手算一句「跨度 12 小时 48 分」—— 看的人要的是区间
    本身，不是我们再替他做的一位数减法（说过一次，别再往这里加回去）。
    FL（插旗）与 NF（无旗）互斥，但加起来不一定等于总局数：判不出完成方式
    的局（老阿比特的 avf 没有元数据）两边都不算，那种日子这半截整段不写。

    brief=True 用多级别卡里的小块：时分就够，秒在那儿太挤。
    """
    fmt = (lambda t: _clock(t)[:5]) if brief else _clock
    out = "%s – %s" % (fmt(t_from), fmt(t_till))
    if fl or nf:
        out += " · FL %d · NF %d" % (int(fl or 0), int(nf or 0))
    return out


def _today_tag(day):
    """真是今天才写「今日」；拿别的日子作图时别硬叫今日。"""
    return " 今日" if day == datetime.now().strftime("%Y-%m-%d") else ""


# --------------------------------------------------------------- 图片部分
def _font_dirs():
    """与 app.font_dirs() 同一套顺序：打包后先看 _MEIPASS，再看 exe 旁边。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(getattr(sys, "_MEIPASS", here), "fonts"),
             os.path.join(here, "fonts")]
    if getattr(sys, "frozen", False):
        cands.append(os.path.join(os.path.dirname(sys.executable), "fonts"))
    out = []
    for c in cands:
        if c not in out and os.path.isdir(c):
            out.append(c)
    return out


_FONT_CACHE = {}


def _font(size, bold=False):
    """MiSans（随程序打包）；找不到就退到系统雅黑，再不行用 PIL 自带的。"""
    from PIL import ImageFont
    key = (size, bold)
    hit = _FONT_CACHE.get(key)
    if hit is not None:
        return hit
    name = "MiSans-Demibold.ttf" if bold else "MiSans-Regular.ttf"
    path = ""
    for d in _font_dirs():
        p = os.path.join(d, name)
        if os.path.isfile(p):
            path = p
            break
    if not path:
        for p in (r"C:\Windows\Fonts\msyhbd.ttc" if bold
                  else r"C:\Windows\Fonts\msyh.ttc",
                  r"C:\Windows\Fonts\simhei.ttf"):
            if os.path.isfile(p):
                path = p
                break
    try:
        f = (ImageFont.truetype(path, int(round(size * SCALE))) if path
             else ImageFont.load_default())
    except OSError:
        f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _px(v):
    return int(round(v * SCALE))


# ---------------------------------------------------- 卡片下方的角色贴纸
# 贴纸必须「无缝」：白底图直接贴会在浅粉页底上留一个白方块，所以素材入库时
# 底色归一成纯白，绘制时按乘性混合（multiply）贴 —— 白的部分正好变成页底色，
# 浅色花瓣也不会被抠出毛边。
MASCOT = "mascot.png"
MASCOT_GAP = 16            # 卡片与贴纸之间的空隙（逻辑像素）
MASCOT_MAX = 490           # 上限：素材原生高度的一半（逻辑像素），再高就是放大，会糊
_MASCOT_ASPECT = None          # 素材原生宽高比，第一次用到时从文件量
_MASCOT_CACHE = {}


def _mascot_dirs():
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [getattr(sys, "_MEIPASS", here), here]
    if getattr(sys, "frozen", False):
        cands.append(os.path.dirname(sys.executable))
    return cands


def mascot(height_px):
    """按设备像素高度取贴纸；找不到图或读不了就返回 None（图照样能出）。"""
    height_px = int(height_px)
    if height_px <= 0:
        return None
    hit = _MASCOT_CACHE.get(height_px)
    if hit is not None:
        return hit or None
    path = ""
    for d in _mascot_dirs():
        p = os.path.join(d, MASCOT)
        if os.path.isfile(p):
            path = p
            break
    img = None
    if path:
        try:
            from PIL import Image
            src = Image.open(path).convert("RGB")
            w = max(1, int(round(src.width * height_px / float(src.height))))
            img = src.resize((w, height_px), Image.LANCZOS)
        except Exception:                     # noqa: BLE001
            img = None
    _MASCOT_CACHE[height_px] = img or False
    return img


def _mascot_aspect():
    """素材的宽高比（从文件量，不写死：换图时不用改代码）。"""
    global _MASCOT_ASPECT
    if _MASCOT_ASPECT is None:
        img = mascot(_px(MASCOT_MAX))
        _MASCOT_ASPECT = (img.width / float(img.height)) if img is not None else 1.44
    return _MASCOT_ASPECT


def _mascot_h(card_w):
    """贴纸高度（设备像素）：宽度跟着卡片走。

    贴在卡片下方时，贴纸是一个「人物横幅」，宽度撑到卡片宽才不显得像个小
    图标；但素材只有 988 px 高，超过原生高度就是放大，会糊，所以高度封顶，
    这时贴纸会比卡片窄、居中站。
    """
    aspect = _mascot_aspect()
    h = min(card_w / float(aspect), _px(MASCOT_MAX))
    return int(max(1, round(h)))


class _Sheet:
    """先在逻辑坐标里排版，最后一次性按 2 倍像素画出来。

    ImageDraw 的圆角矩形与中文都靠 FreeType，字号必须是像素值，所以所有
    坐标在这里乘 SCALE —— 画图代码里就不必到处写 * 2。

    版式是**竖的**：白卡片在上、角色贴纸在下方居中。做成横图（贴纸站右侧）
    时整张 2.5:1，发到手机竖屏上只占中间一条，字得点开放大才看得清；竖图
    的宽度就等于卡片宽，1080 宽的屏上一比一显示，字号才是画出来的那么大。

    排版坐标是相对「白卡片」的，外圈留 MARGIN 的底色：发到群里时白卡片
    自带一圈边，贴在深色背景上也不会糊成一整块。
    """

    MARGIN = 14

    def __init__(self, width, with_mascot=True):
        self.W = width
        self.ops = []
        self.with_mascot = with_mascot

    def text(self, x, y, s, size, color, bold=False, anchor="lt"):
        self.ops.append(("t", x, y, s, _font(size, bold), color, anchor))

    def rect(self, x, y, w, h, color, radius=0):
        self.ops.append(("r", x, y, w, h, color, radius))

    def line(self, x1, y1, x2, y2, color, width=1):
        self.ops.append(("l", x1, y1, x2, y2, color, width))

    def poly(self, points, color, width=1.2):
        """折线。joint="curve" 才不会有折角处的缺口（走势图很密）。"""
        self.ops.append(("p", list(points), color, width))

    def dot(self, x, y, r, color):
        self.ops.append(("e", x, y, r, color))

    def text_w(self, s, size, bold=False):
        return _font(size, bold).getlength(s) / float(SCALE)

    def render(self, height):
        from PIL import Image, ImageChops, ImageDraw
        m = _px(self.MARGIN)
        card_w = _px(self.W)
        card_h = _px(height)
        mk = mascot(_mascot_h(card_w)) if self.with_mascot else None
        # 贴纸底边直接贴住图片下沿：人物是从画面底下冒出来的，底边再留一圈
        # 粉底会像一张被裁掉脚的立牌，越看越别扭。没有贴纸时照常四面留边。
        tail = (_px(MASCOT_GAP) + mk.height) if mk is not None else m
        width = m + card_w + m
        img = Image.new("RGB", (width, m + card_h + tail), PAGE_BG)
        if mk is not None:
            # 乘性混合：贴纸的纯白底乘上页底色还是页底色，等于白底被「去掉」
            page = Image.new("RGB", mk.size, PAGE_BG)
            img.paste(ImageChops.multiply(mk, page),
                      (m + (card_w - mk.width) // 2, m + card_h + _px(MASCOT_GAP)))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((m - 1, m - 1, m + card_w, m + card_h),
                            radius=_px(18), fill=CARD_BG, outline=C_LINE, width=1)
        off = m                 # 所有排版坐标都是相对卡片左上角的
        for op in self.ops:
            if op[0] == "t":
                _k, x, y, s, f, color, anchor = op
                d.text((_px(x) + off, _px(y) + off), s, font=f, fill=color,
                       anchor=anchor)
            elif op[0] == "r":
                _k, x, y, w, h, color, radius = op
                box = (_px(x) + off, _px(y) + off,
                       _px(x + w) + off - 1, _px(y + h) + off - 1)
                if radius:
                    d.rounded_rectangle(box, radius=_px(radius), fill=color)
                else:
                    d.rectangle(box, fill=color)
            elif op[0] == "p":
                _k, pts, color, width = op
                d.line([(_px(a) + off, _px(b) + off) for a, b in pts],
                       fill=color, width=max(1, _px(width)), joint="curve")
            elif op[0] == "e":
                _k, cx, cy, r, color = op
                d.ellipse((_px(cx - r) + off, _px(cy - r) + off,
                           _px(cx + r) + off, _px(cy + r) + off), fill=color)
            else:
                _k, x1, y1, x2, y2, color, width = op
                d.line((_px(x1) + off, _px(y1) + off,
                        _px(x2) + off, _px(y2) + off),
                       fill=color, width=max(1, _px(width)))
        return img



def _chip(sh, x, y, text, accent, size=12, padh=9, h=22):
    """级别小标签：填充级别色 + 白字。返回占用的宽度。"""
    w = sh.text_w(text, size, True) + padh * 2
    sh.rect(x, y, w, h, accent, radius=h / 2.0)
    sh.text(x + w / 2.0, y + h / 2.0 - 1, text, size, "#ffffff", True, anchor="mm")
    return w


def _footer(sh, y, ver, now=None):
    now = now or datetime.now()
    left = "YuanshenToolbox v%s" % ver if ver else "YuanshenToolbox"
    sh.text(PAD, y, left, 10, C_DIM)
    sh.text(sh.W - PAD, y, now.strftime("%Y-%m-%d %H:%M 生成"), 10, C_DIM,
            anchor="rt")


PAD = 30

# 版式尺寸集中在这里，改比例只动这几行。
# 版式是竖的：白卡片在上、角色贴纸在下面居中。卡片宽度就是图片宽度，所以
# 这个数直接决定手机上的缩放比 —— 1080 宽的屏，卡片做到 540（2 倍渲染 →
# 1136 px）正好一比一，字号是画出来的那么大；做成横图（贴纸站右侧）整张
# 2.5:1，在竖屏上只占中间一条，字全被缩小了。
# 卡片宽度**算出来**，不要手写：数字是右对齐在每列右边界 -8 处，手写宽度
# 一旦与列宽脱节，最右那一列就会顶出卡片。
D_LAB_W, D_COL_W, D_ROW_H = 96, 128, 50
D_W = PAD * 2 + D_LAB_W + D_COL_W * 3


def daily_png(row, level_key, level_cn, ver="", now=None, with_mascot=True,
              tag=""):
    """一天的卡片：标题 + 起止/FL·NF + 三行指标表，下面站个角色。"""
    accent = LEVEL_ACCENT.get(level_key, (C_INK, CARD_BG))[0]
    W = D_W
    sh = _Sheet(W, with_mascot=with_mascot)
    y = 32
    title = row["date"] + tag
    sh.text(PAD, y, title, 26, C_INK, bold=True)
    tw = sh.text_w(title, 26, True)
    _chip(sh, PAD + tw + 12, y + 5, level_cn, accent, size=13, padh=10, h=24)
    sh.text(W - PAD, y + 5, "%d 局" % row["games"], 18, C_INK, True, anchor="rt")

    sh.text(PAD, y + 54,
            _meta_text(row["t_from"], row["t_till"],
                       row.get("fl"), row.get("nf")), 14, C_DIM)

    # 指标表：指标名 + 三列数字（最好 / 平均 / 最差）。三行指标的「好」
    # 方向不同（时间越小越好、3BV/s 与 stnb 越大越好），所以表头只能
    # 用「最好 / 平均 / 最差」这种与方向无关的说法。
    top = y + 54 + 40
    lab_w, col_w = D_LAB_W, D_COL_W
    x0 = PAD + lab_w
    heads = ("最好", "平均", "最差")
    for i, h in enumerate(heads):
        sh.text(x0 + col_w * (i + 1) - 8, top, h, 13, C_DIM, anchor="rt")
    sh.line(PAD, top + 26, W - PAD, top + 26, C_LINE)

    rows = (("时间（秒）", (row["best"], row["avg"], row["worst"]), 3),
            ("3BV/s", (row["bvs_max"], row["bvs_avg"], row["bvs_min"]), 3),
            ("stnb", (row["stnb_max"], row["stnb_avg"], row["stnb_min"]), 2))
    row_h = D_ROW_H
    for j, (label, vals, prec) in enumerate(rows):
        ry = top + 38 + row_h * j
        sh.text(PAD, ry, label, 16, C_INK)
        for i, v in enumerate(vals):
            sh.text(x0 + col_w * (i + 1) - 8, ry, "%.*f" % (prec, v), 18,
                    C_INK, True, anchor="rt")
        if j < len(rows) - 1:
            sh.line(PAD, ry + row_h - 14, W - PAD, ry + row_h - 14, C_RULE)
    bottom = top + 38 + row_h * len(rows) - 14

    fy = bottom + 28
    _footer(sh, fy, ver, now)
    return sh.render(fy + 30)


def levels_png(items, ver="", day="", now=None, with_mascot=True):
    """某一天有局的几个级别，一个级别一块往下排。

    以前是「级别 × 九列」的横表，一行一个级别：那种表只有横屏才看得下去，
    竖屏手机上字缩到看不清。三个块上下排，列还是那三列（最好 / 平均 / 最差），
    只是每块自带级别标题，竖着读就行。
    不出一行「合计」：三个级别的 BV 分布不同，把它们的平均时间再平均一次
    没有任何意义（合计局数放在标题右边就够了）。
    """
    items = [(k, cn, r) for k, cn, r in items if r]
    if not items:
        return None
    day = day or items[0][2]["date"]
    if len(items) == 1:
        # 只有一级时矩阵退化成一行，直接套单日卡版式：还能多给出起止与 FL/NF。
        k, cn, r = items[0]
        return daily_png(r, k, cn, ver, now, with_mascot,
                         tag=_today_tag(day))
    total = sum(r["games"] for _k, _cn, r in items)
    W = D_W
    sh = _Sheet(W, with_mascot=with_mascot)
    y = 32
    sh.text(PAD, y, "%s%s" % (day, _today_tag(day)), 26, C_INK, bold=True)
    sh.text(W - PAD, y + 5, "共 %d 局" % total, 18, C_INK, True, anchor="rt")

    # 列标题只写一次，三个块共用同一组列坐标（数字右对齐到同一批 x）
    top = y + 54
    lab_w, col_w = D_LAB_W, D_COL_W
    x0 = PAD + lab_w
    for i, h in enumerate(("最好", "平均", "最差")):
        sh.text(x0 + col_w * (i + 1) - 8, top, h, 13, C_DIM, anchor="rt")
    sh.line(PAD, top + 26, W - PAD, top + 26, C_LINE)

    row_h = D_ROW_H
    by = top + 26 + 18
    for j, (k, cn, r) in enumerate(items):
        accent = LEVEL_ACCENT.get(k, (C_INK, CARD_BG))[0]
        w = _chip(sh, PAD, by, cn, accent, size=13, padh=10, h=24)
        info = "%d 局 · %s" % (r["games"], _meta_text(
            r["t_from"], r["t_till"], r.get("fl"), r.get("nf"), brief=True))
        sh.text(PAD + w + 10, by + 5, info, 12, C_DIM)
        ry = by + 34
        for label, vals, prec in (
                ("时间（秒）", (r["best"], r["avg"], r["worst"]), 3),
                ("3BV/s", (r["bvs_max"], r["bvs_avg"], r["bvs_min"]), 3),
                ("stnb", (r["stnb_max"], r["stnb_avg"], r["stnb_min"]), 2)):
            sh.text(PAD, ry, label, 16, C_INK)
            for i, v in enumerate(vals):
                sh.text(x0 + col_w * (i + 1) - 8, ry, "%.*f" % (prec, v), 18,
                        C_INK, i == 1, anchor="rt")
            ry += row_h
        by = ry + 2
        if j < len(items) - 1:
            sh.line(PAD, by, W - PAD, by, C_RULE)
            by += 18

    fy = by + 12
    _footer(sh, fy, ver, now)
    return sh.render(fy + 30)



def _median(vals):
    """中位数；空列表返回 None（调用方写「—」，不写 0）。

    不过滤 0：详细版会把「相对平时的倍数」送进来，0（正好等于平时）是合法值。
    """
    vals = sorted(v for v in vals if v is not None)
    n = len(vals)
    if not n:
        return None
    if n % 2:
        return vals[n // 2]
    return (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _by_time(games):
    """按开始时刻**升序**。

    core.daily_games 给的是倒序（新的在前，界面上要这样列），但走势图必须从
    第一局画到最后一局，直接拿倒序画就成了「时间反着流」。
    """
    return sorted(games, key=lambda g: g.get("t0") or 0)


# ------------------------------------------------- 详细版的复盘口径（纯函数）
# 所有「快 / 慢」判断都除过盘面：用时只同 BV 可比，3BV 是运气不是实力。
# 参考线 = 窗口内同 BV 的中位用时（平时节奏）；PB / 纪录 = 同 BV 全期最快。
# bvs / stnb 进状态表（整表 ≥门槛），平均受盘面组合影响，靠盘运行一起读。
def _pb_curve(games, min_bv=None):
    """每个 BV 的全期最快。返回 {bv: (time, game)}。"""
    out = {}
    for g in games:
        bv = int(g.get("bv") or 0)
        if min_bv and bv < min_bv:
            continue
        cur = out.get(bv)
        if cur is None or g["time"] < cur[0]:
            out[bv] = (g["time"], g)
    return out


def _med_curve(games, win=1):
    """每个 BV 的中位用时，再按 ±win 个 BV 做一次中位平滑。

    参考线用中位不用 PB：PB 是各盘面各自的最快，相邻 BV 之间没有连续性，
    连起来是一排锯齿；中位是「这个盘面我平时打多久」，线上方慢、线下方快。
    """
    bucket = {}
    for g in games:
        bucket.setdefault(int(g.get("bv") or 0), []).append(float(g["time"]))
    med = dict((bv, _median(ts)) for bv, ts in bucket.items())
    out = {}
    for bv in med:
        near = [med[b] for b in range(bv - win, bv + win + 1) if b in med]
        out[bv] = _median(near)
    return out


def _record_bvs(games, pb_ex, all_games, min_n=3):
    """今天刷新 BV 纪录的具体局，返回 {id(game)}（该 BV 累计至少 min_n 局）。

    pb_ex 只含今天之前的局：平历史最快不算刷新，第一次打这个 BV 也不算。
    不设门槛的话「第一次打这个 BV」也会被叫作纪录。同 BV 有多局时只返回
    当天最快的那局；调用方必须按局匹配，不能把同 BV 的其他局也标成纪录。
    """
    cnt = {}
    for g in all_games:
        bv = int(g.get("bv") or 0)
        cnt[bv] = cnt.get(bv, 0) + 1
    best = {}
    for g in games:
        bv = int(g.get("bv") or 0)
        t = float(g["time"])
        if bv not in best or t < best[bv][0]:
            best[bv] = (t, g)
    hit = set()
    for bv, (t, g) in best.items():
        if cnt.get(bv, 0) < min_n:
            continue
        cur = pb_ex.get(bv)
        if cur and t < cur[0] - 1e-9:
            hit.add(id(g))
    return hit


def _vs_usual(g, med):
    """这一局相对平时快慢（-0.07 = 比平时快 7%）；没有参考线返回 None。"""
    ref = med.get(int(g.get("bv") or 0))
    if not ref or not g.get("time"):
        return None
    return g["time"] / ref - 1.0


def _typical(games, med):
    """中位局相对平时的快慢：对极值局不敏感，代表「典型一局」的状态。"""
    return _median([_vs_usual(g, med) for g in games])


# 对照窗口 = 近 WINDOW_DAYS 个扫雷日（该级别有完成局的日子）。进步曲线上
# 和很久以前比平均没有意义；自然日 30 天会把停玩期也算进「平时」。
WINDOW_DAYS = 7


def _play_window(hist_ex, k=WINDOW_DAYS):
    """今天之前最近 k 个扫雷日的局（hist_ex 已按日期截断在今天之前）。"""
    days = sorted({core.day_text(g.get("t0") or 0) for g in hist_ex},
                  reverse=True)[:k]
    keep = set(days)
    return [g for g in hist_ex if core.day_text(g.get("t0") or 0) in keep]


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _metric(g, key):
    """玩家语言的三指标：time / bvs（3BV/s）/ stnb。"""
    if key == "time":
        return float(g["time"])
    if key == "bvs":
        return g["bv"] / float(g["time"])
    return g.get("stnb")


def _best(pool, key):
    vals = [_metric(g, key) for g in pool]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return min(vals) if key == "time" else max(vals)


# ------------------------------------------------------- 详细版的绘图小元件
def _mix(c1, c2, t):
    """把 c1 往 c2 混 t（0~1），做散点的淡色。"""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * t))
                                   for i in range(3))


def _dash_poly(sh, pts, color, width=1.0, dash=5.0, gap=3.5):
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        dx, dy = x2 - x1, y2 - y1
        L = (dx * dx + dy * dy) ** 0.5
        if L < 0.01:
            continue
        k = 0.0
        while k < L:
            a = k / L
            b = min(1.0, (k + dash) / L)
            sh.line(x1 + dx * a, y1 + dy * a, x1 + dx * b, y1 + dy * b,
                    color, width)
            k += dash + gap


def _scatter(sh, x, y, w, h, games, ref, accent, recs, pb_ref=None):
    """BV × 用时散点 + 平时中位参考线 + PB 折线；新 BV 纪录画成空心环。

    pb_ref 只含今天之前的局：新纪录点落在 PB 线下方，破墙看得见。
    """
    pts = [(int(g["bv"]), float(g["time"])) for g in games if g.get("bv")]
    if len(pts) < 3:
        return False
    bx = [p[0] for p in pts]
    x0, x1 = min(bx), max(bx)
    if x1 - x0 < 1:
        x1 = x0 + 1
    pad = max(1, int(round((x1 - x0) * 0.05)))
    x0, x1 = x0 - pad, x1 + pad
    y1 = max(p[1] for p in pts)
    y0 = min(p[1] for p in pts)
    pin = [(b, v) for b, v in ref.items() if x0 <= b <= x1]
    if pin:
        y0 = min(y0, min(v for _b, v in pin))
    ppb = ([(b, t) for b, (t, _g) in pb_ref.items() if x0 <= b <= x1]
           if pb_ref else [])
    if ppb:
        y0 = min(y0, min(t for _b, t in ppb))
    y0 = y0 * 0.94
    y1 = y1 * 1.03
    if y1 - y0 < 0.05:
        y1 = y0 + 0.05

    def fx(bv):
        return x + (bv - x0) / float(x1 - x0) * w

    def fy(t):
        return y + h - (t - y0) / float(y1 - y0) * h

    for i in (0, 1, 2):
        yy = y + h * i / 2.0
        sh.line(x, yy, x + w, yy, C_RULE if i else C_LINE)
    # 纵轴数字放在绘图区外的留白里，点上的标注永远不会压到它
    sh.text(x - 6, y - 1, "%.2f s" % y1, 9.5, C_DIM, anchor="rt")
    sh.text(x - 6, y + h - 11, "%.2f s" % y0, 9.5, C_DIM, anchor="rt")
    sh.text(x, y + h + 5, "%d BV" % x0, 9.5, C_DIM)
    sh.text(x + w, y + h + 5, "%d BV" % x1, 9.5, C_DIM, anchor="rt")
    if ppb:
        pb_pts = sorted((fx(b), fy(t)) for b, t in ppb)
        pbc = _mix(accent, CARD_BG, 0.7)
        for a, b in zip(pb_pts, pb_pts[1:]):
            sh.line(a[0], a[1], b[0], b[1], pbc, 1.2)
    if pin:
        _dash_poly(sh, sorted((fx(b), fy(t)) for b, t in pin), C_DIM, 1.0)
    soft = _mix(accent, CARD_BG, 0.45)
    for g in games:
        if not g.get("bv"):
            continue
        bv, t = int(g["bv"]), float(g["time"])
        if id(g) in recs:
            sh.dot(fx(bv), fy(t), 3.4, accent)
            sh.dot(fx(bv), fy(t), 1.9, CARD_BG)
        else:
            sh.dot(fx(bv), fy(t), 2.0, soft)
    best = min(games, key=lambda g: g["time"])
    bp = (fx(int(best["bv"])), fy(float(best["time"])))
    sh.dot(bp[0], bp[1], 3.2, accent)
    lab = "%.3f s" % best["time"]
    if bp[0] > x + w - 78:
        sh.text(bp[0] - 7, bp[1] - 14, lab, 9.5, accent, True, anchor="rb")
    elif bp[1] < y + 18:
        sh.text(bp[0] + 7, bp[1] + 13, lab, 9.5, accent, True, anchor="lb")
    else:
        sh.text(bp[0] + 7, bp[1] - 14, lab, 9.5, accent, True, anchor="lb")
    return True


def _rows_fast(sh, x, y, w, games, accent, recs=(), n=5):
    """最快 n 局：用时 · 3BV · 新纪录标 · 完成方式。返回写了几行。"""
    pool = sorted([g for g in games if g.get("time")],
                  key=lambda g: g["time"])[:n]
    for i, g in enumerate(pool):
        ry = y + i * 21
        sh.text(x, ry, "%.3f" % g["time"], 12, C_INK, True)
        sh.text(x + 58, ry + 1, "%d BV" % int(g.get("bv") or 0), 10.5, C_DIM)
        if id(g) in recs:
            sh.text(x + 104, ry + 1, "新纪录", 9.5, accent, True)
        st = core.STYLE_CN.get(g.get("style"))
        if st:
            sh.text(x + w, ry + 1, st, 10.5, accent, True, anchor="rt")
    return len(pool)


def _highlights(sh, x, y, pool, accent):
    """今日 bvs / stnb 最佳局：指标大字 + 用时 · BV · 钟点。慢局不列 ——
    盘面难度不是 BV 唯一决定的，数据差的局常常是数据反映不了的问题。"""
    ry = y
    for key, lab, fmt in (("bvs", "bvs 最佳", "%.2f /s"),
                          ("stnb", "stnb 最佳", "%.2f")):
        g = max(pool, key=lambda x: _metric(x, key) or 0)
        sh.text(x, ry, lab, 10.5, C_DIM)
        sh.text(x + 60, ry, fmt % (_metric(g, key) or 0), 13, accent, True)
        sh.text(x, ry + 17, "%.3f s · %d BV · %s"
                % (g["time"], int(g["bv"]),
                   core.clock_text(g.get("t0") or 0)[:5]), 10.5, C_DIM)
        ry += 42
    return 84


def _rows_all(sh, x, y, w, games, accent, med, n=14):
    """逐局列出（局少时用）：时刻 · 用时 · 3BV · 相对平时 · 完成方式。"""
    seq = _by_time(games)[:n]
    for i, g in enumerate(seq):
        ry = y + i * 21
        sh.text(x, ry, core.clock_text(g.get("t0") or 0)[:5], 11, C_DIM)
        sh.text(x + 52, ry, "%.3f s" % g["time"], 12, C_INK, True)
        sh.text(x + 130, ry + 1, "%d BV" % int(g.get("bv") or 0), 10.5, C_DIM)
        d = _vs_usual(g, med)
        if d is not None:
            sh.text(x + 185, ry + 1, "%+.0f%%" % (d * 100), 10.5,
                    accent if d < 0 else C_DIM, d < 0)
        st = core.STYLE_CN.get(g.get("style"))
        if st:
            sh.text(x + w, ry + 1, st, 10.5, accent, True, anchor="rt")
    return len(seq)


def _segments(sh, x, y, w, games, accent, med, seg=4):
    """按局数均分切段（不按钟点：深夜刷局和白天散打切出来没法看）。

    段间比「中位局相对平时」：段与段的 BV 组合不同，直接比时间不公平。
    """
    seq = _by_time(games)
    if len(seq) < seg * 3:
        return 0
    per = len(seq) // seg
    rows = []
    for i in range(seg):
        part = seq[i * per:(i + 1) * per] if i < seg - 1 else seq[i * per:]
        ds = [_vs_usual(g, med) for g in part]
        d = _median([x for x in ds if x is not None])
        if d is None:
            continue
        rows.append((core.clock_text(part[0]["t0"])[:5], len(part), d))
    for i, (clk, n, d) in enumerate(rows):
        ry = y + i * 21
        sh.text(x, ry, "%s 起" % clk, 10.5, C_DIM)
        sh.text(x + 80, ry, "%d 局" % n, 11, C_INK)
        if d <= -0.005:
            sh.text(x + 140, ry, "比平时快 %.0f%%" % (-d * 100), 11,
                    accent, True)
        elif d >= 0.005:
            sh.text(x + 140, ry, "比平时慢 %.0f%%" % (d * 100), 11, C_DIM)
        else:
            sh.text(x + 140, ry, "与平时持平", 11, C_DIM)
    return len(rows)


def levels_detail_png(items, games_by_level, ver="", day="", now=None,
                      with_mascot=True, all_games=None):
    """详细版 = 一天的复盘卡，围绕玩家语言的三指标（time / 3BV/s / stnb），
    对照窗口 = 近 WINDOW_DAYS 个扫雷日（进步曲线上和很久以前比平均没有意义）：

    1. **今天最好一局**（hero）：大字用时 + BV / 3BV/s / stnb / 完成方式 / 钟点，
       右侧级别 PB 徽章（新 PB 或 PB 日期）。卡内级别 PB 只算 ≥门槛 BV 的局，
       与 PB 页的全PB 口径不同（脚注写明）——小盘刷出来的 PB 会把好局衬坏。
    2. **三指标状态表**：time / 3BV/s / stnb × 今天最好 / 今天平均 / 窗口最好 /
       窗口平均，整表 ≥门槛 BV；表头上方给盘运（今盘均 BV 比窗口 ±%），
       bvs / stnb 的平均对照带着盘面组合读。摘要行报刷新了哪几个指标的全期 PB
       + 中位局比窗口快慢 + BV 纪录数。
    3. **BV × 用时散点** + 窗口同 BV 中位参考线：线上方慢、线下方快；
       另画 PB 折线（只含今天之前的局，新纪录点落在线下方）；
       新 BV 纪录画空心环（全期口径）。
    4. **名单**：最快 5 局（≥门槛 BV）+ 今日 bvs / stnb 最佳局。**不列慢局** ——
       盘面难度不是 BV 唯一决定的，数据差的局常常是数据反映不了的问题。
    5. **节奏**：按局数切 4 段，比中位局相对窗口。

    退化：≥15 局全块；8~14 局散点 + 逐局列表；1~7 局 hero + 逐局列表；
    窗口不足 10 局不出状态表 / 摘要差 / 参考线（只画今天）。
    all_games = 全量历史；按日期截断到 ≤ day，往日的卡不偷看未来。
    不传 all_games 就只画今天。

    items = [(级别键, 级别名, 该天聚合行)]；games_by_level = {级别键: [该天每局]}。
    """
    items = [(k, cn, r) for k, cn, r in items if r]
    if not items:
        return None
    day = day or items[0][2]["date"]
    W = D_W
    sh = _Sheet(W, with_mascot=with_mascot)
    all_day = [g for k, _cn, _r in items
               for g in (games_by_level.get(k) or [])]
    if not all_day:
        return None
    t_from = min(g["t0"] for g in all_day if g.get("t0"))
    t_till = max((g.get("t1") or (g["t0"] + g["time"])) for g in all_day
                 if g.get("t0"))
    fl = sum(1 for g in all_day if g.get("style") == "fl")
    nf = sum(1 for g in all_day if g.get("style") == "nf")

    y = 30
    sh.text(PAD, y, day + _today_tag(day), 26, C_INK, bold=True)
    sh.text(W - PAD, y + 5, "共 %d 局" % len(all_day), 18, C_INK, True,
            anchor="rt")
    sh.text(PAD, y + 40, _meta_text(t_from, t_till, fl, nf), 13, C_DIM)
    y += 40 + 34
    sh.line(PAD, y, W - PAD, y, C_LINE)
    y += 20

    for j, (lv, cn, _r) in enumerate(items):
        gs = games_by_level.get(lv) or []
        if not gs:
            continue
        hist = ([g for g in all_games if g.get("level") == lv
                 and core.day_text(g.get("t0") or 0) <= day]
                if all_games is not None else gs)
        hist_ex = [g for g in hist
                   if core.day_text(g.get("t0") or 0) < day]
        n = len(gs)
        accent = LEVEL_ACCENT.get(lv, (C_INK, CARD_BG))[0]
        gate = core.BV_MIN_DEFAULT[lv]
        win = _play_window(hist_ex)
        has_hist = len(win) >= 10
        med = _med_curve(win) if has_hist else {}
        pb_ex = _pb_curve(hist_ex)
        recs = _record_bvs(gs, pb_ex, hist)
        rated = [g for g in gs if int(g.get("bv") or 0) >= gate]
        rated_win = [g for g in win if int(g.get("bv") or 0) >= gate]
        rated_ex = [g for g in hist_ex if int(g.get("bv") or 0) >= gate]
        typ_today, typ_win = _typical(gs, med), _typical(win, med)

        w = _chip(sh, PAD, y, cn, accent, size=13, padh=10, h=24)
        info = "%d 局 · %s" % (n, _meta_text(
            min(g["t0"] for g in gs),
            max((g.get("t1") or (g["t0"] + g["time"])) for g in gs),
            sum(1 for g in gs if g.get("style") == "fl"),
            sum(1 for g in gs if g.get("style") == "nf"), brief=True))
        sh.text(PAD + w + 10, y + 5, info, 11.5, C_DIM)
        y += 30

        # ---- 摘要行：只写事实，不写形容词
        bits = []
        pb_names = []
        for key in ("time", "bvs", "stnb"):
            prev, cur = _best(rated_ex, key), _best(rated, key)
            if prev is not None and cur is not None and (
                    cur < prev - 1e-9 if key == "time"
                    else cur > prev + 1e-9):
                pb_names.append(key)
        if pb_names:
            bits.append("刷新全期 PB：" + " / ".join(pb_names))
        if has_hist and typ_today is not None and typ_win is not None:
            d = (1 + typ_today) / (1 + typ_win) - 1.0
            bits.append("中位局比近 %d 扫雷日%s %.0f%%"
                        % (WINDOW_DAYS, "快" if d < 0 else "慢", abs(d) * 100))
        if recs:
            bits.append("刷新 %d 个 BV 纪录" % len(recs))
        if bits:
            sh.text(PAD, y, " · ".join(bits), 12, C_DIM)
            y += 22

        # ---- 英雄位：今天最好的一局 + 级别 PB（≥门槛口径）
        best = min(gs, key=lambda g: g["time"])
        sh.text(PAD, y, "今天最好一局", 10.5, C_DIM)
        rated_ex = [g for g in hist_ex if int(g.get("bv") or 0) >= gate]
        prev_pb = min(rated_ex, key=lambda g: g["time"]) if rated_ex else None
        if prev_pb and int(best["bv"]) >= gate \
                and best["time"] < prev_pb["time"] - 1e-9:
            lab = "新级别 PB · 原 %.3f" % prev_pb["time"]
            cw = sh.text_w(lab, 11, True) + 20
            _chip(sh, W - PAD - cw, y - 4, lab, accent, size=11, padh=10, h=20)
        elif prev_pb and int(best["bv"]) >= gate:
            sh.text(W - PAD, y, "级别 PB %.3f（%s）"
                    % (prev_pb["time"],
                       core.day_text(prev_pb.get("t0") or 0)[5:]),
                    11, C_DIM, anchor="rt")
        elif prev_pb:
            sh.text(W - PAD, y, "级别 PB %.3f（≥%d BV）"
                    % (prev_pb["time"], gate), 11, C_DIM, anchor="rt")
        y += 18
        big = "%.3f" % best["time"]
        sh.text(PAD, y, big, 32, C_INK, True)
        bw = sh.text_w(big, 32, True)
        sh.text(PAD + bw + 4, y + 16, "s", 14, C_DIM)
        cx = PAD + bw + 22
        chips = "%d BV · %.2f /s · stnb %.1f · %s · %s" % (
            int(best["bv"]), best["bv"] / best["time"], best.get("stnb") or 0,
            core.STYLE_CN.get(best.get("style"), ""),
            core.clock_text(best.get("t0") or 0)[:5])
        sh.text(cx, y + 18, chips, 12, C_DIM)
        if id(best) in recs:
            cw = sh.text_w(chips, 12)
            sh.text(cx + cw + 8, y + 18, "· BV 新纪录", 12, accent, True)
        y += 46

        # ---- 三指标状态表：今天 / 窗口 × 最好 / 平均。窗口太短整块不出
        if has_hist and (rated or rated_win):
            mb = _mean([float(g["bv"]) for g in gs])
            nb = _mean([float(g["bv"]) for g in win])
            if mb and nb:
                sh.text(PAD, y, "今盘均 %.0f BV · 比窗口%+.0f%%"
                        % (mb, (mb / nb - 1) * 100), 10.5, C_DIM)
                y += 18
            cw4 = (W - PAD * 2 - 90) / 4.0
            base = PAD + 90
            accent_bg = LEVEL_ACCENT.get(lv, (C_INK, CARD_BG))[1]
            # 「今天」整组一条浅色标题带；近 7 日放进右侧浅底参考框，
            # 两组之间留空隙，避免把右边的三列读成并列的历史数据。
            sh.rect(base - 10, y - 4, 2 * cw4 + 10, 24, accent_bg, radius=12)
            sh.text(base + cw4, y + 8, "今天", 11, accent, True, anchor="mm")
            panel_x = base + 2 * cw4 + 12
            panel_w = W - PAD - panel_x
            sh.rect(panel_x, y - 6, panel_w, 3 * 25 + 46, C_PANEL, radius=10)
            sh.text(panel_x + panel_w / 2.0, y + 8,
                    "近 %d 扫雷日" % WINDOW_DAYS, 11, C_DIM, anchor="mm")
            sh.text(panel_x + panel_w - 10, y + 8, "参考", 9.5, C_DIM,
                    anchor="rm")
            y += 26
            for i, h in enumerate(("最好", "平均", "最好", "平均")):
                sh.text(base + cw4 * (i + 1), y, h, 10.5, C_DIM, anchor="rt")
            y += 18
            for key, name, prec, unit in (("time", "time", 3, " s"),
                                          ("bvs", "3BV/s", 2, ""),
                                          ("stnb", "stnb", 2, "")):
                vals = (_best(rated, key),
                        _mean([_metric(g, key) for g in rated]),
                        _best(rated_win, key),
                        _mean([_metric(g, key) for g in rated_win]))
                sh.text(PAD, y, name, 13, C_INK)
                for i, v in enumerate(vals):
                    sh.text(base + cw4 * (i + 1), y,
                            "—" if v is None else "%.*f%s" % (prec, v, unit),
                            15 if i == 0 else 12.5,
                            C_INK if i == 0 else C_DIM, i == 0, anchor="rt")
                y += 25
            y += 8

        # ---- 散点：参考线 = 窗口同 BV 中位，空心环 = 全期 BV 纪录
        if n >= 8:
            sh.text(PAD, y, "BV × 用时", 10.5, C_DIM)
            legend = "● 今天"
            if med:
                legend += "　┄ 近 %d 扫雷日中位" % WINDOW_DAYS
            if pb_ex:
                legend += "　━ PB 线"
            if recs:
                legend += "　○ 新纪录"
            sh.text(PAD + 70, y, legend, 9.5, C_DIM)
            y += 22
            hh = 108 if n >= 15 else 100
            if _scatter(sh, PAD + 42, y, W - PAD * 2 - 42, hh, gs, med,
                        accent, recs, pb_ex):
                y += hh + 22
            else:
                y -= 22

        # ---- 名单：≥15 局最快 5 局 + 指标最佳，否则逐局（局少时列表最详细）
        cw2 = (W - PAD * 2 - 20) / 2.0
        if n >= 15:
            fast_pool = rated if len(rated) >= 3 else gs
            sh.text(PAD, y, "最快 5 局" + (
                "（≥%d BV）" % gate if fast_pool is rated else ""),
                10.5, C_DIM)
            sh.text(PAD + cw2 + 20, y, "今日指标最佳", 10.5, C_DIM)
            y += 18
            n1 = _rows_fast(sh, PAD, y, cw2, fast_pool, accent, recs, 5)
            h1 = _highlights(sh, PAD + cw2 + 20, y, rated or gs, accent)
            y += max(n1 * 21, h1) + 12
        else:
            sh.text(PAD, y, "这一天的每一局" + (
                "（±% = 相对窗口快慢）" if med else ""), 10.5, C_DIM)
            y += 18
            y += _rows_all(sh, PAD, y, W - PAD * 2, gs, accent, med) * 21 + 10

        # ---- 节奏
        if n >= 15:
            sh.text(PAD, y, "节奏", 10.5, C_DIM)
            sh.text(W - PAD, y, "比平时 = 用时 ÷ 窗口同 BV 中位",
                    9.5, C_DIM, anchor="rt")
            y += 18
            nr = _segments(sh, PAD, y, W - PAD * 2, gs, accent, med)
            y += max(nr, 1) * 21 + 10

        if j < len(items) - 1:
            sh.line(PAD, y, W - PAD, y, C_LINE)
            y += 18

    y += 2
    sh.line(PAD, y, W - PAD, y, C_RULE)
    y += 10
    for line in ("窗口 = 今天之前最近 %d 个有完成局的日子（按级别各算）；"
                 % WINDOW_DAYS,
                 "表内最好 / 平均只算 ≥门槛 BV 的局；bvs / stnb 平均受盘面组合影响，看盘运行；",
                 "参考线 / 比平时 = 窗口内同 BV 中位；PB 线 = 今天之前同 BV 全期最快；",
                 "PB / BV 纪录 = 全期口径；级别 PB = ≥门槛 BV 中的全期最快（与 PB 页的全PB 口径不同）。"):
        sh.text(PAD, y, line, 9.5, C_DIM)
        y += 15
    _footer(sh, y + 6, ver, now)
    return sh.render(y + 36)


# --------------------------------------------------------------- 剪贴板
def copy_image(img):
    """把 PIL 图放进剪贴板，用 CF_DIB（Windows 原生，无需 pywin32）。

    64 位下必须声明 argtypes：HGLOBAL 是指针宽度，按默认的 int 传会把句柄
    截断成 32 位，运气好失败、运气不好直接让进程崩掉。
    """
    if img is None:
        return False
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    dib = buf.getvalue()[14:]          # 去掉 BITMAPFILEHEADER，其余就是 DIB
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    k32.GlobalLock.restype = wintypes.LPVOID
    k32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    k32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    k32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
    u32.SetClipboardData.restype = wintypes.HANDLE
    u32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    u32.OpenClipboard.argtypes = (wintypes.HWND,)

    if not u32.OpenClipboard(None):
        return False
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(0x0042, len(dib))       # GMEM_MOVEABLE | ZEROINIT
        if not h:
            return False
        p = k32.GlobalLock(h)
        if not p:
            k32.GlobalFree(h)
            return False
        ctypes.memmove(p, dib, len(dib))
        k32.GlobalUnlock(h)
        if not u32.SetClipboardData(8, h):          # 8 = CF_DIB
            k32.GlobalFree(h)                      # 没移交成功就还是自己的
            return False
        return True                                  # 句柄归剪贴板，别自己释放
    finally:
        u32.CloseClipboard()

"""把一天（或今天）的记录画成可以直接发出去的图。

为什么单独一个文件：这段逻辑「每日」页的右键菜单和「概览」页的今日总结
按钮都要用，两处各写一遍出图，迟早出现一张卡是 37 局、另一张是 36 局这种事。

口径全部来自 core.daily_rows()：一天 = 本地 00:00–24:00，按开始时刻归日；
平均是算术平均。这里只排版，不再做任何统计。

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

SCALE = 2          # 2 倍像素渲染：发到手机上放大也不糊


# ------------------------------------------------- 时间与完成方式的小工具
def _clock(t):
    return core.clock_text(t)


def _style_text(row):
    """完成方式：FL（插旗）与 NF（无旗）各多少局。

    两者互斥，加起来不一定等于总局数 —— 录像里判不出模式的局（老阿比特
    的 avf 没有元数据）两边都不算，这种日子若一局都判不出，整段不写。
    """
    fl = int(row.get("fl") or 0)
    nf = int(row.get("nf") or 0)
    if not fl and not nf:
        return ""
    return "FL %d · NF %d" % (fl, nf)


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
                d.text((_px(x), _px(y) + off), s, font=f, fill=color, anchor=anchor)
            elif op[0] == "r":
                _k, x, y, w, h, color, radius = op
                box = (_px(x), _px(y) + off, _px(x + w) - 1, _px(y + h) + off - 1)
                if radius:
                    d.rounded_rectangle(box, radius=_px(radius), fill=color)
                else:
                    d.rectangle(box, fill=color)
            else:
                _k, x1, y1, x2, y2, color, width = op
                d.line((_px(x1), _px(y1) + off, _px(x2), _px(y2) + off),
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

    # 起止是「这一天从几点打到几点」，不再顺手算一句「历时 12 小时 48 分」：
    # 手机上看的人要的是区间本身，不是我们再帮他做的一位数减法。
    meta = "%s – %s" % (_clock(row["t_from"]), _clock(row["t_till"]))
    style = _style_text(row)
    if style:
        meta += " · " + style
    sh.text(PAD, y + 54, meta, 14, C_DIM)

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
        sh.text(PAD + w + 10, by + 4, "%d 局" % r["games"], 14, C_DIM)
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



def _span_text(sec):
    """4 小时 5 分 / 43 分 12 秒 / 18 秒。总结卡不写这个，详细版才写。"""
    sec = int(round(float(sec or 0)))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    if h:
        return "%d 小时 %d 分" % (h, m)
    if m:
        return "%d 分 %d 秒" % (m, s)
    return "%d 秒" % s


def _median(vals):
    vals = sorted(v for v in vals if v)
    n = len(vals)
    if not n:
        return 0.0
    if n % 2:
        return vals[n // 2]
    return (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _hist_bars(sh, x, y, w, h, times, accent, n=8):
    """每局用时的分布：等宽分桶的竖柱，柱顶写局数。

    不足 4 局或全部用时差不多时不画 —— 那种情况画出来就是一根柱子，
    读不出任何信息，不如省下这块地方。
    """
    times = [float(t) for t in times if t]
    if len(times) < 4:
        return False
    lo, hi = min(times), max(times)
    if hi - lo < 0.5:
        return False
    step = (hi - lo) / float(n)
    counts = [0] * n
    for t in times:
        counts[min(n - 1, int((t - lo) / step))] += 1
    top = max(counts)
    gap = 6
    bw = (w - gap * (n - 1)) / float(n)
    for i, c in enumerate(counts):
        bx = x + i * (bw + gap)
        if not c:
            continue
        bh = max(2.0, h * c / float(top))
        sh.rect(bx, y + h - bh, bw, bh, accent, radius=3)
        sh.text(bx + bw / 2.0, y + h - bh - 5, "%d" % c, 10, C_DIM, anchor="mb")
    sh.line(x, y + h, x + w, y + h, C_LINE)
    sh.text(x, y + h + 6, "%.1f s" % lo, 10, C_DIM)
    sh.text(x + w, y + h + 6, "%.1f s" % hi, 10, C_DIM, anchor="rt")
    return True


def _extreme(sh, x, y, w, games, slowest, accent, n=5):
    """最快 / 最慢各 5 局：用时 · 3BV · 完成方式。返回写了几行。"""
    games = [g for g in games if g.get("time")]
    pick = sorted(games, key=lambda g: g["time"], reverse=slowest)[:n]
    for i, g in enumerate(pick):
        ry = y + i * 21
        sh.text(x, ry, "%.3f" % g["time"], 12, C_INK, True)
        sh.text(x + 62, ry + 1, "%d BV" % int(g.get("bv") or 0), 11, C_DIM)
        st = core.STYLE_CN.get(g.get("style"))
        if st:
            sh.text(x + w, ry + 1, st, 11, accent, True, anchor="rt")
    return len(pick)


def levels_detail_png(items, games_by_level, ver="", day="", now=None,
                      with_mascot=True):
    """详细版的总结：在总结卡的基础上多给「用时分布」与「最快 / 最慢 5 局」。

    总结卡回答「这一天打得怎么样」（最好 / 平均 / 最差），详细版回答「这些数
    是怎么来的」。分布柱看手感波动 —— 一把 16 秒、其余 20 秒，和均匀 19 秒
    平均起来差不多，是两种日子；极值表看极端局是多大盘、什么完成方式。

    items = [(级别键, 级别名, 该天聚合行)]；games_by_level = {级别键: [该天每局]}。
    每局只取 time / bv / style 三个字段，其余不画。
    """
    items = [(k, cn, r) for k, cn, r in items if r]
    if not items:
        return None
    day = day or items[0][2]["date"]
    only = len(items) == 1
    W = D_W
    sh = _Sheet(W, with_mascot=with_mascot)
    total = sum(r["games"] for _k, _cn, r in items)
    t_from = min(r["t_from"] for _k, _c, r in items)
    t_till = max(r["t_till"] for _k, _c, r in items)
    fl = sum(int(r.get("fl") or 0) for _k, _c, r in items)
    nf = sum(int(r.get("nf") or 0) for _k, _c, r in items)

    y = 30
    title = day + _today_tag(day)
    sh.text(PAD, y, title, 26, C_INK, bold=True)
    if only:
        k, cn, _r = items[0]
        tw = sh.text_w(title, 26, True)
        _chip(sh, PAD + tw + 12, y + 5, cn,
              LEVEL_ACCENT.get(k, (C_INK, CARD_BG))[0], size=13, padh=10, h=24)
    sh.text(W - PAD, y + 5, "共 %d 局" % total, 18, C_INK, True, anchor="rt")

    meta = "%s – %s · 跨度 %s" % (_clock(t_from), _clock(t_till),
                                  _span_text(t_till - t_from))
    if fl or nf:
        meta += " · FL %d · NF %d" % (fl, nf)
    sh.text(PAD, y + 40, meta, 13, C_DIM)
    y += 40 + 28
    sh.line(PAD, y, W - PAD, y, C_LINE)
    y += 18

    x0 = PAD + D_LAB_W
    for j, (k, cn, r) in enumerate(items):
        accent = LEVEL_ACCENT.get(k, (C_INK, CARD_BG))[0]
        # 只有一个级别时，级别名与局数在标题里已经写过，不再重复一行
        if not only:
            cw = _chip(sh, PAD, y, cn, accent, size=12, padh=9, h=22)
            sh.text(PAD + cw + 8, y + 3, "%d 局" % r["games"], 12, C_DIM)
            sh.text(W - PAD, y + 3, "%s – %s"
                    % (_clock(r["t_from"])[:5], _clock(r["t_till"])[:5]),
                    12, C_DIM, anchor="rt")
            y += 30

        # 每块自带「最好 / 平均 / 最差」：块之间隔着图和表，共用一次表头会让人
        # 对不上列（总结卡是紧挨着的矩阵，那种才适合共用）。
        for i, h in enumerate(("最好", "平均", "最差")):
            sh.text(x0 + D_COL_W * (i + 1) - 8, y, h, 11, C_DIM, anchor="rt")
        y += 20
        for label, vals, prec in (
                ("时间（秒）", (r["best"], r["avg"], r["worst"]), 3),
                ("3BV/s", (r["bvs_max"], r["bvs_avg"], r["bvs_min"]), 3),
                ("stnb", (r["stnb_max"], r["stnb_avg"], r["stnb_min"]), 2)):
            sh.text(PAD, y, label, 14, C_INK)
            for i, v in enumerate(vals):
                sh.text(x0 + D_COL_W * (i + 1) - 8, y, "%.*f" % (prec, v), 16,
                        C_INK, i == 1, anchor="rt")
            y += 38

        games = games_by_level.get(k) or []
        times = [g.get("time") for g in games if g.get("time")]
        y += 8
        sh.text(PAD, y, "用时分布", 12, C_DIM)
        if times:
            sh.text(W - PAD, y + 1, "中位 %.2f s" % _median(times),
                    10, C_DIM, anchor="rt")
        y += 22
        if _hist_bars(sh, PAD, y, W - PAD * 2, 52, times, accent):
            y += 52 + 20
        else:
            sh.text(PAD, y + 14, "不足 4 局或用时几乎相同，画不出分布",
                    11, C_DIM, anchor="lm")
            y += 40

        y += 8
        if len(games) < 3:
            # 一局两局的日子，「最快 5」和「最慢 5」是同一批局，两栏只会重复
            sh.text(PAD, y, "全部 %d 局" % len(games), 12, C_DIM)
            y += 20
            rows = _extreme(sh, PAD, y, 210, games, False, accent)
            y += max(1, rows) * 21 + 10
        else:
            sh.text(PAD, y, "最快 5 局", 12, C_DIM)
            sh.text(PAD + 240, y, "最慢 5 局", 12, C_DIM)
            y += 20
            rows = _extreme(sh, PAD, y, 210, games, False, accent)
            _extreme(sh, PAD + 240, y, 210, games, True, accent)
            y += max(1, rows) * 21 + 10

        if j < len(items) - 1:
            y += 4
            sh.line(PAD, y, W - PAD, y, C_LINE)
            y += 18

    fy = y + 10
    _footer(sh, fy, ver, now)
    return sh.render(fy + 30)


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
            return False
        ctypes.memmove(p, dib, len(dib))
        k32.GlobalUnlock(h)
        if not u32.SetClipboardData(8, h):          # 8 = CF_DIB
            return False
        return True                                  # 句柄归剪贴板，别自己释放
    finally:
        u32.CloseClipboard()

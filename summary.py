"""把一天（或今天）的记录整理成可以直接发出去的文字 / 图片。

为什么单独一个文件：这段逻辑「每日」页的右键菜单和「概览」页的今日总结
按钮都要用，而且文字与图片必须来自同一份数字 —— 两处各写一遍，迟早出现
图上是 37 局、文字里是 36 局这种事。

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


# --------------------------------------------------------------- 文字部分
def _clock(t):
    return core.clock_text(t)


def span_text(sec):
    """2 小时 55 分 / 43 分 12 秒 / 18 秒。"""
    sec = int(round(float(sec or 0)))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    if h:
        return "%d 小时 %d 分" % (h, m)
    if m:
        return "%d 分 %d 秒" % (m, s)
    return "%d 秒" % s


def _nf_text(row):
    n = int(row.get("nf") or 0)
    g = int(row.get("games") or 0)
    if not g or not n:          # 一局 NF 都没有就别写一行「NF 0（0%）」
        return ""
    return "NF %d（%.0f%%）" % (n, 100.0 * n / g)


def _metric_lines(row):
    """三行指标。标签用表里同名的那三个词，不另造（最快 != 最高）。"""
    return [
        "时间（秒）：最快 %.3f / 平均 %.3f / 最慢 %.3f"
        % (row["best"], row["avg"], row["worst"]),
        "3BV/s：最高 %.3f / 平均 %.3f / 最低 %.3f"
        % (row["bvs_max"], row["bvs_avg"], row["bvs_min"]),
        "stnb：最高 %.3f / 平均 %.3f / 最低 %.3f"
        % (row["stnb_max"], row["stnb_avg"], row["stnb_min"]),
    ]


def _today_tag(day):
    """真是今天才写「今日」；拿别的日子作图时别硬叫今日。"""
    return " 今日" if day == datetime.now().strftime("%Y-%m-%d") else ""


def daily_text(row, level_cn, ver=""):
    """某一天、某一个级别的总结。"""
    if not row:
        return ""
    lines = ["%s · %s · %d 局" % (row["date"], level_cn, row["games"]),
             "%s – %s（%s）"
             % (_clock(row["t_from"]), _clock(row["t_till"]),
                span_text(row.get("span")))]
    nf = _nf_text(row)
    if nf:
        lines.append(nf)
    lines.extend(_metric_lines(row))
    if ver:
        lines.append("—— YuanshenToolbox v%s" % ver)
    return "\n".join(lines)


def today_text(items, ver="", day=""):
    """今天有局的几个级别合在一起。items = [(级别名, 行), ...]"""
    items = [(cn, r) for cn, r in items if r]
    if not items:
        return ""
    day = day or items[0][1]["date"]
    total = sum(r["games"] for _cn, r in items)
    head = "%s%s · 共 %d 局" % (day, _today_tag(day), total)
    lines = [head, " · ".join("%s %d 局" % (cn, r["games"]) for cn, r in items)]
    for cn, r in items:
        lines.append("")
        nf = _nf_text(r)
        tail = " · " + nf if nf else ""
        lines.append("%s · %d 局%s · %s – %s"
                     % (cn, r["games"], tail, _clock(r["t_from"]),
                        _clock(r["t_till"])))
        lines.extend("  " + s for s in _metric_lines(r))
    if ver:
        lines.append("")
        lines.append("—— YuanshenToolbox v%s" % ver)
    return "\n".join(lines)


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


# ------------------------------------------------------------ 右边的贴纸
# 卡片右边站一个角色，纯粹为了好看。它必须「无缝」：白底图直接贴上去会在
# 浅粉页底上留一个白方块，所以贴纸预处理成底色=纯白，绘制时按乘性混合
# （multiply）贴 —— 白的部分正好变成页底色，浅色的花瓣也不会被抠出毛边。
MASCOT = "mascot.png"
MASCOT_GAP = 18            # 贴纸与卡片之间的空隙（逻辑像素）
MASCOT_MAX = 490           # 上限：mascot.png 原生高度的一半（逻辑像素），再高就是放大，会糊
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


def _mascot_h(card_h, card_w, ratio=1.0, target=None):
    """贴纸高度（设备像素）。

    贴纸的宽高比是固定的（`_mascot_aspect()`），所以整张图的宽高比只由
    「卡片宽」和「贴纸高」两个数决定：比例 ≈ 贴纸宽高比 + (卡片宽 + 间隔) / 贴纸高。
    只按卡片高度定倍数的话，局数少的「今日」卡（只有一级）会又矮又宽，
    又变回长条，所以这里按**目标比例**反推高度，再夹在卡片高度倍数与
    素材原生高度之间。素材只有 988 px 高，超过就是放大，会糊。
    """
    want = card_h * ratio
    if target:
        want = max(want, (card_w + _px(MASCOT_GAP))
                   / max(0.2, float(target) - _mascot_aspect()))
    return int(min(max(want, card_h * 0.9), _px(MASCOT_MAX)))


class _Sheet:
    """先在逻辑坐标里排版，最后一次性按 2 倍像素画出来。

    ImageDraw 的圆角矩形与中文都靠 FreeType，字号必须是像素值，所以所有
    坐标在这里乘 SCALE —— 画图代码里就不必到处写 * 2。

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

    def render(self, height, mascot_ratio=None, target=None):
        from PIL import Image, ImageChops, ImageDraw
        m = self.MARGIN
        left = _px(m)
        card_w = _px(self.W)
        card_h = _px(height)
        # 贴纸可以比卡片高：高出来的部分向上出图，卡片在整个内容里**竖直居中**。
        # 试过让卡片贴底、贴纸只向上冒头，结果卡片上方留一条空粉底，重心明显偏下。
        ratio = 1.0 if mascot_ratio is None else mascot_ratio
        mk = None
        if self.with_mascot:
            mk = mascot(_mascot_h(card_h, card_w, ratio, target))
        mk_h = mk.height if mk is not None else 0
        span = max(card_h, mk_h)
        card_top = _px(m) + (span - card_h) // 2
        card_bottom = card_top + card_h
        width = left + card_w + (_px(MASCOT_GAP) + mk.width if mk else 0) + left
        img = Image.new("RGB", (width, _px(m) + span + _px(m)), PAGE_BG)
        if mk is not None:
            # 贴纸底边压在同一条线上：图里的人是齐胸口切的，飘着会像悬空
            page = Image.new("RGB", mk.size, PAGE_BG)
            img.paste(ImageChops.multiply(mk, page),
                      (left + card_w + _px(MASCOT_GAP), _px(m) + span - mk_h))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((left - 1, card_top - 1, left + card_w, card_bottom),
                            radius=_px(16), fill=CARD_BG, outline=C_LINE, width=1)
        off = card_top          # 所有排版坐标都是相对卡片左上角的
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
# 卡片是横表，宽度只由列数决定；想把整张图从「细长条」拉回协调，杠杆只有
# 两个：收窄列宽、让贴纸比卡片高 —— 贴纸宽高比 1.44，与卡片等高时它自己就
# 添了 1.44 倍高度那么宽，整张图必然还是 3.5:1。
# 卡片宽度**算出来**，不要手写：数字是右对齐在每列右边界 -8 处，手写宽度
# 一旦与列宽脱节，最右那一列就会顶出卡片（今天就顶出去过一次）。
D_LAB_W, D_COL_W, D_ROW_H = 96, 128, 46
D_W = PAD * 2 + D_LAB_W + D_COL_W * 3
D_MASCOT_RATIO = 1.25       # 贴纸高度 = 卡片高度 × 它；略高一点，图才不扁
D_TARGET = 2.62             # 整张图的目标宽高比（2.0 以下就得让角色大过卡片，不划算）
T_LAB_W, T_GAMES_W, T_COL_W, T_ROW_H = 70, 50, 64, 46
T_W = PAD * 2 + T_LAB_W + T_GAMES_W + T_COL_W * 9
T_MASCOT_RATIO = 1.4        # 这张卡片更宽，贴纸也得更高，两侧才不失衡
T_TARGET = 2.85


def daily_png(row, level_key, level_cn, ver="", now=None, with_mascot=True,
              mascot_ratio=None, tag=""):
    """一天的卡片：标题 + 起止/NF + 三行指标表，右边站个角色。"""
    accent = LEVEL_ACCENT.get(level_key, (C_INK, CARD_BG))[0]
    W = D_W
    sh = _Sheet(W, with_mascot=with_mascot)
    y = 32
    title = row["date"] + tag
    sh.text(PAD, y, title, 24, C_INK, bold=True)
    tw = sh.text_w(title, 24, True)
    _chip(sh, PAD + tw + 12, y + 4, level_cn, accent, size=13, padh=10, h=24)
    sh.text(W - PAD, y + 4, "%d 局" % row["games"], 16, C_INK, True, anchor="rt")

    meta = "%s – %s · %s" % (_clock(row["t_from"]), _clock(row["t_till"]),
                             span_text(row.get("span")))
    nf = _nf_text(row)
    if nf:
        meta += " · " + nf
    sh.text(PAD, y + 50, meta, 13, C_DIM)

    # 指标表：指标名 + 三列数字（最好 / 平均 / 最差）。三行指标的「好」
    # 方向不同（时间越小越好、3BV/s 与 stnb 越大越好），所以表头只能
    # 用「最好 / 平均 / 最差」这种与方向无关的说法。
    top = y + 50 + 40
    lab_w, col_w = D_LAB_W, D_COL_W
    x0 = PAD + lab_w
    heads = ("最好", "平均", "最差")
    for i, h in enumerate(heads):
        sh.text(x0 + col_w * (i + 1) - 8, top, h, 12, C_DIM, anchor="rt")
    sh.line(PAD, top + 24, W - PAD, top + 24, C_LINE)

    rows = (("时间（秒）", (row["best"], row["avg"], row["worst"]), 3),
            ("3BV/s", (row["bvs_max"], row["bvs_avg"], row["bvs_min"]), 3),
            ("stnb", (row["stnb_max"], row["stnb_avg"], row["stnb_min"]), 3))
    row_h = D_ROW_H
    for j, (label, vals, prec) in enumerate(rows):
        ry = top + 36 + row_h * j
        sh.text(PAD, ry, label, 15, C_INK)
        for i, v in enumerate(vals):
            sh.text(x0 + col_w * (i + 1) - 8, ry, "%.*f" % (prec, v), 16,
                    C_INK, True, anchor="rt")
        if j < len(rows) - 1:
            sh.line(PAD, ry + row_h - 13, W - PAD, ry + row_h - 13, C_RULE)
    bottom = top + 36 + row_h * len(rows) - 13

    fy = bottom + 26
    _footer(sh, fy, ver, now)
    return sh.render(fy + 28, mascot_ratio if mascot_ratio is not None
                     else D_MASCOT_RATIO, D_TARGET)


def today_png(items, ver="", day="", now=None, with_mascot=True,
              mascot_ratio=None):
    """今天三个级别并排：级别 / 局数 / 时间三项 / 3BV/s 三项 / stnb 三项。

    不出一行「合计」：三个级别的 BV 分布不同，把它们的平均时间再平均一次
    没有任何意义（合计局数放在标题右边就够了）。
    """
    from PIL import Image
    items = [(k, cn, r) for k, cn, r in items if r]
    if not items:
        return None
    day = day or items[0][2]["date"]
    if len(items) == 1:
        # 只有一级时矩阵退化成一行，卡片又矮又宽，图必然拉成长条。这一级
        # 的「今日」本来就是一张单日卡，直接用它：还能多给出起止与 NF。
        k, cn, r = items[0]
        return daily_png(r, k, cn, ver, now, with_mascot, mascot_ratio,
                         tag=_today_tag(day))
    total = sum(r["games"] for _k, _cn, r in items)
    W = T_W
    sh = _Sheet(W, with_mascot=with_mascot)
    y = 32
    sh.text(PAD, y, "%s%s" % (day, _today_tag(day)), 24, C_INK, bold=True)
    sh.text(W - PAD, y + 4, "共 %d 局" % total, 16, C_INK, True, anchor="rt")

    # 每个级别一个小标签 + 局数，横向铺开
    x = PAD
    for k, cn, r in items:
        w = _chip(sh, x, y + 46, "%s %d 局" % (cn, r["games"]),
                  LEVEL_ACCENT.get(k, (C_INK, CARD_BG))[0], size=12, padh=10, h=23)
        x += w + 8

    top = y + 46 + 23 + 28
    lab_w, games_w, col_w = T_LAB_W, T_GAMES_W, T_COL_W
    x_lv = PAD
    x_games = x_lv + lab_w
    x_first = x_games + games_w
    groups = (("时间（秒）", 0), ("3BV/s", 1), ("stnb", 2))
    sub_w = sh.text_w("平均", 11.5)
    for name, gi in groups:
        # 分组标题压在中间那一列的**文字**上：子表头是右对齐的，按列的
        # 方框居中会整体偏左十几像素，看着像压在最差那列上。
        mid_right = x_first + col_w * (3 * gi + 2) - 8
        sh.text(mid_right - sub_w / 2.0, top, name, 12, C_DIM, anchor="mt")
    sh.text(x_lv, top, "级别", 12, C_DIM)
    sh.text(x_games + games_w - 8, top, "局数", 12, C_DIM, anchor="rt")
    # 表头两行：分组名在上，「最好/平均/最差」在下，中间不画线 —— 第三组
    # 的右边已经到卡片边，再画线会顶出画面
    sub = top + 24
    for _name, gi in groups:
        for i, h in enumerate(("最好", "平均", "最差")):
            right = x_first + col_w * (3 * gi + i + 1) - 8
            sh.text(right, sub, h, 11.5, C_DIM, anchor="rt")
    sh.line(PAD, sub + 24, W - PAD, sub + 24, C_LINE)

    row_h = T_ROW_H
    for j, (k, cn, r) in enumerate(items):
        ry = sub + 34 + row_h * j
        sh.text(x_lv, ry, cn, 15, C_INK)
        sh.text(x_games + games_w - 8, ry, "%d" % r["games"], 15, C_INK, True,
                anchor="rt")
        for gi, vals in enumerate(((r["best"], r["avg"], r["worst"]),
                                   (r["bvs_max"], r["bvs_avg"], r["bvs_min"]),
                                   (r["stnb_max"], r["stnb_avg"], r["stnb_min"]))):
            for i, v in enumerate(vals):
                right = x_first + col_w * (3 * gi + i + 1) - 8
                sh.text(right, ry, "%.3f" % v if gi < 2 else "%.2f" % v, 15,
                        C_INK, i == 1, anchor="rt")
        if j < len(items) - 1:
            sh.line(PAD, ry + row_h - 13, W - PAD, ry + row_h - 13, C_RULE)

    bottom = sub + 34 + row_h * len(items) - 13
    fy = bottom + 26
    _footer(sh, fy, ver, now)
    return sh.render(fy + 28, mascot_ratio if mascot_ratio is not None
                     else T_MASCOT_RATIO, T_TARGET)


# --------------------------------------------------------------- 剪贴板
def copy_text(widget, text):
    """文本进剪贴板。widget 任意（用它的 clipboard 方法即可，全进程共用）。"""
    widget.clipboard_clear()
    widget.clipboard_append(text)
    return True


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

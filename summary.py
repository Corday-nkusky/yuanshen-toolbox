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


class _Sheet:
    """先在逻辑坐标里排版，最后一次性按 2 倍像素画出来。

    ImageDraw 的圆角矩形与中文都靠 FreeType，字号必须是像素值，所以所有
    坐标在这里乘 SCALE —— 画图代码里就不必到处写 * 2。

    排版坐标是相对「白卡片」的，外圈留 MARGIN 的底色：发到群里时白卡片
    自带一圈边，贴在深色背景上也不会糊成一整块。
    """

    MARGIN = 14

    def __init__(self, width):
        self.W = width
        self.ops = []

    def text(self, x, y, s, size, color, bold=False, anchor="lt"):
        self.ops.append(("t", x, y, s, _font(size, bold), color, anchor))

    def rect(self, x, y, w, h, color, radius=0):
        self.ops.append(("r", x, y, w, h, color, radius))

    def line(self, x1, y1, x2, y2, color, width=1):
        self.ops.append(("l", x1, y1, x2, y2, color, width))

    def text_w(self, s, size, bold=False):
        return _font(size, bold).getlength(s) / float(SCALE)

    def render(self, height):
        from PIL import Image, ImageDraw
        m = self.MARGIN
        img = Image.new("RGB", (_px(self.W + m * 2), _px(height + m * 2)), PAGE_BG)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((_px(m) - 1, _px(m) - 1,
                             _px(m + self.W), _px(m + height)),
                            radius=_px(16), fill=CARD_BG, outline=C_LINE, width=1)
        for op in self.ops:
            if op[0] == "t":
                _k, x, y, s, f, color, anchor = op
                d.text((_px(x + m), _px(y + m)), s, font=f, fill=color, anchor=anchor)
            elif op[0] == "r":
                _k, x, y, w, h, color, radius = op
                box = (_px(x + m), _px(y + m), _px(x + w + m) - 1, _px(y + h + m) - 1)
                if radius:
                    d.rounded_rectangle(box, radius=_px(radius), fill=color)
                else:
                    d.rectangle(box, fill=color)
            else:
                _k, x1, y1, x2, y2, color, width = op
                d.line((_px(x1 + m), _px(y1 + m), _px(x2 + m), _px(y2 + m)),
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


PAD = 26


def daily_png(row, level_key, level_cn, ver="", now=None):
    """一天的卡片：标题 + 起止/NF + 三行指标表。"""
    accent = LEVEL_ACCENT.get(level_key, (C_INK, CARD_BG))[0]
    W = 570
    sh = _Sheet(W)
    y = 24
    sh.text(PAD, y, row["date"], 22, C_INK, bold=True)
    tw = sh.text_w(row["date"], 22, True)
    _chip(sh, PAD + tw + 12, y + 3, level_cn, accent)
    sh.text(W - PAD, y + 4, "%d 局" % row["games"], 15, C_INK, True, anchor="rt")

    meta = "%s – %s · %s" % (_clock(row["t_from"]), _clock(row["t_till"]),
                             span_text(row.get("span")))
    nf = _nf_text(row)
    if nf:
        meta += " · " + nf
    sh.text(PAD, y + 38, meta, 12, C_DIM)

    # 指标表：指标名 + 三列数字（最好 / 平均 / 最差）。三行指标的「好」
    # 方向不同（时间越小越好、3BV/s 与 stnb 越大越好），所以表头只能
    # 用「最好 / 平均 / 最差」这种与方向无关的说法。
    top = y + 38 + 30
    lab_w, col_w = 96, 140
    x0 = PAD + lab_w
    heads = ("最好", "平均", "最差")
    for i, h in enumerate(heads):
        sh.text(x0 + col_w * (i + 1) - 8, top, h, 11, C_DIM, anchor="rt")
    sh.line(PAD, top + 21, W - PAD, top + 21, C_LINE)

    rows = (("时间（秒）", (row["best"], row["avg"], row["worst"]), 3),
            ("3BV/s", (row["bvs_max"], row["bvs_avg"], row["bvs_min"]), 3),
            ("stnb", (row["stnb_max"], row["stnb_avg"], row["stnb_min"]), 3))
    row_h = 31
    for j, (label, vals, prec) in enumerate(rows):
        ry = top + 27 + row_h * j
        sh.text(PAD, ry, label, 13, C_INK)
        for i, v in enumerate(vals):
            sh.text(x0 + col_w * (i + 1) - 8, ry, "%.*f" % (prec, v), 14,
                    C_INK, True, anchor="rt")
        if j < len(rows) - 1:
            sh.line(PAD, ry + row_h - 9, W - PAD, ry + row_h - 9, C_RULE)
    bottom = top + 27 + row_h * len(rows) - 9

    fy = bottom + 18
    _footer(sh, fy, ver, now)
    return sh.render(fy + 22)


def today_png(items, ver="", day="", now=None):
    """今天三个级别并排：级别 / 局数 / 时间三项 / 3BV/s 三项 / stnb 三项。

    不出一行「合计」：三个级别的 BV 分布不同，把它们的平均时间再平均一次
    没有任何意义（合计局数放在标题右边就够了）。
    """
    from PIL import Image
    items = [(k, cn, r) for k, cn, r in items if r]
    if not items:
        return None
    day = day or items[0][2]["date"]
    total = sum(r["games"] for _k, _cn, r in items)
    W = 860
    sh = _Sheet(W)
    y = 24
    sh.text(PAD, y, "%s%s" % (day, _today_tag(day)), 22, C_INK, bold=True)
    sh.text(W - PAD, y + 4, "共 %d 局" % total, 15, C_INK, True, anchor="rt")

    # 每个级别一个小标签 + 局数，横向铺开
    x = PAD
    for k, cn, r in items:
        w = _chip(sh, x, y + 36, "%s %d 局" % (cn, r["games"]),
                  LEVEL_ACCENT.get(k, (C_INK, CARD_BG))[0], size=11, padh=9, h=21)
        x += w + 8

    top = y + 36 + 21 + 20
    lab_w, games_w, col_w = 70, 54, 66
    x_lv = PAD
    x_games = x_lv + lab_w
    x_first = x_games + games_w
    groups = (("时间（秒）", 0), ("3BV/s", 1), ("stnb", 2))
    sub_w = sh.text_w("平均", 10.5)
    for name, gi in groups:
        # 分组标题压在中间那一列的**文字**上：子表头是右对齐的，按列的
        # 方框居中会整体偏左十几像素，看着像压在最差那列上。
        mid_right = x_first + col_w * (3 * gi + 2) - 8
        sh.text(mid_right - sub_w / 2.0, top, name, 11, C_DIM, anchor="mt")
    sh.text(x_lv, top, "级别", 11, C_DIM)
    sh.text(x_games + games_w - 8, top, "局数", 11, C_DIM, anchor="rt")
    # 表头两行：分组名在上，「最好/平均/最差」在下，中间不画线 —— 第三组
    # 的右边已经到卡片边，再画线会顶出画面
    sub = top + 18
    for _name, gi in groups:
        for i, h in enumerate(("最好", "平均", "最差")):
            right = x_first + col_w * (3 * gi + i + 1) - 8
            sh.text(right, sub, h, 10.5, C_DIM, anchor="rt")
    sh.line(PAD, sub + 18, W - PAD, sub + 18, C_LINE)

    row_h = 32
    for j, (k, cn, r) in enumerate(items):
        ry = sub + 24 + row_h * j
        sh.text(x_lv, ry, cn, 13, C_INK)
        sh.text(x_games + games_w - 8, ry, "%d" % r["games"], 13, C_INK, True,
                anchor="rt")
        for gi, vals in enumerate(((r["best"], r["avg"], r["worst"]),
                                   (r["bvs_max"], r["bvs_avg"], r["bvs_min"]),
                                   (r["stnb_max"], r["stnb_avg"], r["stnb_min"]))):
            for i, v in enumerate(vals):
                right = x_first + col_w * (3 * gi + i + 1) - 8
                sh.text(right, ry, "%.3f" % v if gi < 2 else "%.2f" % v, 13,
                        C_INK, i == 1, anchor="rt")
        if j < len(items) - 1:
            sh.line(PAD, ry + row_h - 9, W - PAD, ry + row_h - 9, C_RULE)

    bottom = sub + 24 + row_h * len(items) - 9
    fy = bottom + 18
    _footer(sh, fy, ver, now)
    return sh.render(fy + 22)


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

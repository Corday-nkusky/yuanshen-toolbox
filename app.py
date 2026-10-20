"""元神工具箱 —— 原生窗口（tkinter），二次元清爽风。

兼容元扫雷（Metasweeper，.evf）与阿比特（Arbiter，.avf）的扫雷录像，直接显示：
  · 概览：三级别局数 / BV 覆盖 / 三项最高
  · PB 表：每个（级别 × BV）的最快完成局
  · 前端成绩：三级别 × time / bvs / stnb 的前五局
  · 上传：把本地完成局传到开源扫雷网（openms.top）
"""

import base64
import ctypes
import os
import queue
from datetime import datetime
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

import cache
import core
import diag
import journal
import openms
import paths

APP_TITLE = "元神工具箱"
APP_SUB = "MINESWEEPER REPLAY TOOLBOX"
APP_VER = "0.10.0"

# 随程序打包的字体：MiSans（小米，免费商用）。私有注册，不装进系统字体目录。
BUNDLED_FONTS = ("MiSans-Regular.ttf", "MiSans-Demibold.ttf")
FONT_FILES = []


def font_dirs():
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        os.path.join(getattr(sys, "_MEIPASS", here), "fonts"),
        os.path.join(os.path.dirname(sys.executable), "fonts"),
        os.path.join(here, "fonts"),
    ]
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def ensure_bundled_fonts():
    """注册内置字体。返回已成功注册的文件列表。

    GDI 的 FR_PRIVATE 注册只对当前进程有效，但 Tk 只有在创建时才会枚举字体，
    所以这一步必须早于 tk.Tk()。
    """
    ok = []
    for d in font_dirs():
        if not os.path.isdir(d):
            continue
        for name in BUNDLED_FONTS:
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                continue
            try:
                n = ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0)
            except Exception:
                n = 0
            if n:
                ok.append(path)
    FONT_FILES[:] = ok
    return ok


def load_config():
    try:
        import json
        with open(paths.config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(**kw):
    try:
        import json
        data = load_config()
        data.update(kw)
        p = paths.config_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_roots(seen=None):
    """配置里的录像目录列表：[{"path": ..., "on": True}]。

    兼容 v0.7 及以前的单值 ``root``。顺手做三件事：去重（同一个目录被加了两次）、
    丢掉已经不存在的目录（移动硬盘拔掉、元扫雷卸载后路径变了）、保持原顺序
    （第一个是「主目录」，record.ini 对账就找它）。
    """
    cfg = seen if seen is not None else load_config()
    out = []
    raw = cfg.get("roots")
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, str):
                out.append({"path": it, "on": True})
            elif isinstance(it, dict) and it.get("path"):
                out.append({"path": str(it["path"]), "on": bool(it.get("on", True))})
    if not out and cfg.get("root"):
        out.append({"path": str(cfg["root"]), "on": True})
    keep, done = [], set()
    for it in out:
        p = it["path"]
        try:
            key = os.path.normcase(os.path.abspath(p))
        except OSError:
            continue
        if key in done or not os.path.isdir(p):
            continue
        done.add(key)
        keep.append(it)
    return keep


def save_roots(roots):
    save_config(roots=[{"path": r["path"], "on": bool(r.get("on", True))}
                       for r in roots])


def shrink_path(p, limit=64):
    if len(p) <= limit:
        return p
    parts = p.replace("/", "\\").split("\\")
    tail = "\\".join(parts[-2:])
    head = parts[0] + "\\…\\"
    return head + tail


def confirm_own_videos(parent, path, replacing=False):
    """手动加目录前的确认：加进来的录像一律当作「你自己打的」。

    自动探测只认元扫雷的 replay 目录。除此之外的目录（阿比特的 replays、
    从网上下载来观摩的录像堆）都是手动添加的，程序无法分辨里面哪一条不是
    自己打的 —— 只能靠用户确认「这里没有别人的录像」。
    """
    return messagebox.askokcancel(
        "确认这是你自己的录像目录",
        ("%s\n\n" % ("这会换掉当前的「主目录」。" if replacing else "")) +
        "加进来之后，这个目录（含子目录）里的每一条录像都会被当作你自己打的：\n"
        "  · 计入 PB 表 / 前端成绩\n"
        "  · 出现在上传列表里\n\n"
        "如果里面有从网上下载的、别人的录像（观摩用），请先挪到别处，\n"
        "或者单独放一个文件夹再加。\n\n"
        "目录：\n" + path,
        parent=parent)


# ---------------- 配色（樱花粉 × 湖水蓝 × 薰衣草） ----------------
C_BG = "#faf3fa"          # 主背景：极浅樱粉
C_PANEL = "#ffffff"
C_LINE = "#f2e2ef"        # 描边：粉灰
C_TEXT = "#3c3352"        # 主文字：深梅紫
C_DIM = "#9b8aae"         # 次要文字
C_ACCENT = "#ff6f9c"      # 樱粉（主强调）
C_ACCENT2 = "#5eb9e8"     # 湖水蓝
C_ACCENT3 = "#9b7cf0"     # 薰衣草紫
C_OK = "#3fb98a"
C_WARN = "#e2892f"
C_ERR = "#e4556f"
C_ZEBRA = "#fdf7fc"       # 斑马行
C_SEL = "#ffe3ef"         # 选中行

# 级别配色：(主色, 浅色)
LEVEL_ACCENT = {
    "b": ("#ff6f9c", "#ffe4ee"),
    "i": ("#3fa9dd", "#e0f3fd"),
    "e": ("#8f6ce8", "#ebe4fd"),
}

# 字体族在运行期探测后填充
FAM_HEAD = "MiSans"
FAM_UI = "MiSans"
FAM_MONO = "Consolas"

FONT_UI = (FAM_UI, 11)
FONT_UI_B = (FAM_UI, 11, "bold")
FONT_SMALL = (FAM_UI, 9)
FONT_SECTION = (FAM_HEAD, 13, "bold")
FONT_BRAND = (FAM_HEAD, 27, "bold")
FONT_BIG = (FAM_HEAD, 18, "bold")
FONT_MONO = (FAM_MONO, 11)
FONT_MONO_B = (FAM_MONO, 11, "bold")

# 高 DPI：字体由 Tk 按实际 DPI 缩放，像素尺寸必须自己跟着放大
SCALE = 1.0


def sc(v):
    return max(1, int(round(v * SCALE)))


def fmt_time(v):
    return f"{v:.3f}"


def fmt_bvs(v):
    return f"{v:.3f}"


def fmt_stnb(v):
    return f"{v:.2f}"


def enable_dpi():
    """声明 DPI 感知。必须在任何窗口/DC 创建之前调用。

    顺序讲究：SetProcessDpiAwarenessContext 失败会返回 0（不抛异常），
    所以要逐个判断返回值，否则会误以为已经成功。
    SetProcessDpiAwarenessContext(SYSTEM_AWARE=-2) → shcore(1) → user32()。
    """
    try:
        ctx = ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-2))          # -2 = DPI_AWARENESS_CONTEXT_SYSTEM_AWARE
        if ctx:
            return True
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # 1 = PROCESS_SYSTEM_DPI_AWARE
        return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def detect_scale(win=None):
    """取系统缩放比例。

    顺序：GetDpiForSystem（进程已声明感知，返回的是真实系统 DPI）
    → 注册表 AppliedDPI（GetDpiForSystem 缺失时的兜底）
    → Tk 的 fpixels（窗口存在后才准）。
    """
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi and dpi > 0:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Control Panel\Desktop\WindowMetrics")
        dpi = winreg.QueryValueEx(k, "AppliedDPI")[0]
        if dpi and dpi > 0:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    if win is not None:
        try:
            return max(1.0, win.winfo_fpixels("1i") / 96.0)
        except Exception:
            pass
    return 1.0


# 模块加载即声明 DPI 感知：只要在任何窗口创建之前执行，效果才生效。
# 放在 import 阶段而不是 main() 里，是为了让外部脚本（探测、截图）也拿到一致结果。
DPI_AWARE = enable_dpi()


def work_area():
    """桌面可用区（去掉任务栏），返回 (left, top, width, height)，物理像素。

    比「屏幕高 - 96」这种瞎猜靠谱：不同缩放/任务栏位置下都准。
    """
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    r = RECT()
    try:
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x0030, 0, ctypes.byref(r), 0)      # SPI_GETWORKAREA
        if ok and r.right > r.left and r.bottom > r.top:
            return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ 画布工具
def _hex(c):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def mix(c1, c2, t):
    """两色线性插值，t ∈ [0,1]。"""
    a, b = _hex(c1), _hex(c2)
    return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _stop_color(stops, t):
    """stops: [(pos, color)] 按 pos 升序，返回 t 处颜色。"""
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]
        p1, c1 = stops[i + 1]
        if p0 <= t <= p1:
            k = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
            return mix(c0, c1, k)
    return stops[-1][1]


def gradient_rect(cv, x1, y1, x2, y2, stops, step=None):
    """在画布上铺一条水平渐变（用竖条拼接，条宽 3px 足够平滑）。"""
    step = step or max(2, sc(3))
    w = max(1, x2 - x1)
    x = x1
    while x < x2:
        nx = min(x + step, x2)
        cv.create_rectangle(x, y1, nx, y2, width=0,
                            fill=_stop_color(stops, (x - x1) / w))
        x = nx


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """圆角矩形（smooth 多边形近似）。"""
    r = max(0.0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, **kw)


def sparkle(cv, cx, cy, r, color="#ffffff", tags=()):
    """四角星（二次元高光）。"""
    k = r * 0.28
    pts = [
        cx, cy - r, cx + k, cy - k, cx + r, cy,
        cx + k, cy + k, cx, cy + r, cx - k, cy + k,
        cx - r, cy, cx - k, cy - k,
    ]
    return cv.create_polygon(pts, smooth=True, fill=color, width=0, tags=tags)


def draw_mine(cv, cx, cy, r, color="#ffffff", width=None, tags=()):
    """小地雷图标：圆心 + 八向尖刺。"""
    w = width or max(1, int(r * 0.42))
    d = r * 1.9
    cv.create_line(cx - d, cy, cx + d, cy, fill=color, width=w, tags=tags,
                   capstyle="round")
    cv.create_line(cx, cy - d, cx, cy + d, fill=color, width=w, tags=tags,
                   capstyle="round")
    k = d * 0.62
    for dx, dy in ((-k, -k), (k, -k), (-k, k), (k, k)):
        cv.create_line(cx, cy, cx + dx, cy + dy, fill=color, width=w, tags=tags,
                       capstyle="round")
    cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, width=0, tags=tags)


# ------------------------------------------------------------------ 药丸按钮
class Pill(tk.Canvas):
    """圆角药丸按钮：常态白底粉边，选中态填充主色。"""

    def __init__(self, master, text, command=None, accent=C_ACCENT,
                 bg=None, font=None, padx=18, height=30, min_width=0):
        self._bg = bg or C_BG
        f = font or FONT_UI
        self._font = f
        self._font_b = (f[0], f[1], "bold")
        probe = tkfont.Font(family=f[0], size=f[1], weight="bold")
        w = max(sc(min_width), probe.measure(text) + sc(padx) * 2)
        h = sc(height)
        super().__init__(master, width=w, height=h, bg=self._bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._text = text
        self._command = command
        self._accent = accent
        self._padx = padx
        self._minw = min_width
        self._pw, self._ph = w, h      # 注意：不能用 _w，Tk 内部用 _w 存控件路径
        self._sel = False
        self._phover = False
        self._enabled = True
        self._draw()
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _draw(self):
        self.delete("all")
        r = self._ph / 2.0
        if not self._enabled:
            fill, outline, fg = self._bg, mix(C_LINE, self._bg, 0.4), mix(C_DIM, C_PANEL, 0.5)
        elif self._sel:
            fill, outline, fg = self._accent, self._accent, "#ffffff"
        elif self._phover:
            fill, outline, fg = mix(C_PANEL, self._accent, 0.14), self._accent, self._accent
        else:
            fill, outline, fg = C_PANEL, C_LINE, C_DIM
        round_rect(self, 1, 1, self._pw - 1, self._ph - 1, r,
                   fill=fill, outline=outline, width=sc(1))
        self.create_text(self._pw / 2.0, self._ph / 2.0, text=self._text,
                         fill=fg, font=self._font_b if self._sel else self._font)

    def set_selected(self, flag):
        if self._sel != bool(flag):
            self._sel = bool(flag)
            self._draw()

    def set_text(self, text):
        """改文案并重新量宽度（"上传选中 (3)" 这类计数用）。"""
        if text == self._text:
            return
        self._text = text
        probe = tkfont.Font(family=self._font[0], size=self._font[1], weight="bold")
        w = max(sc(self._minw), probe.measure(text) + sc(self._padx) * 2)
        self._pw = w
        self.configure(width=w)
        self._draw()

    def set_enabled(self, flag):
        self._enabled = bool(flag)
        self._draw()

    def _on_click(self, _e=None):
        if self._enabled and self._command:
            self._command()

    def _on_enter(self, _e=None):
        self._phover = True
        self._draw()
        self.configure(cursor="hand2")

    def _on_leave(self, _e=None):
        self._phover = False
        self._draw()
        self.configure(cursor="")


# ------------------------------------------------------------------ 统计卡片
class StatCard(tk.Canvas):
    """一级别一张卡：圆角白底 + 彩色标签 + 五行指标。"""

    def __init__(self, master, level):
        super().__init__(master, bg=C_BG, highlightthickness=0, bd=0,
                         height=sc(192))
        self.level = level
        self.accent, self.soft = LEVEL_ACCENT[level]
        self.data = {"count": "0 局", "rows": []}
        self._job = None
        self.bind("<Configure>", lambda _e: self._schedule())

    def _schedule(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(16, self._redraw)

    def set_data(self, data):
        self.data = data
        self._redraw()

    def _redraw(self):
        self._job = None
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < sc(120) or h < sc(80):
            return
        pad = sc(3)
        round_rect(self, pad, pad, w - pad, h - pad, sc(20),
                   fill=C_PANEL, outline=self.soft, width=sc(2))

        # 级别标签
        lx, ly = pad + sc(18), pad + sc(14)
        label = core.LEVEL_CN[self.level]
        f = tkfont.Font(family=FAM_HEAD, size=11, weight="bold")
        bw = f.measure(label) + sc(24)
        bh = sc(24)
        round_rect(self, lx, ly, lx + bw, ly + bh, bh / 2.0,
                   fill=self.accent, width=0)
        self.create_text(lx + bw / 2.0, ly + bh / 2.0, text=label,
                         fill="#ffffff", font=FONT_SECTION)
        self.create_text(lx + bw + sc(8), ly + bh / 2.0,
                         text=core.LEVEL_EN[self.level], anchor="w",
                         fill=C_DIM, font=FONT_SMALL)

        # 局数
        count = (self.data or {}).get("count", "0 局")
        self.create_text(w - pad - sc(18), ly + bh / 2.0, text=count, anchor="e",
                         fill=self.accent, font=FONT_BIG)

        # 分隔虚线
        y_sep = ly + bh + sc(8)
        self.create_line(pad + sc(18), y_sep, w - pad - sc(18), y_sep,
                         fill=C_LINE, dash=(sc(2), sc(3)))

        rows = (self.data or {}).get("rows") or []
        row_h = sc(32)
        block = len(rows) * row_h
        avail = h - pad - (y_sep + sc(14))
        y0 = y_sep + sc(10) + max(0, (avail - block) // 2)
        for i, (label_text, value) in enumerate(rows):
            y = y0 + row_h * i + row_h / 2.0
            self.create_text(pad + sc(18), y, text=label_text, anchor="w",
                             fill=C_DIM, font=FONT_UI)
            self.create_text(w - pad - sc(18), y, text=value, anchor="e",
                             fill=C_TEXT, font=FONT_MONO_B)

        if not rows:
            self.create_text(w / 2.0, y0 + sc(28), text="暂无完成局",
                             fill=C_DIM, font=FONT_UI)


# ------------------------------------------------------------------ 通用表格
class Table(ttk.Frame):
    """带排序的 Treeview 封装。columns: (key, 标题, 宽度, 对齐, 类型)

    对齐约定（三个桶，别再随手写）：
      "e"      数量 / 成绩 —— 右对齐，数位对齐才好横向比大小
      "center" 短标签 —— 级别、风格、来源、启用、状态、日期时间
      "w"      可读文本 —— 录像文件、目录路径
    """

    def __init__(self, master, columns, height=6, zebra=True, fit=True,
                 selectmode="browse", iid_key=None, vbar=True):
        super().__init__(master, style="Card.TFrame")
        self.columns_def = columns
        self.zebra = zebra
        self.rows = []
        self.iid_key = iid_key          # 用某个字段当 iid，便于原地更新某一行
        self._sort_key = None
        self._sort_rev = False

        keys = [c[0] for c in columns]
        self.tv = ttk.Treeview(self, columns=keys, show="headings",
                               height=height, selectmode=selectmode)
        for key, title, width, anchor, _kind in columns:
            # 表头跟单元格同向，否则「数字右对齐、标题居中」看着像没对齐
            self.tv.heading(key, text=title, anchor=anchor,
                            command=lambda k=key: self._sort_by(k))
            self.tv.column(key, width=sc(width), anchor=anchor,
                           stretch=(key in ("file", "path", "player")))
        self.tv.grid(row=0, column=0, sticky="nsew")
        if vbar:
            vs = ttk.Scrollbar(self, orient="vertical", command=self.tv.yview)
            self.tv.configure(yscrollcommand=vs.set)
            vs.grid(row=0, column=1, sticky="ns")
        self.vbar = vbar
        # 行高是整数，可用高度除不尽时把余量留在底部（白色留白），
        # 好过让 Treeview 被拉伸、露出半截行
        self.rowconfigure(0, weight=0)
        self.columnconfigure(0, weight=1)

        self.tv.tag_configure("odd", background=C_ZEBRA)
        self.tv.tag_configure("even", background=C_PANEL)
        self.tv.bind("<Double-1>", self._copy_path)
        self._tip = None
        self._fit_job = None
        self._fit_rows = height
        if fit:
            self.bind("<Configure>", self._schedule_fit)

    # --- 高度自适应 ---
    def _schedule_fit(self, _e=None):
        if self._fit_job:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(20, self._fit)

    def _fit(self):
        """按实际可用高度调行数，免得末行被切成半截。"""
        self._fit_job = None
        h = self.winfo_height()
        if h < sc(60):
            return
        row_h = max(1, sc(32))
        head = sc(47)
        kids = self.tv.get_children()
        if kids:
            bb = self.tv.bbox(kids[0])      # bbox 的 y 就是表头实际高度
            if bb:
                head = bb[1]
        rows = max(3, int((h - head) // row_h))
        if rows != self._fit_rows:
            self._fit_rows = rows
            self.tv.configure(height=rows)

    # --- 数据 ---
    def set_rows(self, rows):
        self.rows = list(rows)
        if self._sort_key:
            self._sort_by(self._sort_key, redraw_only=True)
        else:
            self._redraw()

    def _redraw(self):
        self.tv.delete(*self.tv.get_children())
        for i, r in enumerate(self.rows):
            iid = str(r.get(self.iid_key)) if self.iid_key else str(i)
            self.tv.insert("", "end", iid=iid, values=self.row_values(r),
                           tags=("odd" if i % 2 else "even",))

    def row_values(self, r):
        """把一条记录转成 Treeview 的 5 列值（格式化规则在这里统一）。"""
        vals = []
        for key, _t, _w, _a, kind in self.columns_def:
            v = r.get(key, "")
            if kind == "f3" and isinstance(v, (int, float)):
                v = f"{v:.3f}"
            elif kind == "f2" and isinstance(v, (int, float)):
                v = f"{v:.2f}"
            elif kind == "int" and isinstance(v, (int, float)):
                v = f"{int(v)}"
            vals.append(v)
        return vals

    def update_row(self, r):
        """按 iid 原地更新一行，不动选中状态与滚动位置。"""
        iid = str(r.get(self.iid_key)) if self.iid_key else None
        if not iid or not self.tv.exists(iid):
            return False
        self.tv.item(iid, values=self.row_values(r))
        return True

    def select_paths(self, paths):
        wanted = {str(p) for p in paths}
        ids = [str(r.get(self.iid_key)) for r in self.rows
               if self.iid_key and str(r.get(self.iid_key)) in wanted
               and self.tv.exists(str(r.get(self.iid_key)))]
        self.tv.selection_set(ids)

    def selected_paths(self):
        # iid 本身就是这个字段的值（见 _redraw），直接读 selection 即可；
        # 不能用 tv.set(iid, 字段名)，因为该字段不是表格列。
        return set(self.tv.selection()) if self.iid_key else set()

    def _sort_by(self, key, redraw_only=False):
        if not redraw_only:
            if self._sort_key == key:
                self._sort_rev = not self._sort_rev
            else:
                self._sort_key, self._sort_rev = key, False
            # 数值列默认降序更符合直觉（bvs/stnb/局数）
            kind = next(c[4] for c in self.columns_def if c[0] == key)
            if kind in ("f3", "f2", "int") and not self._sort_rev:
                self._sort_rev = True

        kind = next(c[4] for c in self.columns_def if c[0] == key)

        def keyf(r):
            v = r.get(key)
            if isinstance(v, str):
                try:
                    return float(v)
                except ValueError:
                    return v.lower()
            if v is None:
                return float("-inf") if isinstance(r.get(key), float) else ""
            return v

        try:
            self.rows.sort(key=keyf, reverse=self._sort_rev)
        except TypeError:
            self.rows.sort(key=lambda r: str(r.get(key)), reverse=self._sort_rev)

        # 更新表头箭头
        for k, title, _w, _a, _kind in self.columns_def:
            mark = " ▼" if self._sort_rev else " ▲"
            self.tv.heading(k, text=title + (mark if k == key else ""))
        self._redraw()

    def _copy_path(self, _event=None):
        sel = self.tv.selection()
        if not sel:
            return
        if self.iid_key == "path":
            path = sel[0]
        else:
            try:
                path = self.rows[int(sel[0])].get("path")
            except (IndexError, ValueError):
                path = None
        if path:
            self.clipboard_clear()
            self.clipboard_append(path)
            app = self.winfo_toplevel()
            if hasattr(app, "set_status"):
                app.set_status("已复制路径：" + path, "dim")


# ------------------------------------------------------------------ 登录窗
class LoginDialog(tk.Toplevel):
    """验证码登录。

    站点验证码是一次性的（成功失败都作废），且登录限流 60 次/小时/IP，
    所以不做 OCR：人眼输 4 位字母，登录成功后靠 session_id 长期保持。
    """

    def __init__(self, master, client, on_done=None):
        super().__init__(master)
        self.client = client
        self.on_done = on_done
        self.q = queue.Queue()
        self._img = None
        self.hashkey = ""
        self._busy = False

        self.title("登录 openms.top")
        self.configure(bg=C_BG)
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        wrap = tk.Frame(self, bg=C_BG)
        wrap.pack(padx=sc(22), pady=sc(18))
        wrap.columnconfigure(1, weight=1)

        tk.Label(wrap, text="登录开源扫雷网", bg=C_BG, fg=C_TEXT,
                 font=FONT_SECTION).grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Label(wrap, text="验证码只在登录这一刻需要；登录后凭 Cookie 保持 90 天",
                 bg=C_BG, fg=C_DIM, font=FONT_SMALL).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(sc(2), sc(12)))

        self.var_user = tk.StringVar(value=client.store.username())
        self.var_pass = tk.StringVar(value=client.store.password())
        self.var_code = tk.StringVar()
        self.var_keep = tk.BooleanVar(value=True)

        r = 2
        for label, var, secret in (("用户名", self.var_user, False),
                                   ("密码", self.var_pass, True)):
            tk.Label(wrap, text=label, bg=C_BG, fg=C_DIM, font=FONT_UI).grid(
                row=r, column=0, sticky="e", padx=(0, sc(8)), pady=sc(4))
            e = tk.Entry(wrap, textvariable=var, width=26,
                         show="●" if secret else "", bg=C_PANEL, fg=C_TEXT,
                         relief="flat", font=FONT_UI, highlightthickness=1,
                         highlightbackground=C_LINE, highlightcolor=C_ACCENT,
                         insertbackground=C_ACCENT)
            e.grid(row=r, column=1, columnspan=2, sticky="ew", pady=sc(4),
                   ipady=sc(3))
            r += 1

        tk.Label(wrap, text="验证码", bg=C_BG, fg=C_DIM, font=FONT_UI).grid(
            row=r, column=0, sticky="e", padx=(0, sc(8)), pady=sc(4))
        box = tk.Frame(wrap, bg=C_BG)
        box.grid(row=r, column=1, columnspan=2, sticky="w", pady=sc(4))
        self.entry_code = tk.Entry(
            box, textvariable=self.var_code, width=8, bg=C_PANEL, fg=C_TEXT,
            relief="flat", font=FONT_MONO_B, justify="center", highlightthickness=1,
            highlightbackground=C_LINE, highlightcolor=C_ACCENT,
            insertbackground=C_ACCENT, selectbackground=C_SEL)
        self.entry_code.pack(side="left", ipady=sc(4))
        self.lbl_cap = tk.Label(box, text="加载中…", bg=C_PANEL, fg=C_DIM,
                                font=FONT_SMALL, width=14)
        self.lbl_cap.pack(side="left", padx=(sc(8), sc(6)))
        self.lbl_cap.bind("<Button-1>", lambda _e: self.reload_captcha())
        link = tk.Label(box, text="换一张", bg=C_BG, fg=C_ACCENT2, font=FONT_SMALL,
                        cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda _e: self.reload_captcha())
        r += 1

        tk.Checkbutton(wrap, text="保持登录 90 天（推荐）", variable=self.var_keep,
                       bg=C_BG, fg=C_TEXT, activebackground=C_BG,
                       activeforeground=C_TEXT, selectcolor=C_PANEL,
                       font=FONT_SMALL, bd=0, highlightthickness=0).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(sc(8), sc(2)))
        r += 1

        self.lbl_msg = tk.Label(wrap, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL,
                                anchor="w", justify="left")
        self.lbl_msg.grid(row=r, column=0, columnspan=3, sticky="w",
                          pady=(sc(2), sc(10)))
        r += 1

        btns = tk.Frame(wrap, bg=C_BG)
        btns.grid(row=r, column=0, columnspan=3, sticky="e")
        Pill(btns, "取消", command=self.destroy, accent=C_DIM, padx=18,
             height=32).pack(side="right", padx=(sc(8), 0))
        self.btn_ok = Pill(btns, "登 录", command=self.submit, accent=C_ACCENT,
                           padx=26, height=32)
        self.btn_ok.pack(side="right")

        self.bind("<Return>", lambda _e: self.submit())
        self.reload_captcha()
        self.after(60, self._poll)
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        px, py = master.winfo_rootx(), master.winfo_rooty()
        pw, ph = master.winfo_width(), master.winfo_height()
        self.geometry("+%d+%d" % (px + max(0, (pw - w) // 2),
                                  py + max(0, (ph - h) // 3)))
        self.grab_set()
        self.entry_code.focus_set()

    # --- 验证码 ---
    def reload_captcha(self, _e=None):
        if self._busy:
            return
        self.lbl_cap.config(text="加载中…", image="", fg=C_DIM)
        self._img = None
        threading.Thread(target=self._cap_worker, daemon=True).start()

    def _cap_worker(self):
        try:
            self.q.put(("cap", self.client.refresh_captcha()))
        except Exception as exc:                     # noqa: BLE001
            self.q.put(("cap_err", str(exc)))

    def _show_captcha(self, d):
        self.hashkey = d["hashkey"]
        try:
            img = tk.PhotoImage(data=base64.b64encode(d["png"]))
        except tk.TclError:
            p = os.path.join(paths.data_dir(), "captcha-tmp.png")
            with open(p, "wb") as fh:
                fh.write(d["png"])
            img = tk.PhotoImage(file=p)
        if SCALE >= 1.25:                 # 100×30 的原图在 150% 屏上太小
            img = img.zoom(2)
        self._img = img
        self.lbl_cap.config(image=img, text="", width=0, fg=C_DIM)
        self.lbl_msg.config(text="", fg=C_DIM)
        self.var_code.set("")
        self.entry_code.focus_set()

    # --- 提交 ---
    def submit(self):
        if self._busy:
            return
        user = self.var_user.get().strip()
        pwd = self.var_pass.get()
        code = self.var_code.get().strip()
        if not user or not pwd:
            self.lbl_msg.config(text="用户名和密码都要填", fg=C_ERR)
            return
        if not code:
            self.lbl_msg.config(text="请输入验证码", fg=C_ERR)
            return
        if not self.hashkey:
            self.lbl_msg.config(text="验证码还没加载好，点「换一张」", fg=C_ERR)
            return
        self._busy = True
        self.btn_ok.set_enabled(False)
        self.lbl_msg.config(text="登录中…", fg=C_DIM)
        days = 90 if self.var_keep.get() else 0
        threading.Thread(target=self._login_worker,
                         args=(user, pwd, code, self.hashkey, days),
                         daemon=True).start()

    def _login_worker(self, user, pwd, code, hk, days):
        try:
            ok, payload = self.client.login(user, pwd, code, hk, days)
        except Exception as exc:                     # noqa: BLE001
            self.q.put(("login_err", {"category": "net", "_msg": str(exc)}))
            return
        self.q.put(("login_ok" if ok else "login_err", payload))

    def _finish(self, ok, payload):
        self._busy = False
        self.btn_ok.set_enabled(True)
        if ok:
            user = self.client.user or {}
            if self.on_done:
                self.on_done(user)
            self.destroy()
            return
        cat = payload.get("category") if isinstance(payload, dict) else ""
        msg = {
            "captcha": "验证码不对（已失效，已自动换一张）",
            "password": "用户名或密码不对",
            "net": "网络错误：%s" % (payload.get("_msg") or ""),
        }.get(cat) or "登录失败：%s" % (payload,)
        self.lbl_msg.config(text=msg, fg=C_ERR)
        self.reload_captcha()

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "cap":
                    self._show_captcha(payload)
                elif kind == "cap_err":
                    self.lbl_cap.config(text="加载失败", image="")
                    self.lbl_msg.config(text="取验证码失败：" + payload, fg=C_ERR)
                else:
                    self._finish(kind == "login_ok", payload)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(80, self._poll)


class DiagnosticsDialog(tk.Toplevel):
    """数据目录 / 日志 / 导出诊断包。

    存在的理由：打成 --windowed 后没有控制台，程序崩了用户手上什么都没有。
    这个窗口把三件事讲清楚——数据存在哪、怎么一键清干净、出问题发什么给作者。
    """

    def __init__(self, master):
        super().__init__(master)
        self.title("数据与诊断")
        self.configure(bg=C_BG)
        self.transient(master)
        self.geometry(f"{sc(740)}x{sc(470)}")
        self.minsize(sc(620), sc(380))

        wrap = tk.Frame(self, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=sc(20), pady=sc(16))

        tk.Label(wrap, text="数据与诊断", bg=C_BG, fg=C_TEXT,
                 font=FONT_SECTION).pack(anchor="w")
        tk.Label(wrap, text="日志、登录凭据、目录设置都在下面这个文件夹里",
                 bg=C_BG, fg=C_DIM, font=FONT_SMALL).pack(anchor="w", pady=(sc(2), sc(10)))

        box = tk.Frame(wrap, bg=C_PANEL, highlightthickness=1,
                       highlightbackground=C_LINE)
        box.pack(fill="x", pady=(0, sc(10)))
        tk.Label(box, text=paths.data_dir(), bg=C_PANEL, fg=C_TEXT,
                 font=FONT_MONO, anchor="w", justify="left").pack(
            fill="x", padx=sc(12), pady=(sc(9), sc(2)))
        where = ("便携模式：数据就在 exe 旁边" if paths.is_portable()
                 else "exe 所在目录不可写，数据落在 %APPDATA%\\ms-pb-viewer")
        tk.Label(box, text=where + "。删掉这个文件夹 = 清空本地全部数据（含登录状态）。",
                 bg=C_PANEL, fg=C_DIM, font=FONT_SMALL, anchor="w",
                 justify="left", wraplength=sc(660)).pack(
            fill="x", padx=sc(12), pady=(0, sc(9)))

        row = tk.Frame(wrap, bg=C_BG)
        row.pack(fill="x", pady=(0, sc(10)))
        Pill(row, "打开数据目录", command=self.open_dir,
             accent=C_ACCENT, padx=16, height=30).pack(side="left")
        Pill(row, "复制路径", command=self.copy_path,
             accent=C_ACCENT2, padx=16, height=30).pack(side="left", padx=(sc(8), 0))
        Pill(row, "导出诊断包…", command=self.export,
             accent=C_ACCENT3, padx=16, height=30).pack(side="left", padx=(sc(8), 0))
        Pill(row, "清空缓存", command=self.clear_cache,
             accent=C_WARN, padx=16, height=30).pack(side="left", padx=(sc(8), 0))
        Pill(row, "关闭", command=self.destroy,
             accent=C_DIM, padx=18, height=30).pack(side="right")
        self.lbl_msg = tk.Label(row, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL)
        self.lbl_msg.pack(side="left", padx=(sc(10), 0))
        self.lbl_msg.config(text="扫描缓存 %.1f MB" % (cache.size_on_disk() / 1048576.0))

        tk.Label(wrap, text="最近日志（app.log 末尾）", bg=C_BG, fg=C_DIM,
                 font=FONT_SMALL).pack(anchor="w")
        holder = tk.Frame(wrap, bg=C_PANEL, highlightthickness=1,
                          highlightbackground=C_LINE)
        holder.pack(fill="both", expand=True, pady=(sc(4), 0))
        self.txt = tk.Text(holder, bg=C_PANEL, fg=C_TEXT, relief="flat",
                           font=FONT_MONO, wrap="char", height=10,
                           insertbackground=C_ACCENT, selectbackground=C_SEL)
        sb = ttk.Scrollbar(holder, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True, padx=sc(8), pady=sc(6))
        self.txt.insert("1.0", diag.recent_log(300))
        self.txt.config(state="disabled")
        self.txt.yview_moveto(1.0)          # 只在竖直方向滚到底，别把行首推出去

    def open_dir(self):
        try:
            os.startfile(paths.data_dir())
        except Exception as exc:
            self.lbl_msg.config(text="打不开：%s" % exc, fg=C_ERR)

    def copy_path(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(paths.data_dir())
            self.lbl_msg.config(text="路径已复制", fg=C_OK)
        except tk.TclError:
            pass

    def clear_cache(self):
        """清掉扫描缓存。录像没变的话下次要重新逐局重演，会慢一些。"""
        try:
            cache.clear()
        except Exception as exc:            # noqa: BLE001
            self.lbl_msg.config(text="清空失败：%s" % exc, fg=C_ERR)
            return
        diag.log.info("已清空扫描缓存")
        self.lbl_msg.config(text="扫描缓存已清空（0.0 MB）", fg=C_OK)

    def export(self):
        name = "ms-pb-viewer-diagnostics-%s.zip" % datetime.now().strftime("%Y%m%d-%H%M%S")
        target = filedialog.asksaveasfilename(
            parent=self, title="保存诊断包", defaultextension=".zip",
            initialfile=name, initialdir=paths.data_dir(),
            filetypes=[("ZIP 压缩包", "*.zip")])
        if not target:
            return
        try:
            diag.export_bundle(target)
        except Exception as exc:
            diag.log.exception("导出诊断包失败")
            self.lbl_msg.config(text="导出失败：%s" % exc, fg=C_ERR)
            return
        diag.log.info("已导出诊断包：%s", target)
        self.lbl_msg.config(text="已导出", fg=C_OK)
        messagebox.showinfo(
            APP_TITLE,
            "诊断包已生成：\n" + target +
            "\n\n里面只有日志、环境信息和脱敏后的配置（不含密码、不含会话密文）。",
            parent=self)


class PathsDialog(tk.Toplevel):
    """录像目录管理。

    为什么不另开一个「阿比特」tab：阿比特只是另一种后缀，跟元扫雷的 replay
    在分析上是同一件事，分成两个 tab 会让人以为 PB 也分了两套。这里让所有目录
    平级排队，谁有 .avf 就按阿比特解析；「启用」控制参不参与这次分析。
    上传要不要包含阿比特，是上传页的事（那边有「含阿比特录像」勾选框）。
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("录像目录")
        self.configure(bg=C_BG)
        self.transient(app)
        self.geometry(f"{sc(760)}x{sc(492)}")
        self.minsize(sc(640), sc(400))

        self.roots = [dict(r) for r in app.roots]
        self.peeks = {}

        wrap = tk.Frame(self, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=sc(20), pady=sc(16))

        tk.Label(wrap, text="录像目录", bg=C_BG, fg=C_TEXT,
                 font=FONT_SECTION).pack(anchor="w")
        tk.Label(wrap, text="可以加多个文件夹 —— 元扫雷的 replay、阿比特的存档、"
                            "移动硬盘里的备份，分析时一起读",
                 bg=C_BG, fg=C_DIM, font=FONT_SMALL).pack(anchor="w",
                                                          pady=(sc(2), sc(10)))

        holder = tk.Frame(wrap, bg=C_LINE)
        holder.pack(fill="both", expand=True)
        cols = [
            ("on_cn", "启用", 46, "center", "str"),
            ("src_cn", "类型", 66, "center", "str"),
            ("cnt_cn", "录像数", 70, "e", "str"),
            ("path", "目录（双击复制）", 420, "w", "str"),
        ]
        self.tbl = Table(holder, cols, height=7, iid_key="path",
                         selectmode="extended")
        self.tbl.pack(fill="both", expand=True, padx=1, pady=1)

        row = tk.Frame(wrap, bg=C_BG)
        row.pack(fill="x", pady=(sc(10), 0))
        Pill(row, "添加目录…", command=self.add_dir,
             accent=C_ACCENT, padx=16, height=30).pack(side="left")
        Pill(row, "启用 / 停用", command=self.toggle_on,
             accent=C_ACCENT2, padx=15, height=30).pack(side="left", padx=(sc(8), 0))
        Pill(row, "移除", command=self.remove_sel,
             accent=C_ERR, padx=15, height=30).pack(side="left", padx=(sc(8), 0))
        Pill(row, "关闭", command=self.destroy,
             accent=C_DIM, padx=18, height=30).pack(side="right")
        self.lbl_msg = tk.Label(row, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL)
        self.lbl_msg.pack(side="left", padx=(sc(10), 0))

        self.lbl_tip = tk.Label(
            wrap, text="启动时只自动找元扫雷的 replay 目录；阿比特的录像目录一律要手动添加。\n"
                       "加进来的目录会被当作「全是你自己打的」，别把下载来观摩的录像留在里面。\n"
                       "阿比特的 .avf 按内容解析，不看文件名；打比赛产生的录像标识带比赛编号，\n"
                       "先去网站把完整标识登记上，否则上传会被记成「标识不符」。",
            bg=C_BG, fg=C_DIM, font=FONT_SMALL, anchor="w", justify="left",
            wraplength=sc(690))
        self.lbl_tip.pack(fill="x", pady=(sc(8), 0))

        self._refresh()

    # --- 数据 ---
    def _peek_all(self):
        for r in self.roots:
            if r["path"] not in self.peeks:
                self.peeks[r["path"]] = core.peek_dir(r["path"])

    def _refresh(self):
        self._peek_all()
        rows = []
        for r in self.roots:
            pk = self.peeks.get(r["path"]) or {}
            ms, arb = pk.get("ms", 0), pk.get("arb", 0)
            if ms and arb:
                kind = "两者"
            elif ms:
                kind = "元扫雷"
            elif arb:
                kind = "阿比特"
            else:
                kind = "过大" if pk.get("truncated") else "未发现"
            if ms and arb:
                cnt = "%d + %d" % (ms, arb)
            else:
                cnt = str(ms or arb or "—")
            rows.append({
                "path": r["path"],
                "on_cn": "✓" if r.get("on", True) else "—",
                "src_cn": kind,
                "cnt_cn": cnt,
            })
        self.tbl.set_rows(rows)
        n_on = sum(1 for r in self.roots if r.get("on", True))
        big = [r for r in self.roots if (self.peeks.get(r["path"]) or {}).get("truncated")]
        msg = "%d 个目录，%d 个启用" % (len(self.roots), n_on)
        if big:
            msg += "（有目录太大，只数了前 %d 个子目录）" % core.MAX_SCAN_DIRS
        self.lbl_msg.config(text=msg)

    def _apply(self):
        self.app.apply_roots(self.roots, rescan=True)
        self.roots = [dict(r) for r in self.app.roots]
        self._refresh()

    # --- 操作 ---
    def add_dir(self):
        initial = (self.app.root_dir or core.autodetect_root()
                   or os.path.join(os.environ.get("APPDATA") or "", "Metasweeper"))
        d = filedialog.askdirectory(
            parent=self, title="添加录像目录（阿比特的 replays 也在这里加）",
            initialdir=initial if os.path.isdir(initial) else None)
        if not d:
            return
        d = os.path.abspath(d)
        if any(os.path.normcase(r["path"]) == os.path.normcase(d) for r in self.roots):
            self.lbl_msg.config(text="这个目录已经在列表里了", fg=C_WARN)
            return
        if not confirm_own_videos(self, d):
            self.lbl_msg.config(text="没有添加 —— 里面如果有别人的录像，先挪走再说",
                                fg=C_WARN)
            return
        self.roots.append({"path": d, "on": True})
        self._apply()

    def _selected(self):
        return list(self.tbl.tv.selection())

    def toggle_on(self):
        sel = self._selected()
        if not sel:
            self.lbl_msg.config(text="先在上表里点一行", fg=C_WARN)
            return
        for r in self.roots:
            if r["path"] in sel:
                r["on"] = not r.get("on", True)
        self._apply()

    def remove_sel(self):
        sel = set(self._selected())
        if not sel:
            self.lbl_msg.config(text="先在上表里点一行", fg=C_WARN)
            return
        rest = [r for r in self.roots if r["path"] not in sel]
        if not rest and not messagebox.askyesno(
                "录像目录", "这是最后一个目录，移掉之后程序就没有可读的录像了。\n继续吗？",
                parent=self):
            return
        # 只从配置里去掉，绝不动磁盘上的文件
        self.roots = rest
        self._apply()


# ------------------------------------------------------------------ 主窗口
class App(tk.Tk):
    def __init__(self, root_dir=None):
        ensure_bundled_fonts()           # 必须早于 tk.Tk()：Tk 只在创建时枚举字体
        super().__init__()
        enable_dpi()                     # 幂等：manifest 已声明时这里是空操作
        global SCALE
        SCALE = detect_scale(self)
        self._init_fonts()
        self.title(f"{APP_TITLE}  v{APP_VER}")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        wl, wt, ww, wh = work_area() or (0, 0, sw, sh)
        w = min(sc(1200), ww - sc(16))
        h = min(sc(790), wh - sc(16))
        x = wl + max(0, (ww - w) // 2)
        y = wt + max(0, (wh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")
        # 上限锁在可用区内：内容（大表格）的请求尺寸比窗口大时，Tk 会把窗口撑出去
        self.maxsize(ww, wh)
        self.minsize(sc(940), sc(620))
        self.configure(bg=C_BG)

        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
            else os.path.dirname(os.path.abspath(__file__))
        self.roots = load_roots()
        given = [os.path.abspath(p) for p in
                 (root_dir if isinstance(root_dir, (list, tuple)) else [root_dir])
                 if p]                       # 命令行指定的目录 → 视为「主目录」，置顶
        if given:
            keys = {os.path.normcase(p) for p in given}
            self.roots = load_roots({"roots": [{"path": p, "on": True} for p in given] +
                                              [r for r in self.roots
                                               if os.path.normcase(r["path"]) not in keys]})
        if not self.roots:
            # 默认只认元扫雷的 replay（可多个版本）；阿比特目录必须手动添加
            auto = core.default_roots()
            near = core.discover_root(base)      # exe 就放在 Metasweeper 目录里时优先认本地这份
            if near:
                key = os.path.normcase(near)
                auto = [near] + [p for p in auto if os.path.normcase(p) != key]
            if auto:
                self.roots = [{"path": p, "on": True} for p in auto]
                save_config(roots=self.roots)
        self._need_pick = not self.roots
        self.upload_arb = bool(load_config().get("upload_arb", True))
        diag.log.info("启动 v%s | 数据目录 %s | 便携 %s | 录像目录 %s",
                      APP_VER, paths.data_dir(), paths.is_portable(),
                      " ; ".join("%s%s" % ("" if r["on"] else "(停用)", r["path"])
                                 for r in self.roots) or "（未确定）")
        self.games = []
        self.info = {}
        self.pb_rows = []
        self.summary = {}
        self.counts = {}
        self.diffs = []
        self.ini_ok = False
        self._scanning = False
        self._level = "b"
        self._pb_level = ""
        self._pb_style = "nf"          # PB 表风格：nf / fl / all
        self._top_style = "nf"         # 前端成绩风格
        self._bvmin_level = None
        self._bv_guard = False
        self._empty_prompted = False
        self._tab = "overview"

        # 上传相关状态
        self.oms = openms.Client()
        self.oms_user = None
        self.oms_videos = []
        self.oms_idents = []
        self.oms_err = ""
        self.oms_synced = False     # 本次是否真的拉到过网站录像列表
        self._last_failed = set()   # 上一批失败的路径（表格会被网站数据重建，得单独记）
        self._arb_rows = 0          # 本地发现多少个阿比特录像（不受「含阿比特」影响）
        self.up_rows = []
        self.up_sel = set()
        self._up_scopes = set()
        self._up_running = False
        self._up_stop = False
        self._up_total = 0
        self._up_done = 0
        self._up_ok = 0
        self._up_dup = 0
        self._up_fail = 0

        self._set_icon()
        self._refresh_icon()      # 窗口 map 后 Tk 会再设一次，隔一拍覆盖回来
        self._init_style()
        self._build_ui()
        self.after(120, self._apply_dwm)
        self._q = queue.Queue()
        self.after(60, self._poll)
        self.after(80, self._boot)
        self.after(700, self._restore_session)

    # ---------- 字体 / 图标 / 主题 ----------
    def _init_fonts(self):
        global FAM_HEAD, FAM_UI, FAM_MONO
        global FONT_UI, FONT_UI_B, FONT_SMALL, FONT_SECTION
        global FONT_BRAND, FONT_BIG, FONT_MONO, FONT_MONO_B
        fams = set(tkfont.families(self))

        def pick(*cands):
            for c in cands:
                if c in fams:
                    return c
            return "Microsoft YaHei UI"

        FAM_HEAD = pick("MiSans", "HarmonyOS Sans SC", "Noto Sans SC", "思源黑体",
                        "Microsoft YaHei UI", "微软雅黑")
        FAM_UI = FAM_HEAD
        FAM_MONO = pick("Consolas", "Cascadia Mono", "Courier New")

        FONT_UI = (FAM_UI, 11)
        FONT_UI_B = (FAM_UI, 11, "bold")
        FONT_SMALL = (FAM_UI, 9)
        FONT_SECTION = (FAM_HEAD, 13, "bold")
        FONT_BRAND = (FAM_HEAD, 27, "bold")
        FONT_BIG = (FAM_HEAD, 18, "bold")
        FONT_MONO = (FAM_MONO, 11)
        FONT_MONO_B = (FAM_MONO, 11, "bold")

    def _icon_path(self):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        for d in (base, os.path.dirname(os.path.abspath(__file__)),
                  os.path.dirname(sys.executable)):
            p = os.path.join(d, "icon.ico")
            if os.path.isfile(p):
                return p
        return None

    def _set_icon(self):
        """给窗口装图标。

        Tk 的 `iconbitmap` 只从 ico 里取**一张**（按系统大图标尺寸），
        任务栏按钮却按 24dp × 缩放 渲染（150% 下 36px），于是 32 被拉到 36，糊。
        所以这里自己按「任务栏实际像素」精确 LoadImage 再 WM_SETICON：
        ico 里有对应档位（本工具会生成 30/36/42 等非标准档）就是 1:1，零缩放。
        """
        ico = self._icon_path()
        if not ico:
            return
        try:
            self.iconbitmap(ico)         # 兜底：Tk 自己那套也设上
        except Exception:
            pass
        try:
            self.update_idletasks()
            u = ctypes.windll.user32
            hwnd = u.GetAncestor(self.winfo_id(), 2) or u.GetParent(self.winfo_id())
            if not hwnd:
                return
            try:
                dpi = u.GetDpiForWindow(hwnd) or 96
            except Exception:
                dpi = 96
            self._hicons = []            # 保住句柄，别被回收

            def load(px):
                px = max(8, int(round(px)))
                h = u.LoadImageW(None, ico, 1, px, px, 0x10)   # IMAGE_ICON | LR_LOADFROMFILE
                if h:
                    self._hicons.append(h)
                return h

            big = load(24 * dpi / 96.0)      # 任务栏 / Alt-Tab
            small = load(16 * dpi / 96.0)    # 标题栏左上角
            if big:
                u.SendMessageW(hwnd, 0x0080, 1, big)    # WM_SETICON ICON_BIG
            if small or big:
                u.SendMessageW(hwnd, 0x0080, 0, small or big)   # ICON_SMALL
        except Exception:
            pass

    def _refresh_icon(self):
        """窗口 map 之后 Tk 可能再设一次图标，隔一拍补回来。"""
        self.after(260, self._set_icon)

    def _apply_dwm(self):
        """Win11：把标题栏染成樱粉底深紫字，融进界面。"""
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id()) or self.winfo_id()
            cap = ctypes.c_int(0x00F4EAF9)      # 0x00BBGGRR ← #f9eaf4
            txt = ctypes.c_int(0x0052333C)      # ← #3c3352
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35, ctypes.byref(cap), ctypes.sizeof(cap))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 36, ctypes.byref(txt), ctypes.sizeof(txt))
        except Exception:
            pass

    def _init_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=C_BG, foreground=C_TEXT, font=FONT_UI)
        st.configure("TFrame", background=C_BG)
        st.configure("Card.TFrame", background=C_PANEL)
        st.configure("TLabel", background=C_BG, foreground=C_TEXT, font=FONT_UI)
        st.configure("Dim.TLabel", background=C_BG, foreground=C_DIM, font=FONT_UI)
        st.configure("Treeview", background=C_PANEL, fieldbackground=C_PANEL,
                     foreground=C_TEXT, rowheight=sc(32), borderwidth=0,
                     font=FONT_MONO)
        st.configure("Treeview.Heading", background="#f9eef8", foreground="#8a6fa4",
                     relief="flat", font=FONT_UI_B, padding=(sc(7), sc(7)))
        st.map("Treeview", background=[("selected", C_SEL)],
               foreground=[("selected", C_TEXT)])
        st.map("Treeview.Heading", background=[("active", "#f4e4f3")])
        st.configure("Vertical.TScrollbar", background="#f3e6f4", troughcolor=C_PANEL,
                     bordercolor=C_PANEL, arrowcolor=C_DIM, width=sc(12))
        st.map("Vertical.TScrollbar", background=[("active", "#e9d5ec")])
        st.configure("Oms.Horizontal.TProgressbar", troughcolor="#f6eaf5",
                     background=C_ACCENT, bordercolor=C_BG, lightcolor=C_ACCENT,
                     darkcolor=C_ACCENT, thickness=sc(7))

    # ---------- 界面 ----------
    def _build_ui(self):
        self.banner = tk.Canvas(self, height=sc(96), bg=C_ACCENT,
                                highlightthickness=0, bd=0)
        self.banner.pack(fill="x")
        self.banner.bind("<Configure>", self._draw_banner)
        for tag, cmd in (("b_rescan", self.start_scan),
                         ("b_dir", lambda: self.pick_dir()),
                         ("b_csv", self.export_csv),
                         ("b_diag", self.open_diagnostics)):
            self.banner.tag_bind(tag, "<Button-1>", lambda _e, c=cmd: c())

        # 主导航
        nav = tk.Frame(self, bg=C_BG)
        nav.pack(fill="x", padx=sc(20), pady=(sc(12), sc(2)))
        self.tab_pills = {}
        for key, label in (("overview", "概览"), ("pb", "PB 表"),
                           ("top", "前端成绩"), ("upload", "上传")):
            p = Pill(nav, label, command=lambda k=key: self.show_tab(k),
                     accent=C_ACCENT, padx=22, height=32)
            p.pack(side="left", padx=(0, sc(8)))
            self.tab_pills[key] = p
        self.lbl_meta = tk.Label(nav, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL)
        self.lbl_meta.pack(side="right")
        self.btn_paths = Pill(nav, "路径…", command=self.open_paths,
                              accent=C_ACCENT3, padx=14, height=30)
        self.btn_paths.pack(side="right", padx=(sc(10), 0))

        # 状态栏先于内容 pack（side=bottom），保证窗口再小也不会被挤出去
        bar = tk.Frame(self, bg=C_BG)
        bar.pack(side="bottom", fill="x", padx=sc(20), pady=(sc(6), sc(10)))
        self.lbl_status = tk.Label(bar, text="准备中…", bg=C_BG, fg=C_DIM, font=FONT_UI)
        self.lbl_status.pack(side="left")
        self.lbl_check = tk.Label(bar, text="", bg=C_BG, fg=C_DIM, font=FONT_UI)
        self.lbl_check.pack(side="right")
        tk.Frame(self, bg=C_LINE, height=1).pack(side="bottom", fill="x",
                                                 padx=sc(20), pady=(sc(4), 0))

        # 读取进度条：只在扫描期间出现（见 _progress_begin / _progress_end）。
        # 先建不 pack —— 用的时候再 pack(side="bottom")，它会落在分隔线上方。
        self.pbar_box = tk.Frame(self, bg=C_BG)
        self.pbar = ttk.Progressbar(self.pbar_box, style="Oms.Horizontal.TProgressbar",
                                    mode="determinate", maximum=100, value=0)
        self.pbar.pack(fill="x")
        self._scan_total = 0
        self._scan_done = 0

        body = tk.Frame(self, bg=C_BG)
        body.pack(fill="both", expand=True, padx=sc(20), pady=(sc(2), 0))
        self.pages = {
            "overview": tk.Frame(body, bg=C_BG),
            "pb": tk.Frame(body, bg=C_BG),
            "top": tk.Frame(body, bg=C_BG),
            "upload": tk.Frame(body, bg=C_BG),
        }
        self._page_host = body
        # 关键：阻止页面内容反向撑大窗口。
        # 表格会按可用高度自适应行数，行数变大 → 请求高度变大 → 窗口被顶高 →
        # 表格又算出更多行……形成正反馈，窗口一路涨到超出屏幕。
        body.pack_propagate(False)
        self._build_overview()
        self._build_pb()
        self._build_top()
        self._build_upload()
        self.show_tab("overview")

    def show_tab(self, key):
        self._tab = key
        for k, page in self.pages.items():
            if k == key:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        for k, p in self.tab_pills.items():
            p.set_selected(k == key)

    # ---------- 横幅 ----------
    def _draw_banner(self, event=None):
        cv = self.banner
        w = event.width if event else cv.winfo_width()
        h = event.height if event else cv.winfo_height()
        if w < 40 or h < 20:
            return
        cv.delete("all")

        # 粉 → 紫 → 蓝 渐变
        gradient_rect(cv, 0, 0, w, h, [
            (0.0, "#ff6fa4"), (0.42, "#9b6fe3"), (1.0, "#3fb0e6"),
        ])

        # 标题左侧：地雷徽标
        cy = h * 0.46
        r = sc(13)
        cx = sc(38)
        cv.create_oval(cx - r * 1.75, cy - r * 1.75, cx + r * 1.75, cy + r * 1.75,
                       fill="#ffffff", width=0)
        draw_mine(cv, cx, cy, r * 0.78, color=mix(C_ACCENT, C_ACCENT3, 0.5))

        # 标题（先画深色投影再叠白色，避免 1px 偏移出现锯齿毛边）
        tx = cx + sc(38)
        title_font = (FAM_HEAD, 27, "bold")
        cv.create_text(tx + sc(2), cy + sc(2), text=APP_TITLE, anchor="w",
                       fill=mix(C_ACCENT3, "#6b3f6a", 0.45), font=title_font)
        cv.create_text(tx, cy, text=APP_TITLE, anchor="w",
                       fill="#ffffff", font=title_font)
        cv.create_text(tx + sc(3), cy + sc(24),
                       text=f"{APP_SUB}   v{APP_VER}", anchor="w",
                       fill="#fff2f8", font=(FAM_UI, 9))

        # 星屑
        for x, y, s, a in ((w * 0.30, h * 0.28, 9, "#ffffff"),
                           (w * 0.36, h * 0.66, 6, "#ffe6f2"),
                           (w * 0.24, h * 0.72, 5, "#ffffff")):
            sparkle(cv, x, y, sc(s), a)

        # 右侧按钮（白底粉字）
        font_m = tkfont.Font(family=FAM_UI, size=9, weight="bold")
        buttons = [("导出 CSV", "b_csv"), ("换目录…", "b_dir"),
                   ("重新扫描", "b_rescan"), ("诊断…", "b_diag")]
        right = w - sc(22)
        bh = sc(30)
        by = cy - bh / 2.0
        for text, tag in buttons:
            bw = font_m.measure(text) + sc(34)
            x1 = right - bw
            round_rect(cv, x1, by, right, by + bh, bh / 2.0,
                       fill="#ffffff", width=0, tags=(tag,))
            cv.create_text((x1 + right) / 2.0, by + bh / 2.0, text=text,
                           fill=C_ACCENT, font=(FAM_UI, 9, "bold"), tags=(tag,))
            right = x1 - sc(10)

        # 底部柔和收口
        cv.create_line(0, h - 1, w, h - 1, fill="#ffffff", width=1, stipple="gray50")

    # ---------- 小工具 ----------
    def _section(self, parent, title, accent=C_ACCENT, sub=""):
        bar = tk.Frame(parent, bg=C_BG)
        tk.Frame(bar, bg=accent, width=sc(4), height=sc(15)).pack(side="left")
        tk.Label(bar, text=title, bg=C_BG, fg=C_TEXT,
                 font=FONT_SECTION).pack(side="left", padx=(sc(8), 0))
        if sub:
            tk.Label(bar, text=sub, bg=C_BG, fg=C_DIM,
                     font=FONT_SMALL).pack(side="left", padx=(sc(8), 0))
        return bar

    def _table(self, parent, cols, height=8, pady=(0, 0), **kw):
        """带 1px 描边的表格容器，返回 (holder, table)。"""
        holder = tk.Frame(parent, bg=C_LINE)
        holder.pack(fill="both", expand=True, pady=pady)
        t = Table(holder, cols, height=height, **kw)
        t.pack(fill="both", expand=True, padx=1, pady=1)
        return holder, t

    def _build_overview(self):
        page = self.pages["overview"]

        cards = tk.Frame(page, bg=C_BG)
        cards.pack(fill="x", pady=(sc(10), 0))
        for i in range(3):
            cards.columnconfigure(i, weight=1, uniform="c")
        self.cards = {}
        for i, lv in enumerate(core.LEVELS):
            card = StatCard(cards, lv)
            card.grid(row=0, column=i, sticky="nsew",
                      padx=(0 if i == 0 else sc(12), 0))
            self.cards[lv] = card

        head = self._section(page, "最近完成局", C_ACCENT2, "按文件时间倒序，双击复制路径")
        head.pack(fill="x", pady=(sc(16), sc(6)))

        # 说明行先占底部，表格再吃剩余空间，避免被内容挤出窗口
        tk.Label(page, text="✧ 口径：PB = 同级别同 BV 下 time 最小的完成局（NF / FL 各成一张榜）；"
                            "NF/FL 由录像里的 rce（右键有效插旗数）判定，rce = 0 即 NF；"
                            "非完成局不计入，全程只读。",
                 bg=C_BG, fg=C_DIM, font=FONT_SMALL, anchor="w").pack(
            side="bottom", fill="x", pady=(sc(8), sc(2)))

        cols = [
            ("level_cn", "级别", 64, "center", "str"),
            ("style_cn", "风格", 52, "center", "str"),
            ("time", "time", 84, "e", "f3"),
            ("bv", "BV", 52, "e", "int"),
            ("bvs", "3BV/s", 78, "e", "f3"),
            ("stnb", "stnb", 72, "e", "f2"),
            ("date", "日期", 92, "center", "str"),
            ("clock", "时间", 72, "center", "str"),
            ("file", "录像文件", 470, "w", "str"),
        ]
        _, self.tbl_recent = self._table(page, cols, height=5)

        # 首扫时盖在内容区正中（place 不进布局，扫完 place_forget）
        self.lbl_boot = tk.Label(page, text="正在读取录像…\n首次扫描要逐局重演，稍等一下",
                                 bg=C_BG, fg=C_DIM, font=FONT_SECTION, justify="center",
                                 padx=sc(18), pady=sc(8))

    def _build_pb(self):
        page = self.pages["pb"]
        top = tk.Frame(page, bg=C_BG)
        top.pack(fill="x", pady=(sc(10), sc(8)))
        self.pb_seg = {}
        for key, label in (("", "全部"), ("b", "初级"), ("i", "中级"), ("e", "高级")):
            p = Pill(top, label, command=lambda k=key: self.set_pb_level(k),
                     accent=C_ACCENT, padx=16)
            p.pack(side="left", padx=(0, sc(6)))
            self.pb_seg[key] = p

        tk.Frame(top, bg=C_LINE, width=1).pack(side="left", fill="y",
                                               padx=(sc(8), sc(10)), pady=sc(2))
        self.pb_style_seg = {}
        for key, label in (("nf", "NF"), ("fl", "FL"), ("all", "全部风格")):
            p = Pill(top, label, command=lambda k=key: self.set_pb_style(k),
                     accent=C_ACCENT3, padx=14)
            p.pack(side="left", padx=(0, sc(6)))
            self.pb_style_seg[key] = p

        tk.Label(top, text="搜索 BV / 文件名", bg=C_BG, fg=C_DIM,
                 font=FONT_UI).pack(side="left", padx=(sc(14), sc(6)))
        self.var_q = tk.StringVar()
        self._entry(top, self.var_q, width=18).pack(side="left")
        self.var_q.trace_add("write", lambda *_: self.render_pb())
        self.lbl_pb_count = tk.Label(top, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL)
        self.lbl_pb_count.pack(side="right")

        cols = [
            ("level_cn", "级别", 64, "center", "str"),
            ("bv", "BV", 60, "e", "int"),
            ("style_cn", "风格", 56, "center", "str"),
            ("time", "最快时间", 100, "e", "f3"),
            ("bvs", "3BV/s", 90, "e", "f3"),
            ("stnb", "stnb", 90, "e", "f2"),
            ("games", "该 BV 局数", 96, "e", "int"),
            ("file", "录像文件", 520, "w", "str"),
        ]
        _, self.tbl_pb = self._table(page, cols, height=6)   # 初始行数要小，靠 _fit 自适应撑满
        self.set_pb_style(self._pb_style)

    def _entry(self, parent, var, width=8):
        return tk.Entry(parent, textvariable=var, width=width, bg=C_PANEL, fg=C_TEXT,
                        relief="flat", font=FONT_MONO_B, justify="center",
                        highlightthickness=1, highlightbackground=C_LINE,
                        highlightcolor=C_ACCENT, insertbackground=C_ACCENT,
                        selectbackground=C_SEL, selectforeground=C_TEXT)

    def _build_top(self):
        page = self.pages["top"]
        top = tk.Frame(page, bg=C_BG)
        top.pack(fill="x", pady=(sc(10), sc(8)))
        self.top_seg = {}
        for lv in core.LEVELS:
            p = Pill(top, core.LEVEL_CN[lv], command=lambda k=lv: self.set_top_level(k),
                     accent=LEVEL_ACCENT[lv][0], padx=18)
            p.pack(side="left", padx=(0, sc(6)))
            self.top_seg[lv] = p

        tk.Frame(top, bg=C_LINE, width=1).pack(side="left", fill="y",
                                               padx=(sc(8), sc(10)), pady=sc(2))
        self.top_style_seg = {}
        for key, label in (("nf", "NF"), ("fl", "FL"), ("all", "全部风格")):
            p = Pill(top, label, command=lambda k=key: self.set_top_style(k),
                     accent=C_ACCENT3, padx=14)
            p.pack(side="left", padx=(0, sc(6)))
            self.top_style_seg[key] = p

        tk.Label(top, text="BV 下限", bg=C_BG, fg=C_DIM,
                 font=FONT_UI).pack(side="left", padx=(sc(14), sc(6)))
        self.var_bvmin = tk.StringVar(value=str(core.BV_MIN_DEFAULT["b"]))
        self._entry(top, self.var_bvmin, width=6).pack(side="left", ipady=sc(2))
        self.var_bvmin.trace_add("write", lambda *_: self._on_bvmin_change())
        tk.Label(top, text="（默认官方门槛，0 = 不限）",
                 bg=C_BG, fg=C_DIM, font=FONT_SMALL).pack(side="left", padx=(sc(8), 0))

        grid = tk.Frame(page, bg=C_BG)
        # 只吃横向空间：纵向让它按内容高度走，多余高度全留给下方说明面板，
        # 否则「表格区」和「说明面板」各分一半空白，中间会裂出一条空带。
        grid.pack(fill="x")
        for i in range(3):
            grid.columnconfigure(i, weight=1, uniform="t")
        grid.rowconfigure(1, weight=1)

        self.top_tables = {}
        specs = [("time", "最快时间 Top5", "越小越好"),
                 ("bvs", "最高 3BV/s Top5", "每秒解 BV"),
                 ("stnb", "最高 stnb Top5", "综合实力")]
        for i, (metric, title, sub) in enumerate(specs):
            cell = tk.Frame(grid, bg=C_BG)
            cell.grid(row=0, column=i, sticky="new",
                      padx=(0 if i == 0 else sc(12), 0))
            self._section(cell, title, C_ACCENT if i == 0 else
                          (C_ACCENT2 if i == 1 else C_ACCENT3), sub).grid(
                row=0, column=0, sticky="w", pady=(0, sc(6)))
            cols = [
                ("rank", "#", 34, "e", "int"),
                ("style_cn", "风格", 52, "center", "str"),
                ("time", "time", 66, "e", "f3"),
                ("bv", "BV", 46, "e", "int"),
                ("bvs", "3BV/s", 62, "e", "f3"),
                ("stnb", "stnb", 60, "e", "f2"),
            ]
            holder = tk.Frame(cell, bg=C_LINE)
            holder.grid(row=1, column=0, sticky="ew")
            t = Table(holder, cols, height=5, fit=False, vbar=False)
            t.pack(fill="both", expand=True, padx=1, pady=1)
            self.top_tables[metric] = t
        self.set_top_style(self._top_style)

        # 说明面板：左右两栏。按内容高度走，不硬撑到页面底部（否则卡片里会空一大片）
        card = tk.Frame(page, bg=C_LINE)
        card.pack(fill="x", pady=(sc(14), sc(2)))
        inner = tk.Frame(card, bg=C_PANEL)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        inner.rowconfigure(0, weight=1)
        inner.rowconfigure(2, weight=1)
        content = tk.Frame(inner, bg=C_PANEL)
        content.grid(row=1, column=0, sticky="ew")
        content.columnconfigure(0, weight=3, uniform="info")
        content.columnconfigure(1, weight=2, uniform="info")

        left = tk.Frame(content, bg=C_PANEL)
        left.grid(row=0, column=0, sticky="nw", padx=(sc(18), sc(10)), pady=sc(10))
        tk.Label(left, text="✧ 指标说明", bg=C_PANEL, fg=C_TEXT,
                 font=FONT_SECTION).pack(anchor="w", pady=(0, sc(10)))
        lines = [
            ("time", "rtime，从 0 秒起计时，越小越好。"),
            ("bvs", "bv / time，每秒解决的 BV 数，越大越好，最经典的速度指标。"),
            ("stnb", "C × bv / time^1.7，三大参数之一；C 取 36 / 162 / 435，"
                     "使三级别量级可比。"),
            ("rce", "右键有效插旗数：只算「第一次给雷格插旗」，同格反复插拔不重复，"
                    "插在非雷格上也不算。rce = 0 即 NF，否则 FL。"),
        ]
        for key, desc in lines:
            row = tk.Frame(left, bg=C_PANEL)
            row.pack(fill="x", pady=sc(4))
            tk.Label(row, text=key, bg=C_PANEL, fg=C_ACCENT, width=6, anchor="w",
                     font=FONT_MONO_B).pack(side="left")
            tk.Label(row, text=desc, bg=C_PANEL, fg=C_DIM, anchor="w",
                     justify="left", font=FONT_UI).pack(side="left")

        right = tk.Frame(content, bg=C_PANEL)
        right.grid(row=0, column=1, sticky="nw", padx=(sc(10), sc(18)), pady=sc(10))
        tk.Label(right, text="✧ 官方 BV 门槛", bg=C_PANEL, fg=C_TEXT,
                 font=FONT_SECTION).pack(anchor="w", pady=(0, sc(10)))
        gate = tk.Frame(right, bg=C_PANEL)
        gate.pack(fill="x")
        for lv in core.LEVELS:
            cell = tk.Frame(gate, bg=LEVEL_ACCENT[lv][1])
            cell.pack(side="left", padx=(0, sc(8)))
            tk.Label(cell, text=core.LEVEL_CN[lv], bg=LEVEL_ACCENT[lv][1],
                     fg=LEVEL_ACCENT[lv][0], font=FONT_SMALL).pack(
                padx=sc(10), pady=(sc(6), 0))
            tk.Label(cell, text=f"≥ {core.BV_MIN_DEFAULT[lv]}", bg=LEVEL_ACCENT[lv][1],
                     fg=LEVEL_ACCENT[lv][0], font=FONT_BIG).pack(
                padx=sc(12), pady=(0, sc(6)))
        tk.Label(right, text="低于门槛的盘面随机性过大，不参与标准评级。\n"
                             "本页默认按门槛过滤，可直接改写输入框。",
                 bg=C_PANEL, fg=C_DIM, font=FONT_SMALL, justify="left",
                 anchor="w").pack(anchor="w", pady=(sc(8), 0))

    # ---------- 上传页 ----------
    def _build_upload(self):
        page = self.pages["upload"]

        # 第一行：账号
        head = tk.Frame(page, bg=C_BG)
        head.pack(fill="x", pady=(sc(10), sc(6)))
        self.lbl_acct = tk.Label(head, text="账号：未登录", bg=C_BG, fg=C_DIM,
                                 font=FONT_UI)
        self.lbl_acct.pack(side="left")
        self.btn_login = Pill(head, "登录…", command=self.open_login,
                              accent=C_ACCENT, padx=16, height=30)
        self.btn_login.pack(side="left", padx=(sc(18), sc(6)))
        self.btn_logout = Pill(head, "退出登录", command=self.do_logout,
                               accent=C_DIM, padx=15, height=30)
        self.btn_logout.pack(side="left", padx=(0, sc(6)))
        self.btn_sync = Pill(head, "刷新网站记录", command=self.refresh_site,
                             accent=C_ACCENT2, padx=17, height=30)
        self.btn_sync.pack(side="left")
        self.lbl_quota = tk.Label(head, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL)
        self.lbl_quota.pack(side="right")

        # 第二行：范围（可多选叠加）
        row = tk.Frame(page, bg=C_BG)
        row.pack(fill="x", pady=(0, sc(6)))
        tk.Label(row, text="范围", bg=C_BG, fg=C_DIM,
                 font=FONT_UI).pack(side="left", padx=(0, sc(6)))
        self.scope_pills = {}
        for key, label in openms.SCOPES:
            p = Pill(row, label, command=lambda k=key: self.toggle_scope(k),
                     accent=C_ACCENT3, padx=12, height=28)
            p.pack(side="left", padx=(0, sc(5)))
            self.scope_pills[key] = p
        self.btn_sel_all = Pill(row, "全选未传", command=self.select_all_pending,
                                accent=C_ACCENT2, padx=14, height=28)
        self.btn_sel_all.pack(side="right", padx=(sc(6), 0))
        self.btn_sel_none = Pill(row, "清空", command=self.clear_selection,
                                 accent=C_DIM, padx=12, height=28)
        self.btn_sel_none.pack(side="right", padx=(sc(6), 0))
        # 阿比特录像要不要一起传：跟「范围」并列，改动立刻反映在下面的表里
        self.var_arb = tk.BooleanVar(value=self.upload_arb)
        self.chk_arb = tk.Checkbutton(
            row, text="含阿比特录像", variable=self.var_arb,
            command=self.toggle_upload_arb, bg=C_BG, fg=C_TEXT,
            activebackground=C_BG, activeforeground=C_TEXT, selectcolor=C_PANEL,
            font=FONT_SMALL, bd=0, highlightthickness=0)
        self.chk_arb.pack(side="right", padx=(sc(14), sc(4)))

        self.lbl_up_stat = tk.Label(page, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL,
                                    anchor="w")
        self.lbl_up_stat.pack(fill="x", pady=(0, sc(6)))

        # 底部先占位（side=bottom），表格吃剩余空间
        foot = tk.Frame(page, bg=C_BG)
        foot.pack(side="bottom", fill="x", pady=(sc(8), sc(2)))
        self.btn_up_sel = Pill(foot, "上传选中", command=self.upload_selected,
                               accent=C_ACCENT, padx=20, height=34)
        self.btn_up_sel.pack(side="left")
        self.btn_up_all = Pill(foot, "上传全部未传", command=self.upload_all_pending,
                               accent=C_ACCENT2, padx=18, height=34)
        self.btn_up_all.pack(side="left", padx=(sc(8), 0))
        self.btn_stop = Pill(foot, "停止", command=self.stop_upload,
                             accent=C_ERR, padx=14, height=34)
        self.btn_stop.pack(side="left", padx=(sc(8), 0))
        self.btn_stop.set_enabled(False)
        self.btn_retry = Pill(foot, "重试失败", command=self.retry_failed,
                              accent=C_WARN, padx=15, height=34)
        self.btn_retry.pack(side="left", padx=(sc(8), 0))
        self.btn_retry.set_enabled(False)
        self.lbl_up_log = tk.Label(foot, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL,
                                   anchor="e")
        self.lbl_up_log.pack(side="right")

        prog = tk.Frame(page, bg=C_BG)
        prog.pack(side="bottom", fill="x", pady=(0, sc(2)))
        self.pb_up = ttk.Progressbar(prog, style="Oms.Horizontal.TProgressbar",
                                     length=sc(220), mode="determinate", maximum=1)
        self.pb_up.pack(side="left")
        self.lbl_up_prog = tk.Label(prog, text="", bg=C_BG, fg=C_DIM,
                                    font=FONT_SMALL)
        self.lbl_up_prog.pack(side="left", padx=(sc(8), 0))

        cols = [
            ("level_cn", "级别", 56, "center", "str"),
            ("style_cn", "风格", 50, "center", "str"),
            ("src_cn", "来源", 52, "center", "str"),
            ("time", "time", 70, "e", "f3"),
            ("bv", "BV", 44, "e", "int"),
            ("size_cn", "大小", 62, "e", "str"),
            ("status", "本地状态", 110, "w", "str"),
            ("site", "网站状态", 78, "center", "str"),
            ("file", "录像文件", 400, "w", "str"),
        ]
        _, self.tbl_up = self._table(page, cols, height=6,
                                     selectmode="extended", iid_key="path")
        self.tbl_up.tv.bind("<<TreeviewSelect>>",
                            lambda _e: self._on_up_select(), add="+")
        self._refresh_acct_label()

    # ---------- 账号 ----------
    def open_login(self):
        LoginDialog(self, self.oms, self._on_login_done)

    def _on_login_done(self, user):
        self.oms_user = user
        diag.log.info("登录成功：%s（实名=%s）", user.get("username"),
                      bool(user.get("realname")))
        self.set_status("已登录 openms.top：%s" % (user.get("username") or ""), "ok")
        self.refresh_site()

    def do_logout(self):
        if not messagebox.askyesno(
                APP_TITLE,
                "退出会清除本机保存的会话（Cookie），下次上传需要重新输验证码。\n继续吗？"):
            return
        self.oms.logout()
        diag.log.info("退出登录，已清除本机会话")
        self.oms_user = None
        self.oms_videos = []
        self.oms_idents = []
        self._refresh_acct_label()
        self._rebuild_up_rows()
        self.set_status("已退出登录", "dim")

    def refresh_site(self):
        if not self.oms_user:
            self.open_login()
            return
        self.lbl_quota.config(text="正在读取网站记录…", fg=C_DIM)
        threading.Thread(target=self._site_worker, daemon=True).start()

    def _restore_session(self):
        threading.Thread(target=self._site_worker, daemon=True).start()

    def _site_worker(self):
        """读登录态 + 我的录像 + 我的标识。三者都拿不到才算失败。"""
        try:
            u = self.oms.whoami()
        except openms.OmsError as exc:
            self._q.put(("acct", (self.oms_user, self.oms_videos,
                                  self.oms_idents, "连不上 openms.top：%s" % exc)))
            return
        if not u:
            self._q.put(("acct", (None, [], [], "")))
            return
        vids, idents, err = [], [], ""
        try:
            vids = self.oms.videos(u["id"])
        except Exception as exc:                     # noqa: BLE001
            err = "读取录像列表失败：%s" % exc
        try:
            idents = self.oms.identifiers(u["id"])
        except Exception:                            # noqa: BLE001
            pass
        self._q.put(("acct", (u, vids, idents, err)))

    def _on_account(self, user, videos, idents, err):
        self.oms_user = user
        self.oms_videos = videos or []
        self.oms_idents = idents or []
        self.oms_err = err
        # 只有真的拉到了列表，才敢拿它否认「本地台账」里的记录
        self.oms_synced = bool(user) and not err
        self._refresh_acct_label()
        self._rebuild_up_rows()

    def _refresh_acct_label(self):
        if self.oms_user:
            name = self.oms_user.get("username") or "?"
            self.lbl_acct.config(text="账号：%s · 已登录" % name, fg=C_OK)
            self.btn_login.set_enabled(False)
        else:
            self.lbl_acct.config(text="账号：未登录", fg=C_WARN)
            self.btn_login.set_enabled(True)
        bits = []
        if self.oms_user:
            bits.append("网站已有 %d 条" % len(self.oms_videos))
            if self.oms_idents:
                bits.append("标识 " + " / ".join(self.oms_idents))
            if self.oms_user.get("realname"):
                bits.append("已实名")
        n = journal.count()
        if n:
            bits.append("本地台账 %d" % n)
        if self.oms_err:
            bits.append(self.oms_err)
        self.lbl_quota.config(text=" · ".join(bits), fg=C_ERR if self.oms_err else C_DIM)

    # ---------- 上传：选范围 / 选行 ----------
    def _rebuild_up_rows(self):
        have = {}
        for v in self.oms_videos:
            k = openms.server_key(v)
            if k:
                have[k] = v
        idents = set(self.oms_idents or [])
        rows = []
        self._arb_rows = 0
        for g in sorted(self.games, key=lambda x: (core.LEVELS.index(x["level"]),
                                                   x["bv"], x["time"])):
            src = g.get("src") or "ms"
            if src == "arb":
                self._arb_rows += 1
                if not self.upload_arb:      # 用户选择不传阿比特：列表里直接不出现
                    continue
            k = openms.game_key(g)
            v = have.get(k)
            item = {
                "path": g["path"],
                "file": g["file"],
                "level_cn": core.LEVEL_CN[g["level"]],
                "style_cn": core.STYLE_CN.get(g.get("style"), "?"),
                "src_cn": core.SRC_CN.get(src, "元扫雷"),
                "time": g["time"],
                "bv": g["bv"],
                "size_cn": "%.1f KB" % ((g.get("size") or 0) / 1024.0),
                "valid": g.get("valid"),
            }
            if v:
                item["uploaded"] = True
                item["status"] = "网站已有"
                item["site"] = openms.STATE_CN.get(v.get("state"),
                                                   v.get("state") or "—")
            elif k and journal.has(k) and not self.oms_synced:
                # 网站列表这次没拿到（离线/未登录），但本地台账记着传过 —— 先别让人重传
                item["uploaded"] = True
                item["status"] = "已传过（本地记录）"
                item["site"] = "—"
            else:
                item["uploaded"] = False
                item["status"] = "未上传"
                item["site"] = "—"
                ident = g.get("ident") or ""
                if idents and ident and ident not in idents:
                    item["status"] = "标识不符"
                elif g.get("valid") == 1:
                    item["status"] = "会被冻结"
                elif g.get("valid") not in (None, 0):
                    item["status"] = "待人工审核"
            rows.append(item)

        pending = {r["path"] for r in rows if not r["uploaded"]}
        self.up_sel &= pending
        self.up_rows = rows
        self.tbl_up.set_rows(rows)
        self.tbl_up.select_paths(self.up_sel)
        self._update_up_stat()

    def toggle_upload_arb(self):
        self.upload_arb = bool(self.var_arb.get())
        save_config(upload_arb=self.upload_arb)
        diag.log.info("上传包含阿比特录像：%s", self.upload_arb)
        self._rebuild_up_rows()

    def _up_stat_text(self):
        total = len(self.up_rows)
        up = sum(1 for r in self.up_rows if r.get("uploaded"))
        arb = getattr(self, "_arb_rows", 0)
        txt = "本地 %d 个完成局 · 网站已有 %d · 未上传 %d · 已选 %d" % (
            total, up, total - up, len(self.up_sel))
        if arb:
            txt += "（阿比特 %d 个，%s）" % (
                arb, "在本列表里" if self.upload_arb else "已排除，不传")
        return txt

    def _update_up_stat(self):
        pending = {r["path"] for r in self.up_rows if not r.get("uploaded")}
        self.up_sel &= pending
        self.lbl_up_stat.config(text=self._up_stat_text())
        self.btn_up_sel.set_text("上传选中 (%d)" % len(self.up_sel))
        self.btn_up_all.set_text("上传全部未传 (%d)" % len(pending))

    def _on_up_select(self):
        self.up_sel = set(self.tbl_up.selected_paths())
        self.lbl_up_stat.config(text=self._up_stat_text())
        self.btn_up_sel.set_text("上传选中 (%d)" % len(self.up_sel))

    def toggle_scope(self, key):
        if key in self._up_scopes:
            self._up_scopes.discard(key)
        else:
            self._up_scopes.add(key)
        for k, p in self.scope_pills.items():
            p.set_selected(k in self._up_scopes)
        self.select_by_scope()

    def select_by_scope(self):
        if not self._up_scopes:
            self.up_sel = set()
            self.tbl_up.select_paths(self.up_sel)
            self._update_up_stat()
            return
        want = set()
        for s in self._up_scopes:
            want |= openms.scope_paths(self.games, s)
        pending = {r["path"] for r in self.up_rows if not r.get("uploaded")}
        self.up_sel = want & pending
        self.tbl_up.select_paths(self.up_sel)
        self._update_up_stat()

    def select_all_pending(self):
        self.up_sel = {r["path"] for r in self.up_rows if not r.get("uploaded")}
        self.tbl_up.select_paths(self.up_sel)
        self._update_up_stat()

    def clear_selection(self):
        self._up_scopes.clear()
        for p in self.scope_pills.values():
            p.set_selected(False)
        self.up_sel = set()
        self.tbl_up.tv.selection_remove(*self.tbl_up.tv.selection())
        self._update_up_stat()

    # ---------- 上传：执行 ----------
    def _pending_paths(self):
        return [r["path"] for r in self.up_rows
                if not r.get("uploaded") and r["path"] in self.up_sel]

    def upload_selected(self):
        paths = self._pending_paths()
        if not paths:
            messagebox.showinfo(APP_TITLE, "没有选中的待上传录像。\n\n"
                                          "先用上面的「范围」或「全选未传」选一批。")
            return
        self._start_upload(paths)

    def upload_all_pending(self):
        paths = [r["path"] for r in self.up_rows if not r.get("uploaded")]
        if not paths:
            messagebox.showinfo(APP_TITLE, "本地完成局都已经在网站上了。")
            return
        self._start_upload(paths)

    def _start_upload(self, paths):
        if self._up_running:
            return
        if not self.oms_user:
            if messagebox.askyesno(APP_TITLE, "还没登录 openms.top，现在登录吗？"):
                self.open_login()
            return
        self._up_running = True
        self._up_stop = False
        self._up_total = len(paths)
        self._last_failed.clear()
        self._up_done = self._up_ok = self._up_dup = self._up_fail = 0
        self.pb_up.config(maximum=max(1, len(paths)), value=0)
        self.lbl_up_prog.config(text="0/%d" % len(paths))
        self._set_up_enabled(False)
        self.btn_stop.set_enabled(True)
        self._log("开始上传 %d 个录像…" % len(paths), "dim")
        self.set_status("正在上传 %d 个录像…" % len(paths), "dim")
        keys = {g["path"]: openms.game_key(g) for g in self.games}
        threading.Thread(target=self._upload_worker, args=(paths, keys),
                         daemon=True).start()

    def _upload_worker(self, paths, keys=None):
        """串行上传。

        失败分两类，处理方式不同：
          · 账号级（auth / quota / realname / banned）—— 后面的每一条都会同样失败，
            立即停止整批；
          · 单条级（网络重试耗尽、5xx、单条被拒）—— 记下来继续下一条。
        网络类连续 3 条失败就认为断网了，主动停批，避免白等 N 个超时。
        """
        import time as _time
        keys = keys or {}
        net_streak = 0
        for i, p in enumerate(paths, 1):
            if self._up_stop:
                self._q.put(("up_end", {"stopped": True}))
                return
            try:
                res = self.oms.upload(p)
            except openms.OmsError as exc:
                res = {"ok": False, "reason": str(exc), "fatal": None,
                       "transient": True}
            except Exception as exc:                 # noqa: BLE001
                res = {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
            if res.get("ok"):
                net_streak = 0
                k = keys.get(p)
                if k:
                    journal.mark(k, res.get("state"))     # 断点续传的依据
            elif res.get("transient"):
                net_streak += 1
            else:
                net_streak = 0
            self._q.put(("up", (p, res, i, len(paths))))
            if res.get("fatal") in ("auth", "quota", "banned", "realname"):
                self._q.put(("up_end", {"fatal": res.get("fatal"),
                                        "reason": res.get("reason")}))
                return
            if net_streak >= 3:
                self._q.put(("up_end", {"fatal": "net",
                                        "reason": "连续 3 条网络失败"}))
                return
            _time.sleep(0.35)        # 限流 5/s，站点自己用 200ms，这里留余量
        self._q.put(("up_end", {}))

    def _set_up_enabled(self, flag):
        for b in (self.btn_up_sel, self.btn_up_all, self.btn_sel_all,
                  self.btn_sel_none, self.btn_sync, self.btn_login):
            b.set_enabled(flag)
        for b in self.scope_pills.values():
            b.set_enabled(flag)
        if hasattr(self, "btn_retry"):
            n = len(self._last_failed)
            if flag and n:
                self.btn_retry.set_enabled(True)
                self.btn_retry.set_text("重试失败 (%d)" % n)
            else:
                self.btn_retry.set_enabled(False)
                self.btn_retry.set_text("重试失败")

    def stop_upload(self):
        if self._up_running:
            self._up_stop = True
            self._log("正在停止（等当前这个传完）…", "warn")

    def retry_failed(self):
        """只重传上一次失败的条目（已成功的不动，避免重复上传）。"""
        if self._up_running:
            return
        alive = {r["path"]: r for r in self.up_rows}
        paths = [p for p in self._last_failed
                 if p in alive and not alive[p].get("uploaded")]
        if not paths:
            self._last_failed.clear()
            self._set_up_enabled(True)
            messagebox.showinfo(APP_TITLE, "没有失败项可重试。")
            return
        self._start_upload(paths)

    def _log(self, text, kind="dim"):
        color = {"dim": C_DIM, "ok": C_OK, "warn": C_WARN, "err": C_ERR}.get(kind, C_DIM)
        self.lbl_up_log.config(text=text, fg=color)

    def _on_upload_result(self, path, res, idx, total):
        row = next((r for r in self.up_rows if r["path"] == path), None)
        self._last_failed.discard(path)
        if res.get("ok"):
            if res.get("collision"):
                self._up_dup += 1
                status, site = "网站已有", "—"
            else:
                self._up_ok += 1
                state = res.get("state") or ""
                status = "上传成功"
                site = openms.STATE_CN.get(state, state or "—")
            if row:
                row["uploaded"] = True
                row["status"] = status
                row["site"] = site
                self.tbl_up.update_row(row)
            self._log("%s → %s%s" % (os.path.basename(path), status,
                                     "" if site == "—" else "（%s）" % site), "ok")
        else:
            self._up_fail += 1
            self._last_failed.add(path)
            reason = res.get("reason") or "失败"
            if row:
                row["status"] = "失败：" + reason
                self.tbl_up.update_row(row)
            self._log("%s → %s" % (os.path.basename(path), reason), "err")
        diag.log.info("上传 %s → %s", os.path.basename(path), status if res.get("ok")
                      else "失败：" + (res.get("reason") or "?"))
        self.pb_up.config(value=idx)
        self.lbl_up_prog.config(text="%d/%d" % (idx, total))

    def _on_upload_end(self, payload):
        self._up_running = False
        self._set_up_enabled(True)
        self.btn_stop.set_enabled(False)
        fatal = payload.get("fatal")
        if fatal == "auth":
            self.oms_user = None
            self.oms.store.clear_session()
            self._refresh_acct_label()
            messagebox.showwarning(APP_TITLE, "登录已失效，本批已停止。\n\n"
                                             "请重新点「登录…」（只需再输一次验证码）。")
        elif fatal == "quota":
            messagebox.showwarning(APP_TITLE, "录像配额已满（每账号 100 条），本批已停止。\n\n"
                                             "需要先到网站上删掉旧录像，或联系管理员提额。")
        elif fatal == "realname":
            messagebox.showwarning(APP_TITLE, "账号未实名，网站拒绝上传。\n\n"
                                             "请先到 openms.top 个人资料里完成实名。")
        elif fatal == "banned":
            messagebox.showwarning(APP_TITLE, "账号被封禁，无法上传。")
        elif fatal == "net":
            self.set_status("网络连续失败，本批已停止；已传的不会重传", "err")
            messagebox.showwarning(
                APP_TITLE,
                "连续多条网络失败，已停止本批。\n\n"
                "已经传成功的不受影响，也不会重复上传（本地台账已记下）。\n"
                "网络恢复后点「重试失败」只补传剩下的那几条。")

        msg = "上传结束：成功 %d，已有 %d，失败 %d" % (
            self._up_ok, self._up_dup, self._up_fail)
        if payload.get("stopped"):
            msg += "（手动停止）"
        if fatal == "net":
            msg = "网络连续失败已停批 · " + msg
        elif self._up_fail and not fatal:
            msg += "，可点「重试失败」补传"
        self.set_status(msg, "ok" if not self._up_fail else "warn")
        self._refresh_acct_label()      # 上一行会把「登录…」重新启用，这里按真实登录态纠正
        self._update_up_stat()
        # 用服务端数据校正状态（含审核结果 a/c/b/d）
        self.after(400, self.refresh_site)

    # ---------- 启动 / 目录 ----------
    @property
    def root_dir(self):
        """主目录：第一个启用的目录（record.ini 对账找它）。"""
        for r in self.roots:
            if r.get("on", True):
                return r["path"]
        return self.roots[0]["path"] if self.roots else None

    def active_roots(self):
        return [r["path"] for r in self.roots if r.get("on", True)]

    def roots_label(self):
        """导航行右下角那行字：局数在 _on_scanned 里补，这里只管目录。"""
        n = len(self.roots)
        main = self.root_dir
        if not main:
            return "未选择录像目录"
        if n <= 1:
            return shrink_path(main, 62)
        return "%s  +%d 个目录" % (shrink_path(main, 44), n - 1)

    def open_paths(self):
        PathsDialog(self)

    def apply_roots(self, roots, rescan=True):
        """目录列表变了 → 存盘、刷新按钮、重扫。"""
        self.roots = load_roots({"roots": roots})
        save_roots(self.roots)
        self._refresh_paths_btn()
        self.lbl_meta.config(text=self.roots_label())
        diag.log.info("录像目录更新：%s",
                      " ; ".join(r["path"] for r in self.roots) or "（空）")
        if rescan:
            if not self.active_roots():
                self.set_status("所有录像目录都已停用", "warn")
                return
            self._empty_prompted = False
            self.start_scan()

    def _refresh_paths_btn(self):
        n = len(self.roots)
        self.btn_paths.set_text("路径 (%d)…" % n if n else "路径…")

    def _boot(self):
        self._refresh_paths_btn()
        if self._need_pick:
            self.set_status("首次运行：请先指定录像目录", "warn")
            self.lbl_meta.config(text="未选择录像目录")
            self.after(120, self.first_run_pick)
        else:
            self.start_scan()

    def first_run_pick(self):
        messagebox.showinfo(
            APP_TITLE,
            "程序只自动找元扫雷的录像目录，没找到，所以请你指定一个。\n\n"
            "元扫雷的 replay 目录，一般位于：\n"
            "    %APPDATA%\\Metasweeper<版本号>\\replay\n\n"
            "选一次即可，之后会自动记住。\n\n"
            "阿比特的 .avf 目录不会自动加入 —— 打开后点右上角「路径…」→「添加目录…」"
            "手动添加（会先问你一句，确认里面没有别人打的录像）。")
        self.pick_dir(first=True)

    def pick_dir(self, first=False):
        title = "选择录像目录（这个目录里的录像都算你自己打的）"
        initial = (self.root_dir or core.autodetect_root()
                   or os.path.join(os.environ.get("APPDATA") or "", "Metasweeper"))
        d = filedialog.askdirectory(title=title,
                                    initialdir=initial if os.path.isdir(initial) else None)
        if not d:
            if first:
                self.set_status("未选择录像目录 —— 可随时点右上角「换目录…」", "warn")
                self.lbl_meta.config(text="未选择录像目录")
            return
        if not confirm_own_videos(self, os.path.abspath(d), replacing=not first):
            if first:
                self.set_status("未选择录像目录 —— 可随时点右上角「换目录…」", "warn")
            return
        # 「换目录…」= 换掉主目录，其他目录（手动加的）原样保留
        rest = self.roots[1:] if self.roots else []
        self._empty_prompted = False
        self.apply_roots([{"path": d, "on": True}] + rest)

    def start_scan(self):
        if self._scanning:
            return
        roots = self.active_roots()
        if not roots:
            self.first_run_pick()
            return
        self._scanning = True
        head = shrink_path(roots[0], 52)
        self._progress_begin()
        self.set_status("正在清点 %s%s …"
                        % (head, "" if len(roots) == 1 else " 等 %d 个目录" % len(roots)),
                        "dim")
        self.lbl_meta.config(text=self.roots_label())
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            roots = self.active_roots()
            # 清点总数（只读目录项，0.2 秒量级）→ 进度条才有分母
            self._q.put(("scan_total", core.count_videos(roots)))
            games, info = core.scan(roots,
                                    progress=lambda n: self._q.put(("scan", n)))
            pb = core.build_pb(games, split_style=True)   # NF / FL 两张榜各自成行
            cnt = core.count_by_bv(games)
            cnts = {"all": cnt,
                    "nf": core.count_by_bv(games, "nf"),
                    "fl": core.count_by_bv(games, "fl")}
            smy = core.summary(games)
            ini_path = core.find_record_ini(self.root_dir)
            ini = core.read_record_ini(ini_path) if ini_path else {}
            # 对账要用「不分风格」的 PB：record.ini 存的是同 BV 最快，不分 NF/FL。
            # 而且只拿元扫雷自己的录像去比 —— record.ini 是元扫雷写的，
            # 把阿比特的成绩混进来只会得到一堆必然对不上的「冲突」。
            ms_games = [g for g in games if g.get("src") != "arb"]
            diffs = core.cross_check(core.build_pb(ms_games), ini) if ini else []
            self._q.put(("ok", (games, info, pb, cnts, smy, diffs, bool(ini))))
        except Exception as exc:  # noqa: BLE001
            self._q.put(("err", f"{type(exc).__name__}: {exc}"))

    def _poll(self):
        """主线程轮询后台队列。Tk 不是线程安全的，只能在这里碰控件。"""
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "ok":
                    self._on_scanned(*payload)
                elif kind == "err":
                    self._on_scan_failed(payload)
                elif kind == "acct":
                    self._on_account(*payload)
                elif kind == "up":
                    self._on_upload_result(*payload)
                elif kind == "scan_total":
                    self._progress_total(payload)
                    if payload:
                        self.set_status("正在扫描…0 / %d 个录像" % payload, "dim")
                elif kind == "scan":
                    pct = self._progress_set(payload)
                    # 队列里可能一次积压上百个点，只在百分比真的变了才刷文字
                    if pct is not None and pct != self._scan_pct:
                        self._scan_pct = pct
                        self.set_status("正在扫描…已读 %d / %d 个录像（%d%%）"
                                        % (payload, self._scan_total, pct), "dim")
                    elif pct is None:
                        self.set_status("正在扫描…已读到 %d 个录像" % payload, "dim")
                elif kind == "up_end":
                    self._on_upload_end(payload)
        except queue.Empty:
            pass
        except Exception as exc:  # noqa: BLE001
            self.set_status("处理结果出错：" + str(exc), "err")
        if self.winfo_exists():
            self.after(60, self._poll)

    def _on_scan_failed(self, msg):
        self._scanning = False
        self._progress_end()
        diag.log.error("扫描失败：%s", msg)
        self.set_status("扫描失败：" + msg, "err")
        messagebox.showerror(APP_TITLE, "扫描失败\n\n" + msg)

    def _on_scanned(self, games, info, pb, cnt, smy, diffs, ini_ok):
        self._scanning = False
        self._progress_end()
        self.games, self.info, self.pb_rows = games, info, pb
        self.counts = cnt if isinstance(cnt, dict) and "all" in cnt else {"all": cnt or {}}
        self.summary, self.diffs, self.ini_ok = smy, diffs, ini_ok

        self.render_cards()
        self.render_recent()
        # 没有 rce 数据（ms-toollib 缺失）时，风格筛选没有意义，退回「全部风格」
        if not info.get("with_rce"):
            if self._pb_style != "all":
                self.set_pb_style("all")
            if self._top_style != "all":
                self.set_top_style("all")
        self.set_pb_level(self._pb_level)
        self.set_top_level(self._level)
        self._rebuild_up_rows()          # 上传页跟着新数据刷新

        elapsed = info["elapsed"] * 1000
        n_fail = info["fail_skipped"]
        dup = info["duplicates"]
        extra = []
        if n_fail:
            extra.append(f"跳过非完成局 {n_fail}")
        if dup:
            extra.append(f"去重 {dup}")
        if info["unparsed"]:
            extra.append(f"无法解析 {len(info['unparsed'])}")
        if info.get("nf_count") or info.get("fl_count"):
            extra.append(f"NF {info['nf_count']} · FL {info['fl_count']}")
        if info.get("rce_failed"):
            extra.append(f"rce 失败 {info['rce_failed']}")
        if info.get("tampered"):
            extra.append(f"文件名与录像不符 {len(info['tampered'])}")
        if info.get("level_conflict"):
            extra.append(f"级别不符 {len(info['level_conflict'])}")
        n_arb = (info.get("by_src") or {}).get("arb") or 0
        if n_arb:
            extra.append(f"其中阿比特 {n_arb}")
        if info.get("truncated"):
            extra.append(f"只扫了前 {info['dirs_visited'] - 1} 个子目录就停手"
                         f"（目录太大，建议直接指向 replay）")
        ck = info.get("cache") or {}
        if ck.get("hits") and ck.get("misses"):
            extra.append(f"新解析 {ck['misses']}，其余 {ck['hits']} 走缓存")
        tail = ("，" + "，".join(extra)) if extra else ""
        self.set_status(
            f"扫描 {info['files']} 个文件 → {info['unique']} 个完成局{tail}，耗时 {elapsed:.0f} ms",
            "warn" if (info.get("tampered") or info.get("truncated")) else "dim")
        self.lbl_meta.config(text=f"{info['unique']} 局完成 · " + self.roots_label())
        diag.log.info("扫描完成：%d 文件 → %d 局（阿比特 %d%s），NF %s / FL %s，%d ms%s",
                      info["files"], info["unique"], n_arb,
                      "，去掉重复副本 %d" % info["dup_removed"]
                      if info.get("dup_removed") else "",
                      info.get("nf_count"),
                      info.get("fl_count"), elapsed, "（目录数达上限，已截断）"
                      if info.get("truncated") else "")

        if not games:
            self.lbl_check.config(text="未发现 .evf / .avf 录像，请用「换目录…」或「路径…」指定",
                                  foreground=C_WARN)
            if not self._empty_prompted:
                self._empty_prompted = True
                if messagebox.askyesno(
                        APP_TITLE,
                        "这些目录里没有找到录像文件：\n" +
                        "\n".join(self.active_roots()) +
                        "\n\n要重新选择目录吗？"):
                    self.pick_dir()
        elif not ini_ok:
            self.lbl_check.config(text="未找到 record.ini，跳过交叉校验", foreground=C_DIM)
        else:
            # record.ini 是元扫雷写的，所以对账的基数也只能是元扫雷的局
            ms_pb = core.build_pb([g for g in games if g.get("src") != "arb"])
            n_keys = len({(r["level"], r["bv"]) for r in ms_pb})  # NF/FL 拆行后按 BV 去重
            n_same = n_keys - len(diffs)
            only_mine = sum(1 for d in diffs if d["ini"] is None)
            only_ini = sum(1 for d in diffs if d["mine"] is None)
            big = sum(1 for d in diffs if d["mine"] is not None and d["ini"] is not None)
            parts = [f"record.ini 对账 {n_same}/{n_keys} 一致（仅元扫雷）"]
            if only_mine:
                parts.append(f"{only_mine} 条 ini 尚无（录像更新）")
            if only_ini:
                parts.append(f"{only_ini} 条仅 ini 有")
            if big:
                parts.append(f"{big} 条数值冲突")
            color = C_OK if not (only_ini or big) else C_WARN
            self.lbl_check.config(text="· " + "，".join(parts), foreground=color)

    # ---------- 渲染 ----------
    def render_cards(self):
        for lv, card in self.cards.items():
            s = self.summary.get(lv) or {}
            if not s.get("games"):
                card.set_data({"count": "0 局", "rows": [
                    ("最快单局", "—"), ("最高 3BV/s", "—"), ("最高 stnb", "—"),
                    ("BV 覆盖", "—")]})
                continue
            bt, bb, bs = s["bestTime"], s["bestBvs"], s["bestStnb"]
            card.set_data({"count": f"{s['games']} 局", "rows": [
                ("最快单局", f"{fmt_time(bt['time'])} s @BV{bt['bv']}"),
                ("最高 3BV/s", f"{fmt_bvs(bb['bvs'])} @BV{bb['bv']}"),
                ("最高 stnb", f"{fmt_stnb(bs['stnb'])} @BV{bs['bv']}"),
                ("BV 覆盖", f"{s['bvCount']} 个（{s['bvMin']}–{s['bvMax']}）"),
            ]})

    def render_recent(self):
        rows = sorted(self.games, key=lambda g: g.get("mtime") or 0, reverse=True)[:10]
        out = []
        for g in rows:
            item = dict(g)
            item["level_cn"] = core.LEVEL_CN[g["level"]]
            item["style_cn"] = core.STYLE_CN.get(g.get("style"), "?")
            try:
                item["clock"] = datetime.fromtimestamp(g["mtime"]).strftime("%H:%M:%S")
            except Exception:
                item["clock"] = ""
            out.append(item)
        self.tbl_recent.set_rows(out)

    def set_pb_level(self, key):
        self._pb_level = key
        for k, p in self.pb_seg.items():
            p.set_selected(k == key)
        self.render_pb()

    def set_pb_style(self, key):
        self._pb_style = key
        for k, p in self.pb_style_seg.items():
            p.set_selected(k == key)
        self.render_pb()

    def render_pb(self):
        q = self.var_q.get().strip().lower()
        cnt = self.counts.get(self._pb_style, {}) if isinstance(self.counts, dict) else {}
        rows = []
        for r in self.pb_rows:
            if self._pb_level and r["level"] != self._pb_level:
                continue
            if self._pb_style != "all" and r.get("style") != self._pb_style:
                continue
            if q:
                hay = f"{r['bv']} {r['file']}".lower()
                if q not in hay:
                    continue
            item = dict(r)
            item["level_cn"] = core.LEVEL_CN[r["level"]]
            item["games"] = cnt.get((r["level"], r["bv"]), 0)
            rows.append(item)
        self.tbl_pb.set_rows(rows)
        self.lbl_pb_count.config(text=f"{len(rows)} 行")

    def _on_bvmin_change(self):
        if self._bv_guard:
            return
        self.render_top()

    def set_top_style(self, key):
        self._top_style = key
        for k, p in self.top_style_seg.items():
            p.set_selected(k == key)
        self.render_top()

    def set_top_level(self, lv):
        """切级别时，把 BV 下限换成该级别的官方门槛。"""
        changed = lv != self._level or self._bvmin_level != lv
        self._level = lv
        for k, p in self.top_seg.items():
            p.set_selected(k == lv)
        if changed:
            self._bv_guard = True
            self.var_bvmin.set(str(core.BV_MIN_DEFAULT[lv]))
            self._bv_guard = False
            self._bvmin_level = lv
        self.render_top()

    def render_top(self):
        try:
            bvmin = max(0, int(self.var_bvmin.get() or 0))
        except ValueError:
            bvmin = 0
        style = None if self._top_style == "all" else self._top_style
        for metric, table in self.top_tables.items():
            raw = core.top5(self.games, self._level, metric, bvmin, style=style)
            rows = []
            for i, g in enumerate(raw, 1):
                item = dict(g)
                item["rank"] = i
                item["style_cn"] = core.STYLE_CN.get(g.get("style"), "?")
                rows.append(item)
            table.set_rows(rows)

    def set_status(self, text, kind="dim"):
        color = {"dim": C_DIM, "ok": C_OK, "warn": C_WARN, "err": C_ERR}.get(kind, C_DIM)
        self.lbl_status.config(text=text, foreground=color)

    # ---------- 读取进度条 ----------
    def _progress_begin(self):
        """扫描开始：先亮一条「不确定进度」的来回动画，等清点出总数再切成百分比。"""
        self._scan_total = 0
        self._scan_done = 0
        self._scan_pct = -1
        if not self.pbar_box.winfo_ismapped():
            self.pbar_box.pack(side="bottom", fill="x", padx=sc(20), pady=(sc(6), 0))
        self.pbar.config(mode="indeterminate", value=0, maximum=100)
        try:
            self.pbar.start(14)
        except tk.TclError:
            pass
        self._boot_hint(True)

    def _progress_total(self, total):
        """清点完成：切成 0~100 的确定进度。总数 0 就继续不确定动画。"""
        self._scan_total = max(0, int(total or 0))
        self._scan_pct = -1
        if not self._scan_total:
            return
        try:
            self.pbar.stop()
        except tk.TclError:
            pass
        self.pbar.config(mode="determinate", maximum=100, value=0)

    def _progress_set(self, done):
        self._scan_done = int(done or 0)
        if not self._scan_total:
            return
        pct = min(100, self._scan_done * 100 // self._scan_total)
        self.pbar.config(value=pct)
        return pct

    def _progress_end(self):
        try:
            self.pbar.stop()
        except tk.TclError:
            pass
        self.pbar.config(mode="determinate", value=0)
        if self.pbar_box.winfo_ismapped():
            self.pbar_box.pack_forget()
        self._boot_hint(False)

    def _boot_hint(self, show):
        """概览页正中一行「正在读取录像…」，免得首扫时看到一片空白以为程序死了。"""
        lbl = getattr(self, "lbl_boot", None)
        if lbl is None:
            return
        if show:
            lbl.place(relx=0.5, rely=0.62, anchor="center")
            lbl.lift()
        else:
            lbl.place_forget()

    # ---------- 导出 ----------
    def open_diagnostics(self):
        win = getattr(self, "_diag_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_set()
            return
        self._diag_win = DiagnosticsDialog(self)

    def export_csv(self):
        if not self.pb_rows:
            messagebox.showinfo(APP_TITLE, "还没有数据可导出。")
            return
        path = filedialog.asksaveasfilename(
            title="导出 PB 表", defaultextension=".csv",
            initialfile="pb_records.csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        import csv
        cnt = self.counts.get("all", {}) if isinstance(self.counts, dict) else {}
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["level", "level_cn", "bv", "style", "rce", "best_time",
                        "bvs", "stnb", "games_in_bv", "date", "file", "path"])
            for r in self.pb_rows:
                w.writerow([r["level"], core.LEVEL_CN[r["level"]], r["bv"],
                            r.get("style", ""), r.get("rce", ""),
                            f"{r['time']:.3f}", f"{r['bvs']:.3f}", f"{r['stnb']:.2f}",
                            cnt.get((r["level"], r["bv"]), 0),
                            r["date"], r["file"], r["path"]])
        self.set_status("已导出：" + path, "ok")


def main():
    enable_dpi()                      # 幂等；模块导入时已经调过一次
    diag.setup()
    tk_callback = diag.install_hooks()
    root_dir = None
    root_dirs = []                    # --root 可以写多次：多个录像目录
    start_tab = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--root" and i + 1 < len(args):
            root_dir = args[i + 1]
            root_dirs.append(args[i + 1])
        elif a == "--tab" and i + 1 < len(args):
            start_tab = args[i + 1]
    try:
        app = App(root_dirs if len(root_dirs) > 1 else root_dir)
    except Exception:
        diag.log.exception("主窗口创建失败")
        diag._alert(APP_TITLE, "程序启动失败，详情见日志：\n"
                               + os.path.join(paths.log_dir(), "app.log"))
        raise
    app.report_callback_exception = tk_callback   # Tk 回调异常默认只打 stderr
    if start_tab in app.pages:
        app.after(400, lambda: app.show_tab(start_tab))
    if "--probe" in args:
        # 自检：把窗口实际尺寸写到程序目录，用于排查打包后的 DPI / 布局问题
        def _probe():
            out = os.path.join(paths.log_dir(), "probe_out.txt")
            try:
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write("version=%s\n" % APP_VER)
                    fh.write("frozen=%s\n" % bool(getattr(sys, "frozen", False)))
                    fh.write("dpi_aware=%s\n" % DPI_AWARE)
                    fh.write("scale=%s\n" % SCALE)
                    fh.write("screen=%sx%s\n" % (app.winfo_screenwidth(),
                                                 app.winfo_screenheight()))
                    fh.write("workarea=%s\n" % (work_area(),))
                    fh.write("win=%sx%s\n" % (app.winfo_width(), app.winfo_height()))
                    fh.write("geom=%s\n" % app.winfo_geometry())
                    fh.write("maxsize=%s\n" % (app.maxsize(),))
                    fh.write("req=%sx%s\n" % (app.winfo_reqwidth(),
                                              app.winfo_reqheight()))
            except Exception as e:
                try:
                    with open(os.path.join(os.environ.get("TEMP", "."),
                                           "ms_pb_win.txt"), "w") as fh:
                        fh.write("ERR %r\n" % (e,))
                except Exception:
                    pass
            app.destroy()
        app.after(2600, _probe)
    app.mainloop()


if __name__ == "__main__":
    main()

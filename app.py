"""YuanshenToolbox —— 原生窗口（tkinter），二次元清爽风。

兼容元扫雷（Metasweeper，.evf）与阿比特（Arbiter，.avf）的扫雷录像，直接显示：
  · 概览：三级别局数 / 三项最高
  · 每日：按天看每天打了多少局、当天最快 / 平均 / 最慢（右键某天可复制
    当天的总结图，或详细版总结图）
  · PB 表：每个（级别 × BV）的最快完成局
  · 前端成绩：三级别 × time / bvs / stnb 的前五局
  · 上传：把本地完成局传到开源扫雷网（openms.top）
  · 自动上传：玩的时候盯新录像，符合范围就自动传
"""

import base64
import ctypes
import os
import queue
import subprocess
from datetime import datetime
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

import autolog
import autoup
import cache
import core
import diag
import journal
import mslaunch
import openms
import paths
import summary

APP_TITLE = "YuanshenToolbox"
APP_SUB = "MINESWEEPER REPLAY TOOLBOX"
APP_VER = "0.38.9"
APP_DISPLAY = f"{APP_TITLE} v{APP_VER}"

# 随程序打包的字体：MiSans（小米，免费商用）。私有注册，不装进系统字体目录。
BUNDLED_FONTS = ("MiSans-Regular.ttf", "MiSans-Demibold.ttf")
FONT_FILES = []


# --------------------------------------------------------------- 后台跑界面
# 跑测试、截图脚本时会弹出一堆窗口，把用户正在做的事挡掉、还会抢走键盘焦点。
# 设了 MS_PB_QUIET=1：窗口照常布局（截图还能抓），但对用户完全透明 ——
# alpha 0 看不见、点不穿、不抢焦点、不占任务栏。
# 为什么不是挪到屏幕外：屏幕外的窗口 DWM 不给合成面，截图只能抓到一片白。
# 正常启动没有这个变量，行为完全不变。打包后的 exe 一律不认这个变量 ——
# 万一环境里残留了它，用户会看见一个「打不开的程序」。
QUIET = (os.environ.get("MS_PB_QUIET") == "1"
         and not getattr(sys, "frozen", False))

if QUIET:
    # 句柄是 64 位，不声明 restype 会被截成 32 位，SetForegroundWindow 就废了
    _u32 = ctypes.windll.user32
    _u32.GetForegroundWindow.restype = ctypes.c_void_p
    _u32.GetAncestor.restype = ctypes.c_void_p
    _u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    _u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _u32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    _u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _u32.SetForegroundWindow.argtypes = [ctypes.c_void_p]


def _quiet_style(win):
    """完全透明 + 不抢焦点 + 不进任务栏 + 鼠标穿透。"""
    try:
        win.attributes("-alpha", 0.0)
    except tk.TclError:
        pass
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetAncestor(win.winfo_id(), 2)       # GA_ROOT
        if not hwnd:
            return
        gwl_exstyle = -20
        # TOOLWINDOW（不在任务栏/Alt-Tab）| NOACTIVATE（不抢焦点）
        # | TRANSPARENT（鼠标穿透，免得挡住用户点自己的窗口）
        add = 0x80 | 0x08000000 | 0x20
        cur = user32.GetWindowLongW(hwnd, gwl_exstyle)
        user32.SetWindowLongW(hwnd, gwl_exstyle, cur | add)
    except Exception:
        pass


def _foreground():
    try:
        return ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        return 0


def _ours(hwnd):
    """这个窗口是不是本进程的。"""
    try:
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception:
        return False


def _give_back_focus(hwnd):
    """把前台还给用户原来那个窗口。

    Tk 在映射窗口时会把样式重设一遍（NOACTIVATE 被抹掉），Windows 就顺手把
    新窗口激活了 —— 用户正在敲字时这一下会吃掉按键。所以映射完把前台还回去。

    只在「前台现在是本进程的窗口」时才抢回来：用户中途点了别的窗口，
    说明他已经换了目标，别跟人抢。
    """
    if not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        cur = user32.GetForegroundWindow()
        if cur and cur != hwnd and _ours(cur):
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


if QUIET:
    _orig_tk_init = tk.Tk.__init__
    _orig_top_init = tk.Toplevel.__init__

    def _quiet_apply(win):
        """窗口映射时 Tk 会把窗口样式重设一遍，所以要在建好之后再来一次。

        <Map> 会从子控件冒泡上来，所以这里认的是窗口本身，不看 event.widget。
        映射那一下 Windows 会把新窗口设成前台，所以把用户原来的前台还回去。
        """
        prev = _foreground()

        def again(_e=None):
            _quiet_style(win)
            _give_back_focus(prev)

        _quiet_style(win)
        try:
            win.bind("<Map>", again, add="+")
            win.after(80, again)          # 映射之后 Tk 会重设样式，再盖一遍
            win.after(260, again)
        except tk.TclError:
            pass

    def _quiet_tk_init(self, *a, **kw):
        _orig_tk_init(self, *a, **kw)
        _quiet_apply(self)

    def _quiet_top_init(self, *a, **kw):
        _orig_top_init(self, *a, **kw)
        _quiet_apply(self)

    tk.Tk.__init__ = _quiet_tk_init
    tk.Toplevel.__init__ = _quiet_top_init


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


def config_auto_scopes(cfg):
    """自动上传按成绩筛的三项范围。

    只有「字段缺失或类型不对」才回落到默认三项；空列表是用户把三项都
    取消之后的选择，必须原样留着，否则重启后关掉的范围会自己回来。
    """
    raw = (cfg or {}).get("auto_scopes")
    if isinstance(raw, list):
        return [s for s in raw if isinstance(s, str)]
    return ["top", "top_nf", "pb"]


def ms_exes_from_cfg(cfg):
    """设置里登记过的扫雷程序（去重、保持顺序），并把当前选中的补进来。

    老配置只有 ``ms_exe`` 一个路径：这里把它并进列表，首次打开设置就能看到；
    选中项仍然是 ``ms_exe`` —— 自动启动只认这一项，不会把整个列表都拉起来。
    """
    out = []
    raw = (cfg or {}).get("ms_exes")
    if isinstance(raw, list):
        for p in raw:
            if not isinstance(p, str) or not p.strip():
                continue
            ap = os.path.abspath(p)
            if all(os.path.normcase(ap) != os.path.normcase(x) for x in out):
                out.append(ap)
    sel = str((cfg or {}).get("ms_exe") or "")
    if sel:
        ap = os.path.abspath(sel)
        if all(os.path.normcase(ap) != os.path.normcase(x) for x in out):
            out.insert(0, ap)
    return out


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


def lsf_anchor_path():
    return os.path.join(paths.data_dir(), "lsf-anchors.json")


def load_lsf_anchors():
    """上次拉到的锚点。LSF 没有锚点就算不出来，断网时靠这份兜底。"""
    try:
        import json
        with open(lsf_anchor_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        anchors = data.get("anchors")
        if isinstance(anchors, dict) and anchors:
            return anchors, int(data.get("saved_at") or 0)
    except Exception:
        pass
    return {}, 0


def save_lsf_anchors(anchors):
    try:
        import json
        p = lsf_anchor_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"saved_at": int(time.time()), "anchors": anchors},
                      fh, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


# 赛季 LSF 基准（硬编码）。来源＝大表 ArbiterStatsAuto57.9.xlsm「Set」!AI27:AK35
# 的赛季内置锚点（s6，2026-01-01 开启）：大表离线自带这套锚点算分，不随网站实时漂移。
# 单位换算到大表/网站原始口径：t 秒→毫秒 ×1000；s 显示值→原始值 ÷ 等级系数(初36/中162/高435)；b 原样。
# 勾「固定 LSF 基准」就用它。换赛季时按新一期大表更新此表与日期。
SEASON_ANCHORS_AT = "2026-01-01"
SEASON_ANCHORS = {k: {"value": v, "sample_count": 20} for k, v in {
    "std:bt": 1.355 * 1000,
    "std:bs": 230 / 36,
    "std:bb": 9.245,
    "std:it": 8.371 * 1000,
    "std:is": 177.9 / 162,
    "std:ib": 6.128,
    "std:et": 34.08 * 1000,
    "std:es": 157.9 / 435,
    "std:eb": 4.86,
    "nf:bt": 1.463 * 1000,
    "nf:bs": 193.5 / 36,
    "nf:bb": 7.486,
    "nf:it": 9.331 * 1000,
    "nf:is": 147.3 / 162,
    "nf:ib": 5.222,
    "nf:et": 38.64 * 1000,
    "nf:es": 125.7 / 435,
    "nf:eb": 4.201,
}.items()}


def load_roots(seen=None):
    """配置里的录像目录列表：[{"path": ..., "on": True}]。

    顺手做三件事：去重（同一个目录被加了两次）、丢掉已经不存在的目录
    （移动硬盘拔掉、元扫雷卸载后路径变了）、保持原顺序
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
    """手动加目录前的确认：告诉用户加进来的录像会全部计入成绩统计。

    自动探测只认元扫雷的 replay 目录。除此之外的目录（阿比特的 replays、
    从网上下载来观摩的录像堆）都是手动添加的，程序无法分辨里面哪一条不是
    自己打的 —— 只能靠用户确认「这里没有别人的录像」。
    """
    return messagebox.askokcancel(
        "确认添加目录",
        ("%s\n\n" % ("这会替换当前的主目录。" if replacing else "")) +
        "添加后，这个目录（含子目录）里的录像会全部计入成绩统计，"
        "并出现在上传列表。\n\n"
        "如果里面有从网上下载的、别人的录像，请先移出去。\n\n"
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
FONT_LSF = (FAM_HEAD, 23, "bold")

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


def fmt_span(sec):
    """秒 → 「2 小时 56 分」。不足 1 分钟只写秒（一天里连着两局刷盘时会出现）。"""
    sec = int(round(float(sec or 0)))
    if sec < 60:
        return "%d 秒" % sec
    h, m = sec // 3600, (sec % 3600) // 60
    if h:
        return "%d 小时 %d 分" % (h, m)
    return "%d 分" % m


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


# ------------------------------------------------------------------ 窗口图标
def icon_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for d in (base, os.path.dirname(os.path.abspath(__file__)),
              os.path.dirname(sys.executable)):
        p = os.path.join(d, "icon.ico")
        if os.path.isfile(p):
            return p
    return None


_ICON_HANDLES = {}       # dpi → (任务栏档, 标题栏档)，句柄全进程共用


def _icon_handles(ico, dpi):
    if dpi not in _ICON_HANDLES:
        u = ctypes.windll.user32

        def load(px):
            px = max(8, int(round(px)))
            return u.LoadImageW(None, ico, 1, px, px, 0x10)   # IMAGE_ICON | LR_LOADFROMFILE
        _ICON_HANDLES[dpi] = (load(24 * dpi / 96.0), load(16 * dpi / 96.0))
    return _ICON_HANDLES[dpi]


def set_window_icon(win):
    """给顶层窗口装应用图标（标题栏 / 任务栏 / Alt-Tab）。

    Tk 的 `iconbitmap` 只从 ico 里取**一张**（按系统大图标尺寸），
    任务栏按钮却按 24dp × 缩放 渲染（150% 下 36px），于是 32 被拉到 36，糊。
    所以这里自己按「实际像素」精确 LoadImage 再 WM_SETICON：
    ico 里有对应档位（本工具会生成 30/36/42 等非标准档）就是 1:1，零缩放。

    带标题栏的外层窗口要到映射之后才建得出来，所以没映射的窗口只走 Tk 那套。
    """
    if not win.winfo_exists():
        return
    ico = icon_path()
    if not ico:
        return
    try:
        win.iconbitmap(ico)         # 兜底：Tk 自己那套也设上
    except tk.TclError:
        pass
    try:
        win.update_idletasks()
        u = ctypes.windll.user32
        wid = win.winfo_id()
        hwnd = u.GetAncestor(wid, 2) or u.GetParent(wid)
        if not hwnd or hwnd == wid:
            return
        try:
            dpi = u.GetDpiForWindow(hwnd) or 96
        except Exception:
            dpi = 96
        big, small = _icon_handles(ico, dpi)
        if big:
            u.SendMessageW(hwnd, 0x0080, 1, big)    # WM_SETICON ICON_BIG
        if small or big:
            u.SendMessageW(hwnd, 0x0080, 0, small or big)   # ICON_SMALL
    except Exception:
        pass


class Dialog(tk.Toplevel):
    """带应用图标的弹窗基类：标题栏不再显示 Tk 默认的羽毛图标。

    Tk 在映射时会按自己的取法再设一次图标，所以隔一拍盖回来（同主窗口）。
    """

    def __init__(self, master=None):
        super().__init__(master)
        self._icon_job = None
        ico = icon_path()
        if ico:
            try:
                self.iconbitmap(ico)
            except tk.TclError:
                pass
        self.bind("<Map>", self._icon_on_map, add="+")

    def _icon_on_map(self, e):
        if e.widget is not self:
            return                      # <Map> 会从子控件冒泡上来
        if self._icon_job is not None:
            self.after_cancel(self._icon_job)
        self._icon_job = self.after(260, lambda: set_window_icon(self))


class SettingsDialog(Dialog):
    """「设置」弹窗：与应用同风格的白底圆角卡片，模态。

    操作收在一排药丸里；强无猜上传是三档单选药丸；LSF 基准是两态胶囊
    （实时 / 固定），胶囊文案与下方说明随状态切换，不再是一句定死的勾选文案。
    「路径」已单独放到横幅（启动元扫雷右边），不在这里。
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self._closed = False
        self.title("设置")
        self.configure(bg=C_BG)
        self.resizable(False, False)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        pad = sc(18)
        head = tk.Frame(self, bg=C_BG)
        head.pack(fill="x", padx=pad, pady=(pad, sc(8)))
        tk.Label(head, text="设置", bg=C_BG, fg=C_TEXT,
                 font=FONT_UI_B).pack(side="left")
        Pill(head, "✕", command=self.close, bg=C_BG,
             height=26, min_width=30, padx=10).pack(side="right")
        card = tk.Frame(self, bg=C_PANEL, highlightbackground=C_LINE,
                        highlightthickness=sc(1))
        card.pack(fill="both", expand=True, padx=pad, pady=(0, pad))
        inner = tk.Frame(card, bg=C_PANEL)
        inner.pack(fill="both", expand=True, padx=sc(16), pady=sc(14))

        tk.Label(inner, text="操作", bg=C_PANEL, fg=C_DIM,
                 font=FONT_SMALL).pack(anchor="w")
        row = tk.Frame(inner, bg=C_PANEL)
        row.pack(fill="x", pady=(sc(4), sc(10)))
        for text, cmd in (("重新扫描", app.start_scan),
                          ("换目录…", app.pick_dir),
                          ("导出 CSV", app.export_csv),
                          ("诊断…", app.open_diagnostics)):
            Pill(row, text, command=self._act(cmd), bg=C_PANEL,
                 height=28, padx=14).pack(side="left", padx=(0, sc(8)))

        tk.Frame(inner, bg=C_LINE, height=sc(1)).pack(fill="x", pady=sc(6))
        tk.Label(inner, text="元扫雷程序", bg=C_PANEL, fg=C_DIM,
                 font=FONT_SMALL).pack(anchor="w")
        self._ms_quiet = True
        self.ms_list = tk.Listbox(
            inner, height=3, width=64, exportselection=False,
            bg=C_BG, fg=C_TEXT, selectbackground=C_ACCENT,
            selectforeground="#ffffff", activestyle="none",
            highlightthickness=sc(1), highlightbackground=C_LINE,
            font=FONT_SMALL, bd=0)
        self.ms_list.pack(fill="x", pady=(sc(4), sc(6)))
        self.ms_list.bind("<<ListboxSelect>>", self._on_ms_pick)
        row = tk.Frame(inner, bg=C_PANEL)
        row.pack(fill="x", pady=(0, sc(8)))
        Pill(row, "添加…", command=self._add_ms_exe, bg=C_PANEL,
             height=28, padx=14).pack(side="left")
        Pill(row, "移除", command=self._remove_ms_exe, bg=C_PANEL,
             height=28, padx=14).pack(side="left", padx=(sc(8), 0))
        chk_kw = dict(bg=C_PANEL, fg=C_TEXT, activebackground=C_PANEL,
                      activeforeground=C_TEXT, selectcolor=C_BG,
                      font=FONT_UI, bd=0, highlightthickness=0, anchor="w")
        self.var_ms_auto = tk.BooleanVar(value=bool(app._ms_auto_launch))
        self.chk_ms_auto = tk.Checkbutton(
            row, text="打开工具箱时自动启动", variable=self.var_ms_auto,
            command=self._toggle_ms_auto, **chk_kw)
        self.chk_ms_auto.pack(side="left", padx=(sc(24), 0))
        self._refresh_ms()

        tk.Frame(inner, bg=C_LINE, height=sc(1)).pack(fill="x", pady=sc(6))
        tk.Label(inner, text="强无猜上传（不计入统计）", bg=C_PANEL, fg=C_DIM,
                 font=FONT_SMALL).pack(anchor="w")
        row = tk.Frame(inner, bg=C_PANEL)
        row.pack(fill="x", pady=(sc(4), sc(10)))
        self._ng_pills = {}
        for val, lab in (("", "关闭"), ("top", "进榜才传"), ("all", "全部上传")):
            p = Pill(row, lab, command=self._ng(val), bg=C_PANEL,
                     height=28, padx=14)
            p.pack(side="left", padx=(0, sc(8)))
            self._ng_pills[val] = p

        tk.Frame(inner, bg=C_LINE, height=sc(1)).pack(fill="x", pady=sc(6))
        tk.Label(inner, text="LSF 基准", bg=C_PANEL, fg=C_DIM,
                 font=FONT_SMALL).pack(anchor="w")
        row = tk.Frame(inner, bg=C_PANEL)
        row.pack(fill="x", pady=(sc(4), sc(2)))
        self._lsf_pill = Pill(row, "", command=self._toggle_lsf, bg=C_PANEL,
                              height=28, padx=16, min_width=110)
        self._lsf_pill.pack(side="left")
        self._lsf_note = tk.Label(inner, text="", bg=C_PANEL, fg=C_DIM,
                                  font=FONT_SMALL, anchor="w", justify="left")
        self._lsf_note.pack(fill="x", pady=(sc(2), 0))
        self._refresh()
        self.grab_set()
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = app.winfo_rootx() + (app.winfo_width() - w) // 2
        y = app.winfo_rooty() + (app.winfo_height() - h) // 3
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))
        self.focus_set()

    def _refresh_ms(self):
        """重画设置里的程序列表，选中当前生效的那一项。"""
        self._ms_quiet = True
        try:
            self.ms_list.delete(0, "end")
            sel = None
            for i, p in enumerate(self.app.ms_exes):
                text = shrink_path(p, 66)
                if not os.path.isfile(p):
                    text += "（不存在）"
                self.ms_list.insert("end", text)
                if os.path.normcase(p) == os.path.normcase(self.app.ms_exe or ""):
                    sel = i
            if sel is not None:
                self.ms_list.selection_set(sel)
                self.ms_list.see(sel)
        finally:
            self._ms_quiet = False
        self.var_ms_auto.set(bool(self.app._ms_auto_launch))

    def _on_ms_pick(self, _e=None):
        if self._ms_quiet:
            return
        sel = self.ms_list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.app.ms_exes):
            self.app.ms_select_exe(self.app.ms_exes[idx])

    def _add_ms_exe(self):
        initial = ""
        if self.app.ms_exe and os.path.isfile(self.app.ms_exe):
            initial = os.path.dirname(self.app.ms_exe)
        else:
            guess = mslaunch.discover()
            if guess:
                initial = os.path.dirname(guess[0])
        p = filedialog.askopenfilename(
            parent=self, title="选择扫雷主程序",
            initialdir=initial if os.path.isdir(initial) else None,
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if not p:
            return
        self.app.ms_remember_exe(p)
        self._refresh_ms()

    def _remove_ms_exe(self):
        sel = self.ms_list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if not (0 <= idx < len(self.app.ms_exes)):
            return
        self.app.ms_remove_exe(self.app.ms_exes[idx])
        self._refresh_ms()

    def _toggle_ms_auto(self):
        reason = self.app.set_ms_auto_launch(bool(self.var_ms_auto.get()))
        if reason:
            self.var_ms_auto.set(False)
            messagebox.showwarning("设置", reason, parent=self)

    def _act(self, cmd):
        def run():
            self.close()
            cmd()
        return run

    def _ng(self, val):
        def run():
            self.app.ng_scope_var.set(val)
            self.app.toggle_ng_scope()
            self._refresh()
        return run

    def _toggle_lsf(self):
        self.app.var_lsf_fixed.set(not self.app.var_lsf_fixed.get())
        self.app.toggle_lsf_fixed()
        self._refresh()

    def _refresh(self):
        for val, p in self._ng_pills.items():
            p.set_selected(self.app.ng_scope_var.get() == val)
        fixed = self.app.var_lsf_fixed.get()
        self._lsf_pill.set_text("固定基准" if fixed else "实时基准")
        self._lsf_pill.set_selected(fixed)
        self._lsf_note.configure(
            text=("赛季快照 %s · 不随网站更新" % SEASON_ANCHORS_AT) if fixed
            else "跟随网站实时锚点")

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

# ------------------------------------------------------------------ 统计卡片
class StatCard(tk.Canvas):
    """一级别一张卡：圆角白底 + 彩色标签 + 三行指标。"""

    def __init__(self, master, level):
        super().__init__(master, bg=C_BG, highlightthickness=0, bd=0,
                         height=sc(168))
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


class LsfCard(tk.Frame):
    """前端成绩页顶部：个人 LSF。左=总分与定阶，右=普通/NF 各级别软实力。

    每一级下面挂一行这一级单独算的定阶。「明细」把九格摊开，看弱在哪一项。
    """

    def __init__(self, master):
        super().__init__(master, bg=C_LINE)
        self._data = None
        self._detail = None
        inner = tk.Frame(self, bg=C_PANEL)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        inner.columnconfigure(1, weight=1)

        left = tk.Frame(inner, bg=C_PANEL)
        left.grid(row=0, column=0, sticky="w", padx=(sc(18), sc(16)), pady=sc(10))
        head = tk.Frame(left, bg=C_PANEL)
        head.pack(anchor="w")
        tk.Label(head, text="LSF", bg=C_PANEL, fg=C_DIM,
                 font=FONT_SECTION).pack(side="left", padx=(0, sc(10)))
        self.lbl_value = tk.Label(head, text="—", bg=C_PANEL, fg=C_TEXT,
                                  font=FONT_LSF)
        self.lbl_value.pack(side="left")
        self.lbl_grade = tk.Label(left, text="", bg=C_PANEL, fg=C_ACCENT3,
                                  font=FONT_UI_B)
        self.lbl_grade.pack(anchor="w")
        self.lbl_sens = tk.Label(left, text="", bg=C_PANEL, fg=C_DIM,
                                 font=FONT_SMALL)
        self.lbl_sens.pack(anchor="w", pady=(sc(2), 0))
        self.pill_detail = Pill(left, "明细", command=self.open_detail,
                                accent=C_ACCENT2, bg=C_PANEL, font=FONT_SMALL,
                                padx=12, height=24)
        self.pill_detail.pack(anchor="w", pady=(sc(6), 0))

        mid = tk.Frame(inner, bg=C_PANEL)
        mid.grid(row=0, column=1, sticky="e", padx=(sc(16), sc(20)), pady=sc(8))
        for col, text in enumerate(("", "初", "中", "高", "综合")):
            tk.Label(mid, text=text, bg=C_PANEL, fg=C_DIM,
                     font=FONT_SMALL).grid(row=0, column=col, sticky="e",
                                           padx=(sc(10), 0))
        self.cells = []
        for row, name in ((1, "普通"), (2, "NF")):
            tk.Label(mid, text=name, bg=C_PANEL, fg=C_DIM,
                     font=FONT_UI).grid(row=row, column=0, sticky="w",
                                        padx=(0, sc(10)), pady=(sc(1), 0))
            line = []
            for col in range(1, 5):
                cell = tk.Frame(mid, bg=C_PANEL)
                cell.grid(row=row, column=col, sticky="e", padx=(sc(10), 0),
                          pady=(sc(1), 0))
                val = tk.Label(cell, text="—", bg=C_PANEL, fg=C_TEXT,
                               font=FONT_MONO, anchor="e")
                val.pack(anchor="e")
                sub = tk.Label(cell, text="", bg=C_PANEL, fg=C_DIM,
                               font=FONT_SMALL, anchor="e")
                sub.pack(anchor="e")
                line.append((val, sub))
            self.cells.append(line)

    @staticmethod
    def _sub_text(info):
        """一格下面的小字：这一级单独算出来的定阶。

        单级 et 四格并排会把卡片撑得比窗口还宽，这里只写定阶。
        """
        grade = (info or {}).get("grade")
        return "%s %.1f" % (grade[0], grade[1]) if grade else ""

    def set_data(self, local=None):
        self._data = local
        if not local:
            self.lbl_value.config(text="—", fg=C_DIM)
            self.lbl_grade.config(text="")
            self.lbl_sens.config(text="")
            for line in self.cells:
                for val, sub in line:
                    val.config(text="—", fg=C_DIM)
                    sub.config(text="")
            return

        soft = local["soft"]
        self.lbl_value.config(text="%.2f" % soft["lsf"], fg=C_TEXT)
        grade = local.get("grade")
        et = local.get("et")
        bits = []
        if grade:
            bits.append("%s %.1f 阶" % (grade[0], grade[1]))
        if et:
            bits.append("et %.1f s" % et)
        self.lbl_grade.config(text=" · ".join(bits))
        sens = local.get("sens") or {}
        if 1 in sens and 25 in sens:
            self.lbl_sens.config(
                text="单局上限 %.2f · 25 局均值 %.2f"
                     % (sens[1]["lsf"], sens[25]["lsf"]))
        else:
            self.lbl_sens.config(text="")

        for line, levels, total in (
                (self.cells[0], local["levels"], soft["std:sum"]),
                (self.cells[1], local["nf_levels"], soft["nf:sum"])):
            for (val, sub), lv in zip(line[:3], core.LEVELS):
                val.config(text="%.2f" % levels[lv]["soft"], fg=C_TEXT)
                sub.config(text=self._sub_text(levels[lv]))
            val, sub = line[3]
            val.config(text="%.2f" % total, fg=C_ACCENT3)
            sub.config(text="")

    def open_detail(self):
        if not self._data:
            return
        if self._detail is not None and self._detail.winfo_exists():
            self._detail.lift()
            self._detail.focus_set()
            return
        self._detail = LsfDetailDialog(self.winfo_toplevel(), self._data)


# ------------------------------------------------------------------ 文件夹置前
def _explorer_windows():
    """当前开着的文件资源管理器窗口（按 Z 序，最近用的在前）。"""
    if os.name != "nt":
        return []
    u = mslaunch._user32()
    try:
        u.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int
        u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
    except Exception:
        return []
    # 必须用 mslaunch 给 EnumWindows 声明过的那个回调类型：wintypes.BOOL 是
    # c_long，跟 ctypes.c_bool 不是一回事，类型对不上会被 ctypes 直接拒绝。
    out = []

    def _cb(hwnd, _lp):
        try:
            cls = ctypes.create_unicode_buffer(64)
            if not u.GetClassNameW(hwnd, cls, 64):
                return True
            if cls.value not in ("CabinetWClass", "ExploreWClass"):
                return True
            if not u.IsWindowVisible(hwnd):
                return True
            out.append(hwnd)
        except Exception:
            pass
        return True

    try:
        u.EnumWindows(mslaunch._ENUMPROC(_cb), 0)
    except Exception:
        pass
    return out


def _window_title(u, hwnd):
    try:
        n = u.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(n)
        u.GetWindowTextW(hwnd, buf, n)
        return buf.value
    except Exception:
        return ""


# ------------------------------------------------------------------ 通用表格
def _delta_text(v, nd=3):
    """差值文本：正负号本身就是方向，不再另加箭头。

    「距当前 PB」是「本局 − PB」（时间越长越差 → 正数），3BV/s / stnb 也是
    「本局 − 参照」（越高越好 → 负数表示不如参照）。PB 那一行拿旧 PB 做参照，
    时间就成了负数（快了这么多），跟旁边几列读法完全一致。

    四舍五入后归零的写 0.000 / 0.00，不冒出 "-0.00" 这种自相矛盾的符号。
    """
    return (f"{v:+.{nd}f}" if abs(v) >= 0.5 * 10 ** -nd
            else f"{0:.{nd}f}")


def _delta_num(v):
    """差距列的排序值（float）；没法比的返回 None。

    单元格里存的是显示文本（"PB -1.164"、"PB"、"+0.27"、"—"），数字就在
    文本里，排序时必须取出来比 —— 当成一个固定值的话，破 PB 的行会全部并列，
    破 1.1 秒和破 0.03 秒并排站着，看着就是瞎排。
    只有 "PB"（该 BV 的第一个成绩，没有旧值可比）当 0；"—" 返回 None，垫底。
    """
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s.startswith("PB"):
        s = s[2:].strip()
        if not s:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def _auto_reasons(row):
    """一条自动上传记录里的完整理由（多条时按「；」拆开）。"""
    why = row.get("why") or ""
    return [s.strip() for s in why.split("；") if s.strip() and s.strip() != "—"]


def _auto_why_pair(s):
    """「涨分·NF · 中级 3BV/s 4.31（…）」→（"涨分·NF", "中级 3BV/s 4.31（…）"）。

    详情按两列摆：左边是这条理由属于哪个榜，右边是具体成绩。拆开之后左边
    一样长，右边才会从同一条竖线开始 —— 整串连在一起时每行行首缩进不同，
    看着就是上下错位。
    """
    head, sep, tail = s.partition(" · ")
    if sep and head and len(head) <= 8:
        return head, tail
    return "", s


# 备注那格放几条短写。一局同时破 PB、进三张榜时理由有六七条，全铺开又回到
# 「一行塞不下」的老样子；多出来的用省略号带过，完整理由在下面的详情条里。
AUTO_NOTE_MAX = 3


def _auto_note(items):
    items = [str(s) for s in (items or []) if s]
    if not items:
        return "—"
    if len(items) > AUTO_NOTE_MAX:
        return " · ".join(items[:AUTO_NOTE_MAX]) + " …"
    return " · ".join(items)


# ------------------------------------------------------------------ 弹出菜单
MENU_KEY = "#010101"      # 圆角外的透明色：文字抗锯齿怎么混也调不出这种近黑


class StyleMenu(tk.Toplevel):
    """与应用同风格的弹出菜单，代替原生 tk.Menu：白底圆角、粉灰描边、悬停浅粉。

    只留右键菜单用得到的接口：add_command / add_separator，然后 popup(x, y) 贴出。
    popup 不阻塞；点到菜单外、切到别的程序、Esc、点中某一项都会收起并自毁。
    """

    POLL_MS = 100
    _current = None

    def __init__(self, master):
        super().__init__(master)
        self.withdraw()            # 位置算好再露面，免得先在左上角闪一下
        self._items = []           # ("cmd", 文案, 回调) / ("sep",)
        self._rows = []            # 每一项的纵向区间 (y0, y1)，与 _cmds 对齐
        self._cmds = []
        self._mw = self._mh = 0
        self._hover = None
        self._closed = False
        self._job = None
        self._prev_focus = None
        self.configure(bg=MENU_KEY)
        self.cv = tk.Canvas(self, bg=MENU_KEY, highlightthickness=0, bd=0, takefocus=0)
        self.cv.pack(fill="both", expand=True)
        # grab 生效时，菜单外的指针事件投递给 grab 窗口（本窗口），菜单内的才落到画布上，
        # 所以两处都要绑；判定一律用屏幕坐标，不依赖事件落在谁身上。
        for w in (self, self.cv):
            w.bind("<Motion>", self._on_motion)
            w.bind("<Button-1>", self._on_click)
            w.bind("<Button-3>", self._on_right)
        self.cv.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<Return>", lambda _e: self._run(self._hover))
        self.bind("<Up>", lambda _e: self._move_hover(-1))
        self.bind("<Down>", lambda _e: self._move_hover(1))

    def add_command(self, label, command=None):
        self._items.append(("cmd", label, command))

    def add_separator(self):
        # 开头和连续两条分隔线不画；结尾那条留到贴出时去掉
        if self._items and self._items[-1][0] != "sep":
            self._items.append(("sep",))

    def labels(self):
        return [it[1] for it in self._items if it[0] == "cmd"]

    def popup(self, x, y):
        """在屏幕坐标 (x, y) 贴出菜单；浮点坐标取整。没有可点的项就不弹。"""
        if StyleMenu._current is not None and StyleMenu._current.winfo_exists():
            StyleMenu._current.close()
        StyleMenu._current = None
        if not self.labels():
            self.destroy()
            return
        self._prev_focus = self.focus_displayof()
        w, h = self._build()
        left, top, aw, ah = work_area()
        margin = sc(4)
        x = int(max(left + margin, min(int(x), left + aw - w - margin)))
        y = int(max(top + margin, min(int(y), top + ah - h - margin)))
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.overrideredirect(True)
        self.attributes("-topmost", True)      # 菜单开着时压过主窗口；收起即销毁
        self.attributes("-transparentcolor", MENU_KEY)
        self.deiconify()
        self.update_idletasks()
        self.grab_set()              # 应用内的点击都先交给菜单：点到外面才知道该收起
        self.focus_set()             # Esc / 上下键要落在菜单上
        StyleMenu._current = self
        self._job = self.after(self.POLL_MS, self._tick)

    def _build(self):
        while self._items and self._items[-1][0] == "sep":
            self._items.pop()
        rh, pad, x0, x1 = sc(30), sc(5), sc(14), sc(18)
        sep = 2 * sc(5) + sc(1)

        def measure(s):
            return int(self.tk.call("font", "measure", FONT_UI, s))

        w = max([measure(it[1]) for it in self._items if it[0] == "cmd"]
                + [sc(150)]) + x0 + x1
        h = pad * 2 + sum(sep if it[0] == "sep" else rh for it in self._items)
        self._mw, self._mh = w, h
        cv = self.cv
        cv.configure(width=w, height=h)
        cv.delete("all")
        round_rect(cv, 1, 1, w - 1, h - 1, sc(10), fill=C_PANEL,
                   outline=C_LINE, width=sc(1), tags="card")
        y, self._rows, self._cmds = pad, [], []
        for it in self._items:
            if it[0] == "sep":
                cv.create_line(x0, y + sc(5), w - x1, y + sc(5),
                               fill=C_LINE, width=sc(1))
                y += sep
                continue
            cv.create_text(x0, y + rh / 2.0, text=it[1], anchor="w",
                           fill=C_TEXT, font=FONT_UI)
            self._rows.append((y, y + rh))
            self._cmds.append(it[2])
            y += rh
        return w, h

    def _row_at(self, y):
        for i, (y0, y1) in enumerate(self._rows):
            if y0 <= y < y1:
                return i
        return None

    def _set_hover(self, i):
        if i == self._hover:
            return
        self._hover = i
        cv = self.cv
        cv.delete("hover")
        if i is None:
            return
        y0, y1 = self._rows[i]
        gap = sc(3)
        round_rect(cv, gap + 1, y0 + gap, self._mw - gap - 1, y1 - gap,
                   (y1 - y0) / 2.0 - gap, fill=C_SEL, width=0, tags="hover")
        cv.tag_raise("hover", "card")       # 压在卡片上、文字下

    def _on_motion(self, e):
        # 事件可能来自 grab 窗口本身，y 相对谁不固定，一律换算到画布上
        self._set_hover(self._row_at(e.y_root - self.cv.winfo_rooty())
                        if self._inside(e.x_root, e.y_root) else None)

    def _move_hover(self, step):
        if self._rows:
            i = 0 if self._hover is None else (self._hover + step) % len(self._rows)
            self._set_hover(i)

    def _run(self, i):
        if i is None:
            return
        cmd = self._cmds[i]
        self.close()
        if cmd:
            cmd()

    def _inside(self, x_root, y_root):
        return (self.winfo_rootx() <= x_root < self.winfo_rootx() + self._mw
                and self.winfo_rooty() <= y_root < self.winfo_rooty() + self._mh)

    def _on_click(self, e):
        if not self._inside(e.x_root, e.y_root):
            self.close()             # 这一下不传给底下的控件，跟原生菜单一致
            return
        i = self._row_at(e.y)
        if i is not None:
            self._run(i)

    def _on_right(self, e):
        if self._inside(e.x_root, e.y_root):
            return
        target = self.winfo_containing(e.x_root, e.y_root)
        self.close()
        if target is not None and target.winfo_exists():
            # 在本程序的别处又按了右键：换到那里重开（资源管理器也是这样）
            target.event_generate("<Button-3>",
                                  x=e.x_root - target.winfo_rootx(),
                                  y=e.y_root - target.winfo_rooty(),
                                  rootx=e.x_root, rooty=e.y_root)

    def _tick(self):
        """盯前台窗口：切到别的程序就收起（grab 管不到别的程序的点击）。"""
        self._job = None
        if not self._closed:
            if not _ours(_foreground()):
                self.close()
            else:
                self._job = self.after(self.POLL_MS, self._tick)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        if StyleMenu._current is self:
            StyleMenu._current = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        prev = self._prev_focus
        if prev is not None and prev.winfo_exists():
            prev.focus_set()


class Table(ttk.Frame):
    """带排序的 Treeview 封装。columns: (key, 标题, 宽度, 对齐, 类型)

    对齐约定（三个桶，别再随手写）：
      "e"      数量 / 成绩 —— 右对齐，数位对齐才好横向比大小
      "center" 短标签 —— 级别、风格、来源、启用、状态、日期时间
      "w"      可读文本 —— 录像文件、目录路径
    """

    def __init__(self, master, columns, height=6, zebra=True, fit=True,
                 selectmode="browse", iid_key=None, vbar=True, on_double=None,
                 on_menu=None, min_rows=3):
        super().__init__(master, style="Card.TFrame")
        self.columns_def = columns
        self.min_rows = max(1, int(min_rows))   # 高度自适应时的下限行数
        self.zebra = zebra
        self.rows = []
        self.iid_key = iid_key          # 用某个字段当 iid，便于原地更新某一行
        self.on_double = on_double      # 自定义双击行为；None 时按后缀播放录像
        # 右键菜单的自定义项：没有本地录像行的表（每日页）靠它挂自己的命令，
        # 「播放 / 复制路径」那三项只对真有文件的行才有意义
        self.on_menu = on_menu
        self._sort_key = None
        self._sort_rev = False

        keys = [c[0] for c in columns]
        self.tv = ttk.Treeview(self, columns=keys, show="headings",
                               height=height, selectmode=selectmode)
        self._displayed = tuple(keys)
        for key, title, width, anchor, _kind in columns:
            # 表头跟单元格同向，否则「数字右对齐、标题居中」看着像没对齐
            self.tv.heading(key, text=title, anchor=anchor,
                            command=lambda k=key: self._sort_by(k))
            self.tv.column(key, width=sc(width), anchor=anchor,
                           stretch=(key in ("file", "path", "player", "note",
                                            "vs_pb")))
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
        self.tv.bind("<Double-1>", self._on_double)
        self.tv.bind("<Button-3>", self._on_right_click)
        self._tip = None
        self._fit_job = None
        self._fit_rows = height
        # 表格底下还挂着一个块（比如详情条）时填进来：算行数要把它扣掉，
        # 否则最后一行会压到它上面。
        self.extra_bottom = None
        if fit:
            self.bind("<Configure>", self._schedule_fit)

    # --- 高度自适应 ---
    def _schedule_fit(self, _e=None):
        if self._fit_job:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(20, self._fit)

    def refit(self):
        """内容变了（比如底下的块变高）时重算行数。"""
        self._schedule_fit()

    def _fit(self):
        """按实际可用高度调行数，免得末行被切成半截。"""
        self._fit_job = None
        h = self.winfo_height()
        if self.extra_bottom is not None:
            try:
                h -= self.extra_bottom.winfo_reqheight()
            except tk.TclError:
                pass
        if h < sc(60):
            return
        row_h = max(1, sc(32))
        head = sc(47)
        kids = self.tv.get_children()
        if kids:
            bb = self.tv.bbox(kids[0])      # bbox 的 y 就是表头实际高度
            if bb:
                head = bb[1]
        fit_rows = int((h - head) // row_h)
        # 够宽裕时至少留 min_rows 行；实在放不下就别硬撑 —— 硬撑的那几行会
        # 把底下的详情条顶出表格外，最后几行理由就被裁掉了。
        rows = max(self.min_rows, fit_rows) if fit_rows >= self.min_rows \
            else max(1, fit_rows)
        if rows != self._fit_rows:
            self._fit_rows = rows
            self.tv.configure(height=rows)

    # --- 数据 ---
    def set_hidden(self, keys):
        """临时藏掉指定列，不改变底层 rows 里的数据。"""
        hide = set(keys or ())
        disp = tuple(c[0] for c in self.columns_def if c[0] not in hide)
        if disp != self._displayed:
            self._displayed = disp
            self.tv.configure(displaycolumns=disp or ("#all",))

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
        """把一条记录转成 Treeview 的列值（格式化规则在这里统一）。"""
        vals = []
        for key, _t, _w, _a, kind in self.columns_def:
            v = r.get(key, "")
            if kind == "f3" and isinstance(v, (int, float)):
                v = f"{v:.3f}"
            elif kind == "f2" and isinstance(v, (int, float)):
                v = f"{v:.2f}"
            elif kind == "clock" and isinstance(v, (int, float)):
                # epoch 秒 → HH:MM:SS；定宽字符串，字典序就是时间序
                v = core.clock_text(v)
            elif kind == "int" and isinstance(v, (int, float)):
                v = f"{int(v)}"
            elif kind == "delta" and isinstance(v, (int, float)):
                # 与基准的差距：正负号就是方向（+ 慢了多少，− 快了多少）。
                # 非数值（"PB" / "—"）原样显示 —— 它们不是数字。
                v = _delta_text(v, 3)
            elif kind == "delta2" and isinstance(v, (int, float)):
                # 3BV/s（= bv/time）、stnb（= C·bv/time^1.7）在同一 BV 下都只随
                # 用时单调递减，而 PB 就是该 BV 最快的局 —— 这两项本局恒不高于
                # PB，所以相对当前 PB 只会是负数（比 PB 低多少）。
                v = _delta_text(v, 2)
            elif kind == "gain":
                # 涨分固定占一列，不涨的行留空；两位小数后仍能看清小幅涨分。
                v = f"+{v:.2f}" if isinstance(v, (int, float)) and v > 0.005 else ""
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
            switched = self._sort_key != key
            if not switched:
                self._sort_rev = not self._sort_rev
            else:
                self._sort_key, self._sort_rev = key, False
            # 数值列第一次点默认降序更符合直觉（bvs/stnb/局数）；换到这一列
            # 才算「第一次」，第二次点必须能反向 —— 否则数值列永远回不来。
            # 差距列里，时间越小越好（升序即从好到坏），3BV/s、stnb 越大越好
            # （得从降序起步），所以只有 delta2 进这个默认降序名单。
            kind = next(c[4] for c in self.columns_def if c[0] == key)
            if switched and kind in ("f3", "f2", "int", "gain", "delta2") \
                    and not self._sort_rev:
                self._sort_rev = True

        kind = next(c[4] for c in self.columns_def if c[0] == key)

        def keyf(r):
            v = r.get(key)
            if kind in ("delta", "delta2"):
                # 差距列按「从好到坏」排：时间越小越好（升序），3BV/s、stnb
                # 越大越好（降序，见上面 _sort_by 给的默认方向）。
                return _delta_num(v)
            if kind == "gain":
                # 涨分列有一半是空的（没涨），空要能参与比较，而且不能被
                # 当成字符串 —— "+12.30" 按字典序会排在 "+2.80" 前面。
                return float(v) if isinstance(v, (int, float)) else float("-inf")
            if isinstance(v, str):
                try:
                    return float(v)
                except ValueError:
                    return v.lower()
            if v is None:
                return float("-inf") if isinstance(r.get(key), float) else ""
            return v

        try:
            if kind in ("delta", "delta2"):
                # 没法比的「—」不分方向都压在最底下：按数值排的话，升序时它
                # 会被当成最大、降序时当成最小，两头都往最前面凑，反一次就
                # 顶到第一行去了。所以先切开，只排有数的那些。
                has = [r for r in self.rows if _delta_num(r.get(key)) is not None]
                none = [r for r in self.rows if _delta_num(r.get(key)) is None]
                has.sort(key=keyf, reverse=self._sort_rev)
                self.rows = has + none
            else:
                self.rows.sort(key=keyf, reverse=self._sort_rev)
        except TypeError:
            self.rows.sort(key=lambda r: str(r.get(key)), reverse=self._sort_rev)

        # 更新表头箭头
        for k, title, _w, _a, _kind in self.columns_def:
            mark = " ▼" if self._sort_rev else " ▲"
            self.tv.heading(k, text=title + (mark if k == key else ""))
        self._redraw()

    def _row_of(self, iid):
        """iid → 行数据 dict。取不到就返回空 dict。"""
        if not iid:
            return {}
        if self.iid_key:
            for r in self.rows:
                if str(r.get(self.iid_key)) == iid:
                    return r
            return {}
        try:
            return self.rows[int(iid)]
        except (IndexError, ValueError):
            return {}

    def _path_of(self, iid):
        """这一行对应的本地路径（目录表就是目录本身，录像表是录像文件）。"""
        if not iid:
            return ""
        return self._row_of(iid).get("path") or (
            iid if self.iid_key == "path" else "")

    def _on_double(self, event=None):
        """双击：录像交给元扫雷播放，其余（目录之类）仍然复制路径。

        按后缀分派而不是按表格配置：同一张表里，本地行有录像、站点行没有，
        用后缀判断最省事，也不会因为以后加了一张表就漏掉。
        """
        iid = self.tv.identify_row(event.y) if event is not None else None
        if iid:
            self.tv.selection_set(iid)
        else:
            sel = self.tv.selection()
            iid = sel[0] if sel else None
        if not iid:
            return
        if self.on_double is not None:
            self.on_double(iid)
            return
        path = self._path_of(iid)
        if not path:
            top = self.winfo_toplevel()
            if hasattr(top, "set_status"):
                top.set_status("这一行没有对应的本地录像文件", "dim")
            return
        top = self.winfo_toplevel()
        if mslaunch.is_replay(path) and hasattr(top, "open_replay"):
            top.open_replay(path)
        else:
            self._copy_path_text(path)

    def _on_right_click(self, event):
        iid = self.tv.identify_row(event.y)
        if not iid:
            return
        self.tv.selection_set(iid)
        m = StyleMenu(self)
        path = self._path_of(iid)
        top = self.winfo_toplevel()
        # 详情排第一：点右键盘就是为了先看一眼这局，别让它排在播放下面。
        # 只有真有录像文件的行才有详情（汇总行、站点行没有 path）。
        if path and hasattr(top, "show_game_detail"):
            m.add_command(label="详细信息",
                          command=lambda p=path: top.show_game_detail(p))
            m.add_separator()
        if self.on_menu is not None:
            self.on_menu(iid, m)
        if path:
            m.add_separator()
            if mslaunch.is_replay(path) and hasattr(top, "open_replay"):
                m.add_command(label="用元扫雷播放",
                              command=lambda p=path: top.open_replay(p))
            m.add_command(label="复制路径",
                          command=lambda p=path: self._copy_path_text(p))
            m.add_command(label="打开所在文件夹",
                          command=lambda p=path: self._reveal(p))
        m.popup(event.x_root, event.y_root)

    def _copy_path_text(self, path):
        self.clipboard_clear()
        self.clipboard_append(path)
        top = self.winfo_toplevel()
        if hasattr(top, "set_status"):
            top.set_status("已复制路径：" + path, "dim", hold=5000)

    def _reveal(self, path):
        """在资源管理器里定位它；目录就直接打开目录。"""
        top = self.winfo_toplevel()
        if hasattr(top, "_reveal_path"):
            top._reveal_path(path)
            return
        # 还没建好主窗口时的兜底：普通打开，至少不会什么都没发生
        if os.path.isdir(path):
            os.startfile(path)
        elif os.path.isfile(path):
            subprocess.Popen('explorer /select,"%s"' % os.path.normpath(path))


# ------------------------------------------------------------------ PB 网格
# 每级四档底色：最深 / 中间 / 更浅 / 最浅。空缺另有一种（灰紫，不带级别色）。
PB_TONE = {
    "b": ("#ff6f9c", "#ffa8c4", "#ffc9dc", "#ffe4ee"),
    "i": ("#3fa9dd", "#8fcbe9", "#b7dcf3", "#e0f3fd"),
    "e": ("#8f6ce8", "#bda6f3", "#d3c3f8", "#ebe4fd"),
}
PB_EMPTY_BG = "#f7f1f6"
PB_GATE = {"b": 10, "i": 30, "e": 100}       # 官方门槛，低于它的格子描一圈
# 网格从哪开始画：下限固定，上限跟着已有成绩走（最大 BV 所在的那一段）。
# 初级 0–9 那行里 0、1 两个位置留空 —— BV 从 2 起，空着比画出来好看。
PB_BV_FLOOR = {"b": 2, "i": 30, "e": 100}
FONT_PB = (FAM_MONO, 9)


class PBGrid(tk.Frame):
    """三级 PB 纵向同页；每格以 (级别, BV) 标识，按 BV 十位段和个位落位。"""

    COLS = 10
    ROW_H = 26

    def __init__(self, master, on_pick=None):
        super().__init__(master, bg=C_BG)
        self._on_pick = on_pick
        self.metric = "time"
        self.cat = "all"
        self.data = {}
        self.counts = {}
        self.hits = set()
        self.picked = None
        self.mode = "rank"        # rank=按全国名次上色 / self=按本级快慢上色
        self._bv_of = {}
        self._sections = []       # (级别, 首行 BV, 控件池起点, 行数)

        self.cv = tk.Canvas(self, bg=C_BG, highlightthickness=0, bd=0,
                            yscrollincrement=sc(self.ROW_H + 2))
        self.vs = ttk.Scrollbar(self, orient="vertical", command=self.cv.yview)
        self.cv.configure(yscrollcommand=self._on_yscroll)
        self._vs_on = False
        self.cv.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.cv, bg=C_BG)
        self._win = self.cv.create_window(0, 0, window=self.inner, anchor="nw")
        self.cv.bind("<Configure>", self._on_configure)
        self.cv.bind("<MouseWheel>", self._on_wheel)
        self.inner.bind("<Configure>", self._on_inner)
        self.inner.bind("<MouseWheel>", self._on_wheel)

        tk.Label(self.inner, text="", bg=C_BG).grid(row=0, column=0)
        for c in range(self.COLS):
            tk.Label(self.inner, text=str(c), bg=C_BG, fg=C_DIM,
                     font=FONT_SMALL).grid(row=0, column=c + 1, sticky="ew",
                                           pady=(0, sc(4)))
        self.inner.columnconfigure(0, minsize=sc(48))
        for c in range(self.COLS):
            self.inner.columnconfigure(c + 1, weight=1, uniform="pbg")

        self._section_titles = {}
        self._section_empty = {}
        for lv in core.LEVELS:
            title = tk.Label(self.inner, bg=C_PANEL,
                             fg=LEVEL_ACCENT[lv][0], font=FONT_UI_B,
                             anchor="w", padx=sc(10))
            empty = tk.Label(self.inner, text="还没有成绩", bg=C_BG, fg=C_DIM,
                             font=FONT_SMALL, anchor="w", padx=sc(10))
            self._section_titles[lv] = title
            self._section_empty[lv] = empty
        for child in self.inner.winfo_children():
            child.bind("<MouseWheel>", self._on_wheel)
        self._rowlab = []
        self._cells = []
        self._max_h = 0
        self._need_h = 0

    # --- 滚动 ---
    def _on_configure(self, event):
        self.cv.itemconfigure(self._win, width=event.width)
        self._sync_vs()

    def _on_inner(self, _e=None):
        self.cv.configure(scrollregion=self.cv.bbox("all"))
        self._sync_vs()

    def _on_yscroll(self, first, last):
        self.vs.set(first, last)

    def _sync_vs(self):
        # 隐藏行后的 reqheight 仍可能是旧值，滚动条只看已计算的内容高度。
        need = self._need_h > self.cv.winfo_height() + 2
        if need != self._vs_on:
            self._vs_on = need
            if need:
                self.vs.pack(side="right", fill="y", before=self.cv)
            else:
                self.vs.pack_forget()

    def set_max_height(self, h):
        """网格最多占多高；行数少时它自己收缩，剩下的空由页面留白。"""
        self._max_h = max(sc(80), int(h))
        self._apply_height()

    def _apply_height(self):
        h = self._need_h
        if self._max_h:
            h = min(h, self._max_h)
        if h > 0:
            self.cv.configure(height=h)
        self._sync_vs()

    def _on_wheel(self, event):
        if self._vs_on and event.delta:
            self.cv.yview_scroll(-3 if event.delta > 0 else 3, "units")
            return "break"

    # --- 控件池 ---
    def _ensure_rows(self, n):
        while len(self._cells) < n:
            i = len(self._cells)
            lab = tk.Label(self.inner, text="", bg=C_BG, fg=C_DIM,
                           font=FONT_SMALL, anchor="e", padx=sc(8))
            lab.grid(row=i + 1, column=0, sticky="nsew", pady=(0, sc(2)))
            lab.bind("<MouseWheel>", self._on_wheel)
            row = []
            for c in range(self.COLS):
                cell = tk.Label(self.inner, text="", bg=PB_EMPTY_BG, fg=C_TEXT,
                                font=FONT_PB, bd=0, highlightthickness=1,
                                highlightbackground=PB_EMPTY_BG,
                                highlightcolor=PB_EMPTY_BG, cursor="hand2")
                cell.grid(row=i + 1, column=c + 1, sticky="nsew",
                          padx=(0, sc(2)), pady=(0, sc(2)))
                cell.bind("<Button-1>", self._on_click)
                cell.bind("<Double-1>", self._on_double)
                cell.bind("<MouseWheel>", self._on_wheel)
                self._bv_of[cell] = None
                row.append(cell)
            self.inner.rowconfigure(i + 1, minsize=sc(self.ROW_H))
            self._rowlab.append(lab)
            self._cells.append(row)

    # --- 数据 ---
    def set_counts(self, counts):
        self.counts = counts or {}

    def bind_data(self, metric, rows, cat="all"):
        """灌入三级同榜数据，返回 (有成绩的 BV 数, 网格里一共多少格)。"""
        self.metric, self.cat = metric, cat
        self.data = dict(rows or {})
        self.picked = None
        self.hits = set()
        self._sections = []
        for widgets in (self._section_titles, self._section_empty):
            for widget in widgets.values():
                widget.grid_remove()
        for lab in self._rowlab:
            lab.grid_remove()
        for row in self._cells:
            for cell in row:
                cell.grid_remove()

        grid_row = 1
        pool_row = 0
        total_cells = 0
        need_h = sc(24)
        self.mode = "rank" if any(self.rank_of(r) for r in self.data.values()) else "self"
        for lv in core.LEVELS:
            bvs = sorted(bv for level, bv in self.data if level == lv)
            top_pad = sc(8) if grid_row > 1 else 0
            self._section_titles[lv].config(
                text="%s · %d 个 BV" % (core.LEVEL_CN[lv], len(bvs)))
            self._section_titles[lv].grid(
                row=grid_row, column=0, columnspan=self.COLS + 1,
                sticky="ew", padx=(0, sc(2)), pady=(top_pad, sc(4)))
            grid_row += 1
            need_h += sc(30) + top_pad
            if not bvs:
                self._section_empty[lv].grid(
                    row=grid_row, column=0, columnspan=self.COLS + 1,
                    sticky="ew", pady=(0, sc(4)))
                grid_row += 1
                need_h += sc(26)
                continue

            # 没成绩的十位段整行留着，「哪一段一格没有」才看得见。
            floor = PB_BV_FLOOR.get(lv, 1)
            lo = (floor // 10) * 10
            hi = max(lo, (bvs[-1] // 10) * 10)
            n = (hi - lo) // 10 + 1
            self._ensure_rows(pool_row + n)
            self._sections.append((lv, lo, pool_row, n))
            for i in range(n):
                r = pool_row + i
                self.inner.rowconfigure(grid_row + i, minsize=sc(self.ROW_H))
                self._rowlab[r].grid(row=grid_row + i, column=0,
                                     sticky="nsew", pady=(0, sc(2)))
                for c, cell in enumerate(self._cells[r]):
                    cell.grid(row=grid_row + i, column=c + 1, sticky="nsew",
                              padx=(0, sc(2)), pady=(0, sc(2)))
            grid_row += n
            pool_row += n
            need_h += n * (sc(self.ROW_H) + sc(2))
            total_cells += n * self.COLS - sum(
                1 for bv in range(lo, lo + n * self.COLS) if bv < floor)

        self._paint()
        self._need_h = need_h
        self._apply_height()
        self.cv.yview_moveto(0)
        return len(self.data), total_cells

    def set_hits(self, hits):
        self.hits = set(hits or ())
        self._paint()

    def pick(self, key):
        self.picked = key
        self._paint()
        if self._on_pick:
            self._on_pick(key)

    # --- 上色 ---
    def rank_of(self, row):
        """这一格在全国的名次；本地比网站快时网站名次不代表这条成绩。"""
        rank = row.get("site_rank")
        if not rank or row.get("source") == "local":
            return None
        try:
            return int(rank)
        except (TypeError, ValueError):
            return None

    def count_of(self, row):
        key = "%s:%s:%d" % ("nf" if self.cat == "nf" else "std",
                            row["level"], int(row.get("bv") or 0))
        return self.counts.get(key)

    def _tiers(self, level):
        """按本级 3BV/s 三分位分档，用于没登录（拿不到名次）时。"""
        vals = sorted(((float(r.get("bvs") or 0), bv)
                       for (lv, bv), r in self.data.items() if lv == level),
                      reverse=True)
        n = len(vals)
        return {bv: (0 if k < n / 3.0 else 1 if k < 2 * n / 3.0 else 2)
                for k, (_v, bv) in enumerate(vals)}

    def _tone_index(self, row, tiers):
        rank, cnt = self.rank_of(row), self.count_of(row)
        if rank and cnt:
            p = rank / float(cnt)
            if p <= 0.10:
                return 0
            if p <= 0.35:
                return 1
            return 2 if p <= 0.65 else 3
        if tiers:
            return tiers.get(int(row.get("bv") or 0), 3)
        return 3

    def _text(self, row):
        m = self.metric
        if m == "bvs":
            return "%.2f" % float(row.get("bvs") or 0)
        if m == "stnb":
            return "%.1f" % float(row.get("stnb") or 0)
        if m == "rank":
            rank, cnt = self.rank_of(row), self.count_of(row)
            return "%d/%d" % (rank, cnt) if rank and cnt else "—"
        t = float(row.get("time") or 0)
        if t >= 100:
            return "%.0f" % t
        return "%.1f" % t if t >= 10 else "%.2f" % t

    def _paint(self):
        for lv, lo, first, n in self._sections:
            tones = PB_TONE.get(lv, PB_TONE["b"])
            gate = PB_GATE.get(lv, 0)
            floor = PB_BV_FLOOR.get(lv, 1)
            tiers = self._tiers(lv) if self.mode == "self" else {}
            for i in range(n):
                r = first + i
                start = lo + i * self.COLS
                self._rowlab[r].config(text="%d–%d" % (start, start + 9))
                for c, cell in enumerate(self._cells[r]):
                    bv = start + c
                    key = (lv, bv)
                    if bv < floor:            # 级别起点之前的格子留空
                        cell.config(text="", bg=C_BG, highlightbackground=C_BG,
                                    cursor="")
                        self._bv_of[cell] = None
                        continue
                    row = self.data.get(key)
                    if row is None:
                        cell.config(text="·", bg=PB_EMPTY_BG, fg="#d3c3cd",
                                    highlightbackground=PB_EMPTY_BG, cursor="")
                        self._bv_of[cell] = None
                        continue
                    idx = self._tone_index(row, tiers)
                    bg = tones[idx]
                    # 最浅档也要靠描边与空格区分。
                    ring = mix(bg, C_TEXT, 0.16)
                    if bv < gate:
                        ring = tones[0]
                    if key in self.hits:
                        ring = C_ACCENT3
                    if key == self.picked:
                        ring = C_TEXT
                    cell.config(text=self._text(row), bg=bg,
                                fg="#ffffff" if idx == 0 else C_TEXT,
                                highlightbackground=ring, cursor="hand2")
                    self._bv_of[cell] = key

    # --- 交互 ---
    def _on_click(self, event):
        key = self._bv_of.get(event.widget)
        if key is not None:
            self.pick(key)

    def _on_double(self, event):
        key = self._bv_of.get(event.widget)
        if key is None:
            return
        self.pick(key)
        self.play(self.data.get(key) or {})

    def play(self, row):
        """把这一格的录像交给元扫雷播放。"""
        path = (row or {}).get("path") or ""
        top = self.winfo_toplevel()
        if not path:
            if hasattr(top, "set_status"):
                top.set_status("这条成绩没有本地录像", "warn")
            return
        if not hasattr(top, "open_replay"):
            return
        top.open_replay(path)

    def reveal(self, row):
        path = (row or {}).get("path") or ""
        top = self.winfo_toplevel()
        if not path:
            if hasattr(top, "set_status"):
                top.set_status("这条成绩没有本地录像", "warn")
            return
        if hasattr(top, "_reveal_path"):
            top._reveal_path(path)
            return
        try:
            if os.path.isfile(path):
                subprocess.Popen('explorer /select,"%s"' % os.path.normpath(path))
            else:
                folder = os.path.dirname(path)
                if os.path.isdir(folder):
                    os.startfile(folder)
                else:
                    raise OSError("路径不存在")
        except OSError:
            if hasattr(top, "set_status"):
                top.set_status("这个位置打不开了：%s" % shrink_path(path, 50), "warn")


# ------------------------------------------------------------------ LSF 明细
class LsfDetailDialog(Dialog):
    """LSF 明细：九个格子（三级 × 三项），普通和 NF 并排。

    格子是「前五均值 · 软实力」。点一行看撑起它的五局，普通 / NF 用上面两枚
    小标签切换。不放说明文字 —— 这些词玩家都认识。
    """

    METRIC_CN = {"time": "Time", "bvs": "3BV/s", "stnb": "stnb"}
    STAT = {"time": "t", "bvs": "b", "stnb": "s"}

    def __init__(self, master, report):
        super().__init__(master)
        self.title("LSF 明细")
        self.configure(bg=C_BG)
        self.transient(master)
        self.minsize(sc(720), sc(400))
        self._report = report or {}
        self._mode = "std"
        self._rows = self._build_rows()

        wrap = tk.Frame(self, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=sc(20), pady=sc(16))

        # 底边先占住：窗口拉高时多出来的空白落在两张表之间，按钮不会被顶走
        foot = tk.Frame(wrap, bg=C_BG)
        foot.pack(side="bottom", fill="x", pady=(sc(10), 0))
        Pill(foot, "关闭", command=self.destroy, accent=C_DIM,
             padx=18, height=30).pack(side="right")

        sens = self._report.get("sens") or {}
        if sens:
            sf = tk.Frame(wrap, bg=C_PANEL)
            sf.pack(fill="x", pady=(0, sc(10)))
            for n in sorted(sens):
                cell = tk.Frame(sf, bg=C_PANEL)
                cell.pack(side="left", padx=(sc(14), sc(22)), pady=sc(8))
                tk.Label(cell, text="只取最好 %d 局" % n, bg=C_PANEL,
                         fg=C_DIM, font=FONT_SMALL).pack(anchor="w")
                tk.Label(cell, text="%.2f" % sens[n]["lsf"], bg=C_PANEL,
                         fg=C_TEXT, font=FONT_UI_B).pack(anchor="w")
                g = sens[n].get("grade")
                tk.Label(cell, text=("%s %.1f 阶" % g) if g else "",
                         bg=C_PANEL, fg=C_DIM,
                         font=FONT_SMALL).pack(anchor="w")
            tk.Label(sf, text="三档越接近，水平越稳；\n上限明显更高 = 有爆发、缺稳定。",
                     bg=C_PANEL, fg=C_DIM, font=FONT_SMALL,
                     justify="left").pack(side="left", padx=(sc(6), sc(14)))

        cols = [
            ("level_cn", "级别", 90, "center", "str"),
            ("metric_cn", "项目", 110, "center", "str"),
            ("std", "普通", 242, "center", "str"),
            ("nf", "NF", 242, "center", "str"),
        ]
        holder = tk.Frame(wrap, bg=C_LINE)
        holder.pack(fill="x")
        self.tab = Table(holder, cols, height=9, fit=False)
        self.tab.pack(fill="both", expand=True, padx=1, pady=1)
        self.tab.set_rows(self._rows)
        self.tab.tv.bind("<<TreeviewSelect>>", self._on_pick)

        bar = tk.Frame(wrap, bg=C_BG)
        bar.pack(fill="x", pady=(sc(12), sc(6)))
        self.pills = {}
        for mode, text in (("std", "普通"), ("nf", "NF")):
            pill = Pill(bar, text, command=lambda m=mode: self._set_mode(m),
                        padx=14, height=26)
            pill.pack(side="left", padx=(0, sc(8)))
            pill.set_selected(mode == self._mode)
            self.pills[mode] = pill
        self.lbl_games = tk.Label(bar, text="", bg=C_BG, fg=C_DIM,
                                  font=FONT_SMALL, anchor="w")
        self.lbl_games.pack(side="left", padx=(sc(12), 0))

        holder2 = tk.Frame(wrap, bg=C_LINE)
        holder2.pack(fill="x")
        gcols = [
            ("rank", "#", 36, "e", "int"),
            ("time", "time", 84, "e", "f3"),
            ("bv", "3BV", 56, "e", "int"),
            ("bvs", "3BV/s", 74, "e", "f3"),
            ("stnb", "stnb", 66, "e", "f2"),
            ("date", "日期", 112, "center", "str"),
            ("source_cn", "来源", 56, "center", "str"),
            ("file", "录像", 200, "w", "str"),
        ]
        self.tab_games = Table(holder2, gcols, height=5, fit=False)
        self.tab_games.pack(fill="both", expand=True, padx=1, pady=1)

        if self._rows:
            self.tab.tv.selection_set("0")
            self._on_pick()
        self._fit_on_screen()

    def _fit_on_screen(self):
        """按内容长完之后收进屏幕里。底边掉到屏幕外，关闭按钮就点不到了。"""
        self.update_idletasks()
        wa = work_area()
        if not wa:
            return
        wx, wy, ww, wh = wa
        w = min(self.winfo_reqwidth(), ww - sc(40))
        h = min(self.winfo_reqheight(), wh - sc(48))
        x = wx + max(0, (ww - w) // 2)
        y = wy + max(0, (wh - h) // 2)
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))

    # --- 数据 ---
    def _fmt_mean(self, mean, level, metric):
        """前五均值按习惯写：时间带秒、stnb 用站点显示的整数刻度。"""
        if not mean or (metric == "time" and mean >= core.LSF_MISS_TIMEMS):
            return "—"
        if metric == "time":
            return "%.3f s" % (mean / 1000.0)
        if metric == "stnb":
            return "%.1f" % (mean * core.STNB_CONST[level])
        return "%.3f" % mean

    def _cell(self, mean, level, metric, anchor, stat):
        text = self._fmt_mean(mean, level, metric)
        if text == "—" or anchor <= 0:
            return text
        return "%s · %.0f%%" % (text, core.lsf_radius(mean, anchor, stat))

    def _build_rows(self):
        rep = self._report
        means = rep.get("means") or {}
        anchors = rep.get("anchors") or {}
        rows = []
        for lv in core.LEVELS:
            for metric in ("time", "bvs", "stnb"):
                stat = self.STAT[metric]
                item = {
                    "_level": lv,
                    "_metric": metric,
                    "level_cn": core.LEVEL_CN[lv],
                    "metric_cn": self.METRIC_CN[metric],
                }
                for mode in ("std", "nf"):
                    anchor = core.anchor_value(
                        anchors, "%s:%s%s" % (mode, lv, stat))
                    item[mode] = self._cell(means.get((mode, lv, metric), 0.0),
                                            lv, metric, anchor, stat)
                rows.append(item)
        return rows

    def _set_mode(self, mode):
        self._mode = mode
        for m, pill in self.pills.items():
            pill.set_selected(m == mode)
        self._on_pick()

    def _on_pick(self, _e=None):
        if not self._rows:
            return
        sel = self.tab.tv.selection()
        try:
            idx = int(sel[0]) if sel else 0
        except ValueError:
            idx = 0
        row = self._rows[min(idx, len(self._rows) - 1)]
        self._show_games(row["_level"], row["_metric"])

    def _show_games(self, level, metric):
        games = (self._report.get("tops") or {}).get(
            (self._mode, level, metric)) or []
        out = []
        for i, g in enumerate(games, 1):
            item = dict(g)
            item["rank"] = i
            item["source_cn"] = core.TOP_SOURCE_CN.get(g.get("source"), "")
            out.append(item)
        self.tab_games.set_rows(out)
        title = "%s %s" % (core.LEVEL_CN[level], self.METRIC_CN[metric])
        if len(out) < core.LSF_TOP:
            title += "　%d 局" % len(out)
        self.lbl_games.config(text=title)


# ------------------------------------------------------------------ 当天明细
class DailyDialog(Dialog):
    """某一天的全部完成局（每日页双击某天，或点「当天明细」）。

    一天最多几百局，所以不做分页也不加搜索：一屏滚下来就是当天从晚到早
    的完整记录。双击某局直接用元扫雷播放 —— 与 PB 页、概览页同一个动作。
    """

    COLS = [
        ("clock", "时刻", 92, "center", "str"),
        ("time", "time", 92, "e", "f3"),
        ("bv", "BV", 58, "e", "int"),
        ("bvs", "3BV/s", 88, "e", "f3"),
        ("stnb", "stnb", 80, "e", "f2"),
        ("style_cn", "风格", 60, "center", "str"),
        ("vs_pb", "距当前 PB", 120, "e", "delta"),
    ]

    def __init__(self, master, title, summary, rows):
        super().__init__(master)
        self.app = master
        self.title("当天明细")
        self.configure(bg=C_BG)
        self.transient(master)
        self.minsize(sc(680), sc(260))

        wrap = tk.Frame(self, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=sc(20), pady=sc(16))

        # 底边先占住：窗口拉高时多出来的空白落在表格里，按钮不会被顶走
        foot = tk.Frame(wrap, bg=C_BG)
        foot.pack(side="bottom", fill="x", pady=(sc(10), 0))
        Pill(foot, "关闭", command=self.destroy, accent=C_DIM,
             padx=18, height=30).pack(side="right")
        best = min(rows, key=lambda r: r["time"]) if rows else None
        if best and best.get("path"):
            Pill(foot, "播放最快", command=lambda p=best["path"]: self.open_replay(p),
                 accent=C_ACCENT, padx=16, height=30).pack(side="right",
                                                          padx=(0, sc(8)))
        tk.Label(foot, text="双击某一局，用元扫雷播放", bg=C_BG, fg=C_DIM,
                 font=FONT_SMALL).pack(side="left")

        tk.Label(wrap, text=title, bg=C_BG, fg=C_TEXT, font=FONT_SECTION,
                 anchor="w").pack(fill="x")
        tk.Label(wrap, text=summary, bg=C_BG, fg=C_DIM, font=FONT_SMALL,
                 anchor="w").pack(fill="x", pady=(sc(2), sc(10)))

        holder = tk.Frame(wrap, bg=C_LINE)
        holder.pack(fill="x")
        # 行数跟着当天局数走：只打了 1 局就别弹出一个半屏空白的框；
        # 上限 14 行，再多的当天滚动看（单日最多见过 578 局）。
        self.tab = Table(holder, self.COLS, height=min(max(3, len(rows)), 14),
                         fit=False)
        self.tab.pack(fill="both", expand=True, padx=1, pady=1)
        self.tab.set_rows(rows)
        # 表格按行数定高，窗口再多出来的空间留在下面（背景色），
        # 不能让 holder 拉长 —— 那会在表格下沿拖出一块白底
        tk.Frame(wrap, bg=C_BG).pack(fill="both", expand=True)
        self._fit_on_screen()

    # 表格的双击按后缀找 open_replay，而 winfo_toplevel() 是本对话框：
    # 转发给主窗口，动作和别处一模一样；set_status 同理。
    def open_replay(self, path):
        self.app.open_replay(path)

    def set_status(self, text, kind="dim", hold=None, transient=False):
        self.app.set_status(text, kind, hold=hold, transient=transient)

    def _fit_on_screen(self):
        self.update_idletasks()
        wa = work_area()
        if not wa:
            return
        wx, wy, ww, wh = wa
        w = min(self.winfo_reqwidth(), ww - sc(40))
        h = min(self.winfo_reqheight(), wh - sc(48))
        x = wx + max(0, (ww - w) // 2)
        y = wy + max(0, (wh - h) // 2)
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))


# ------------------------------------------------------------------ 登录窗
class LoginDialog(Dialog):
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
        self.hashkey = ""          # 旧的那张已经失效，别拿它去提交
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
                    self.lbl_msg.config(
                        text="验证码没取到，点「换一张」重试", fg=C_ERR)
                else:
                    self._finish(kind == "login_ok", payload)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(80, self._poll)


class DiagnosticsDialog(Dialog):
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
                 else "exe 所在目录不可写，数据落在 %APPDATA%\\YuanshenToolbox")
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
        name = "YuanshenToolbox-diagnostics-%s.zip" % datetime.now().strftime("%Y%m%d-%H%M%S")
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


class UploadConfirmDialog(Dialog):
    """上传前的二次确认：把「这一批到底要传什么」摊开给人看。

    为什么不直接用 messagebox：
      · 选中项散落在上万行的表里，用户在表里根本看不到自己选了什么，点了
        「上传选中」心里没底；原生弹窗只能塞一段纯文本，列不出清单；
      · 原生弹窗是系统白底灰按钮，跟这套粉紫界面完全不搭。
    所以这里用同一套配色 + 药丸按钮 + 同款表格，把级别/风格/成绩/文件列清楚。

    返回：`UploadConfirmDialog.ask(...)` 为 True 表示用户点了「开始上传」。
    """

    LIMIT = 400          # 超过这个数只列前 400 行：建几万个 Treeview 行会卡顿

    COLS = [
        ("level_cn", "级别", 56, "center", "str"),
        ("style_cn", "风格", 50, "center", "str"),
        ("src_cn", "来源", 52, "center", "str"),
        ("time", "time", 70, "e", "f3"),
        ("bv", "BV", 44, "e", "int"),
        ("size_cn", "大小", 62, "e", "str"),
        ("file", "录像文件", 300, "w", "str"),
    ]

    def __init__(self, master, rows, title="确认上传", action="上传", hint=""):
        super().__init__(master)
        self.result = False
        self.configure(bg=C_BG)
        self.title(title)
        self.transient(master)
        self.resizable(True, True)

        rows = list(rows or [])
        rows.sort(key=lambda r: (core.LEVELS.index(r["level"])
                                 if r.get("level") in core.LEVELS else 9,
                                 r.get("bv") or 0, r.get("time") or 0))

        wrap = tk.Frame(self, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=sc(20), pady=sc(16))

        # --- 抬头：一句话说清这批是什么 ---
        tk.Label(wrap, text="%s %d 个录像" % (action, len(rows)), bg=C_BG,
                 fg=C_TEXT, font=FONT_SECTION).pack(anchor="w")
        sub = hint or "下面是这一批的全部录像，确认无误再开始。"
        tk.Label(wrap, text=sub, bg=C_BG, fg=C_DIM, font=FONT_SMALL).pack(
            anchor="w", pady=(sc(2), sc(10)))

        # --- 摘要卡：级别构成 / 来源 / 合计大小 ---
        card = tk.Frame(wrap, bg=C_PANEL, highlightthickness=1,
                        highlightbackground=C_LINE)
        card.pack(fill="x", pady=(0, sc(10)))
        for text, color in self._summary_bits(rows):
            tk.Label(card, text=text, bg=C_PANEL, fg=color,
                     font=FONT_SMALL).pack(side="left", padx=(sc(12), 0),
                                           pady=sc(9))

        over = len(rows) - self.LIMIT
        if over > 0:
            tk.Label(wrap, text="共 %d 个，下面只列前 %d 个（其余 %d 个同样会上传）"
                                % (len(rows), self.LIMIT, over),
                     bg=C_BG, fg=C_WARN, font=FONT_SMALL).pack(
                anchor="w", pady=(0, sc(4)))

        # 文件名列按实际内容量宽：截断到「…Corday(Tongcheng).e」这种程度
        # 就失去了「看到自己传了什么」的意义，不如把窗口撑开。
        cols = self._fit_file_col(rows[:self.LIMIT])

        holder = tk.Frame(wrap, bg=C_LINE)
        holder.pack(fill="both", expand=True)
        self.tbl = Table(holder, cols, height=10, selectmode="browse",
                         iid_key="path")
        self.tbl.pack(fill="both", expand=True, padx=1, pady=1)
        self.tbl.set_rows(rows[:self.LIMIT])

        # --- 底部按钮 ---
        foot = tk.Frame(wrap, bg=C_BG)
        foot.pack(fill="x", pady=(sc(12), 0))
        Pill(foot, "取消", command=self._cancel, accent=C_DIM, padx=18,
             height=32).pack(side="right", padx=(sc(8), 0))
        self.btn_ok = Pill(foot, "开始上传 (%d)" % len(rows), command=self._ok,
                           accent=C_ACCENT, padx=22, height=32)
        self.btn_ok.pack(side="right")
        tk.Label(foot, text="上传后按网站审核状态回填：待审核 / 已通过 / 冻结",
                 bg=C_BG, fg=C_DIM, font=FONT_SMALL).pack(side="left")

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())

        self.update_idletasks()
        w = max(sc(660), self._table_width(cols))
        w = min(w, self.winfo_screenwidth() - sc(60))
        h = max(sc(420), min(sc(560), self.winfo_reqheight()))
        self.geometry("%dx%d" % (w, h))
        try:
            px, py = master.winfo_rootx(), master.winfo_rooty()
            pw, ph = master.winfo_width(), master.winfo_height()
            self.geometry("+%d+%d" % (px + max(0, (pw - w) // 2),
                                      py + max(0, (ph - h) // 3)))
        except tk.TclError:
            pass
        self.grab_set()
        self.btn_ok.focus_set()

    # --- 摘要文字 ---
    def _fit_file_col(self, rows):
        """按最长文件名给「录像文件」列量宽（上限 560，别把窗口顶出屏幕）。

        注意 Table 内部会对列宽再做一次 sc()，所以这里换算回逻辑单位。
        """
        probe = tkfont.Font(family=FAM_UI, size=FONT_UI[1])
        need = 0
        for r in rows:
            need = max(need, probe.measure(str(r.get("file") or "")))
        logical = need / (SCALE or 1.0) + 18
        file_w = max(220, min(logical, 560))
        return [c if c[0] != "file" else ("file", "录像文件", int(file_w), "w", "str")
                for c in self.COLS]

    @staticmethod
    def _table_width(cols):
        """列宽合计 + 滚动条/内边距 + 外层 wrap 的左右留白（逻辑单位，外面统一 sc()）。

        少算 wrap 那 40，最后的「录像文件」列会被挤窄、文件名又被截掉。
        """
        return sc(sum(c[2] for c in cols) + 48 + 40)

    def _summary_bits(self, rows):
        bits = []
        for lv in core.LEVELS:
            n = sum(1 for r in rows if r.get("level") == lv)
            if n:
                bits.append(("%s %d" % (core.LEVEL_CN[lv], n),
                             LEVEL_ACCENT[lv][0]))
        srcs = {}
        for r in rows:
            srcs[r.get("src_cn") or "—"] = srcs.get(r.get("src_cn") or "—", 0) + 1
        if len(srcs) > 1 or "阿比特" in srcs:
            bits.append((" · ".join("%s %d" % (k, v) for k, v in srcs.items()),
                         C_ACCENT2))
        total = sum(int(r.get("size") or 0) for r in rows)
        if total >= 1048576:
            size_txt = "合计 %.1f MB" % (total / 1048576.0)
        else:
            size_txt = "合计 %.0f KB" % (total / 1024.0)
        bits.append((size_txt, C_TEXT))
        return bits

    def _ok(self):
        self.result = True
        self.destroy()

    def _cancel(self):
        self.result = False
        self.destroy()

    @classmethod
    def ask(cls, master, rows, title="确认上传", action="上传", hint=""):
        dlg = cls(master, rows, title=title, action=action, hint=hint)
        dlg.wait_window()
        return bool(dlg.result)


class IdentsDialog(Dialog):
    """账号在网站上的全部标识。

    为什么不在状态行里平铺：真实账号常有几十个标识（站点上每换一次玩家名就多一个），
    铺出来会把整条状态行挤出屏幕，等于一个都看不清。状态行只留「标识 ×N」，
    明细放这里 —— 带序号、可滚、能一次复制走。
    """

    def __init__(self, master, idents, username=""):
        super().__init__(master)
        self.idents = [str(x) for x in (idents or [])]
        self.configure(bg=C_BG)
        self.title("网站标识")
        self.transient(master)
        self.resizable(True, True)

        wrap = tk.Frame(self, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=sc(20), pady=sc(16))

        tk.Label(wrap, text="%s 的网站标识 · %d 个"
                            % (username or "该账号", len(self.idents)),
                 bg=C_BG, fg=C_TEXT, font=FONT_SECTION).pack(anchor="w")
        tk.Label(wrap, text="名字对得上其中之一，这条录像就算已经传过。",
                 bg=C_BG, fg=C_DIM, font=FONT_SMALL).pack(anchor="w",
                                                          pady=(sc(2), sc(12)))

        holder = tk.Frame(wrap, bg=C_PANEL, highlightthickness=1,
                          highlightbackground=C_LINE)
        holder.pack(fill="both", expand=True)
        self.txt = tk.Text(holder, bg=C_PANEL, fg=C_TEXT, relief="flat",
                           font=FONT_MONO, wrap="char", height=12,
                           insertbackground=C_ACCENT, selectbackground=C_SEL)
        sb = ttk.Scrollbar(holder, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True,
                      padx=sc(10), pady=sc(8))
        self.txt.insert("1.0", "\n".join("%3d.  %s" % (i + 1, s)
                                         for i, s in enumerate(self.idents)))
        self.txt.config(state="disabled")

        foot = tk.Frame(wrap, bg=C_BG)
        foot.pack(fill="x", pady=(sc(12), 0))
        Pill(foot, "关闭", command=self.destroy, accent=C_DIM, padx=18,
             height=30).pack(side="right")
        Pill(foot, "复制全部", command=self._copy, accent=C_ACCENT2, padx=16,
             height=30).pack(side="right", padx=(0, sc(8)))
        self.lbl_msg = tk.Label(foot, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL)
        self.lbl_msg.pack(side="left")

        self.bind("<Escape>", lambda _e: self.destroy())
        self.update_idletasks()
        # 宽度按最长那条标识量（一行放得下就不用滚），高度按条数但不超 12 行
        probe = tkfont.Font(family=FAM_MONO, size=FONT_MONO[1])
        need = max([probe.measure(s) for s in self.idents] or [0]) + sc(96)
        w = min(max(sc(380), int(need)), self.winfo_screenwidth() - sc(80))
        h = max(sc(260), min(sc(520), self.winfo_reqheight()))
        self.geometry("%dx%d" % (w, h))
        try:
            px, py = master.winfo_rootx(), master.winfo_rooty()
            pw, ph = master.winfo_width(), master.winfo_height()
            self.geometry("+%d+%d" % (px + max(0, (pw - w) // 2),
                                      py + max(0, (ph - h) // 3)))
        except tk.TclError:
            pass
        self.grab_set()

    def _copy(self):
        try:
            self.clipboard_clear()
            self.clipboard_append("\n".join(self.idents))
            self.lbl_msg.config(text="已复制 %d 个标识" % len(self.idents), fg=C_OK)
        except tk.TclError:
            pass


class GameDetail(Dialog):
    """一局摊开看：成绩、这局在同 3BV / 同级别里的位置、什么时候打的。

    只读扫描时已经算好的字段 —— 不重演录像、不启动元扫雷，右键就出来。
    """

    def __init__(self, app, game):
        super().__init__(app)
        self.app = app
        self.game = game
        self.title("录像详情")
        self.configure(bg=C_BG)
        self.transient(app)
        self.resizable(False, False)
        self.bind("<Escape>", lambda _e: self.destroy())
        self._build()
        self._place()

    # ---------- 布局 ----------
    def _build(self):
        g = self.game
        wrap = tk.Frame(self, bg=C_BG)
        wrap.pack(fill="both", expand=True, padx=sc(20), pady=(sc(16), sc(14)))

        tk.Label(wrap, text=self._when(), bg=C_BG, fg=C_DIM,
                 font=FONT_SMALL, anchor="w").pack(fill="x")
        head = "%s · %s · 3BV %s" % (
            core.LEVEL_CN.get(g.get("level"), ""),
            core.STYLE_CN.get(g.get("style"), ""),
            g.get("bv") or 0)
        tk.Label(wrap, text=head, bg=C_BG, fg=C_TEXT, font=FONT_SECTION,
                 anchor="w").pack(fill="x", pady=(0, sc(10)))

        # 标签列、值列都定宽，两组内容才从同一条竖线开始；名次紧跟数值，
        # 不让多余宽度把名次甩到窗口另一头（见 _grid）。
        self._wrap = wrap
        self._metrics(self._grid())

        # 一条细线代替分组标题：上面是成绩，下面是这一局的来源。
        tk.Frame(wrap, bg=C_LINE, height=1).pack(fill="x", pady=(sc(10), sc(8)))

        body2 = self._grid()
        src = {"ms": "元扫雷", "arb": "阿比特"}.get(g.get("src"), "")
        if src:
            self._pair(body2, "来源", src)
        if g.get("ident"):
            self._pair(body2, "玩家", g["ident"])

        # 文件名一个人占一行：挤在值列里会在单词中间折行，读起来很别扭
        tk.Label(wrap, text=g.get("file") or "", bg=C_BG, fg=C_TEXT,
                 font=FONT_MONO, anchor="w", justify="left",
                 wraplength=sc(430)).pack(fill="x", pady=(sc(6), 0))

        foot = tk.Frame(wrap, bg=C_BG)
        foot.pack(fill="x", pady=(sc(14), 0))
        Pill(foot, "复制路径", command=self._copy_path, accent=C_ACCENT2,
             padx=16, height=30).pack(side="left")
        Pill(foot, "关闭", command=self.destroy, accent=C_DIM,
             padx=18, height=30).pack(side="right")

    def _grid(self):
        """标签列 + 值列都定宽；宽度只按内容取，不吸收窗口多余宽度。"""
        f = tk.Frame(self._wrap, bg=C_BG)
        f.pack(anchor="w")
        f.columnconfigure(0, minsize=sc(76))
        f.columnconfigure(1, minsize=sc(66))
        return f

    def _pair(self, body, k, v):
        row = len(body.grid_slaves()) // 2
        tk.Label(body, text=k, bg=C_BG, fg=C_DIM, font=FONT_SMALL,
                 anchor="w").grid(row=row, column=0, sticky="w", pady=sc(2))
        tk.Label(body, text=v, bg=C_BG, fg=C_TEXT, font=FONT_MONO,
                 anchor="w", justify="left").grid(row=row, column=1, sticky="w",
                                                  pady=sc(2))

    def _metrics(self, body):
        """三指标 + 同 BV + 当天，各自后面跟「第几 / 共几局」。"""
        g = self.game
        ranks = self._ranks()
        rows = (
            ("time", fmt_time(g.get("time") or 0), ranks.get("time")),
            ("3BV/s", fmt_bvs(g.get("bvs") or 0), ranks.get("bvs")),
            ("stnb", fmt_stnb(g.get("stnb") or 0), ranks.get("stnb")),
            ("BV", "", ranks.get("bv")),
            (g.get("date") or "", "", ranks.get("day")),
        )
        for row, (label, val, rk) in enumerate(rows):
            tk.Label(body, text=label, bg=C_BG, fg=C_DIM, font=FONT_SMALL,
                     anchor="w").grid(row=row, column=0, sticky="w",
                                      pady=sc(3))
            tk.Label(body, text=val, bg=C_BG, fg=C_TEXT, font=FONT_MONO,
                     anchor="w").grid(row=row, column=1, sticky="w",
                                      pady=sc(3))
            # 名次右端对齐（"%5d"）；没有名次时给一个同样宽度的短横，
            # 让它落在数字那一列上，而不是贴在最左边
            tk.Label(body, text=("%5s" % rk) if rk else "%5s" % "—",
                     bg=C_BG, fg=C_DIM,
                     font=FONT_MONO, anchor="w").grid(
                         row=row, column=2, sticky="w", pady=sc(3))

    # ---------- 内容 ----------
    def _when(self):
        t0 = self.game.get("t0")
        return ("%s %s" % (core.day_text(t0), core.clock_text(t0))).strip()

    def _ranks(self):
        """三指标的本级名次，加上同 BV、当天的名次。

        三项指标的分母是「本级过了门槛的局」—— 门槛跟排行榜一致，没过门槛的局
        榜里本来就不出现；同值算同一名次。同 BV 和当天只看本级，不分风格：
        同一盘面、同一天，风格不改变题目本身。扫描时已经算好 time / bvs / stnb，
        这里不重演录像。
        """
        g = self.game
        lv = g.get("level")
        floor = core.BV_MIN_DEFAULT.get(lv, 0)
        bv = g.get("bv") or 0
        day = g.get("date") or ""
        t = g.get("time") or 0
        b = g.get("bvs") or 0
        s = g.get("stnb") or 0
        rt = rb = rs = cnt = 0
        r_bv = n_bv = 0
        r_day = n_day = 0
        for x in self.app.games:
            if x.get("level") != lv:
                continue
            xt = x.get("time") or 0
            if (x.get("bv") or 0) == bv:
                n_bv += 1
                if xt < t:
                    r_bv += 1
            if day and x.get("date") == day:
                n_day += 1
                if xt < t:
                    r_day += 1
            if (x.get("bv") or 0) < floor:
                continue
            cnt += 1
            if xt < t:
                rt += 1
            if (x.get("bvs") or 0) > b:
                rb += 1
            if (x.get("stnb") or 0) > s:
                rs += 1
        out = {"bv": "%5d / %d" % (r_bv + 1, n_bv),
               "day": "%5d / %d" % (r_day + 1, n_day)}
        if bv >= floor and cnt:
            out.update({"time": "%5d / %d" % (rt + 1, cnt),
                        "bvs": "%5d / %d" % (rb + 1, cnt),
                        "stnb": "%5d / %d" % (rs + 1, cnt)})
        return out

    def _copy_path(self):
        p = self.game.get("path") or ""
        if not p:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(p)
        except tk.TclError:
            return
        if hasattr(self.app, "set_status"):
            self.app.set_status("已复制路径：" + p, "dim", hold=5000)

    # ---------- 位置 ----------
    def _place(self):
        """贴着鼠标出现；右边放不下就翻到左边，底下放不下就往上抬。"""
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        px, py = self.winfo_pointerxy()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x, y = px + sc(14), py + sc(10)
        if x + w > sw - sc(16):
            x = max(sc(8), px - w - sc(14))
        if y + h > sh - sc(60):
            y = max(sc(8), sh - h - sc(60))
        self.geometry("+%d+%d" % (x, y))


class PathsDialog(Dialog):
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

        self.var_auto = tk.BooleanVar(value=bool(getattr(app, "_watch_enabled", True)))
        tk.Checkbutton(
            wrap, text="有新录像就自动刷新",
            variable=self.var_auto, command=self._toggle_auto, bg=C_BG, fg=C_TEXT,
            activebackground=C_BG, activeforeground=C_TEXT, selectcolor=C_PANEL,
            font=FONT_SMALL, bd=0, highlightthickness=0).pack(anchor="w", pady=(sc(8), 0))

        self.lbl_tip = tk.Label(
            wrap, text="启动时只自动找元扫雷的录像目录；阿比特的录像目录要手动添加。\n"
                       "手动添加的目录，里面的录像会全部计入成绩统计并出现在上传列表，\n"
                       "混有下载来观摩的录像要先移出去。\n"
                       "阿比特的 .avf 按内容解析，不看文件名；打比赛产生的录像标识带比赛编号，\n"
                       "先去网站把完整标识登记上，否则上传会被记成「标识不符」。",
            bg=C_BG, fg=C_DIM, font=FONT_SMALL, anchor="w", justify="left",
            wraplength=sc(690))
        self.lbl_tip.pack(fill="x", pady=(sc(8), 0))

        self._refresh()

        # 尺寸按内容算：写死高度装不下复选框 + 多行说明，底部那几行会被切掉
        self.update_idletasks()
        w = max(sc(760), self.winfo_reqwidth())
        w = min(w, self.winfo_screenwidth() - sc(80))
        h = min(self.winfo_reqheight(), self.winfo_screenheight() - sc(120))
        self.geometry("%dx%d" % (w, h))
        try:
            px, py = app.winfo_rootx(), app.winfo_rooty()
            pw, ph = app.winfo_width(), app.winfo_height()
            self.geometry("+%d+%d" % (px + max(0, (pw - w) // 2),
                                      py + max(0, (ph - h) // 3)))
        except tk.TclError:
            pass

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

    def _toggle_auto(self):
        self.app.set_auto_refresh(bool(self.var_auto.get()))

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
            self.lbl_msg.config(text="没有添加。里面有别人的录像的话，移出去后再重新添加",
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
                "录像目录", "这是最后一个录像目录，移掉之后就没有录像可分析了。\n继续吗？",
                parent=self):
            return
        # 只从配置里去掉，绝不动磁盘上的文件
        self.roots = rest
        self._apply()


# ------------------------------------------------------------------ 主窗口
class App(tk.Tk):
    # 上传结束后「成功 N 条」这行挂多久（毫秒）再退回常态；有失败时不退
    UP_STATUS_HOLD_MS = 6000

    def __init__(self, root_dir=None):
        ensure_bundled_fonts()           # 必须早于 tk.Tk()：Tk 只在创建时枚举字体
        super().__init__()
        enable_dpi()                     # 幂等：manifest 已声明时这里是空操作
        global SCALE
        SCALE = detect_scale(self)
        self._init_fonts()
        self.title(APP_DISPLAY)
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
        self._watch_enabled = bool(load_config().get("auto_refresh", True))
        note = paths.legacy_note()
        if note:
            diag.log.info(note)
        diag.log.info("启动 %s | 数据目录 %s | 便携 %s | 录像目录 %s",
                      APP_DISPLAY, paths.data_dir(), paths.is_portable(),
                      " ; ".join("%s%s" % ("" if r["on"] else "(停用)", r["path"])
                                 for r in self.roots) or "（未确定）")
        self.games = []
        self.ng_games = []             # 强无猜：只用于上传，不进任何统计
        self.info = {}
        self.pb_local_rows = []
        self.pb_rows = []
        self.site_pb = []
        self.site_pb_uid = None
        self.site_pb_err = ""
        self.pb_counts = {}            # 每个小榜的人数，网站名次的分母
        self.site_top = {}             # 站点 18 个前端 Top5 小榜的原始记录
        self.site_top_details = {}     # 上述录像的 staticdetailbatch 明细
        self.site_top_uid = None
        self.site_top_err = ""
        self.site_top_detail_err = ""
        self.site_ng = {}              # 强无猜两榜的个人纪录（进榜才传用）
        self.site_ng_uid = None
        self.site_ng_err = ""
        # LSF：锚点是全站前二十名的水平，只能联网取一次；断网时用上次存下来的。
        self.lsf_anchors, self.lsf_anchors_at = load_lsf_anchors()
        self.var_lsf_fixed = tk.BooleanVar(
            value=bool(load_config().get("lsf_fixed")))
        self._lsf_cache = None         # (数据指纹, 报告)
        self._gain_cache = None        # (数据指纹, {path: 涨分})
        self._site_seq = 0             # 网站请求代次：退出/换号后旧结果直接丢弃
        self.summary = {}
        self.counts = {}
        self.diffs = []
        self.ini_ok = False
        self._scanning = False
        self._scan_auto = False        # 本次扫描是不是自动刷新触发的（不显示进度条）
        self._scan_sig = None          # 上次扫描的 (文件数, 局数, 最新 mtime)，判断「真的变了吗」
        self._level = "b"
        self._daily_level = "e"        # 每日页当前级别：打开就看到高级（练习主场）
        self._daily_date = None        # 每日页选中的那一天
        self.daily_rows = []           # 每日页当前的行（测试与导出用）
        self._pb_style = "all"         # PB 榜：all=全PB / nf=NF PB
        self._pb_metric = "time"       # 格子里显示的数字：time / bvs / stnb / rank
        self._pb_index = {}            # 当前网格里 (级别, BV) -> 行
        self._pb_picked = None
        self._top_style = "all"        # 前端成绩：all=全PB / nf=NF PB
        self._rank_metric = "time"     # 排行榜看哪个指标：time / bvs / stnb
        self._rank_style = "all"       # 排行榜风格：all / nf
        self._rank_n = 10              # 排行榜每个级别显示几条，0 = 全部录像
        self.rank_grid = None          # 排行榜的三列表格容器（量高度用）
        self._rank_fit_job = None      # 「等页面排好版再量一次」的定时器
        self._rank_fit_tries = 0       # 已经重试过几拍
        self._bvmin_level = None
        self._bvmin_eff = 0            # BV 下限当前生效值（输入框里可能是半截）
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
        # 账号已确认、但「我的录像 / 标识」还没拉回来。为 True 时界面不能把
        # oms_videos 当权威数据用（那会儿它是空的）。
        self.oms_loaded = False
        # 最近一次「完整账号事件」属于哪一代请求。全量上传要拿它确认
        # 当前账号的录像列表已经到齐 —— 退出 / 换号 / 刷新期间，旧一代
        # 留下的 True 不算数。
        self._auto_acct_seq = -1
        self._last_failed = set()   # 上一批失败的路径（表格会被网站数据重建，得单独记）
        self._arb_rows = 0          # 本地发现多少个阿比特录像（不受「含阿比特」影响）
        self.up_rows = []
        self.up_view = []           # 表格里实际显示的子集（受范围 Pill 过滤）
        self.up_sel = set()
        self._up_scopes = set()
        self._scope_want = None     # 范围 -> 路径集合的缓存
        self._scope_skipped_pb = set()   # 因网站 PB 更快而排除的本地 PB
        self._scope_skipped_top = set()  # 因网站 Top5 已更快而排除的本地局
        self._view_note = ""        # 范围过滤后的说明（排除条数 / 为空原因）
        self._up_running = False
        self._up_stop = False
        self._up_total = 0
        self._up_done = 0
        self._up_ok = 0
        self._up_dup = 0
        self._up_fail = 0
        self._up_dirty = True      # 上传页的行要不要重建（不在该页时先不算，省 0.5 s）

        # 自动上传：玩的时候盯新录像。判定逻辑全在 autoup，这里只负责门控与落盘。
        _cfg = load_config()
        self.auto_flags = autoup.flags_of(config_auto_scopes(_cfg))
        # 「全部新增完成局」是独立开关：开着的时候覆盖上面三项，但它自己
        # 不写进 auto_scopes —— 关掉之后三项的勾选状态要原样回来。
        self._auto_all = _cfg.get("auto_all") is True
        # 记录表的合并刷新（见 _auto_record）：连续写一批时只重建一次
        self._auto_flush_job = None
        # 「打开工具箱就自动开始上传」是页面上一个显式选项。老配置里只有
        # auto_upload（上次退出时是开是关），按它迁一次就够。
        self._auto_start = bool(_cfg.get("auto_start",
                                         _cfg.get("auto_upload", False)))
        self._auto_on = self._auto_start
        self._auto_watermark = int(_cfg.get("auto_watermark") or 0)
        # 排行榜看多少条记着上次的：调成 50 之后重开还是 50，不用每次重新填。
        # 0 表示「全部」，存的就是这个口径。
        try:
            self._rank_n = max(0, int(_cfg.get("rank_n", 10)))
        except (TypeError, ValueError):
            self._rank_n = 10
        self._auto_tracker = autoup.Tracker(self._auto_watermark)
        # 强无猜上传：小众选项，收在设置里。它复用自动上传的总开关与队列，
        # 只是候选池换成 ng_games，不碰标准模式的任何统计。
        # ng_scope: "" 关 / "top" 进榜才传 / "all" 全部上传（老配置 ng_all 迁过来）。
        self._ng_scope = str(_cfg.get("ng_scope")
                             or ("all" if _cfg.get("ng_all") else ""))
        self._ng_watermark = int(_cfg.get("ng_watermark") or 0)
        self._ng_tracker = autoup.Tracker(self._ng_watermark)
        self.ng_scope_var = tk.StringVar(value=self._ng_scope)
        self._auto_stop = ""       # 需要用户动手才能恢复（额度 / 登录 / 封禁）
        self._auto_backoff = 0.0   # 网络类临时失败：到这个时间点再试
        self._auto_session = 0     # 本次会话自动传成功几条
        self._auto_why = {}        # path -> [理由]，上传回调用
        self._auto_brief = {}      # path -> [短写]，同上
        self._auto_retry = {}      # path -> 已重试次数（网络类临时失败）
        self._up_silent = False    # 这一批是不是自动上传发的（决定要不要弹窗）

        # 启动元扫雷：横幅按钮。位置来源见 mslaunch 的说明（配置 → 运行中的进程 →
        # 默认安装位置 → 用户指定一次）。这里只存状态，找路径的事全交给它。
        # 设置里可以登记多份程序（不同版本、非标准版本都行），ms_exe 是"上次选中"
        # 的那一份；自动启动只认它，不会把列表里的程序都拉起来。
        self.ms_exes = ms_exes_from_cfg(_cfg)
        self.ms_exe = str(_cfg.get("ms_exe") or "")
        if not self.ms_exe and self.ms_exes:
            self.ms_exe = self.ms_exes[0]
            save_config(ms_exe=self.ms_exe)
        self._ms_auto_launch = bool(_cfg.get("ms_auto_launch", False))
        if self._ms_auto_launch and not self.ms_exe:
            # 没有可选程序时这个开关不能是开的；把旧配置里不可能成立的状态修掉
            self._ms_auto_launch = False
            save_config(ms_auto_launch=False)
        self._ms_running = False   # 元扫雷此刻在不在跑
        self._ms_path = ""         # 从运行中的进程反查到的路径
        self._ms_launch_at = 0.0   # 上次点启动的时刻，用于冷启动宽限
        self._ms_focus_until = 0.0   # 让元扫雷露脸的截止时刻，过了就不管了
        self._ms_status = ""       # 状态栏那句「元扫雷已启动」，关掉时要收回
        # 播放录像时别把元扫雷拉起两个（原因见 open_replay 上方那段说明）
        self._play_path = ""       # 上次交给它的录像
        self._play_at = 0.0        # 上次交给它的时刻
        self._play_wait = 0.0      # 从什么时候开始等它启动完（0 = 没在等）
        self._play_pending = ""    # 等它起来之后要补交的录像
        self._play_job = None      # 补交的定时器

        # 自动刷新：盯「启用目录 + 它们的所有子目录」的 mtime，变了就重扫
        self._watch_dirs = {}      # 目录 -> 上次看到的 mtime_ns
        self._watch_sig = (0, 0)   # (录像数, 最新 mtime)：目录时间戳看不出来的改动靠它兜底
        self._watch_job = None
        self._watch_ticks = 0
        self._watch_rescan = False  # 强制下一拍重扫（读到过半截文件时用）

        set_window_icon(self)
        self.after(260, lambda: set_window_icon(self))   # 窗口 map 后 Tk 会再设一次，隔一拍覆盖回来
        self._init_style()
        self._build_ui()
        self.after(120, self._apply_dwm)
        self._q = queue.Queue()
        self.after(60, self._poll)
        self.after(80, self._boot)
        self.after(700, self._restore_session)
        self.after(900, self._lsf_start)
        self.after(1200, self._poll_ms)     # 横幅那个按钮的「在不在跑」状态
        # 手动刷新：快捷键；自动刷新从第一轮扫描结束后开始（见 _on_scanned → _watch_rebuild）
        self.bind("<F5>", lambda _e: self.start_scan())
        self.bind("<FocusIn>", self._on_focus)
        # 点到输入框以外就把它放开（见 _drop_entry_focus）
        self.bind("<Button-1>", self._drop_entry_focus, add="+")

    def _drop_entry_focus(self, ev=None):
        """点到输入框以外的地方，把键盘焦点收回来。

        Tk 换焦点的条件是「点到了另一个能聚焦的控件」，按钮、标签、表格都不算 ——
        从输入框点到这些地方，插入光标会一直留在框里闪，用户以为卡死了（只有
        按 Tab 才走得掉）。这里补一手：目标是文本控件就不管，否则把焦点收回主窗口。
        """
        w = ev.widget if ev is not None else None
        while w is not None and w is not self:
            if isinstance(w, (tk.Entry, ttk.Entry, tk.Text)):
                return                      # 点到另一个输入框：正常换焦点
            w = getattr(w, "master", None)
        box = getattr(self, "bvmin_box", None)
        if box is not None:
            # 框里可能还留着半截数字（"007"、"abc"、空）没提交，点走时就地归一。
            # 不看焦点在哪里：点到表格时 Tk 的 class 绑定会抢先换焦点，那时再查
            # focus_get() 已经查不到这个框了。
            if (box.get() or "").strip() != str(self._bvmin_eff):
                self._settle_bvmin(quiet=True)
            if isinstance(self.focus_get(), (tk.Entry, ttk.Entry, tk.Text)):
                self.focus_set()

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
        # 读网站记录那条：白底上要看得见槽，否则只剩一小块粉色像坏掉了
        st.configure("Oms.Site.Horizontal.TProgressbar", troughcolor="#f1e0f0",
                     background=C_ACCENT, bordercolor="#f1e0f0",
                     lightcolor=C_ACCENT, darkcolor=C_ACCENT, thickness=sc(8))

    # ---------- 界面 ----------
    def _build_ui(self):
        self.banner = tk.Canvas(self, height=sc(96), bg=C_ACCENT,
                                highlightthickness=0, bd=0)
        self.banner.pack(fill="x")
        self.banner.bind("<Configure>", self._draw_banner)
        for tag, cmd in (("b_settings", self.open_settings),
                         ("b_paths", self.open_paths),
                         ("b_play", self.launch_game)):
            self.banner.tag_bind(tag, "<Button-1>", lambda _e, c=cmd: c())

        # 主导航
        nav = tk.Frame(self, bg=C_BG)
        nav.pack(fill="x", padx=sc(20), pady=(sc(12), sc(2)))
        self.tab_pills = {}
        for key, label in (("overview", "概览"), ("daily", "每日"),
                           ("pb", "PB 表"),
                           ("top", "lsf"), ("rank", "排行榜"),
                           ("upload", "上传"), ("auto", "自动上传")):
            p = Pill(nav, label, command=lambda k=key: self.show_tab(k),
                     accent=C_ACCENT, padx=22, height=32)
            p.pack(side="left", padx=(0, sc(8)))
            self.tab_pills[key] = p
        self.lbl_meta = tk.Label(nav, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL)
        self.lbl_meta.pack(side="right")

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
            "daily": tk.Frame(body, bg=C_BG),
            "pb": tk.Frame(body, bg=C_BG),
            "top": tk.Frame(body, bg=C_BG),
            "rank": tk.Frame(body, bg=C_BG),
            "upload": tk.Frame(body, bg=C_BG),
            "auto": tk.Frame(body, bg=C_BG),
        }
        self._page_host = body
        # 关键：阻止页面内容反向撑大窗口。
        # 表格会按可用高度自适应行数，行数变大 → 请求高度变大 → 窗口被顶高 →
        # 表格又算出更多行……形成正反馈，窗口一路涨到超出屏幕。
        body.pack_propagate(False)
        self._build_overview()
        self._build_daily()
        self._build_pb()
        self._build_top()
        self._build_rank()
        self._build_upload()
        self._build_auto()
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
        if key == "upload":
            self._ensure_up_rows()      # 懒重建：切到这一页才把 2 万行算出来
        elif key == "auto":
            self._refresh_auto_view()
        elif key == "rank":
            self.render_rank()

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
                       text=f"v{APP_VER}   {APP_SUB}", anchor="w",
                       fill="#fff2f8", font=(FAM_UI, 9))

        # 星屑
        for x, y, s, a in ((w * 0.30, h * 0.28, 9, "#ffffff"),
                           (w * 0.36, h * 0.66, 6, "#ffe6f2"),
                           (w * 0.24, h * 0.72, 5, "#ffffff")):
            sparkle(cv, x, y, sc(s), a)

        # 右侧按钮（白底粉字）。画法是「从右往左」，所以列表最后一项在最左边：
        # 启动元扫雷放最后 —— 它在阅读顺序上最先被看到，位置也固定不随其他按钮增减。
        # 路径单独成按钮（启动元扫雷右边）；其余不常用操作收进「设置」弹窗。
        font_m = tkfont.Font(family=FAM_UI, size=9, weight="bold")
        # 三态：没在跑 / 刚点过启动、等它起来 / 确认在跑。宽度按最长那句
        # 算死，所以整排按钮不会因为状态变化而左右跳。
        if self._ms_running:
            play = "元扫雷正在运行"
        elif self._ms_pending():
            play = "正在启动元扫雷"
        else:
            play = "启动元扫雷"
        n = len(self.roots)
        buttons = [("设置", "b_settings"),
                   ("路径 (%d)" % n if n else "路径", "b_paths")]
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

        # 启动元扫雷：宽度按最长文案算死，切换状态时整排按钮不会左右跳
        tri = sc(4.6)
        inner = tri * 1.5 + sc(7) + font_m.measure("启动元扫雷")
        bw = max(inner, font_m.measure("元扫雷正在运行")) + sc(32)
        x1 = right - bw
        if self._ms_running or self._ms_pending():
            round_rect(cv, x1, by, right, by + bh, bh / 2.0,
                       fill="#ffe9f1", width=0, tags=("b_play",))
            cv.create_text((x1 + right) / 2.0, by + bh / 2.0, text=play,
                           fill="#cf94ab", font=(FAM_UI, 9, "bold"),
                           tags=("b_play",))
        else:
            round_rect(cv, x1, by, right, by + bh, bh / 2.0,
                       fill="#ffffff", width=0, tags=("b_play",))
            # 三角形自己画，不依赖字体里有没有 ▶；三角 + 文字整体居中
            tcy = by + bh / 2.0
            gx = (x1 + right) / 2.0 - inner / 2.0
            cv.create_polygon(gx, tcy - tri, gx, tcy + tri,
                              gx + tri * 1.5, tcy, fill=C_ACCENT,
                              width=0, tags=("b_play",))
            cv.create_text(gx + tri * 1.5 + sc(7), tcy, text="启动元扫雷",
                           anchor="w", fill=C_ACCENT, font=(FAM_UI, 9, "bold"),
                           tags=("b_play",))

        # 底部柔和收口
        cv.create_line(0, h - 1, w, h - 1, fill="#ffffff", width=1, stipple="gray50")

    # ---------- 启动元扫雷 ----------
    MS_POLL_MS = 2500          # 查一次进程的间隔（一次约 6 ms）
    MS_FOCUS_WINDOW = 10.0     # 启动之后这几秒内，补着把它的窗口提到前面
    MS_STATUS_HOLD_MS = 6000   # 「元扫雷已启动」这行挂多久（到点自己退场）

    def _ms_name(self):
        """当前选中程序的名字，用来认进程和窗口。

        非标准版本、阿比特这类文件名不带 metasweeper 也按它自己的名字找；
        没选过程序时退回默认的 metasweeper（老配置的兜底）。
        """
        return mslaunch.exe_stem(self.ms_exe)

    def _ms_is_metasweeper(self):
        """选中的是不是元扫雷本体 —— 只有它需要防 5555/5556 抢端口。"""
        return self._ms_name().lower().startswith(mslaunch._NAME_PREFIX)

    def ms_remember_exe(self, path):
        """把路径登记进设置列表并选中。已登记过的只切换选中项。"""
        path = os.path.abspath(path)
        if all(os.path.normcase(path) != os.path.normcase(p)
               for p in self.ms_exes):
            self.ms_exes.append(path)
        changed = os.path.normcase(path) != os.path.normcase(self.ms_exe or "")
        old_name = self._ms_name()
        self.ms_exe = path
        save_config(ms_exe=self.ms_exe, ms_exes=self.ms_exes)
        if changed:
            diag.log.info("元扫雷程序切换为：%s", path)
            if old_name.lower() != self._ms_name().lower():
                self._ms_forget_state()

    def ms_select_exe(self, path):
        """设置列表里换一个程序。下次启动（含自动启动）就用它。"""
        self.ms_remember_exe(path)

    def ms_remove_exe(self, path):
        """从列表里删掉一个程序；删的是当前选中项时顺延到下一项。"""
        path = os.path.abspath(path)
        self.ms_exes = [p for p in self.ms_exes
                        if os.path.normcase(p) != os.path.normcase(path)]
        if os.path.normcase(path) == os.path.normcase(self.ms_exe or ""):
            self.ms_exe = self.ms_exes[0] if self.ms_exes else ""
        if not self.ms_exe:
            self._ms_auto_launch = False
        save_config(ms_exe=self.ms_exe, ms_exes=self.ms_exes,
                    ms_auto_launch=self._ms_auto_launch)
        diag.log.info("设置里移除扫雷程序：%s（剩余 %d 个）",
                      path, len(self.ms_exes))
        self._ms_forget_state()

    def set_ms_auto_launch(self, on):
        """开关「打开工具箱自动启动」。没有可用程序时拒绝并返回原因。"""
        on = bool(on)
        if on and not self.ms_exe:
            return "请先指定元扫雷位置。"
        if on and not os.path.isfile(self.ms_exe):
            return "指定的程序不存在，请先指定元扫雷位置。"
        self._ms_auto_launch = on
        save_config(ms_auto_launch=on)
        diag.log.info("打开工具箱时自动启动元扫雷：%s", on)
        return ""

    def _ms_forget_state(self):
        """换了程序：旧的运行状态/路径不再适用，横幅下一拍按新程序重查。"""
        self._ms_running = False
        self._ms_path = ""
        self._ms_launch_at = 0.0
        self._ms_focus_until = 0.0
        self._draw_banner()

    def auto_launch_game(self):
        """工具箱启动后自动拉起上次选中的程序，并把它提到最前。

        只认设置里选中的那一项：程序没配置/文件不在时安静地留在后台，
        不去扫盘猜别的版本，也不弹选择框打断启动。
        """
        if not self._ms_auto_launch:
            return
        if not self.ms_exe:
            return
        if not os.path.isfile(self.ms_exe):
            diag.log.warning("自动启动：程序位置不存在：%s", self.ms_exe)
            self.set_status("没能自动启动元扫雷：设置里的程序位置不存在", "warn",
                            hold=8000)
            return
        self.launch_game(auto=True)

    def open_replay(self, path):
        """双击录像：交给元扫雷播放。

        元扫雷没在跑时它自己会启动并直接进播放；已经在跑时，新起的那个
        进程只是把路径转交给已有窗口（本地 socket），随即退出 —— 效果跟
        把文件拖进元扫雷窗口一样。播放不会产生新录像，也不会动 record.ini。
        """
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            self.set_status("这个录像已经不在了：%s" % shrink_path(path, 46), "warn")
            return
        if not self._play_gate(path):
            return
        exe = mslaunch.resolve(self.ms_exe, self._ms_path)
        if not exe:
            exe = self._ask_ms_exe()
            if not exe:
                return
        if os.path.normcase(exe) != os.path.normcase(self.ms_exe or ""):
            self.ms_remember_exe(exe)
        ok, err = mslaunch.open_file(exe, path)
        if not ok:
            diag.log.warning("用元扫雷打开录像失败：%s（%s）", err, path)
            self.set_status("没能用元扫雷打开这条录像", "err")
            return
        diag.log.info("用元扫雷打开录像：%s", path)
        self._play_path = path
        self._play_at = time.monotonic()
        self._play_wait = 0.0
        self.set_status("正在用元扫雷打开 %s…" % os.path.basename(path), "dim",
                        hold=5000)
        # 元扫雷的播放窗口默认会压在工具箱下面（Windows 只让前台进程抢前台），
        # 所以这里主动把它提上来 —— 双击之后用户要看的是录像，不是工具箱。
        self._ms_want_front()
        self._ms_try_focus()

    # ---- 播放前先确认「现在只能起一个元扫雷」 -------------------------------
    #
    # 元扫雷同一时刻只允许一个实例：它启动时会把插件服务绑在 5555/5556/5557。
    # 冷启动的头一两秒里它还没开始接「把文件路径转交给已有窗口」的本地 socket，
    # 这段时间里再起一个进程，新进程找不到已有窗口，就自己去绑同一组端口，
    # 撞上正在启动的那个 —— 元扫雷会直接弹「Address in use」的未处理异常框。
    #
    # 实测踩过：同一路录像连点两下，两个进程落在同一秒里（见 app.log），
    # 第二个就是这么崩的。所以同一局短时间内只认一次；如果它还在启动，
    # 等它把插件端口开出来再交，而不是再起一个。
    MS_PLUGIN_PORT = 5556          # 基础 5555 派生出的 PUB 端口
    PLAY_REPEAT_GUARD = 3.0        # 同一路录像这几秒内的重复点击算一次
    PLAY_STARTUP_WAIT = 8.0        # 等它启动完最多等这么久

    def _play_gate(self, path):
        """现在能不能安心起一个进程去播这条路录像；不能就排到稍后再交。"""
        now = time.monotonic()
        if path == self._play_path and now - self._play_at < self.PLAY_REPEAT_GUARD:
            return False                    # 连点了同一局，上一次已经在打开
        if (mslaunch.find_running(self._ms_name()) is not None
                and self._ms_is_metasweeper()
                and not mslaunch.port_open(self.MS_PLUGIN_PORT)):
            # 进程在、插件端口还没开 = 还在启动。等它起来再交，别起第二个。
            if not self._play_wait:
                self._play_wait = now
                self.set_status("元扫雷正在启动，稍等一下…", "dim", hold=2500)
            if now - self._play_wait < self.PLAY_STARTUP_WAIT:
                self._play_pending = path
                if self._play_job is None:
                    self._play_job = self.after(500, self._play_resume)
                return False
            # 等够久了它还没开插件服务，那就不等了 —— 别把用户的点击吞掉
        return True

    def _play_resume(self):
        """元扫雷启动完了，把刚才那条录像补交过去。"""
        self._play_job = None
        if not self.winfo_exists():
            return
        path, self._play_pending = self._play_pending, ""
        if path:
            self.open_replay(path)

    def _reveal_path(self, path):
        """在资源管理器里定位录像；是目录就直接打开，并把窗口提到最前。"""
        try:
            is_file = os.path.isfile(path)
            folder = path if os.path.isdir(path) else os.path.dirname(path)
            if not os.path.isdir(folder):
                raise OSError("路径不存在")
            before = set(_explorer_windows())
            mslaunch.allow_foreground()
            if is_file:
                # explorer 自己对引号敏感，用字符串交给它，比列表形式可靠
                subprocess.Popen('explorer /select,"%s"' % os.path.normpath(path))
            else:
                os.startfile(folder)
            # 资源管理器从工具箱这个后台进程起来时，窗口会压在工具箱下面。
            # 等它建好再提；复用了已有窗口（没有新窗口）时按文件夹名找。
            self.after(320, lambda f=folder, b=before:
                       self._focus_opened_folder(f, b))
        except OSError:
            self.set_status("这个位置打不开了：%s" % shrink_path(path, 50),
                            "warn")

    def _focus_opened_folder(self, folder, before):
        """把刚打开的（或本来就在用的）文件夹窗口提到最前面。"""
        if os.name != "nt":
            return
        u = mslaunch._user32()
        hwnds = _explorer_windows()
        fresh = [h for h in hwnds if h not in before]
        target = fresh[0] if fresh else None
        if target is None:
            name = os.path.basename(folder.rstrip("\\/")).lower()
            if name:
                for h in hwnds:
                    if name in _window_title(u, h).lower():
                        target = h
                        break
        if target:
            mslaunch.focus_hwnd(target)

    def launch_game(self, auto=False):
        """横幅上的「启动元扫雷」：找到路径 → 启动 → 盯着它真的起来。

        路径一个都没有时只让用户指一次，指完记进配置；换安装位置或换台机器，
        也只需要这一次。``auto=True`` 是打开工具箱时的自动启动：只用设置里
        选中的那一项，缺路径/启动失败都只写状态栏，不弹窗。
        """
        if self._ms_running or self._ms_pending():
            return                          # 已经在跑 / 刚点过还没确认，别开第二个
        # 按钮画出来到第一次轮询之间有一小段空窗，这期间元扫雷可能早就开着
        # （很多人习惯先开游戏再开工具箱）。这时再 Popen 只是多起一个转发
        # 进程，还会让状态栏卡在「正在启动」——所以先同步看一眼。
        running, _p = self._ms_probe()
        if running:
            self._ms_running = True
            self._draw_banner()
            self.set_status("元扫雷已经在运行", "dim")
            if auto:
                self._ms_want_front(mslaunch.START_GRACE)
                self._ms_try_focus()
            return
        if auto:
            path = self.ms_exe if os.path.isfile(self.ms_exe or "") else ""
            if not path:
                diag.log.warning("自动启动：没有可用的程序位置（%s）",
                                 self.ms_exe or "未配置")
                self.set_status("没能自动启动元扫雷：请先在设置里指定程序位置",
                                "warn", hold=8000)
                return
        else:
            path = mslaunch.resolve(self.ms_exe, self._ms_path)
            if not path:
                path = self._ask_ms_exe()
                if not path:
                    self.set_status("没有启动元扫雷", "dim")
                    return
        if os.path.normcase(path) != os.path.normcase(self.ms_exe or ""):
            self.ms_remember_exe(path)
        ok, err = mslaunch.launch(path)
        if not ok:
            diag.log.warning("启动元扫雷失败：%s（%s）", err, path)
            self.set_status("元扫雷没有启动起来", "err")
            if auto:
                self.set_status("没能自动启动元扫雷：%s" % err, "err", hold=8000)
            else:
                messagebox.showerror(
                    APP_TITLE, "没能启动元扫雷。\n\n%s\n\n程序：%s"
                    % (err, shrink_path(path, 72)))
            return
        diag.log.info("%s元扫雷：%s", "自动启动" if auto else "启动", path)
        # 这里只记「点过启动」，运行状态一律交给进程检测。若像以前那样抢先
        # 把 _ms_running 置 True，_poll_ms 的确认分支就永远进不去，状态栏会
        # 一直停在「正在启动元扫雷…」。
        self._ms_launch_at = time.time()
        self._draw_banner()
        # 过渡话：启动成功之后要能干净地退场，不能把「正在启动…」留成底稿
        self.set_status("正在启动元扫雷…", "dim", transient=True,
                        hold=int(mslaunch.START_GRACE * 1000) + 3000)
        # 自动启动时工具箱可能还要忙一阵（首轮扫描），多给一点置前窗口
        self._ms_want_front(mslaunch.START_GRACE if auto
                            else self.MS_FOCUS_WINDOW)

    def _ask_ms_exe(self):
        """让用户指一次扫雷主程序（装到哪都认）。取消返回空串。

        不再按文件名挡「不像元扫雷」的选择：非标准版本、阿比特都有用户
        主动指定的场景，启动入口本身不需要替用户判断能不能开。
        """
        initial = ""
        if self.ms_exe and os.path.isfile(self.ms_exe):
            initial = os.path.dirname(self.ms_exe)
        if not initial:
            guess = mslaunch.discover()
            initial = (os.path.dirname(guess[0]) if guess
                       else (os.environ.get("ProgramFiles") or ""))
        p = filedialog.askopenfilename(
            parent=self, title="选择扫雷主程序",
            initialdir=initial if os.path.isdir(initial) else None,
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if not p:
            return ""
        p = os.path.abspath(p)
        diag.log.info("用户指定扫雷主程序：%s", p)
        return p

    def _ms_probe(self):
        """查一次选中的程序在不在跑，返回 (在不在跑, 反查到的路径)。

        从进程反查到的路径比任何猜测都准（用户自己从开始菜单开的那次也算）；
        配置里的路径失效时顺手改过来。按当前选中程序的名字找，换成阿比特
        或非标准版本也认的是对应的进程。
        """
        try:
            found = mslaunch.find_running(self._ms_name())
        except Exception:
            found = None
        path = (found[1] if found and len(found) > 1 else "") or ""
        if path and os.path.isfile(path):
            self._ms_path = path
            if not os.path.isfile(self.ms_exe or ""):
                self.ms_remember_exe(path)
        return bool(found), path

    def _ms_pending(self):
        """刚点过启动、还没被进程检测确认的这几秒（防连点 + 按钮文案）。"""
        return (bool(self._ms_launch_at)
                and time.time() - self._ms_launch_at < mslaunch.START_GRACE)

    def _ms_want_front(self, seconds=None):
        """从现在起一段时间内，把目标程序的窗口提到最前面。

        冷启动时窗口要过几百毫秒才建出来，一次抢不到，所以开个窗口期让
        轮询补几次；抢到、超时、或者用户已经去干别的了，就收工。
        """
        if seconds is None:
            seconds = self.MS_FOCUS_WINDOW
        self._ms_focus_until = time.time() + seconds

    def _ms_try_focus(self):
        """补一次「让目标程序露脸」。只在前台还是工具箱自己时才动手。

        用户双击完就去点浏览器了，说明他这会儿不想被打断 —— 这时候再把
        元扫雷拽到最前面是很讨厌的。
        """
        if not self._ms_focus_until:
            return
        if time.time() > self._ms_focus_until:
            self._ms_focus_until = 0.0
            return
        if not mslaunch.foreground_is_self():
            self._ms_focus_until = 0.0
            return
        if mslaunch.focus(self._ms_name()):
            self._ms_focus_until = 0.0

    def _poll_ms(self):
        """每两秒半看一眼元扫雷在不在跑；状态变了才重画横幅。"""
        self._ms_try_focus()
        running, _path = self._ms_probe()
        if running:
            if not self._ms_running:
                self._ms_running = True
                self._draw_banner()
                if self._ms_launch_at:          # 这次是按钮启动的，才回一句
                    self._ms_launch_at = 0.0
                    if self._auto_on:
                        text = "元扫雷已启动"
                    else:
                        text = "元扫雷已启动 · 自动上传关着，新成绩不会自己传"
                    # 记下这行，用户把元扫雷关掉时要能立刻收回
                    self.set_status(text, "ok" if self._auto_on else "warn",
                                    hold=self.MS_STATUS_HOLD_MS)
                    self._ms_status = text
        else:
            if self._ms_running:
                # 用户自己关掉的：静默回弹即可。以前这里会误报「没能启动起来」
                self._ms_running = False
                self._ms_launch_at = 0.0
                self._draw_banner()
                self._drop_ms_status()
            elif self._ms_launch_at and not self._ms_pending():
                # 点过启动、宽限期过了仍然没见到进程：这才是真没起来
                self._ms_launch_at = 0.0
                self._draw_banner()
                self.set_status("元扫雷没能启动起来，换个位置试试", "err")
        self.after(self.MS_POLL_MS, self._poll_ms)

    def _drop_ms_status(self):
        """元扫雷关掉之后，底下那句「已启动」不能继续挂着。

        界面上下要一致：横幅已经变回「启动元扫雷」了，状态栏还留着一句
        「元扫雷已启动」，用户只会觉得程序没跟上。
        """
        text = getattr(self, "_ms_status", "")
        self._ms_status = ""
        if text and self.lbl_status.cget("text") == text:
            self._status_back()

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

        head = self._section(page, "最近完成局", C_ACCENT2,
                             "按时间倒序，取最近 100 局与近三天中较多者，"
                             "双击用元扫雷播放")
        head.pack(fill="x", pady=(sc(16), sc(6)))
        # 今日总结：点一下选「总结 / 详细总结」，图进剪贴板，直接粘到群里
        self.btn_today = Pill(head, "今日总结", command=self._today_menu,
                              accent=C_ACCENT2, padx=13, height=26)
        self.btn_today.pack(side="right", padx=(sc(6), 0))

        cols = [
            ("level_cn", "级别", 72, "center", "str"),
            ("style_cn", "风格", 56, "center", "str"),
            ("time", "time", 96, "e", "f3"),
            ("bv", "BV", 56, "e", "int"),
            ("bvs", "3BV/s", 86, "e", "f3"),
            ("stnb", "stnb", 78, "e", "f2"),
            ("date", "日期", 100, "center", "str"),
            ("clock", "时刻", 84, "center", "str"),
            ("vs_pb", "距当前 PB", 120, "e", "delta"),
            ("vs_pb_bvs", "3BV/s 距 PB", 104, "e", "delta2"),
            ("vs_pb_stnb", "stnb 距 PB", 100, "e", "delta2"),
            ("gain", "涨分", 64, "e", "gain"),
        ]
        _, self.tbl_recent = self._table(page, cols, height=5)
        # 多出来的空间由其余列一起分摊；涨分固定只需容纳 +xx.xx，不跟着拉宽。
        for key in (c[0] for c in cols):
            self.tbl_recent.tv.column(key, stretch=(key != "gain"))

        # 首扫时盖在内容区正中（place 不进布局，扫完 place_forget）
        self.lbl_boot = tk.Label(page, text="正在读取录像…\n初始化要逐局重演录像，会比较慢，请耐心等待",
                                 bg=C_BG, fg=C_DIM, font=FONT_SECTION, justify="center",
                                 padx=sc(18), pady=sc(8))

    # ---------- 每日 ----------
    # 十三列：日期 / 局数 / 起止 / 时间三项 / 3BV/s 三项 / stnb 三项。
    # 宽度是初始值兼组头定位的兜底（实际位置按 bbox 算，见 _draw_daily_groups）。
    # 合计刻意压在 880 以内：窗口拉到最小（940）时列也不会被截掉右边 ——
    # Tk 的 Treeview 只会平摊多出来的宽度，不够时**不会**自动缩列。
    DAILY_COLS = [
        ("date", "日期", 94, "center", "str"),
        ("games", "局数", 48, "e", "int"),
        ("t_from", "起", 70, "center", "clock"),
        ("t_till", "止", 70, "center", "clock"),
        ("best", "最快", 70, "e", "f3"),
        ("avg", "平均", 70, "e", "f3"),
        ("worst", "最慢", 70, "e", "f3"),
        ("bvs_max", "最高", 70, "e", "f3"),
        ("bvs_avg", "平均", 70, "e", "f3"),
        ("bvs_min", "最低", 70, "e", "f3"),
        ("stnb_max", "最高", 58, "e", "f2"),
        ("stnb_avg", "平均", 58, "e", "f2"),
        ("stnb_min", "最低", 58, "e", "f2"),
    ]
    DAILY_GROUPS = (("best", "worst", "time"),
                    ("bvs_max", "bvs_min", "3BV/s"),
                    ("stnb_max", "stnb_min", "stnb"))

    def _build_daily(self):
        page = self.pages["daily"]
        top = tk.Frame(page, bg=C_BG)
        top.pack(fill="x", pady=(sc(10), sc(6)))
        # 级别单选：三级的 BV 分布完全不同，「三级混在一起的平均时间」没有意义
        self.daily_seg = {}
        for lv in core.LEVELS:
            p = Pill(top, core.LEVEL_CN[lv],
                     command=lambda k=lv: self.set_daily_level(k),
                     accent=LEVEL_ACCENT[lv][0], padx=16)
            p.pack(side="left", padx=(0, sc(6)))
            self.daily_seg[lv] = p
        self.lbl_daily_count = tk.Label(top, text="", bg=C_BG, fg=C_DIM,
                                        font=FONT_SMALL)
        self.lbl_daily_count.pack(side="right")

        # 表头上方那一条：把六列数字分成「时间」和「3BV/s」两组。
        # 三对「最快/平均/最慢」「最高/平均/最低」光看表头分不清哪三列是哪个指标。
        self.daily_strip = tk.Canvas(page, height=sc(18), bg=C_BG,
                                     highlightthickness=0, bd=0)
        self.daily_strip.pack(fill="x")
        self.daily_strip.bind("<Configure>", lambda _e: self._draw_daily_groups())

        # 明细条先 pack 到底边：内容区拉高时多出来的高度全给表格，不给这一条
        holder = tk.Frame(page, bg=C_LINE)
        holder.pack(side="bottom", fill="x", pady=(sc(8), 0))
        bar = tk.Frame(holder, bg=C_PANEL)
        bar.pack(fill="x", padx=1, pady=1)
        self.btn_daily_open = Pill(bar, "当天明细", command=self._open_daily,
                                   accent=C_ACCENT, padx=12, height=24)
        self.btn_daily_open.pack(side="right", padx=(sc(6), sc(10)), pady=sc(5))
        self.lbl_daily_detail = tk.Label(bar, text="", bg=C_PANEL, fg=C_DIM,
                                         font=FONT_UI, anchor="w")
        self.lbl_daily_detail.pack(side="left", fill="x", expand=True,
                                   padx=(sc(12), 0), pady=sc(7))

        _, self.tbl_daily = self._table(
            page, self.DAILY_COLS, height=8, iid_key="date",
            # 双击直接把这一行的日期传过去：Treeview 的选中变化事件是异步的，
            # 不能指望 _daily_date 在双击回调里已经更新（会打开上一天）
            on_double=lambda iid: self._open_daily(iid),
            on_menu=self._daily_menu)
        # 列合计比内容区窄，让它们平摊多出来的宽度：右边留一大块空白
        # 看着像表格没画完。组头的位置按实际列宽算（见 _draw_daily_groups）。
        for key in (c[0] for c in self.DAILY_COLS):
            self.tbl_daily.tv.column(key, stretch=True)
        self.tbl_daily.tv.bind("<<TreeviewSelect>>", self._on_daily_pick)
        # 列宽随窗口变，组头的位置也跟着变：表格自己一换尺寸就重画
        self.tbl_daily.tv.bind("<Configure>", lambda _e: self._draw_daily_groups(),
                               add="+")
        # 建完就把级别按钮点成默认那个（高级）。以前这里没调，页面默认画
        # 初级但三个按钮全灰着 —— 用户看不出当前在看哪一级。
        self.set_daily_level(self._daily_level)

    def _draw_daily_groups(self):
        """在表头上方标出「时间」「3BV/s」「stnb」三段。

        位置按 Treeview 的**实际**列宽算（bbox），不能用 DAILY_COLS 里的
        设计宽度 —— 列是 stretch 的，实际宽度取决于窗口。
        """
        cv = self.daily_strip
        cv.delete("all")
        kids = self.tbl_daily.tv.get_children() if hasattr(self, "tbl_daily") else ()
        boxes = {}
        anchor = None
        for kid in kids:                 # 第一行可能滚出视口，取第一个看得见
            if self.tbl_daily.tv.bbox(kid):
                anchor = kid
                break
        if anchor:
            for key, _t, _w, _a, _k in self.DAILY_COLS:
                bb = self.tbl_daily.tv.bbox(anchor, key)
                if bb:
                    boxes[key] = (bb[0], bb[2])
        widths = [sc(c[2]) for c in self.DAILY_COLS]
        keys = [c[0] for c in self.DAILY_COLS]
        for first, last, text in self.DAILY_GROUPS:
            if first in boxes and last in boxes:
                left = boxes[first][0] + 1
                right = boxes[last][0] + boxes[last][1] + 1
            else:
                i = keys.index(first)
                left = sum(widths[:i]) + 1
                right = left + sum(widths[i:keys.index(last) + 1])
            cv.create_text((left + right) / 2.0, sc(9), text=text, fill=C_DIM,
                           font=FONT_SMALL)

    def set_daily_level(self, key):
        self._daily_level = key
        for k, p in self.daily_seg.items():
            p.set_selected(k == key)
        self.render_daily()

    def _daily_top_date(self):
        """视口里最上面那一行的日期（行 iid 就是日期）。空表返回 None。

        注意不是 get_children()[0] —— 表往下滚过之后第一行已经出了视口，
        要找第一个 bbox 非空的才算「正在看的那一天」。
        """
        tv = self.tbl_daily.tv
        for iid in tv.get_children():
            if tv.bbox(iid):
                return iid
        return None

    @staticmethod
    def _nearest_day(dates, anchor):
        """dates 里离 anchor 最近的一天；一样近就取较新的。ISO 日期可直接比大小。"""
        best, best_gap = None, None
        a = datetime.fromisoformat(anchor)
        for d in dates:
            gap = abs((datetime.fromisoformat(d) - a).days)
            if best_gap is None or gap < best_gap or (gap == best_gap and d > best):
                best, best_gap = d, gap
        return best

    def _daily_scroll_to(self, day):
        """把某一天滚到视口第一行（列表末尾行数不够时尽力让它可见）。"""
        tv = self.tbl_daily.tv
        if day not in tv.get_children():
            return
        tv.see(day)                     # 先保证进视口，far away 时也能定位
        self.update_idletasks()
        # yview_moveto / see 会有最多一行的四舍五入误差，用 bbox 反馈推到第一行。
        # 末尾几天下面行数不够，滚不动 —— 那时它已经可见，不再强求置顶。
        for _ in range(3):
            bb = tv.bbox(day)
            if not bb or bb[3] <= 0:
                break
            first_y = None
            for iid in tv.get_children():
                b = tv.bbox(iid)
                if b:
                    first_y = b[1]
                    break
            if first_y is None:
                break
            steps = int(round((bb[1] - first_y) / float(bb[3])))
            if steps <= 0:
                break
            tv.yview_scroll(steps, "units")
            self.update_idletasks()

    def render_daily(self):
        """按天聚合，一天一行。

        不做「近 30 天 / 近 90 天」这类范围筛选：有局的天数最多也就几百，
        一屏之外滚动就够，而「我从哪天开始练、总共练了多少天」本来是最该
        一眼看见的信息。（实测 23183 局：聚合一整个级别 1.2–7.1 ms。）
        """
        if not hasattr(self, "tbl_daily"):
            return
        # 换数据前先记下正在看的那天：Treeview 整表 delete+insert 后滚动位置
        # 按百分比保留，切级别时三级行数不同，同一个百分比落到的日期就变了。
        # 这里在不同级别之间共用「天」，切过去还在同一天；没有那天就取最近的。
        anchor = self._daily_top_date()
        # 三个级别各聚合一次：右键菜单要给出「当天全部级别」的总结，一弹出就要
        # 知道那天有哪几级（标签要写出来），不能等点下去再算。实测 22723 局
        # 三级合计约 20 ms，一次渲染付一次，之后菜单只是查表。
        self.daily_by_level = {lv: core.daily_rows(self.games, lv)
                               for lv in core.LEVELS}
        rows = self.daily_by_level[self._daily_level]
        self.daily_rows = rows
        self.tbl_daily.set_rows(rows)
        self.lbl_daily_count.config(
            text="%d 天 · %d 局" % (len(rows), sum(r["games"] for r in rows)))
        self._daily_date = None
        self.lbl_daily_detail.config(
            text="双击某一天看当天每一局" if rows else "这个级别还没有完成局",
            fg=C_DIM)
        self.btn_daily_open.set_enabled(False)
        if anchor and rows:
            # 有同一天就停同一天，没有就取日期最近的一天（平局取较新）
            if any(r["date"] == anchor for r in rows):
                target = anchor
            else:
                target = self._nearest_day([r["date"] for r in rows], anchor)
            self._daily_scroll_to(target)
        self._draw_daily_groups()       # 行变了，组头按新的列宽重画

    def _on_daily_pick(self, _e=None):
        sel = self.tbl_daily.tv.selection()
        row = self.tbl_daily._row_of(sel[0]) if sel else {}
        if not row:
            self._daily_date = None
            self.btn_daily_open.set_enabled(False)
            return
        self._daily_date = row["date"]
        self.btn_daily_open.set_enabled(True)
        self.lbl_daily_detail.config(
            text="%s · %d 局 · 最快 %.3f s · 最慢 %.3f s · 跨度 %s · NF %d 局"
                 % (row["date"], row["games"], row["best"], row["worst"],
                    fmt_span(row["span"]), row.get("nf") or 0),
            fg=C_TEXT)

    def _open_daily(self, day=None):
        """当天明细：弹出这一天的每一局，双击某局直接播。

        day 由双击传入（那一行的日期）；按钮走默认值，用选中的那一行。
        """
        day = day or self._daily_date
        if not day:
            self.set_status("先在上面选一天", "dim")
            return
        rows = core.daily_games(self.games, day, self._daily_level)
        if not rows:
            self.set_status("%s 这个级别没有完成局" % day, "dim")
            return
        pb = {(r.get("category") or "all", r.get("level"), r.get("bv")): r
              for r in (self.pb_rows or [])}
        out = []
        for g in rows:
            item = dict(g)
            item["style_cn"] = core.STYLE_CN.get(g.get("style"), "?")
            item["vs_pb"] = self._gap_to_pb(g, pb)
            out.append(item)
        t_first = min(g["t0"] for g in rows)
        t_last = max(g.get("t1") or (g["t0"] + g["time"]) for g in rows)
        best = min(rows, key=lambda g: g["time"])
        nf = sum(1 for g in rows if g.get("nf") is True)
        title = "%s · %s · %d 局" % (day, core.LEVEL_CN[self._daily_level], len(rows))
        summary = ("%s – %s · 跨度 %s · 最快 %.3f s @BV%d · NF %d 局 · FL %d 局"
                   % (core.clock_text(t_first), core.clock_text(t_last),
                      fmt_span(t_last - t_first), best["time"], best["bv"],
                      nf, len(rows) - nf))
        old = getattr(self, "_daily_dialog", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        self._daily_dialog = DailyDialog(self, title, summary, out)

    # ---------- 当日 / 今日总结 ----------
    def _daily_menu(self, iid, menu):
        """每日页的右键菜单：总结 / 详细总结 × 这一级 / 当天全部级别。

        只有图片，没有文本 —— 文本那版没人用，两个入口都只出图，所以标签里
        不再写「图片」二字。详细版多给「用时 / stnb 走势」「用时分布」
        「最快 5 局 / stnb 最低 5 局」与「前半程 / 后半程、FL / NF 平均」。
        """
        row = self.tbl_daily._row_of(iid) if hasattr(self, "tbl_daily") else {}
        day = row.get("date")
        if not day:
            return
        cn = core.LEVEL_CN[self._daily_level]
        menu.add_command(label="复制总结 · %s" % cn,
                         command=lambda d=day: self._copy_level(d, False))
        menu.add_command(label="复制详细总结 · %s" % cn,
                         command=lambda d=day: self._copy_level(d, True))
        menu.add_separator()
        menu.add_command(label="复制总结 · 全部级别",
                         command=lambda d=day: self._copy_levels(d, False))
        menu.add_command(label="复制详细总结 · 全部级别",
                         command=lambda d=day: self._copy_levels(d, True))

    def _daily_row(self, day=None):
        """当天那一行（聚合后的）；day 为空就用当前选中的那天。"""
        day = day or self._daily_date
        if not day:
            return None
        for r in getattr(self, "daily_rows", []) or []:
            if r["date"] == day:
                return r
        return None

    def _copy_level(self, day=None, detail=False):
        """当天、正在看的那一级：总结或详细总结。"""
        row = self._daily_row(day)
        if not row:
            self.set_status("先在上面选一天", "dim")
            return
        cn = core.LEVEL_CN[self._daily_level]
        lv = self._daily_level
        try:
            if detail:
                games = core.daily_games(self.games, row["date"], lv)
                img = summary.levels_detail_png(
                    [(lv, cn, row)], {lv: games}, APP_VER, row["date"],
                    all_games=self.games)
            else:
                img = summary.daily_png(row, lv, cn, APP_VER)
        except Exception as e:                  # noqa: BLE001
            diag.log.exception("画当日%s图失败" % ("详细总结" if detail else "总结"))
            self.set_status("画图失败：%s" % e, "err")
            return
        self._put_summary(img, "%s · %s %s" % (row["date"], cn,
                                               "详细总结" if detail else "总结"))

    def _put_summary(self, img, what):
        """把画好的图放进剪贴板，状态栏报一句人话。三处入口共用。"""
        if img is None or not summary.copy_image(img):
            self.set_status("图片复制失败（剪贴板被别的程序占着？）", "err",
                            hold=5000)
            return
        # 复制回执是「说完就过期」的话，必须自己退场 —— 它在最底下一行，
        # 不设 hold 就会一直挂在那儿，跟后来切到哪页都对不上。
        self.set_status("已复制 %s（图片 %d×%d）"
                        % (what, img.size[0], img.size[1]), "ok", hold=5000)

    def _today_rows(self):
        """今天有完成局的级别：[(级别, 名称, 该天聚合行), ...]。"""
        today = core.day_text(time.time())
        return today, self._day_level_rows(today)

    def _day_level_rows(self, day):
        """某一天有完成局的级别，按 初级/中级/高级 顺序。

        走 render_daily 缓存的 daily_by_level（三级各一份），右键菜单弹出时只是
        查表，不会为了某一天再扫一遍两万多局。
        """
        by = getattr(self, "daily_by_level", None)
        if not by:              # 每日页还没建好就先算一份，别静默返回空
            by = {lv: core.daily_rows(self.games, lv) for lv in core.LEVELS}
        out = []
        for lv in core.LEVELS:
            r = next((x for x in by.get(lv) or [] if x["date"] == day), None)
            if r:
                out.append((lv, core.LEVEL_CN[lv], r))
        return out

    def _today_menu(self):
        m = StyleMenu(self)
        m.add_command(label="复制今日总结",
                      command=lambda: self._copy_today(False))
        m.add_command(label="复制今日详细总结",
                      command=lambda: self._copy_today(True))
        b = self.btn_today
        m.popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + sc(4))

    def _copy_today(self, detail=False):
        """今天的总结：把有局的级别并在一张图里。"""
        self._copy_levels(core.day_text(time.time()), detail)

    def _copy_levels(self, day, detail=False):
        """某一天全部级别的总结 / 详细总结。

        与概览页那个按钮共用一套版式：日子不同只影响标题里的「今日」后缀，
        所以每日页任意一天都能直接出多级别卡片，不必先等到今天。
        """
        items = self._day_level_rows(day)
        is_today = day == core.day_text(time.time())
        word = "今日" if is_today else "当日"
        if not items:
            self.set_status("今天还没有完成局" if is_today
                            else "%s 没有完成局" % day, "dim")
            return
        try:
            if detail:
                gbl = {lv: core.daily_games(self.games, day, lv)
                       for lv, _cn, _r in items}
                img = summary.levels_detail_png(items, gbl, APP_VER, day,
                                                all_games=self.games)
            else:
                img = summary.levels_png(items, APP_VER, day)
        except Exception as e:                  # noqa: BLE001
            diag.log.exception("画 %s%s图失败"
                               % (word, "详细总结" if detail else "总结"))
            self.set_status("画图失败：%s" % e, "err")
            return
        what = "%s · %s · %d 个级别" % (day, word + ("详细总结" if detail
                                                     else "总结"), len(items))
        self._put_summary(img, what)

    def _build_pb(self):
        page = self.pages["pb"]
        top = tk.Frame(page, bg=C_BG)
        top.pack(fill="x", pady=(sc(10), sc(8)))
        self.pb_style_seg = {}
        for key, label in (("all", "全PB"), ("nf", "NF PB")):
            p = Pill(top, label, command=lambda k=key: self.set_pb_style(k),
                     accent=C_ACCENT3, padx=14)
            p.pack(side="left", padx=(0, sc(6)))
            self.pb_style_seg[key] = p

        tk.Frame(top, bg=C_LINE, width=1).pack(side="left", fill="y",
                                               padx=(sc(8), sc(10)), pady=sc(2))
        self.pb_metric_seg = {}
        for key, label in (("time", "time"), ("bvs", "3BV/s"),
                           ("stnb", "stnb"), ("rank", "排名")):
            p = Pill(top, label, command=lambda k=key: self.set_pb_metric(k),
                     accent=C_ACCENT2, padx=13)
            p.pack(side="left", padx=(0, sc(6)))
            self.pb_metric_seg[key] = p

        self.lbl_pb_count = tk.Label(top, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL)
        self.lbl_pb_count.pack(side="right")
        self.pb_top = top

        sub = tk.Frame(page, bg=C_BG)
        sub.pack(fill="x", pady=(0, sc(8)))
        tk.Label(sub, text="搜索 BV / 文件名", bg=C_BG, fg=C_DIM,
                 font=FONT_SMALL).pack(side="left", padx=(0, sc(6)))
        self.var_q = tk.StringVar()
        self._entry(sub, self.var_q, width=20).pack(side="left", ipady=sc(1))
        self.var_q.trace_add("write", lambda *_: self.render_pb())
        self.lbl_pb_hint = tk.Label(sub, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL)
        self.lbl_pb_hint.pack(side="right")
        self.pb_sub = sub

        # 详情紧贴网格下沿，别把点选结果钉到窗口底部。
        self.pb_grid = PBGrid(page, on_pick=self._on_pb_pick)
        self.pb_grid.pack(side="top", fill="x")
        self._build_pb_detail(page)
        tk.Frame(page, bg=C_BG).pack(side="top", fill="both", expand=True)
        page.bind("<Configure>", self._pb_fit)

        self.set_pb_style(self._pb_style)

    def _pb_fit(self, _e=None):
        """网格能占的高度 = 页面剩下的高度。行数少时它自己收缩，别拉长。"""
        page = self.pages["pb"]
        if not hasattr(self, "pb_grid"):
            return
        self.update_idletasks()
        used = (self.pb_top.winfo_reqheight() + self.pb_sub.winfo_reqheight()
                + self.pb_detail_holder.winfo_reqheight())
        self.pb_grid.set_max_height(page.winfo_height() - used - sc(34))

    def _build_pb_detail(self, page):
        holder = tk.Frame(page, bg=C_LINE)
        holder.pack(side="top", fill="x", pady=(sc(8), 0))
        self.pb_detail_holder = holder
        bar = tk.Frame(holder, bg=C_PANEL)
        bar.pack(fill="x", padx=1, pady=1)
        bar.columnconfigure(0, weight=1)

        self.pb_detail = tk.Label(bar, text="", bg=C_PANEL, fg=C_TEXT,
                                  font=FONT_UI, anchor="w", justify="left")
        self.pb_detail.grid(row=0, column=0, sticky="ew",
                            padx=(sc(12), sc(8)), pady=(sc(7), sc(2)))
        self.pb_history = tk.Label(bar, text="", bg=C_PANEL, fg=C_DIM,
                                   font=FONT_SMALL, anchor="w", justify="left")
        self.lbl_pb_file = tk.Label(bar, text="", bg=C_PANEL, fg=C_DIM,
                                    font=FONT_SMALL, anchor="w", justify="left")

        actions = tk.Frame(bar, bg=C_PANEL)
        actions.grid(row=0, column=1, sticky="e",
                     padx=(0, sc(10)), pady=sc(5))
        self.pb_actions = actions
        self.btn_pb_reveal = Pill(actions, "定位", command=self._reveal_pb,
                                  accent=C_ACCENT3, padx=12, height=24)
        self.btn_pb_reveal.pack(side="left", padx=(0, sc(6)))
        self.btn_pb_play = Pill(actions, "播放", command=self._play_pb,
                                accent=C_ACCENT, padx=12, height=24)
        self.btn_pb_play.pack(side="left")
        self.btn_pb_play.set_enabled(False)
        self.btn_pb_reveal.set_enabled(False)
        holder.bind("<Configure>", self._pb_detail_fit)

    def _pb_detail_fit(self, _e=None):
        width = self.pb_detail_holder.winfo_width()
        if width <= 1:
            return
        full = max(sc(120), width - sc(28))
        main = max(sc(120), width - self.pb_actions.winfo_reqwidth() - sc(48))
        self.pb_detail.config(wraplength=main)
        self.pb_history.config(wraplength=full)
        self.lbl_pb_file.config(wraplength=full)
        self.after_idle(self._pb_fit)

    def _set_pb_detail(self, main, history="", filename="", dim=False):
        self.pb_detail.config(text=main, fg=C_DIM if dim else C_TEXT)
        for label, text, row in ((self.pb_history, history, 1),
                                 (self.lbl_pb_file, filename, 2)):
            label.config(text=text)
            if text:
                label.grid(row=row, column=0, columnspan=2, sticky="ew",
                           padx=sc(12), pady=(0, sc(7)))
            else:
                label.grid_remove()
        self._pb_detail_fit()

    def _reveal_pb(self):
        row = (self._pb_index or {}).get(self._pb_picked) or {}
        self.pb_grid.reveal(row)

    def _play_pb(self):
        row = (self._pb_index or {}).get(self._pb_picked) or {}
        self.pb_grid.play(row)

    def _on_pb_pick(self, key):
        """单击格子：成绩、纪录历史与文件名各占一行，窄窗口内自动折行。"""
        self._pb_picked = key
        r = (self._pb_index or {}).get(key)
        if not r:
            self._set_pb_detail("还没有成绩", dim=True)
            self.btn_pb_play.set_enabled(False)
            self.btn_pb_reveal.set_enabled(False)
            return
        lv, bv = key
        rank = self.pb_grid.rank_of(r)
        cnt = self.pb_grid.count_of(r)
        bits = [core.LEVEL_CN[lv], "BV %d" % bv,
                "%.3f s" % float(r.get("time") or 0),
                "%.2f 3BV/s" % float(r.get("bvs") or 0),
                "stnb %.1f" % float(r.get("stnb") or 0)]
        if rank and cnt:
            bits.append("第 %d / %d" % (rank, cnt))
        games = (self.counts.get(self._pb_style) or {}).get((lv, bv), 0)
        if games:
            bits.append("%d 局" % games)
        history = []
        streak = core.pb_streak(self.games, lv, bv,
                                nf_only=self._pb_style == "nf")
        if streak:
            history.append("保持 %d 天" % streak["held"])
            if streak["gap"] is not None:
                history.append("破上条纪录用 %d 天" % streak["gap"])
        name = r.get("file") or ""
        date = (r.get("date") or "").strip()
        filename = "%s  %s" % (date, name) if date else name
        self._set_pb_detail("  ·  ".join(bits), "  ·  ".join(history), filename)
        self.btn_pb_play.set_enabled(bool(r.get("path")))
        self.btn_pb_reveal.set_enabled(bool(r.get("path")))

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
        for key, label in (("all", "全PB"), ("nf", "NF PB")):
            p = Pill(top, label, command=lambda k=key: self.set_top_style(k),
                     accent=C_ACCENT3, padx=14)
            p.pack(side="left", padx=(0, sc(6)))
            self.top_style_seg[key] = p

        tk.Label(top, text="BV 下限", bg=C_BG, fg=C_DIM,
                 font=FONT_UI).pack(side="left", padx=(sc(14), sc(6)))
        self.var_bvmin = tk.StringVar(value=str(core.BV_MIN_DEFAULT["b"]))
        self._bvmin_eff = core.BV_MIN_DEFAULT["b"]      # 当前真正生效的值
        self.bvmin_box = self._entry(top, self.var_bvmin, width=6)
        self.bvmin_box.pack(side="left", ipady=sc(2))
        self.var_bvmin.trace_add("write", lambda *_: self._on_bvmin_change())
        # 回车 / Esc / 点到别处都算收工：光标不能被困在这个框里
        self.bvmin_box.bind("<Return>", lambda _e: self._settle_bvmin())
        self.bvmin_box.bind("<Escape>", lambda _e: self._settle_bvmin(restore=True))
        self.bvmin_box.bind("<FocusOut>", lambda _e: self._settle_bvmin(quiet=True))
        tk.Label(top, text="（默认官方门槛，0 = 不限）",
                 bg=C_BG, fg=C_DIM, font=FONT_SMALL).pack(side="left", padx=(sc(8), 0))

        self.lsf_card = LsfCard(page)
        self.lsf_card.pack(fill="x", pady=(sc(10), sc(4)))

        self.lbl_top_site = tk.Label(page, text="", bg=C_BG, fg=C_DIM,
                                     font=FONT_SMALL, anchor="w")
        self.lbl_top_site.pack(fill="x", pady=(0, sc(6)))

        # 表格高度就是五行数据本身，不随窗口拉长——空框子比留白更难看
        grid = tk.Frame(page, bg=C_BG)
        grid.pack(fill="x", pady=(0, sc(2)))
        for i in range(3):
            grid.columnconfigure(i, weight=1, uniform="t")

        self.top_tables = {}
        specs = [("time", "time 最快 Top5"),
                 ("bvs", "最高 3BV/s Top5"),
                 ("stnb", "最高 stnb Top5")]
        for i, (metric, title) in enumerate(specs):
            cell = tk.Frame(grid, bg=C_BG)
            cell.grid(row=0, column=i, sticky="new",
                      padx=(0 if i == 0 else sc(12), 0))
            self._section(cell, title, C_ACCENT if i == 0 else
                          (C_ACCENT2 if i == 1 else C_ACCENT3)).grid(
                row=0, column=0, sticky="w", pady=(0, sc(6)))
            cols = [
                ("rank", "#", 32, "e", "int"),
                ("style_cn", "风格", 48, "center", "str"),
                ("time", "time", 64, "e", "f3"),
                ("bv", "BV", 44, "e", "int"),
                ("bvs", "3BV/s", 60, "e", "f3"),
                ("stnb", "stnb", 58, "e", "f2"),
                ("source_cn", "来源", 50, "center", "str"),
            ]
            holder = tk.Frame(cell, bg=C_LINE)
            holder.grid(row=1, column=0, sticky="ew")
            t = Table(holder, cols, height=5, fit=False, vbar=False)
            t.pack(fill="both", expand=True, padx=1, pady=1)
            self.top_tables[metric] = t
        self.set_top_style(self._top_style)


    # ---------- 排行榜 ----------
    def _build_rank(self):
        """本地全部录像的三级别排行：指标切换 + 全部/NF + 自定义条数。"""
        page = self.pages["rank"]
        bar = tk.Frame(page, bg=C_BG)
        bar.pack(fill="x", pady=(sc(10), sc(6)))
        self.rank_bar = bar

        # 三指标和 3BV 同时上表，这三个按钮只决定按哪一列排。
        tk.Label(bar, text="排序", bg=C_BG, fg=C_DIM,
                 font=FONT_UI).pack(side="left", padx=(0, sc(8)))
        self.rank_metric_seg = {}
        for key, label in (("time", "time"), ("bvs", "3BV/s"), ("stnb", "stnb")):
            p = Pill(bar, label, command=lambda k=key: self.set_rank_metric(k),
                     accent=C_ACCENT, padx=16)
            p.pack(side="left", padx=(0, sc(6)))
            self.rank_metric_seg[key] = p

        tk.Frame(bar, bg=C_LINE, width=1).pack(side="left", fill="y",
                                               padx=(sc(8), sc(10)), pady=sc(2))
        self.rank_style_seg = {}
        for key, label in (("all", "全部"), ("nf", "NF")):
            p = Pill(bar, label, command=lambda k=key: self.set_rank_style(k),
                     accent=C_ACCENT3, padx=14)
            p.pack(side="left", padx=(0, sc(6)))
            self.rank_style_seg[key] = p

        tk.Frame(bar, bg=C_LINE, width=1).pack(side="left", fill="y",
                                               padx=(sc(8), sc(10)), pady=sc(2))
        tk.Label(bar, text="Top", bg=C_BG, fg=C_DIM,
                 font=FONT_MONO_B).pack(side="left", padx=(0, sc(6)))
        self.var_rank_n = tk.StringVar(
            value="全部" if self._rank_n <= 0 else str(self._rank_n))
        box = ttk.Combobox(bar, textvariable=self.var_rank_n, width=6,
                           values=("10", "20", "50", "100", "全部"),
                           font=FONT_MONO_B, justify="center")
        box.pack(side="left", ipady=sc(2))
        self.rank_n_box = box
        # 回车 / 选下拉项 / 鼠标点到别处（失焦）都提交。
        # 失焦那条不抢焦点 —— 焦点本来就在离开，再 focus_set 会抢走用户要点的地方。
        for ev in ("<<ComboboxSelected>>", "<Return>"):
            box.bind(ev, lambda *_: self._on_rank_n())
        box.bind("<FocusOut>", lambda *_: self._on_rank_n(settle_focus=False))

        grid = tk.Frame(page, bg=C_BG)
        grid.pack(fill="both", pady=(sc(6), 0))
        self.rank_grid = grid
        for i in range(3):
            grid.columnconfigure(i, weight=1, uniform="rank")

        self.rank_tables = {}
        for i, lv in enumerate(core.LEVELS):
            cell = tk.Frame(grid, bg=C_BG)
            cell.grid(row=0, column=i, sticky="nwe",
                      padx=(0 if i == 0 else sc(12), 0))
            # cell 被 grid 拉宽了，但里面的表格是 pack 的：不给 column 权重，
            # 表格只占它自己请求的宽度，右边留一条空档，像没画完。
            cell.columnconfigure(0, weight=1)
            self._section(cell, core.LEVEL_CN[lv],
                          LEVEL_ACCENT[lv][0]).grid(row=0, column=0, sticky="w")
            holder = tk.Frame(cell, bg=C_LINE)
            holder.grid(row=1, column=0, sticky="ew", pady=(sc(6), 0))
            cols = [
                ("rank", "#", 44, "e", "int"),
                ("time", "time", 62, "e", "f3"),
                ("bvs", "3BV/s", 62, "e", "f3"),
                ("stnb", "stnb", 56, "e", "f2"),
                ("bv", "3BV", 44, "e", "int"),
                ("style_cn", "风格", 52, "center", "str"),
            ]
            t = Table(holder, cols, height=10, fit=False)
            t.pack(fill="x", padx=1, pady=1)
            # 三个指标列吸收多余宽度，表就铺满这一格，右端不会留一条空档；
            # 窗口变窄时也是它们先收缩，名次和 3BV 不会第一个被裁掉。
            for key in ("time", "bvs", "stnb"):
                t.tv.column(key, stretch=True)
            self.rank_tables[lv] = t
        page.bind("<Configure>", lambda _e: self._rank_fit())
        self.set_rank_metric(self._rank_metric)

    def set_rank_metric(self, key):
        self._rank_metric = key
        for k, p in self.rank_metric_seg.items():
            p.set_selected(k == key)
        self.render_rank()

    def set_rank_style(self, key):
        self._rank_style = key
        for k, p in self.rank_style_seg.items():
            p.set_selected(k == key)
        self.render_rank()

    def _on_rank_n(self, settle_focus=True):
        raw = (self.var_rank_n.get() or "").strip()
        if raw in ("全部", "所有", "all"):
            n = 0
        else:
            try:
                n = max(0, int(float(raw)))
            except ValueError:
                return              # 空着或正在改，别把用户没输完的内容顶回去
        if n != self._rank_n:
            self._rank_n = n
            save_config(rank_n=n)   # 下次打开还是这个范围
            self.render_rank()
        # 提交后两件事一起做，缺一个都会被用户看见：
        #   1) Tk 在 <<ComboboxSelected>> 后把文字整段选中（灰底），要清掉；
        #   2) 回车/选下拉项时把焦点交出去，别一直闪光标。
        # 失焦触发时焦点本就在走，不抢它（否则抢走用户正要点的地方）。
        self.rank_n_box.selection_clear()
        if settle_focus:
            self.rank_n_box.tk_focusNext().focus_set()

    def render_rank(self):
        """三级别各取前 N 局；N=0 时放开到这个级别的全部录像。"""
        if not getattr(self, "rank_tables", None):
            return
        self._rank_fit_tries = 0        # 每次重画都重新给「等排版」的预算
        metric = self._rank_metric
        style = None if self._rank_style == "all" else "nf"
        for lv, t in self.rank_tables.items():
            # 清掉上一次点表头留下的排序键和箭头：榜的名次由 core.top_n 定，
            # 不能让残留的 sort_key 在 set_rows 时把它重排成别的顺序。
            t._sort_key = None
            for key, title, _w, _a, _k in t.columns_def:
                t.tv.heading(key, text=title)
            pool = core.top_n(self.games, lv, metric,
                              core.BV_MIN_DEFAULT[lv], style, self._rank_n)
            rows = []
            for i, g in enumerate(pool, 1):
                rows.append({
                    "rank": i,
                    "time": g.get("time") or 0,
                    "bvs": g.get("bvs") or 0,
                    "stnb": g.get("stnb") or 0,
                    "bv": g.get("bv") or 0,
                    "style_cn": core.STYLE_CN.get(g.get("style"), ""),
                    "path": g.get("path") or "",
                })
            t.set_rows(rows)
            # 换指标/风格/条数后视图必须回顶：Treeview 换数据时保留旧的
            # yview，看过「全部」再切回 Top50 会卡在半截，只见 #35–49。
            t.tv.yview_moveto(0)
        self._rank_fit()
        # 刚切到这一页时 page 的高度还是上一次布局的值（或半截），
        # 量出来只有三四行；<Configure> 又恰好不会再触发。隔一拍补量。
        if self._rank_fit_job is None:
            self._rank_fit_job = self.after(60, self._rank_fit_again)

    def _rank_fit(self):
        """表格高度 = 数据行数，最多到页面放得下；不拉出一截空框。

        每列行数可能差很多（全部时初/中/高 1 万/1 万/400），
        但页面高度只够显示一屏 —— 超出部分留给滚动条，绝不让表格撑满。

        可用高度必须**量**出来，不能用常数推：每列表格上方还有 40px 的级别
        标题和边距，行高也随缩放变（1.5 倍屏上一行 48px，不是 45px）。
        以前按常数算，多算出一行，最后一行正好被挤出可视区。
        """
        if not getattr(self, "rank_tables", None):
            return
        page = self.pages["rank"]
        ph = page.winfo_height()
        top = self.rank_grid.winfo_y()  # 工具条占掉的高度（含页面上下 padding）
        if ph < sc(120) or top <= 0:    # 页面刚切过来，尺寸还没定，过一拍再量
            self._rank_fit_retry()
            return
        avail = ph - top - sc(8)        # 三张表一共能用的高度，底部留一点空
        for t in self.rank_tables.values():
            tv = t.tv
            cell = t.master.master      # holder -> cell（cell 里还有级别标题）
            head, row_h = sc(47), max(1, sc(32))
            kids = tv.get_children()
            if kids:
                bb = tv.bbox(kids[0])   # bbox 的 y 是表头高，h 是一行的高
                if bb:
                    head, row_h = bb[1], max(1, bb[3])
            # 表格上方只有级别标题 + 6px 间距。不能拿 cell 高减 tv 高：
            # 表格还没画出来时 tv 的高度是 1，减出来是一个几百像素的假标题，
            # 能把一屏 15 行压成 4 行。
            gb = cell.grid_bbox(0, 0)
            extra = (gb[3] + sc(6)) if gb and gb[3] > sc(10) else sc(36)
            cap = max(3, int((avail - extra - head) // row_h))
            n = len(t.rows)
            tv.configure(height=min(n, cap) if n else 3)
        self._rank_fit_tries = 0

    def _rank_fit_retry(self):
        """页面刚切过来时尺寸还没定，隔一拍再量；只重试几拍，别空转。"""
        if not self.winfo_exists() or self._rank_fit_tries >= 12:
            return
        if self._rank_fit_job is not None:
            return
        self._rank_fit_tries += 1
        self._rank_fit_job = self.after(40, self._rank_fit_again)

    def _rank_fit_again(self):
        self._rank_fit_job = None
        if self.winfo_exists():
            self._rank_fit()

    # ---------- 右键详情 ----------
    def show_game_detail(self, path):
        """把这一局摊开看：成绩、同 3BV / 同级里的位置、来源与文件名。

        只读扫描结果，不重演、不起元扫雷 —— 多数时候只是想看一眼，不是要播。
        """
        game = self._find_game(path)
        if game is None:
            self.set_status("这一行没有对应的本地录像", "dim")
            return
        old = getattr(self, "_detail_win", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
        self._detail_win = GameDetail(self, game)

    def _find_game(self, path):
        if not path:
            return None
        for g in self.games:
            if g.get("path") == path:
                return g
        return None

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
        # 标识放最右：先 pack 的在最右，数量少、位置固定，标识再多也不挤状态行
        self.lbl_idents = tk.Label(head, text="", bg=C_BG, fg=C_ACCENT2,
                                   font=FONT_SMALL, cursor="hand2")
        self.lbl_idents.pack(side="right", padx=(sc(14), 0))
        self.lbl_idents.bind("<Button-1>", lambda _e: self._open_idents())
        self.lbl_quota = tk.Label(head, text="", bg=C_BG, fg=C_DIM, font=FONT_SMALL)
        self.lbl_quota.pack(side="right")

        # 第二行：范围（可多选叠加）
        row = tk.Frame(page, bg=C_BG)
        row.pack(fill="x", pady=(0, sc(6)))
        self.row_scope = row
        # 读取网站记录的进度：几千条明细要分几十批取，十几秒里没动静像卡住了。
        # 只在读的时候插到账号行下面（见 _on_site_prog），读完立刻收回。
        self.site_prog = tk.Frame(page, bg=C_BG)
        self.pb_site = ttk.Progressbar(self.site_prog,
                                       style="Oms.Site.Horizontal.TProgressbar",
                                       length=sc(220), mode="indeterminate",
                                       maximum=100, value=0)
        self.pb_site.pack(side="left")
        self.lbl_site_prog = tk.Label(self.site_prog, text="", bg=C_BG,
                                      fg=C_DIM, font=FONT_SMALL)
        self.lbl_site_prog.pack(side="left", padx=(sc(8), 0))
        self._site_prog_on = False
        self._site_prog_det = False
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
        self.row_up_prog = prog
        self.pb_up = ttk.Progressbar(prog, style="Oms.Horizontal.TProgressbar",
                                     length=sc(220), mode="determinate", maximum=1)
        self.pb_up.pack(side="left")
        self.lbl_up_prog = tk.Label(prog, text="", bg=C_BG, fg=C_DIM,
                                    font=FONT_SMALL)
        self.lbl_up_prog.pack(side="left", padx=(sc(8), 0))
        prog.pack_forget()              # 没在传的时候不占一行（传完就收回）

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

    # ---------- 自动上传页 ----------
    def _build_auto(self):
        page = self.pages["auto"]

        head = tk.Frame(page, bg=C_BG)
        head.pack(fill="x", pady=(sc(12), sc(12)))
        self.btn_auto = Pill(head, "", command=self.toggle_auto_upload,
                             accent=C_OK, padx=20, height=34)
        self.btn_auto.pack(side="left")
        self.lbl_auto_acct = tk.Label(head, text="", bg=C_BG, fg=C_DIM,
                                      font=FONT_SMALL)
        self.lbl_auto_acct.pack(side="left", padx=(sc(14), 0))

        # 五个开关横着排一行：左边四个管「传什么」，最右边那个管「什么时候开始」，
        # 靠一段空档分开，不另起一行占高度。
        row = tk.Frame(page, bg=C_BG)
        row.pack(fill="x", pady=(0, sc(10)))
        chk_kw = dict(bg=C_BG, fg=C_TEXT, activebackground=C_BG,
                      activeforeground=C_TEXT, selectcolor=C_PANEL,
                      font=FONT_UI, bd=0, highlightthickness=0, anchor="w")
        self.auto_vars = {}
        self.auto_checks = {}
        for key, _scopes, label in autoup.GROUPS:
            var = tk.BooleanVar(value=bool(self.auto_flags.get(key)))
            self.auto_vars[key] = var
            cb = tk.Checkbutton(row, text=label, variable=var,
                                command=self.toggle_auto_scope,
                                disabledforeground=C_DIM, **chk_kw)
            cb.pack(side="left", padx=(0, sc(26)))
            self.auto_checks[key] = cb

        # 第四个范围：新出现的完成局一律传。它覆盖前面三项（不是叠加），所以
        # 勾上时那三项变灰 —— 勾选状态留着，取消这里之后原样回来。
        self.var_auto_all = tk.BooleanVar(value=bool(self._auto_all))
        self.chk_auto_all = tk.Checkbutton(
            row, text="所有新局", variable=self.var_auto_all,
            command=self.toggle_auto_all, **chk_kw)
        self.chk_auto_all.pack(side="left", padx=(0, sc(26)))

        self.var_auto_start = tk.BooleanVar(value=bool(self._auto_start))
        tk.Checkbutton(
            row, text="每次打开工具箱都自动开始上传",
            variable=self.var_auto_start, command=self.toggle_auto_start,
            **chk_kw).pack(side="left", padx=(sc(36), 0))

        self.lbl_auto_status = tk.Label(page, text="", bg=C_BG, fg=C_DIM,
                                        font=FONT_SMALL, anchor="w", justify="left")
        self.lbl_auto_status.pack(fill="x", pady=(sc(8), sc(8)))

        foot = tk.Frame(page, bg=C_BG)
        foot.pack(side="bottom", fill="x", pady=(sc(8), sc(2)))
        self.btn_auto_check = Pill(foot, "立即检查一次", command=self.check_auto_now,
                                   accent=C_ACCENT2, padx=16, height=32)
        self.btn_auto_check.pack(side="left")
        self.btn_auto_clear = Pill(foot, "清空记录", command=self.clear_auto_log,
                                   accent=C_DIM, padx=14, height=32)
        self.btn_auto_clear.pack(side="left", padx=(sc(8), 0))
        self.lbl_auto_stat = tk.Label(foot, text="", bg=C_BG, fg=C_DIM,
                                      font=FONT_SMALL, anchor="e")
        self.lbl_auto_stat.pack(side="right")

        # 结果排在长文本列前面：为什么不传/传成什么样，先看到。备注只写短写
        # （pb -1.164s / 涨分 3BV/s 第4），完整理由在下方详情条里。
        cols = [
            ("t", "时刻", 108, "center", "str"),
            ("level_cn", "级别", 56, "center", "str"),
            ("time", "time", 74, "e", "f3"),
            ("bv", "3BV", 48, "e", "int"),
            ("result", "结果", 190, "center", "str"),
            ("note", "备注", 420, "w", "str"),
        ]
        holder, self.tbl_auto = self._table(page, cols, height=6, fit=True,
                                           min_rows=2)
        # 表格能滚，就别钉在 6 行：下面空着的整块给表格，记录多了能一屏多看
        # 几条。详情条的高度单独留出来（见 _refresh_auto_view），不会被挤掉。
        holder.pack_forget()
        holder.pack(fill="both", expand=True)

        # 点一行在这里展开完整理由。备注列只放得下短写，一条局既涨分又是 PB
        # 时原因有四五条 —— 表格保持一行一局，完整理由单独一行一条看。
        # 详情放在表格内部（tv 下一行），才会紧贴表格下沿；放 page 级用
        # side=bottom 会被先 pack 的 foot 顶到窗口最底部，中间空一大段。
        #
        # 用「左边榜名 + 右边成绩」两列而不是一整段文本：理由长短差很多，
        # 一条长句里还夹着「·」分隔符，堆在一起每行行首都不一样，读起来上下
        # 跳。分列之后左列宽度由最长的榜名决定，右边的成绩全部从同一条竖线开始。
        self.frm_auto_detail = tk.Frame(self.tbl_auto, bg=C_PANEL)
        self.frm_auto_detail.grid(row=1, column=0, columnspan=2, sticky="ew",
                                  padx=sc(4), pady=(sc(3), sc(4)))
        self.frm_auto_detail.columnconfigure(1, weight=1)
        self._auto_rows = []          # [(榜名 Label, 成绩 Label)]
        self._auto_lines = 1
        # 高度自适应时把这行让出来，否则行数按整帧算，最后一行会盖住详情
        self.tbl_auto.extra_bottom = self.frm_auto_detail

        self.tbl_auto.tv.bind("<<TreeviewSelect>>",
                              self._on_auto_select, add="+")
        self._refresh_auto_view()

    def _auto_scopes(self):
        """当前生效的范围。全量开着时它覆盖三项，但不改三项的保存值。"""
        if self._auto_all:
            return (autoup.ALL,)
        return autoup.scopes_of(self.auto_flags)

    def _sync_auto_scope_controls(self):
        """全量开着时把三项变灰（勾选状态留着，不点掉）。"""
        state = "disabled" if self._auto_all else "normal"
        for cb in self.auto_checks.values():
            cb.config(state=state)

    def toggle_auto_upload(self):
        self.set_auto_upload(not self._auto_on)

    def toggle_auto_start(self):
        """「每次打开工具箱都自动开始上传」这个选项本身。

        勾上如果当前还没在传，就顺手开起来 —— 用户勾它是想传，不是想让
        开关停在「已勾上但关闭」这种自相矛盾的状态。
        """
        self._auto_start = bool(self.var_auto_start.get())
        save_config(auto_start=self._auto_start)
        diag.log.info("自动上传：打开工具箱就自动开始 = %s", self._auto_start)
        if self._auto_start and not self._auto_on:
            self.set_auto_upload(True)
        else:
            self._refresh_auto_view()

    def set_auto_upload(self, on):
        self._auto_on = bool(on)
        if self._auto_on:
            self._auto_stop = ""
            self._auto_backoff = 0.0
            # 换一个 tracker，以当前水位重建基线：历史成绩一律不补传，但
            # 「关机期间打的局」（mtime 高于水位）进候选池，等下一轮扫描确认。
            # games 还没扫出来时保持未初始化，让第一轮扫描自己 prime。
            self._auto_tracker = autoup.Tracker(self._auto_watermark)
            if self.games:
                self._auto_tracker.prime(self.games)
            self._ng_tracker = autoup.Tracker(self._ng_watermark)
            if self.ng_games:
                self._ng_tracker.prime(self.ng_games)
            diag.log.info("自动上传：开启（水位 %s，范围 %s）",
                          self._auto_watermark,
                          "、".join(autoup.SCOPE_CN.get(s, s)
                                   for s in self._auto_scopes()) or "未选择")
            self.after(150, self._auto_tick)
        else:
            diag.log.info("自动上传：关闭")
        self._refresh_auto_view()

    def toggle_auto_scope(self):
        """三项按成绩筛的范围。全量开着时点不动（控件已 disabled），
        真被程序调用也先把勾选还原，避免把用户的偏好改掉。"""
        if self._auto_all:
            for k, v in self.auto_vars.items():
                v.set(bool(self.auto_flags.get(k)))
            return
        self.auto_flags = {k: bool(v.get()) for k, v in self.auto_vars.items()}
        scopes = autoup.scopes_of(self.auto_flags)
        save_config(auto_scopes=list(scopes))
        diag.log.info("自动上传范围：%s",
                      "、".join(autoup.SCOPE_CN.get(s, s) for s in scopes)
                      or "未选择")
        self._refresh_auto_view()
        self.after(150, self._auto_tick)

    def toggle_auto_all(self):
        """「全部完成局」开关本身：只存 auto_all，不动三项的勾选与保存值。

        切完立刻按新范围判一次已经等着的候选；不重建 Tracker（历史局不补传），
        也不把暂停改成开始 —— 暂停时 _auto_tick 会自己返回。
        """
        self._auto_all = bool(self.var_auto_all.get())
        save_config(auto_all=self._auto_all)
        self._sync_auto_scope_controls()
        diag.log.info("自动上传范围：%s（三项偏好保持 %s）",
                      "、".join(autoup.SCOPE_CN.get(s, s)
                               for s in self._auto_scopes()) or "未选择",
                      autoup.scopes_of(self.auto_flags) or "未选择")
        self._refresh_auto_view()
        self.after(150, self._auto_tick)

    def toggle_ng_scope(self):
        """强无猜上传范围（设置里的子菜单）。只存 ng_scope，不动标准模式的范围。

        切到非关闭时以当前水位重建 ng 基线：历史强无猜局不补传，关机期间打的
        补进来。它跟着自动上传总开关走 —— 总开关关着时选了也不会传。
        """
        self._ng_scope = str(self.ng_scope_var.get() or "")
        save_config(ng_scope=self._ng_scope)
        self._ng_tracker = autoup.Tracker(self._ng_watermark)
        if self.ng_games:
            self._ng_tracker.prime(self.ng_games)
        diag.log.info("强无猜上传：%s",
                      {"": "关", "top": "进榜才传", "all": "全部上传"}
                      .get(self._ng_scope, self._ng_scope))
        # 进榜才传需要强无猜榜成绩：选上就补读一次（已登录时）
        if self._ng_scope == "top" and self.oms_user:
            self.refresh_site()
        self._refresh_auto_view()
        self.after(150, self._auto_tick)

    def _auto_ready_reason(self):
        """现在能不能传（用户视角的原因），空字符串表示可以。"""
        if not self._auto_on:
            return "自动上传已关闭"
        scopes = self._auto_scopes()
        ng = self._ng_scope
        if not scopes and not ng:
            return "还没勾选要传什么"
        if not self._watch_enabled:
            return "自动刷新关着，看不到新录像"
        if not self.oms_user:
            return "还没登录 openms.top"
        # 全量与强无猜全上传都不对照成绩，唯一的去重依据是网站录像列表：必须等
        # 当前这一代请求把列表完整拉回来。PB / Top5 到没到跟它们无关。
        if scopes == (autoup.ALL,) or ng == "all":
            if self._auto_acct_seq != self._site_seq or not self.oms_loaded:
                return "正在读取你在网站上的录像记录…"
            if not self.oms_synced:
                return "读取网站录像记录失败，稍后自动重试"
        elif scopes and not autoup.ready(
                scopes, self.site_pb, self.site_top,
                pb_ok=(self.site_pb_uid is not None and not self.site_pb_err),
                top_ok=(self.site_top_uid is not None
                        and not self.site_top_err)):
            return "还没读到你在网站上的成绩"
        if ng == "top" and (self.site_ng_uid is None or self.site_ng_err):
            return "还没读到你的强无猜榜成绩"
        if not self.games and not (ng and self.ng_games):
            return "还没找到录像，先确认一下路径"
        return ""

    def _refresh_auto_status(self):
        if not hasattr(self, "lbl_auto_status"):
            return
        self._sync_auto_scope_controls()
        self.btn_auto.set_text("暂停自动上传" if self._auto_on else "开始自动上传")
        self.btn_auto.set_selected(self._auto_on)
        name = (self.oms_user or {}).get("username") or ""
        self.lbl_auto_acct.config(
            text=("账号：%s · 已登录" % name) if name else "账号：未登录",
            fg=C_OK if name else C_WARN)

        why = self._auto_stop or self._auto_ready_reason()
        left = time.monotonic() - self._auto_backoff
        if left < 0:
            why = "网络不稳，%d 秒后再试" % max(1, int(-left))
        if why:
            txt, color = why, C_WARN
        else:
            # 一切正常时什么都不用说。留着这个 label 是为了出问题时那行原因
            # 出现不会把下面的表格顶下去。
            txt, color = "", C_DIM
        self.lbl_auto_status.config(text=txt, fg=color)

        n_log = len(autolog.items())
        self.lbl_auto_stat.config(
            text="本次已传 %d 条 · 记录 %d 条" % (self._auto_session, n_log))

    def _refresh_auto_view(self):
        if not hasattr(self, "tbl_auto"):
            return
        rows = []
        for x in autolog.items():
            r = dict(x)
            if not r.get("note"):
                # 老记录只存了长句，短写从长句里抠（见 autoup.briefs）
                r["note"] = _auto_note(autoup.briefs(r.get("why") or ""))
            rows.append(r)
        self.tbl_auto.set_rows(rows)
        # 详情条的高度按「这批记录里理由最多的一条」定死：选中不同行时表格
        # 不会忽高忽低，也不会把详情条挤掉。理由最多几条是数得出来的。
        # 至少留一行：全量模式不写理由，整屏都是空的时候也不能让这条塌掉，
        # 否则一点某一行它又长出来，表格跟着跳。
        self._auto_lines = max([1] + [len(_auto_reasons(r)) for r in rows])
        self._auto_detail_rows(self._auto_lines)
        self.frm_auto_detail.update_idletasks()
        self.tbl_auto.refit()
        self._refresh_auto_status()
        # 刷新会把选中清掉：默认停在最新一条上，详情条不留空
        if rows and not self.tbl_auto.tv.selection():
            self.tbl_auto.tv.selection_set("0")
        self._on_auto_select()      # 刷新后把详情对齐到仍选中（或清空）的那行

    def _auto_detail_rows(self, n):
        """把详情条调成正好 n 行；行数没变时复用原来的控件，只改文字。"""
        rows = self._auto_rows
        while len(rows) < n:
            i = len(rows)
            top = sc(2) if i == 0 else 0
            tag = tk.Label(self.frm_auto_detail, text="", bg=C_PANEL, fg=C_DIM,
                           font=FONT_SMALL, anchor="nw")
            txt = tk.Label(self.frm_auto_detail, text="", bg=C_PANEL, fg=C_TEXT,
                           font=FONT_SMALL, anchor="nw", justify="left")
            tag.grid(row=i, column=0, sticky="nw", padx=(sc(8), sc(10)),
                     pady=(top, 0))
            txt.grid(row=i, column=1, sticky="nw", padx=(0, sc(8)),
                     pady=(top, 0))
            rows.append((tag, txt))
        while len(rows) > n:
            for lb in rows.pop():
                lb.destroy()
        return rows

    def _on_auto_select(self, _e=None):
        """选中一行 → 在表格下方展开完整理由，一条理由一行。

        记录里的 why 是写日志时用「；」拼起来的（见 _auto_record），这里拆回
        多条，再按「榜名 / 成绩」分两列摆。没有选中（或表刚刷新）就把文字清空，
        行数保持不变，表格不会跟着跳。
        """
        if not hasattr(self, "frm_auto_detail"):
            return
        parts = []
        sel = self.tbl_auto.tv.selection()
        if sel:
            try:
                parts = _auto_reasons(self.tbl_auto.rows[int(sel[0])])
            except (ValueError, IndexError):
                parts = []
        pairs = [_auto_why_pair(s) for s in parts] or [("", "—")]
        n = max(self._auto_lines, len(pairs))
        grew = n != len(self._auto_rows)
        rows = self._auto_detail_rows(n)
        for i, (tag, txt) in enumerate(rows):
            head, body = pairs[i] if i < len(pairs) else ("", "")
            tag.config(text=head, fg=C_DIM)
            txt.config(text=body)
        if grew:
            # 万一某条记录的理由比刷新时算出的还多，让表格重新扣一次高度，
            # 否则多出来的那行会被表格最后一行盖住。
            self._auto_lines = n
            self.frm_auto_detail.update_idletasks()
            self.tbl_auto.refit()

    def _auto_record(self, game, why, result, ok=False, brief=None):
        autolog.add({
            "level_cn": core.LEVEL_CN.get(game.get("level"), "?"),
            "time": game.get("time"),
            "bv": game.get("bv"),
            # 表格那一格只放短写（pb -1.164s / 涨分 3BV/s 第4），完整理由在
            # 下面的详情条里；why 留着给详情用。
            "note": _auto_note(brief if brief else autoup.briefs(
                "；".join(w for w in (why or []) if w))),
            "why": "；".join([w for w in (why or []) if w]) or "—",
            "result": result,
            "ok": bool(ok),
            "path": game.get("path") or "",     # 双击这行能回看刚传的那局
        })
        # 一批几十条时不能每条都重建表格、每条都重写一遍文件：合并成一次，
        # 间隔 120 ms 用户看不出来。autolog 那边同时把落盘也按时间合并了。
        if self._auto_flush_job is None:
            self._auto_flush_job = self.after(120, self._auto_flush_now)

    def _auto_flush_now(self):
        self._auto_flush_job = None
        autolog.flush()
        if getattr(self, "_tab", "") == "auto":
            self._refresh_auto_view()

    def _auto_save_watermark(self):
        mt = self._auto_tracker.newest(self.games)
        if mt and mt != self._auto_watermark:
            self._auto_watermark = mt
            save_config(auto_watermark=mt)
        ng_mt = self._ng_tracker.newest(self.ng_games)
        if ng_mt and ng_mt != self._ng_watermark:
            self._ng_watermark = ng_mt
            save_config(ng_watermark=ng_mt)

    def check_auto_now(self):
        if not self._auto_on:
            messagebox.showinfo(APP_TITLE, "自动上传还没开启。\n\n"
                                          "先点上面的按钮开启，再点这里。")
            return
        self.start_scan()

    def clear_auto_log(self):
        if not autolog.items():
            return
        if not messagebox.askyesno(
                APP_TITLE,
                "清空「最近自动上传」这张表？\n\n"
                "只清这份列表。网站上的录像不受影响，也不会因此重新上传。"):
            return
        if self._auto_flush_job is not None:
            self.after_cancel(self._auto_flush_job)
            self._auto_flush_job = None
        autolog.clear()
        self._refresh_auto_view()

    def _auto_handle_fatal(self, fatal, reason=""):
        """自动上传撞到账号级问题时暂停，不弹窗 —— 元扫雷可能正在前台跑。"""
        if not fatal:
            return
        if fatal == "net":
            self._auto_backoff = time.monotonic() + 300
            diag.log.warning("自动上传：连续网络失败，5 分钟后再试")
            return
        text = {"auth": "登录已失效，先重新登录",
                "quota": "网站的录像额度已经用完",
                "realname": "账号还没实名，网站不收",
                "identity": "账号还没通过上传准入",
                "banned": "账号被封禁"}.get(fatal)
        if fatal == "auth":
            # 与手动上传的收尾一致：会话作废，界面如实显示未登录
            self.oms.store.clear_session()
            self.oms_user = None
            self.oms_loaded = False
        if text:
            self._auto_stop = text
            diag.log.warning("自动上传暂停：%s", text)

    def _auto_tick(self):
        """自动上传的外部入口：判一次，并保证「还在等写稳」的局有下一拍。"""
        try:
            self._auto_tick_once()
        finally:
            self._arm_auto_tick()

    def _arm_auto_tick(self):
        """有局在等写稳就排一次闹钟。

        扫描是「有新文件才跑」的，一局落地之后不会有第二次扫描；而刚写完的
        录像要等 settle 秒才算写稳。只靠扫描驱动的话，这一局得等到下一局落地
        （或者用户手动点「立即检查」）才会被判定 —— 期间界面上什么也不发生。
        """
        if not self._auto_on or getattr(self, "_auto_job", None) is not None:
            return
        if self._up_running or self._scanning:
            return          # 这两种收尾时都会再叫一次 _auto_tick
        if not self._auto_tracker.pending() and not self._ng_tracker.pending():
            return
        delay = self._auto_tracker.settle * 1000 + 250
        left = (self._auto_backoff - time.monotonic()) * 1000
        self._auto_job = self.after(int(max(300, delay, left)), self._auto_beat)

    def _auto_beat(self):
        self._auto_job = None
        if self.winfo_exists():
            self._auto_tick()

    def _auto_tick_once(self):
        """每轮扫描之后跑一次：认出值得传的新局，交给上传线程。

        门控顺序就是「能不能判」的顺序：没开、正在传、正在扫、账号级暂停、
        网络退避、网站成绩没读到 —— 任何一条不满足都直接返回，不消费候选
        （候选留在 tracker 里，下一拍还拿得到）。
        """
        # 那行「还没读到你在网站上的成绩」是跟着账号、成绩、录像列表变化的：
        # 这一拍就算没有新局可判，也得重算一次。只在「有新局要判」时才刷，
        # 它会一直停在启动那一刻的说法上，切走再切回来才对。
        self._refresh_auto_status()
        if not self._auto_on or self._up_running or self._scanning:
            return
        if self._auto_stop or time.monotonic() < self._auto_backoff:
            return
        if not self.games and not (self._ng_scope and self.ng_games):
            return
        try:
            ready = self._auto_tracker.observe(self.games) if self.games else []
            ng_ready = (self._ng_tracker.observe(self.ng_games)
                        if self._ng_scope else [])
        except Exception as exc:            # noqa: BLE001  判定坏掉不能拖垮主流程
            diag.log.warning("自动上传：识别新录像失败：%s", exc)
            return
        if not ready and not ng_ready:
            return
        why_not = self._auto_ready_reason()
        if why_not:
            self._refresh_auto_status()
            return

        scopes = self._auto_scopes()
        # 跟上传页的「含阿比特录像」保持同一个开关：不勾就不采信阿比特的成绩。
        # 池子变了，PB / 涨分的比较基准也跟着变 —— 与上传页是一致的。
        games = autoup.pool(self.games, self.upload_arb)
        n_arb = len(self.games) - len(games)
        if n_arb and set(ready) & {g["path"] for g in self.games
                                   if (g.get("src") or "ms") == "arb"}:
            diag.log.info("自动上传：跳过阿比特录像（上传页没勾「含阿比特录像」）")
        cands = autoup.judge(games, scopes, set(ready),
                             site_pb=self.site_pb, site_top=self.site_top)
        if ng_ready:
            # 强无猜单独判：全上传走全量路径，进榜才传对照网站两榜的 personal best。
            cands.extend(autoup.judge_ng(self.ng_games, set(ng_ready),
                                         self._ng_scope, self.site_ng))
        keys = set()
        for v in self.oms_videos or []:
            k = openms.server_key(v)
            if k:
                keys.add(k)

        todo, why, brief = [], {}, {}
        for c in cands:
            g = c["game"]
            reason = autoup.blocked(g, self.oms_idents or None)
            if not reason:
                k = openms.game_key(g)
                if autoup.uploaded_before(g, keys) or (k and journal.has(k)):
                    reason = "网站上已经有这条了"
            if reason:
                self._auto_record(g, c["why"], "没传：" + reason, ok=False,
                                  brief=c.get("brief"))
                continue
            todo.append(c["path"])
            why[c["path"]] = c["why"]
            brief[c["path"]] = c.get("brief")

        # 判定完了才 commit：中途因为数据没就绪返回时，这局下次还能再判
        self._auto_tracker.commit(ready)
        self._ng_tracker.commit(ng_ready)
        self._auto_save_watermark()
        if not todo:
            self._refresh_auto_status()
            return
        diag.log.info("自动上传：%d 条新成绩符合范围", len(todo))
        self._start_upload(todo, silent=True, why=why, brief=brief)

    # ---------- 账号 ----------
    def open_login(self):
        LoginDialog(self, self.oms, self._on_login_done)

    def _on_login_done(self, user):
        self.oms_user = user
        # 刚登录：录像列表还没拉，先标成「未加载」，别让界面把 0 条当真
        self.oms_videos = []
        self.oms_idents = []
        self.oms_loaded = False
        self.oms_synced = False
        diag.log.info("登录成功：%s（实名=%s）", user.get("username"),
                      bool(user.get("realname")))
        # 换账号了：上一账号的网站 PB 不能留着
        self._site_seq += 1
        self.site_pb = []
        self.site_pb_uid = None
        self.site_pb_err = ""
        self.pb_counts = {}
        self._clear_site_top()
        self._clear_site_ng()
        self._site_pb_changed()
        self._site_top_changed()
        self.set_status("已登录 openms.top：%s" % (user.get("username") or ""), "ok")
        self._auto_stop = ""            # 重新登录后把「登录已失效」这类暂停解掉
        self._refresh_auto_view()
        self.refresh_site()

    def do_logout(self):
        if self._up_running:
            self.set_status("上传进行中，先等本批结束再退出登录", "warn")
            return
        if not messagebox.askyesno(
                APP_TITLE,
                "退出会清除本机保存的会话（Cookie），下次上传需要重新输验证码。\n继续吗？"):
            return
        self.oms.logout()
        diag.log.info("退出登录，已清除本机会话")
        self._site_seq += 1                 # 让还在路上的网站请求作废
        self._site_prog_hide()               # 那批请求的收尾事件已经被上面作废了
        self.oms_user = None
        self.oms_videos = []
        self.oms_idents = []
        self.oms_loaded = False
        self.site_pb = []
        self.site_pb_uid = None
        self.site_pb_err = ""
        self.pb_counts = {}
        self._clear_site_top()
        self._clear_site_ng()
        self._refresh_acct_label()
        self._mark_up_dirty()
        self._site_pb_changed()
        self._site_top_changed()
        self.set_status("已退出登录", "dim")

    def refresh_site(self):
        if not self.oms_user:
            self.open_login()
            return
        self.lbl_quota.config(text="正在读取网站记录…", fg=C_DIM)
        self._site_seq += 1
        threading.Thread(target=self._site_worker, args=(self._site_seq,),
                         daemon=True).start()

    def _restore_session(self):
        # 没保存过会话就别发匿名请求：离线启动时也少一次无意义的等待
        if not self.oms.has_session():
            return
        self._site_seq += 1
        threading.Thread(target=self._site_worker, args=(self._site_seq,),
                         daemon=True).start()

    def _lsf_start(self):
        """锚点跟登录无关，开机就取一次。"""
        threading.Thread(target=self._lsf_worker, daemon=True).start()

    def _lsf_worker(self):
        try:
            anchors = self.oms.top_anchors()
        except Exception as exc:                     # noqa: BLE001
            diag.log.warning("读取全站水平失败：%s", exc)
            return
        self._q.put(("lsf", ("anchors", anchors)))

    def _on_lsf(self, what, value=None):
        if what == "anchors":
            self.lsf_anchors = value or {}
            self.lsf_anchors_at = int(time.time())
            save_lsf_anchors(self.lsf_anchors)
            self._lsf_cache = None
            self._gain_cache = None        # 尺子换了，涨分跟着重算
            self.render_lsf()
            if hasattr(self, "tbl_recent"):
                self.render_recent()

    def _site_worker(self, seq=0):
        """包一层，只负责在开头/结尾通知界面「开始读 / 读完了」。

        读的过程有异常也不能把进度条留在屏幕上，所以收尾放 finally。
        """
        self._q.put(("site_prog", (seq, -1, 0)))
        try:
            self._site_load(seq)
        finally:
            self._q.put(("site_prog", (seq, -2, 0)))

    def _site_load(self, seq=0):
        """读登录态 + 网站 PB + 我的录像 + 我的标识。

        网站 PB 单独成一个事件：它只有一次轻量请求，先到先显示；录像列表
        要再分批补明细，慢得多，不该把 PB 表一起拖住。
        """
        try:
            u = self.oms.whoami()
        except openms.OmsError as exc:
            self._q.put(("acct", (seq, self.oms_user, self.oms_videos,
                                  self.oms_idents, "连不上 openms.top：%s" % exc)))
            return
        if not u:
            self._q.put(("site_pb", (seq, None, [], "")))
            self._q.put(("site_top", (seq, None, {}, {}, "", "")))
            self._q.put(("site_ng", (seq, None, {}, "")))
            self._q.put(("acct", (seq, None, [], [], "")))
            return

        # 登录态一确认就先把账号送到界面。录像列表在下面要串行拉几千条明细
        # （实测 2068 条花 11 秒），这期间界面若还写着「未登录」，点上传就会
        # 弹登录框 —— 而 Cookie 其实是好的。videos/idents 传 None 表示「还没拉」。
        self._q.put(("acct", (seq, u, None, None, "")))

        try:
            pb = self.oms.pb_records(u["id"])
            diag.log.info("网站 PB：%d 条", len(pb))
        except Exception as exc:                     # noqa: BLE001
            diag.log.warning("读取网站 PB 失败：%s", exc)
            # 右上角只放短句；完整异常进日志，免得把搜索框挤没
            self._q.put(("site_pb", (seq, u["id"], [], "网站 PB 读取失败")))
        else:
            # 小榜人数只用来给名次做分母，读不到就不显示百分位，不影响 PB 表
            counts = {}
            try:
                counts = self.oms.pb_counts()
            except Exception as exc:                 # noqa: BLE001
                diag.log.warning("读取小榜人数失败：%s", exc)
            self._q.put(("site_pb", (seq, u["id"], pb, "", counts)))

        try:
            top = self.oms.top_records(u["id"])
            ids = core.site_top_video_ids(top)
            diag.log.info("网站 Top5：%d 个小榜，%d 条录像", len(top), len(ids))
        except Exception as exc:                     # noqa: BLE001
            diag.log.warning("读取网站 Top5 失败：%s", exc)
            self._q.put(("site_top", (seq, u["id"], {}, {}, "网站 Top5 读取失败", "")))
        else:
            details, detail_err = {}, ""
            if ids:
                try:
                    details = self.oms.static_details(ids, cache=cache)
                except Exception as exc:             # noqa: BLE001
                    detail_err = "网站 Top5 明细读取失败"
                    diag.log.warning("读取网站 Top5 明细失败：%s", exc)
            self._q.put(("site_top", (seq, u["id"], top, details, "", detail_err)))

        # 强无猜榜只在「进榜才传」时才读：标准模式玩家不发这个请求。
        if self._ng_scope == "top":
            try:
                ng = self.oms.ng_records(u["id"])
                diag.log.info("强无猜榜：%s", "、".join(ng.keys()))
            except Exception as exc:                 # noqa: BLE001
                diag.log.warning("读取强无猜榜失败：%s", exc)
                self._q.put(("site_ng", (seq, u["id"], {}, "强无猜榜读取失败")))
            else:
                self._q.put(("site_ng", (seq, u["id"], ng, "")))

        vids, idents, err = [], [], ""
        try:
            vids = self.oms.videos(
                u["id"], progress=lambda done, total:
                self._q.put(("site_prog", (seq, done, total))), cache=cache)
            lack = sum(1 for v in vids if not openms.server_key(v))
            diag.log.info("网站录像列表：%d 条，其中 %d 条缺上去重明细", len(vids), lack)
        except Exception as exc:                     # noqa: BLE001
            err = "读取录像列表失败：%s" % exc
            diag.log.warning("读取网站录像列表失败：%s", exc)
        try:
            idents = self.oms.identifiers(u["id"])
        except Exception:                            # noqa: BLE001
            pass
        self._q.put(("acct", (seq, u, vids, idents, err)))

    def _on_site_prog(self, seq, done, total):
        """读取网站记录的进度条：-1 开始，-2 收尾，其余是「已读 / 总数」。

        列表没到之前报不出总数，先来回跑；明细是几十批串行取的，每批报一次。
        """
        if seq != self._site_seq:
            return                        # 退出/换号前发出的请求，结果作废
        if done == -2:
            self._site_prog_hide()
            return
        self._site_prog_show()
        if done < 0 or total <= 0:
            return
        if not self._site_prog_det:
            self._site_prog_det = True
            try:
                self.pb_site.stop()
            except tk.TclError:
                pass
            self.pb_site.config(mode="determinate", maximum=100, value=0)
        self.pb_site.config(value=min(100, done * 100 // total))
        self.lbl_site_prog.config(text="%d / %d" % (done, total))

    def _site_prog_show(self):
        if self._site_prog_on:
            return
        self._site_prog_on = True
        self._site_prog_det = False
        self.lbl_site_prog.config(text="")
        self.pb_site.config(mode="indeterminate", maximum=100, value=0)
        try:
            self.pb_site.start(14)
        except tk.TclError:
            pass
        self.site_prog.pack(fill="x", pady=(0, sc(6)), before=self.row_scope)

    def _site_prog_hide(self):
        self._site_prog_on = False
        self._site_prog_det = False
        try:
            self.pb_site.stop()
        except tk.TclError:
            pass
        self.pb_site.config(mode="indeterminate", value=0)
        self.lbl_site_prog.config(text="")
        self.site_prog.pack_forget()

    def _on_site_pb(self, seq, uid, rows, err, counts=None):
        if seq != self._site_seq:
            return                        # 退出/换号前发出的请求，结果作废
        if uid is None:
            self.site_pb = []
            self.site_pb_uid = None
            self.site_pb_err = ""
            self.pb_counts = {}
        else:
            cur = self.oms_user or {}
            if not cur and not self.oms.has_session():
                return                    # 已退出登录，迟到的结果不能复活网站 PB
            if cur.get("id") not in (None, uid):
                return                    # 账号已经换了，旧结果不能落盘
            self.site_pb = rows or []
            self.site_pb_uid = uid
            self.site_pb_err = err
            self.pb_counts = counts or {}
        self._site_pb_changed()

    def _site_pb_changed(self):
        """网站 PB 更新/清空后：PB 表与上传范围都要重算。"""
        self._recompute_pb()
        self._scope_want = None
        self._scope_skipped_pb = set()
        self._scope_skipped_top = set()
        if self._up_scopes:
            self.up_sel &= self._scope_path_set()
            self._refresh_up_view()
        # 网站成绩刚回来：之前因为「还没读到你的成绩」压着的候选，现在可以判了
        self.after(200, self._auto_tick)

    def _clear_site_top(self):
        self.site_top = {}
        self.site_top_details = {}
        self.site_top_uid = None
        self.site_top_err = ""
        self.site_top_detail_err = ""

    def _on_site_top(self, seq, uid, records, details, err, detail_err=""):
        if seq != self._site_seq:
            return                        # 退出/换号前发出的请求，结果作废
        if uid is None:
            self._clear_site_top()
        else:
            cur = self.oms_user or {}
            if not cur and not self.oms.has_session():
                return                    # 已退出登录，迟到的结果不能复活网站 Top5
            if cur.get("id") not in (None, uid):
                return                    # 账号已经换了，旧结果不能落盘
            self.site_top = records or {}
            self.site_top_details = details or {}
            self.site_top_uid = uid
            self.site_top_err = err
            self.site_top_detail_err = detail_err
        self._site_top_changed()

    def _site_top_changed(self):
        """网站 Top5 更新/清空后：前端成绩页与上传范围都要重算。"""
        self.render_top()
        self._scope_want = None
        self._scope_skipped_pb = set()
        self._scope_skipped_top = set()
        if self._up_scopes:
            self.up_sel &= self._scope_path_set()
            self._refresh_up_view()
        self.after(200, self._auto_tick)

    def _clear_site_ng(self):
        self.site_ng = {}
        self.site_ng_uid = None
        self.site_ng_err = ""

    def _on_site_ng(self, seq, uid, records, err):
        if seq != self._site_seq:
            return                        # 退出/换号前发出的请求，结果作废
        if uid is None:
            self._clear_site_ng()
        else:
            cur = self.oms_user or {}
            if not cur and not self.oms.has_session():
                return                    # 已退出登录，迟到的结果不能复活强无猜榜
            if cur.get("id") not in (None, uid):
                return                    # 账号已经换了，旧结果不能落盘
            self.site_ng = records or {}
            self.site_ng_uid = uid
            self.site_ng_err = err
        self.after(200, self._auto_tick)

    def _on_account(self, seq, user, videos, idents, err):
        if seq != self._site_seq:
            return                        # 退出/换号前发出的请求，结果作废
        # videos 为 None 表示「只带账号的早期事件」，录像列表与标识还没拉回来。
        # 这时绝不能写 oms_videos：空列表会被当成「网站一条都没有」，上传时
        # 把本地台账全判成未传 → 重复上传。
        partial = videos is None
        self.oms_user = user
        if not partial:
            self.oms_videos = videos or []
            self.oms_idents = idents or []
            self.oms_err = err
            self.oms_loaded = bool(user)
            # 只有真的拉到了列表，才敢拿它否认「本地台账」里的记录
            self.oms_synced = bool(user) and not err
        # 账号明确为空，或网站 PB/ Top5 属于另一个账号 → 清掉，避免把别人的成绩显示成本地
        if not user:
            if self.site_pb or self.site_pb_err:
                self.site_pb = []
                self.site_pb_uid = None
                self.site_pb_err = ""
                self._site_pb_changed()
            if self.site_top or self.site_top_err:
                self._clear_site_top()
                self._site_top_changed()
            if self.site_ng or self.site_ng_err:
                self._clear_site_ng()
        else:
            if self.site_pb_uid is not None and self.site_pb_uid != user.get("id"):
                self.site_pb = []
                self.site_pb_uid = None
                self.site_pb_err = ""
                self._site_pb_changed()
            if self.site_top_uid is not None and self.site_top_uid != user.get("id"):
                self._clear_site_top()
                self._site_top_changed()
            if self.site_ng_uid is not None and self.site_ng_uid != user.get("id"):
                self._clear_site_ng()
        self._refresh_acct_label()
        if not partial:
            self._mark_up_dirty()
            # 记下这次完整事件的代次：全量上传靠它确认「当前账号的录像列表
            # 已经到齐」，不依赖 PB / Top5 回调。
            self._auto_acct_seq = seq
            self.after(200, self._auto_tick)
        # Top5 通常比账号信息先到，顶部提示要等登录态落定后重画一次，
        # 否则会一直停在「未登录，仅显示本地成绩」
        self.render_top()

    def _refresh_acct_label(self):
        if self.oms_user:
            name = self.oms_user.get("username") or "?"
            self.lbl_acct.config(text="账号：%s · 已登录" % name, fg=C_OK)
            self.btn_login.set_enabled(False)
        else:
            self.lbl_acct.config(text="账号：未登录", fg=C_WARN)
            self.btn_login.set_enabled(True)
        self._refresh_idents_chip()
        bits = []
        if self.oms_user and not self.oms_loaded:
            # 账号已确认，录像列表还在路上（几千条要十几秒）
            bits.append("正在读取网站记录…")
        elif self.oms_user:
            bits.append("网站已有 %d 条" % len(self.oms_videos))
            # 站点改版后明细是单独批量取的；个别录像（未公开/无权限）拿不到解析字段，
            # 就比对不了去重三元组 —— 明说出来，别让人以为是「一条都没传过」。
            unknown = self._site_keyless()
            if unknown:
                bits.append("其中 %d 条缺少明细，按未上传处理" % unknown)
            if self.oms_user.get("realname"):
                bits.append("已实名")
            elif not self.oms_user.get("verified"):
                # 上传闸门：实名或玩家认证满足其一，否则站点直接拒（identity_access_required）
                bits.append("未实名且无玩家认证，上传会被拒")
        # 「本机记着传过 N 条」只在网站记录没读到的时候才说：那时候它是唯一依据
        # （断网重开不至于把传过的再传一遍）。平时网站列表已经说明一切，两个数字
        # 并排只会让人怀疑哪个是真的。
        n = journal.count()
        if n and self.oms_user and (self.oms_err or not self.oms_loaded):
            bits.append("网站记录还没读到，本机记着 %d 条已传" % n)
        if self.oms_err:
            bits.append(self.oms_err)
        self.lbl_quota.config(text=" · ".join(bits), fg=C_ERR if self.oms_err else C_DIM)

    def _site_keyless(self):
        """网站列表里拿不到 timems/bv/file_size 的条数（无法参与去重比对）。"""
        return sum(1 for v in self.oms_videos
                   if isinstance(v, dict) and not openms.server_key(v))

    # ---------- 标识 ----------
    def _refresh_idents_chip(self):
        """右上角只放「标识 ×N」。

        真实账号常有几十个标识（每换一次玩家名就多一个），全平铺出来会把整条状态行
        挤到屏幕外，等于什么也看不见。要看明细点这个胶囊。
        """
        n = len(self.oms_idents or [])
        if not self.oms_user or not self.oms_loaded:
            self.lbl_idents.config(text="", cursor="arrow")
        elif n:
            self.lbl_idents.config(text="标识 ×%d ▸" % n, fg=C_ACCENT2,
                                   cursor="hand2")
        else:
            self.lbl_idents.config(text="无标识", fg=C_DIM, cursor="arrow")

    def _open_idents(self):
        if not (self.oms_user and self.oms_loaded):
            return
        IdentsDialog(self, self.oms_idents or [],
                     self.oms_user.get("username") or "")

    # ---------- 上传：选范围 / 选行 ----------
    def _mark_up_dirty(self):
        """数据变了：上传页那些行作废。

        正在看上传页就立刻重建（不然眼前是旧表），否则只打个标记 ——
        2 万局构造 2 万行要 0.5 s，人不在这一页时算它就是白烧。
        """
        self._up_dirty = True
        if getattr(self, "_tab", "") == "upload":
            self._ensure_up_rows()

    def _ensure_up_rows(self):
        if getattr(self, "_up_dirty", True):
            self._rebuild_up_rows()

    def _rebuild_up_rows(self):
        self._up_dirty = False
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
                "level": g["level"],
                "level_cn": core.LEVEL_CN[g["level"]],
                "style_cn": core.STYLE_CN.get(g.get("style"), "?"),
                "src_cn": core.SRC_CN.get(src, "元扫雷"),
                "time": g["time"],
                "bv": g["bv"],
                "size": g.get("size") or 0,      # 字节，确认框算合计大小用
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
        self._scope_want = None          # 录像重扫过，范围对应的路径要重算
        self._scope_skipped_pb = set()
        self._scope_skipped_top = set()
        self._refresh_up_view()

    def _refresh_up_view(self):
        """表格里只放「当前范围」的行。

        几万局全铺在表里没有意义：用户眼睛只看得见前几行，真正关心的是自己
        刚选的那批。所以选了范围 Pill，表里就只剩那几个范围的录像（含已上传的，
        好让人看出这批里哪些已经在网站上）。没有选范围时才显示全部。

        `up_rows` 始终是全集，逻辑判断（待传集合、重试）都用它；
        `up_view` 只是显示子集，别拿它当数据源。
        """
        if self._up_scopes:
            want = self._scope_path_set()
            rows = [r for r in self.up_rows if r["path"] in want]
            notes = []
            if not rows:
                notes.append(self._empty_view_note(want))
            else:
                notes.extend(self._skipped_notes())
            for site_note in (self._site_pb_scope_note(),
                              self._site_top_scope_note()):
                if site_note:
                    notes.append(site_note)
            self._view_note = " · ".join(notes)
        else:
            rows = list(self.up_rows)
            self._view_note = ""
        self.up_view = rows
        self.tbl_up.set_rows(rows)
        self.tbl_up.select_paths(self.up_sel)
        self._update_up_stat()

    def _skipped_notes(self):
        """范围里被网站更快成绩压掉、没有进入勾选的本地局。"""
        notes = []
        if self._scope_skipped_pb:
            notes.append("已排除 %d 条本地 PB（网站 PB 更快）"
                         % len(self._scope_skipped_pb))
        if self._scope_skipped_top:
            notes.append("已排除 %d 条本地成绩（网站 Top5 更快）"
                         % len(self._scope_skipped_top))
        return notes

    def _empty_view_note(self, want):
        """范围过滤后一行都不剩时，说清是「被网站成绩排除」「被阿比特挡掉」还是真的没有。"""
        bits = self._skipped_notes()
        if not self.upload_arb and self._arb_rows:
            for g in self.games:
                if ((g.get("src") or "ms") == "arb"
                        and g["path"] in want):
                    bits.append("阿比特录像已排除，勾上「含阿比特录像」才看得到")
                    break
        return "；".join(bits) if bits else "这个范围没有可显示的录像"

    def _site_pb_ready(self):
        """网站 PB 是否可用于当前账号的上传范围对照。"""
        if self.site_pb_err or self.site_pb_uid is None:
            return False
        uid = (self.oms_user or {}).get("id")
        return uid is None or uid == self.site_pb_uid

    def _site_pb_scope_note(self):
        """PB 范围没能和网站对比时，明说原因，别让人以为已经排除过了。"""
        if not (self._up_scopes & {"pb", "pb_nf"}):
            return ""
        if self.site_pb_err:
            return "网站 PB 读取失败，未与网站 PB 对比"
        if self._site_pb_ready():
            return ""
        if not self.oms_user:
            return "未登录，未与网站 PB 对比"
        return "网站 PB 尚未读取，未与网站 PB 对比"

    def _site_top_ready(self):
        """网站 Top5 是否可用于当前账号的上传范围对照。"""
        if self.site_top_err or self.site_top_uid is None or not self.oms_user:
            return False        # Top5 可能先到，账号未落定前不并入页面
        return self.oms_user.get("id") == self.site_top_uid

    def _site_top_scope_note(self):
        """Top5 范围没能和网站对比时，明说原因。"""
        if not (self._up_scopes & {"top", "top_nf"}):
            return ""
        if self.site_top_err:
            return "网站 Top5 读取失败，未与网站 Top5 对比"
        if self._site_top_ready():
            return ""
        if not self.oms_user:
            return "未登录，未与网站 Top5 对比"
        return "网站 Top5 尚未读取，未与网站 Top5 对比"

    def _scope_path_set(self):
        """当前选中范围对应的路径集合（缓存在 _scope_want，top5 算起来不便宜）。

        登录后两个 PB 范围对照网站 PB：同一（级别 × BV × 榜）里本地 PB
        比网站慢的直接排除；同速保留。两个 Top5 范围对照网站 Top5：网站
        已有五条更好成绩时，本地局挤不进前五，也直接排除。排除的路径
        分别记在 ``_scope_skipped_pb`` / ``_scope_skipped_top``，只统计
        仍未上传的那部分。
        """
        self._ensure_up_rows()
        want = getattr(self, "_scope_want", None)
        if want is None:
            want = set()
            skipped_pb, skipped_top = set(), set()
            site_pb = self.site_pb if self._site_pb_ready() else []
            site_top = self.site_top if self._site_top_ready() else {}
            for s in self._up_scopes:
                skipped = skipped_top if s.startswith("top") else skipped_pb
                want |= openms.scope_paths(self.games, s,
                                           site_pb=site_pb,
                                           site_top=site_top,
                                           skipped=skipped)
            pending = {r["path"] for r in self.up_rows if not r.get("uploaded")}
            self._scope_skipped_pb = (skipped_pb - want) & pending
            self._scope_skipped_top = (skipped_top - want) & pending
            self._scope_want = want
        return want

    def toggle_upload_arb(self):
        self.upload_arb = bool(self.var_arb.get())
        save_config(upload_arb=self.upload_arb)
        diag.log.info("上传包含阿比特录像：%s", self.upload_arb)
        self._rebuild_up_rows()         # 用户就在这一页点了勾，必须当场变

    def _up_stat_text(self):
        self._ensure_up_rows()
        total = len(self.up_rows)
        up = sum(1 for r in self.up_rows if r.get("uploaded"))
        txt = "本地 %d 个完成局 · 网站已有 %d · 未上传 %d" % (total, up, total - up)
        if self._up_scopes:
            v_pending = sum(1 for r in self.up_view if not r.get("uploaded"))
            txt += " · 当前范围 %d 行（待传 %d）" % (len(self.up_view), v_pending)
            if getattr(self, "_view_note", ""):
                txt += " · " + self._view_note
        txt += " · 已选 %d" % len(self.up_sel)
        arb = getattr(self, "_arb_rows", 0)
        if arb:
            txt += "（阿比特 %d 个，%s）" % (
                arb, "在本列表里" if self.upload_arb else "已排除，不传")
        return txt

    def _view_pending(self):
        """当前表格视图里的待传路径。范围过滤后，「上传全部未传」只算眼前这些。"""
        self._ensure_up_rows()
        return {r["path"] for r in self.up_view if not r.get("uploaded")}

    def _update_up_stat(self):
        pending = {r["path"] for r in self.up_rows if not r.get("uploaded")}
        self.up_sel &= pending
        self.lbl_up_stat.config(text=self._up_stat_text())
        self.btn_up_sel.set_text("上传选中 (%d)" % len(self.up_sel))
        self.btn_up_all.set_text("上传全部未传 (%d)" % len(self._view_pending()))

    def _on_up_select(self):
        self._ensure_up_rows()
        self.up_sel = set(self.tbl_up.selected_paths())
        self.lbl_up_stat.config(text=self._up_stat_text())
        self.btn_up_sel.set_text("上传选中 (%d)" % len(self.up_sel))

    def toggle_scope(self, key):
        if key in self._up_scopes:
            self._up_scopes.discard(key)
        else:
            self._up_scopes.add(key)
        self._scope_want = None          # 范围变了，缓存作废
        for k, p in self.scope_pills.items():
            p.set_selected(k in self._up_scopes)
        self.select_by_scope()

    def select_by_scope(self):
        self._ensure_up_rows()
        if not self._up_scopes:
            self.up_sel = set()
            self._refresh_up_view()      # 取消范围：表里恢复全部
            return
        want = self._scope_path_set()
        pending = {r["path"] for r in self.up_rows if not r.get("uploaded")}
        self.up_sel = want & pending
        self._refresh_up_view()          # 表里只剩这个范围的行，并选中待传的那几条

    def select_all_pending(self):
        self._ensure_up_rows()
        self.up_sel = self._view_pending()
        self.tbl_up.select_paths(self.up_sel)
        self._update_up_stat()

    def clear_selection(self):
        self._ensure_up_rows()
        self._up_scopes.clear()
        self._scope_want = None
        for p in self.scope_pills.values():
            p.set_selected(False)
        self.up_sel = set()
        self._refresh_up_view()          # 清空范围，表格恢复全部
        self.tbl_up.tv.selection_remove(*self.tbl_up.tv.selection())
        self._update_up_stat()

    # ---------- 上传：执行 ----------
    def _pending_paths(self):
        self._ensure_up_rows()
        return [r["path"] for r in self.up_rows
                if not r.get("uploaded") and r["path"] in self.up_sel]

    def _confirm_batch(self, rows, action="上传", hint=""):
        """二次确认。测试里会把它替换掉，别在里面做副作用。"""
        if not rows:
            return False
        return UploadConfirmDialog.ask(self, rows, action=action, hint=hint)

    def _rows_for(self, paths):
        self._ensure_up_rows()
        want = set(paths)
        return [r for r in self.up_rows if r["path"] in want]

    def upload_selected(self):
        paths = self._pending_paths()
        if not paths:
            messagebox.showinfo(APP_TITLE, "没有选中的待上传录像。\n\n"
                                          "先用上面的「范围」或「全选未传」选一批。")
            return
        rows = self._rows_for(paths)
        if not self._confirm_batch(
                rows, "上传", "下面是你在表里选中的录像，确认无误再开始。"):
            return
        self._start_upload(paths)

    def upload_all_pending(self):
        pending = self._view_pending()      # 有范围时只算当前范围
        paths = [r["path"] for r in self.up_view if r["path"] in pending]
        if not paths:
            messagebox.showinfo(APP_TITLE, "本地完成局都已经在网站上了。")
            return
        rows = self._rows_for(paths)
        hint = ("下面是当前范围里全部未上传的录像，确认无误再开始。"
                if self._up_scopes else
                "下面是把本地所有未上传的录像，确认无误再开始。")
        if not self._confirm_batch(rows, "上传", hint):
            return
        self._start_upload(paths)

    def _start_upload(self, paths, silent=False, why=None, brief=None):
        """silent=True 是自动上传发的：不确认、不锁按钮、失败不弹窗。

        自动上传每局都会走这条路，任何弹窗都会抢走元扫雷的焦点，一局就废了。
        """
        if self._up_running:
            if not silent:
                self.set_status("自动上传正在传新成绩，等这一条传完再点", "warn")
            return
        if not self.oms_user:
            if silent:
                return
            # Cookie 还在、只是账号信息还没读回来（启动后那几秒）：这时候把人
            # 推去输验证码纯属折腾，等目录栏转成「已登录」再点就行。
            if self.oms.has_session() and not self.oms_err:
                self.set_status("正在确认网站登录态，稍等一下再点上传", "warn")
                return
            if messagebox.askyesno(APP_TITLE, "还没登录 openms.top，现在登录吗？"):
                self.open_login()
            return
        self._up_running = True
        self._up_stop = False
        self._up_silent = bool(silent)
        self._auto_why = dict(why or {})
        self._auto_brief = dict(brief or {})
        self._up_total = len(paths)
        self._last_failed.clear()
        self._up_done = self._up_ok = self._up_dup = self._up_fail = 0
        self.pb_up.config(maximum=max(1, len(paths)), value=0)
        self.lbl_up_prog.config(text="0/%d" % len(paths))
        if not silent and not self.row_up_prog.winfo_ismapped():
            self.row_up_prog.pack(side="bottom", fill="x", pady=(0, sc(2)))
        if not silent:
            self._set_up_enabled(False)
            self.btn_stop.set_enabled(True)
        # 记下开传之前那一行：全部成功时原样还回去，免得总结永远挂在底下。
        prev = self._status_before_upload()
        if prev:
            self._status_saved = prev
        self._log("开始上传 %d 个录像…" % len(paths), "dim")
        self.set_status(("自动上传 %d 条新成绩…" if silent else "正在上传 %d 个录像…")
                        % len(paths), "dim")
        keys = {g["path"]: openms.game_key(g) for g in self.games}
        threading.Thread(target=self._upload_worker, args=(paths, keys),
                         daemon=True).start()

    def _status_before_upload(self):
        """「开传之前那一行」，传完全部成功时还原它。

        三类话不能收：上一批的结论（「上传结束：成功 1」）、正在进行的这件事
        本身、以及**已经作废的扫描进度**（「已读 22400 / 22555」）。把进度当
        常态存下来，传完之后底下就永远挂着一句停在 99% 的假进度。
        返回 None 表示「这行不值得记」，沿用上一次记下的。
        """
        cur = self.lbl_status.cget("text")
        if not cur or cur.startswith(("正在上传", "上传结束", "自动上传",
                                      "正在扫描", "正在清点")):
            return None
        return (cur, self.lbl_status.cget("foreground"))

    def _upload_worker(self, paths, keys=None):
        """串行上传。

        失败分两类，处理方式不同：
          · 账号级（auth / quota / realname / identity / banned）—— 后面的每一条都会同样失败，
            立即停止整批；
          · 单条级（网络重试耗尽、5xx、单条被拒）—— 记下来继续下一条。
        网络 / 服务端连续 3 条失败就认为连不上，主动停批，避免白等 N 个超时。
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
            if res.get("fatal") in ("auth", "quota", "banned", "realname", "identity"):
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
                  self.btn_sel_none, self.btn_sync, self.btn_login,
                  self.btn_logout):
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
        if not self._confirm_batch(self._rows_for(paths), "重传",
                                   "下面是上一次失败的录像，只重传这几条。"):
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
        if self._up_silent:
            g = next((x for x in self.games if x["path"] == path), None)
            if g is None:
                g = next((x for x in self.ng_games if x["path"] == path), None)
            if g is not None:
                if res.get("ok"):
                    self._auto_session += 1
                    self._auto_retry.pop(path, None)
                    state = "" if res.get("collision") else (res.get("state") or "")
                    self._auto_record(
                        g, self._auto_why.get(path),
                        "网站已有" if res.get("collision")
                        else openms.STATE_CN.get(state, "已上传"), ok=True,
                        brief=self._auto_brief.get(path))
                    # 自动上传不重拉录像列表（那是 2000 条、21 批请求），但页面
                    # 上的「网站已有」和「未上传」是拿这个列表算的 —— 所以把刚传
                    # 成功的这条按站点字段补进内存，否则下一次重扫会把它显示回
                    # 「未上传」，让人以为没传上去。
                    if not res.get("collision"):
                        ms = g.get("timems")
                        if ms is None:
                            ms = int(round(float(g.get("time") or 0) * 1000))
                        self.oms_videos.append({
                            "id": res.get("vid"), "state": res.get("state"),
                            "timems": ms, "bv": g.get("bv"),
                            "file_size": g.get("size"),
                        })
                else:
                    self._auto_record(g, self._auto_why.get(path),
                                      "没传：%s" % (res.get("reason") or "失败"),
                                      ok=False,
                                      brief=self._auto_brief.get(path))
                    # 断网、5xx 这种可能自己好起来的：放回候选池，20/40/60 秒后再试。
                    # 判定错（格式、级别、审核码）不放回 —— 再试还是同样的结果。
                    if res.get("transient"):
                        n = self._auto_retry.get(path, 0) + 1
                        self._auto_retry[path] = n
                        if n <= 3:
                            self._auto_tracker.requeue(path)
                            self._ng_tracker.requeue(path)
                            self._auto_backoff = max(self._auto_backoff,
                                                     time.monotonic() + 20.0 * n)
        self.pb_up.config(value=idx)
        self.lbl_up_prog.config(text="%d/%d" % (idx, total))

    def _on_upload_end(self, payload):
        self._up_running = False
        silent = self._up_silent
        self._up_silent = False
        fatal = payload.get("fatal")
        if silent:
            # 自动上传：只写状态栏和日志。任何 messagebox 都会抢走元扫雷的焦点。
            self._auto_handle_fatal(fatal, payload.get("reason"))
            msg = "自动上传：成功 %d，已有 %d，失败 %d" % (
                self._up_ok, self._up_dup, self._up_fail)
            self.set_status(msg, "ok" if not self._up_fail else "warn")
            self._end_up_progress(keep_status=bool(fatal) or self._up_fail > 0)
            self._refresh_acct_label()
            self._update_up_stat()
            self._refresh_auto_view()
            # 传完必须重读网站：前端成绩 / 网站 PB / 「网站已有」全靠这份数据，
            # 不重读的话刚涨的分在界面上还是旧的，看着像没传上去。
            # 但 auth 失效已经把用户清掉：refresh_site 见无用户会弹模态登录窗，
            # 静默批不许抢元扫雷的焦点。
            if self.oms_user:
                self.after(400, self.refresh_site)
            # 这一批传完，队列里可能还压着刚落地的局
            self.after(120, self._auto_tick)
            return
        self._set_up_enabled(True)
        self.btn_stop.set_enabled(False)
        if fatal == "auth":
            self.oms_user = None
            self.oms_loaded = False
            self.oms.store.clear_session()
            self._refresh_acct_label()
            messagebox.showwarning(APP_TITLE, "登录已失效，本批已停止。\n\n"
                                             "请重新点「登录…」（只需再输一次验证码）。")
        elif fatal == "quota":
            messagebox.showwarning(APP_TITLE, "录像额度已满，本批已停止。\n\n"
                                             "请联系网站管理员增加录像额度。")
        elif fatal == "realname":
            messagebox.showwarning(APP_TITLE, "账号未实名，网站拒绝上传。\n\n"
                                             "请先到 openms.top 个人资料里完成实名。")
        elif fatal == "identity":
            messagebox.showwarning(APP_TITLE, "账号还没通过上传准入，本批已停止。\n\n"
                                             "网站要求「实名」或「玩家认证」满足其一，"
                                             "请先到 openms.top 个人资料里办一项。")
        elif fatal == "banned":
            messagebox.showwarning(APP_TITLE, "账号被封禁，无法上传。")
        elif fatal == "net":
            self.set_status("网络或服务器连续失败，本批已停止；已传的不会重传", "err")
            messagebox.showwarning(
                APP_TITLE,
                "连续多条网络或服务器错误，已停止本批。\n\n"
                "已经传成功的不受影响，重试也不会重复上传。\n"
                "恢复后点「重试失败」只补传剩下的那几条。")

        msg = "上传结束：成功 %d，已有 %d，失败 %d" % (
            self._up_ok, self._up_dup, self._up_fail)
        if payload.get("stopped"):
            msg += "（手动停止）"
        if fatal == "net":
            msg = "连续失败已停批 · " + msg
        elif self._up_fail and not fatal:
            msg += "，可点「重试失败」补传"
        self.set_status(msg, "ok" if not self._up_fail else "warn")
        self._end_up_progress(keep_status=bool(fatal) or self._up_fail > 0
                              or bool(payload.get("stopped")))
        self._refresh_acct_label()      # 上一行会把「登录…」重新启用，这里按真实登录态纠正
        self._update_up_stat()
        # 用服务端数据校正状态（含审核结果 a/c/b/d）
        self.after(400, self.refresh_site)

    def _end_up_progress(self, keep_status=False, hold_ms=None):
        """一批传完的收尾：进度行收回、右下角那行清掉。

        右下角写的是「正在传哪个」，不是历史 —— 传完就该空出来，历史在表格里。
        全部成功时总结只留几秒，然后还回开传前那一行；有失败或账号级问题就留着，
        那是要人处理的事，不能自己消失。
        """
        try:
            self.row_up_prog.pack_forget()
        except tk.TclError:
            pass
        self.lbl_up_prog.config(text="")
        self.lbl_up_log.config(text="")
        if keep_status:
            return
        hold_ms = self.UP_STATUS_HOLD_MS if hold_ms is None else hold_ms
        text, color = getattr(self, "_status_saved", ("", C_DIM))
        if text:
            self._status_later(hold_ms, text, color)

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
        if self._up_running:
            self.set_status("上传进行中，先等本批结束再改录像目录", "warn")
            return
        PathsDialog(self)

    def open_settings(self):
        """横幅「设置」：同风格模态弹窗（操作 / 强无猜 / LSF 两态基准）。"""
        old = getattr(self, "_settings_dlg", None)
        if old is not None and old.winfo_exists():
            old.close()
        self._settings_dlg = SettingsDialog(self)

    def apply_roots(self, roots, rescan=True):
        """目录列表变了 → 存盘、刷新按钮、重扫。"""
        if self._up_running:
            self.set_status("上传进行中，先等本批结束再改录像目录", "warn")
            return
        self.roots = load_roots({"roots": roots})
        save_roots(self.roots)
        self.lbl_meta.config(text=self.roots_label())
        diag.log.info("录像目录更新：%s",
                      " ; ".join(r["path"] for r in self.roots) or "（空）")
        self._watch_rebuild()               # 停用/移除的目录立刻退出监控清单
        # 换了目录：重扫出来的局一律按「旧局」算，否则刚加进来的历史录像会被
        # 当成新成绩自动传上去。保持未初始化，让下一轮扫描自己建基线。
        self._auto_tracker = autoup.Tracker(self._auto_watermark)
        self._ng_tracker = autoup.Tracker(self._ng_watermark)
        if rescan:
            if not self.active_roots():
                self.set_status("所有录像目录都已停用", "warn")
                return
            self._empty_prompted = False
            self.start_scan()

    # ---------- 自动刷新（盯目录时间戳，变了就重扫）----------
    WATCH_MS = 1500                    # 轻量检查间隔：只 stat 目录，一轮 0.1 ms
    WATCH_FULL_EVERY = 40              # 每 40 拍走一遍全树（约 1 分钟）

    def set_auto_refresh(self, on):
        self._watch_enabled = bool(on)
        save_config(auto_refresh=self._watch_enabled)
        diag.log.info("自动刷新：%s", "开" if self._watch_enabled else "关")
        if self._watch_enabled:
            self._watch_rebuild()
        elif self._watch_job is not None:
            try:
                self.after_cancel(self._watch_job)
            except Exception:               # noqa: BLE001
                pass
            self._watch_job = None
        self.set_status("自动刷新已%s" % ("开启" if self._watch_enabled else "关闭"), "dim")

    def _watch_rebuild(self):
        """重建监控清单：启用的目录 + 它们的所有子目录，各记一个 mtime。

        只碰配置里启用的目录 —— 停用、移除的当场就不再看（实测本机 6 个目录
        一轮 0.1 ms，等于没有开销）。必须连子目录一起盯：`D:\\扫雷` 那种
        目录树，录像九成在 replays\\ 这类子目录里。
        """
        if not getattr(self, "_watch_enabled", False):
            return
        roots = self.active_roots()
        try:
            self._watch_dirs, self._watch_sig = (
                self._watch_scan(roots) if roots else ({}, (0, 0)))
        except Exception:                   # noqa: BLE001  监控坏掉不能影响主流程
            self._watch_dirs, self._watch_sig = {}, (0, 0)
        self._watch_ticks = 0
        self._watch_schedule()

    def _watch_take(self, info):
        """扫描刚走完整棵树，把它看到的目录时间戳直接当基线，省掉再走一遍。

        只有扫描被目录数上限截断时才退回自己走一遍 —— 那种情况基线是残缺的。
        """
        dirs = info.get("dirs_mtime") or {}
        if not dirs or info.get("truncated"):
            self._watch_rebuild()
            return
        self._watch_dirs = dict(dirs)
        self._watch_sig = (info.get("files", 0), info.get("newest", 0))
        self._watch_ticks = 0
        self._watch_schedule()

    def _watch_scan(self, roots):
        """走一遍 roots，返回 ({目录: mtime_ns}, (录像数, 最新 mtime_ns))。"""
        dirs, n, newest = {}, 0, 0
        stack = [r for r in roots if r]
        while stack:
            d = stack.pop()
            if d in dirs:
                continue
            try:
                dirs[d] = os.stat(d).st_mtime_ns
                with os.scandir(d) as it:
                    entries = list(it)
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.name.lower() not in core.SKIP_DIRS:
                            stack.append(e.path)
                        continue
                    if core.video_src(e.name) is None:
                        continue
                    n += 1
                    m = e.stat().st_mtime_ns
                except OSError:
                    continue
                if m > newest:
                    newest = m
        return dirs, (n, newest)

    def _watch_changed(self):
        for d, m in self._watch_dirs.items():
            try:
                if os.stat(d).st_mtime_ns != m:
                    return True
            except OSError:
                return True                 # 目录被删 / 移动盘拔了：交给扫描重新收拢
        return False

    def _watch_tick(self):
        self._watch_job = None
        if not self.winfo_exists():
            return
        try:
            if not self._watch_enabled or self._scanning or self._up_running:
                return
            changed = self._watch_rescan
            self._watch_rescan = False
            if not changed and self._watch_dirs:
                changed = self._watch_changed()
            self._watch_ticks += 1
            if not changed and self._watch_ticks >= self.WATCH_FULL_EVERY:
                # 目录时间戳只在「增删文件」时变，原地覆盖同名文件它不会变。
                # 所以每分钟走一遍全树核对（22k 文件 0.04 s，察觉不到）。
                self._watch_ticks = 0
                dirs, sig = self._watch_scan(self.active_roots())
                if sig != self._watch_sig or dirs != self._watch_dirs:
                    changed = True
            if changed:
                self.start_scan(auto=True)
        except Exception:                   # noqa: BLE001
            pass
        finally:
            self._watch_schedule()

    def _watch_schedule(self):
        if not getattr(self, "_watch_enabled", False) or not self.winfo_exists():
            return
        if self._watch_job is None:
            self._watch_job = self.after(self.WATCH_MS, self._watch_tick)

    def _on_focus(self, event=None):
        """切回窗口先看一眼，不用等下一拍；双屏不切窗口的场景由定时器兜着。

        <FocusIn> 会从任意子控件冒泡上来，所以用时间戳去抖（1 秒最多一次），
        不去分辨是谁拿了焦点。
        """
        now = time.monotonic()
        if now - getattr(self, "_focus_at", 0.0) < 1.0:
            return
        self._focus_at = now
        if not self._watch_enabled or self._scanning or self._up_running:
            return
        if self._watch_job is not None:
            try:
                self.after_cancel(self._watch_job)
            except Exception:               # noqa: BLE001
                pass
            self._watch_job = None
        self._watch_tick()

    def _boot(self):
        if self._need_pick:
            self.set_status("首次运行：请先指定录像目录", "warn")
            self.lbl_meta.config(text="未选择录像目录")
            self.after(120, self.first_run_pick)
        else:
            self.start_scan()

    def first_run_pick(self):
        messagebox.showinfo(
            APP_TITLE,
            "没有自动找到元扫雷的录像目录，请手动指定一个。\n\n"
            "元扫雷的录像目录一般在：\n"
            "    %APPDATA%\\Metasweeper<版本号>\\replay\n\n"
            "选一次即可，之后会自动记住。\n\n"
            "阿比特的录像目录不会自动加入：打开后点右上角「路径…」→「添加目录…」手动添加。")
        self.pick_dir(first=True)

    def pick_dir(self, first=False):
        if self._up_running:
            self.set_status("上传进行中，先等本批结束再换目录", "warn")
            return
        title = "选择录像目录"
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

    def start_scan(self, auto=False):
        """auto=True = 自动刷新触发的：不显示进度条、不抢状态栏、不弹窗。"""
        if self._scanning:
            return
        if self._up_running:
            if not auto:
                self.set_status("上传进行中，先等本批结束再重新扫描", "warn")
            return
        roots = self.active_roots()
        if not roots:
            if not auto:
                self.first_run_pick()
            return
        self._scanning = True
        self._scan_auto = bool(auto)
        if auto:
            self.set_status("发现新录像，正在更新…", "dim")
        else:
            head = shrink_path(roots[0], 52)
            self._progress_begin()
            self.set_status("正在清点 %s%s …"
                            % (head, "" if len(roots) == 1 else " 等 %d 个目录" % len(roots)),
                            "dim")
            self.lbl_meta.config(text=self.roots_label())
        threading.Thread(target=self._scan_worker, args=(bool(auto),),
                         daemon=True).start()

    def _scan_worker(self, auto=False):
        try:
            roots = self.active_roots()
            if not auto:
                # 清点总数（只读目录项，0.2 秒量级）→ 进度条才有分母
                self._q.put(("scan_total", core.count_videos(roots)))
            games, info = core.scan(
                roots,
                progress=None if auto else (lambda n: self._q.put(("scan", n))))
            pb = core.build_pb_rows(games)   # 全PB / NF PB 两张榜
            cnt = core.count_by_bv(games)
            cnts = {"all": cnt,
                    "nf": core.count_by_bv(games, "nf")}
            smy = core.summary(games)
            ini_path = core.find_record_ini(self.root_dir)
            ini = core.read_record_ini(ini_path) if ini_path else {}
            # record.ini 只在元扫雷退出时落盘：正在扫雷时它必然比录像旧，
            # 对账得知道这件事，否则会把「刚破了 PB」报成数值冲突。
            try:
                info["ini_mtime"] = os.path.getmtime(ini_path) if ini_path else 0
            except OSError:
                info["ini_mtime"] = 0
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
                elif kind == "site_pb":
                    self._on_site_pb(*payload)
                elif kind == "site_prog":
                    self._on_site_prog(*payload)
                elif kind == "site_top":
                    self._on_site_top(*payload)
                elif kind == "site_ng":
                    self._on_site_ng(*payload)
                elif kind == "lsf":
                    self._on_lsf(*payload)
                elif kind == "up":
                    self._on_upload_result(*payload)
                elif kind == "scan_total":
                    self._progress_total(payload)
                    if payload:
                        self.set_status("正在扫描…0 / %d 个录像" % payload, "dim",
                                        transient=True)
                elif kind == "scan":
                    pct = self._progress_set(payload)
                    eta = self._scan_eta_text()
                    # 队列里可能一次积压上百个点，只在百分比真的变了才刷文字
                    if pct is not None and pct != self._scan_pct:
                        self._scan_pct = pct
                        txt = "正在扫描…已读 %d / %d 个录像（%d%%）" \
                              % (payload, self._scan_total, pct)
                        if eta:
                            txt += "，预计还需 %s" % eta
                        self.set_status(txt, "dim", transient=True)
                        if eta:
                            self.lbl_boot.config(
                                text="正在读取录像…\n预计还需 %s —— 初始化较慢，请耐心等待" % eta)
                    elif pct is None:
                        self.set_status("正在扫描…已读到 %d 个录像" % payload, "dim",
                                        transient=True)
                elif kind == "up_end":
                    self._on_upload_end(payload)
        except queue.Empty:
            pass
        except Exception as exc:  # noqa: BLE001
            self.set_status("处理结果出错：" + str(exc), "err")
        if self.winfo_exists():
            self.after(60, self._poll)

    def _on_scan_failed(self, msg):
        auto = self._scan_auto
        self._scan_auto = False
        self._scanning = False
        self._progress_end()
        diag.log.error("扫描失败：%s", msg)
        self.set_status("扫描失败：" + msg, "err")
        if not auto:
            messagebox.showerror(APP_TITLE, "扫描失败\n\n" + msg)

    def _on_scanned(self, games, info, pb, cnt, smy, diffs, ini_ok):
        self._scanning = False
        self._progress_end()
        self.games, self.info = games, info
        self.ng_games = info.get("ng_games") or []
        self.pb_local_rows = pb or []
        self.counts = cnt if isinstance(cnt, dict) and "all" in cnt else {"all": cnt or {}}
        self.summary, self.diffs, self.ini_ok = smy, diffs, ini_ok

        # 自动刷新：数据其实没变就别重画（22k 行表格重画一遍 0.3 s，白闪一下
        # 没有任何收益）。判据是文件数 + 局数 + 最新一局的 mtime。
        auto = self._scan_auto
        sig = (info.get("files"), info.get("unique"), info.get("newest"))
        unchanged = bool(auto and sig[2] and sig == self._scan_sig)
        self._scan_sig = sig
        self._scan_auto = False
        if unchanged:
            self.set_status("没有新录像", "dim")
            self._watch_take(info)
            return

        self.render_cards()
        # 没有 rce 数据（ms-toollib 缺失）时，本地分不出 NF/FL，前端成绩的
        # NF 榜一定是空的，退回全PB；PB 榜不强制 —— 网站 PB 不依赖本地重演，
        # NF PB 仍可能有内容（来源列会标「网站」）。
        self._local_rce = bool(info.get("with_rce"))
        if not info.get("with_rce") and self._top_style != "all":
            self.set_top_style("all")
        self._recompute_pb()          # 顺带重画最近完成局（那列要给「距当前 PB」）
        self.set_top_level(self._level)
        self.set_daily_level(self._daily_level)   # 顺带把级别按钮点亮
        self._mark_up_dirty()           # 上传页跟着新数据刷新（不在该页就先不算）

        elapsed = info["elapsed"] * 1000
        n_fail = info["fail_skipped"]
        dup = info["duplicates"]
        extra = []
        if info.get("non_std"):
            extra.append(f"跳过 {info['non_std']} 个非标准局")
        if info.get("ng_count"):
            extra.append(f"强无猜 {info['ng_count']} 局（不计入统计）")
        if n_fail:
            extra.append(f"跳过 {n_fail} 个没打完的")
        if dup:
            extra.append(f"重复的 {dup} 个")
        if info["unparsed"]:
            extra.append(f"读不出 {len(info['unparsed'])} 个")
        if info.get("nf_count") or info.get("fl_count"):
            extra.append(f"NF {info['nf_count']} · FL {info['fl_count']}")
        if info.get("rce_failed"):
            extra.append(f"读不出 {info['rce_failed']} 条")
        if info.get("lite_used"):
            extra.append("ms-toollib 不可用，%d 局由内置解析提供 NF/FL"
                         % info["lite_used"])
        elif info.get("lib_missing"):
            extra.append("ms-toollib 未加载，本地 NF/FL 不可用")
        elif not info.get("with_rce"):
            extra.append("本地 NF/FL 不可用（录像重演失败）")
        if info.get("tampered"):
            extra.append(f"文件名与录像不符 {len(info['tampered'])}")
        if info.get("level_conflict"):
            extra.append(f"级别不符 {len(info['level_conflict'])}")
        if info.get("refilled"):
            # 一次性迁移：老缓存里没有录像内时间，这一轮就地补上了
            extra.append(f"补齐 {info['refilled']} 局录像时间")
        if info.get("retimed"):
            extra.append(f"{info['retimed']} 局读不出录制时间，按文件时间归日")
        n_arb = (info.get("by_src") or {}).get("arb") or 0
        if n_arb:
            extra.append(f"其中阿比特 {n_arb}")
        if info.get("truncated"):
            extra.append(f"只扫了前 {info['dirs_visited'] - 1} 个子目录就停手"
                         f"（目录太大，建议直接指向 replay）")
        tail = ("，" + "，".join(extra)) if extra else ""
        # 「用时」按秒说：毫秒精度对用户没有意义，缓存命中率更是纯内部实现
        used = ("%.1f 秒" % (elapsed / 1000.0)) if elapsed < 10000 \
            else ("%d 秒" % round(elapsed / 1000.0))
        self.set_status(
            f"扫描 {info['files']} 个文件 → {info['unique']} 个完成局{tail}，用时 {used}",
            "warn" if (info.get("tampered") or info.get("truncated")) else "dim")
        self.lbl_meta.config(text=f"{info['unique']} 局完成 · " + self.roots_label())
        diag.log.info("扫描完成：%d 文件 → %d 局（阿比特 %d%s），NF %s / FL %s，"
                      "非标准 %s，%d ms%s",
                      info["files"], info["unique"], n_arb,
                      "，去掉重复副本 %d" % info["dup_removed"]
                      if info.get("dup_removed") else "",
                      info.get("nf_count"),
                      info.get("fl_count"), info.get("non_std"), elapsed,
                      "（目录数达上限，已截断）" if info.get("truncated") else "")

        # 自动上传放在扫描结论写完「之后」：它一开传就会把当前状态栏当成
        # 「开传前那一行」存下来，若此刻底下还挂着扫描进度，传完之后还原的
        # 就是一句已经作废的「已读 22400 / 22555（99%）」。
        self._auto_tick()

        if not games:
            self.lbl_check.config(text="未发现 .evf / .avf 录像，请用「换目录…」或「路径…」指定",
                                  foreground=C_WARN)
            if not self._empty_prompted and not auto:
                self._empty_prompted = True
                if messagebox.askyesno(
                        APP_TITLE,
                        "这些目录里没有找到录像文件：\n" +
                        "\n".join(self.active_roots()) +
                        "\n\n要重新选择目录吗？"):
                    self.pick_dir()
        elif not ini_ok:
            diag.log.info("未找到 record.ini，跳过对账")
            self.lbl_check.config(text="", foreground=C_DIM)
        else:
            # record.ini 是元扫雷写的，所以对账的基数也只能是元扫雷的局
            ms_pb = core.build_pb([g for g in games if g.get("src") != "arb"])
            n_keys = len({(r["level"], r["bv"]) for r in ms_pb})  # 全PB：每个 (级别, BV) 一条
            # record.ini 只在元扫雷退出时落盘：它开着时 ini 必然落后，本地更快
            # 是必然现象。落后时只把「本地更慢 / ini 有而录像没有」当真异常。
            stale = core.record_ini_stale(info.get("ini_mtime"),
                                          info.get("newest_ms"))
            j = core.judge_record_diffs(diffs, n_keys, stale)
            only_mine, only_ini = j["only_mine"], j["only_ini"]
            big, faster = j["conflict"], j["faster"]
            n_same = j["same"]
            # 对账是后台校验，不是常驻信息：只有真冲突才占状态栏，其余写日志。
            # 文案按用户视角写：record.ini / mtime 这类词只留在日志和注释里。
            parts = []
            if only_ini:
                parts.append(f"{only_ini} 条成绩只在元扫雷记录里，找不到录像")
            if big:
                parts.append(f"{big} 条成绩与元扫雷记录不一致")
            self.lbl_check.config(
                text="；".join(parts) if parts else "",
                foreground=C_WARN if parts else C_DIM)
            diag.log.info("record.ini 对账：%d/%d 一致，%d 条仅录像有，%d 条仅 ini 有，"
                          "%d 条冲突%s", n_same, n_keys, only_mine, only_ini, big,
                          ("（record.ini 落后，另有 %d 条本地更快属正常）" % faster)
                          if faster else "")

        if info.get("rce_failed"):
            # 可能读到了元扫雷正在写的半截文件。它不会被写进缓存，下一拍再按一遍。
            self._watch_rescan = True
        self._watch_take(info)

    # ---------- 渲染 ----------
    def render_cards(self):
        for lv, card in self.cards.items():
            s = self.summary.get(lv) or {}
            if not s.get("games"):
                card.set_data({"count": "0 局", "rows": [
                    ("最快单局", "—"), ("最高 3BV/s", "—"), ("最高 stnb", "—")]})
                continue
            bt, bb, bs = s["bestTime"], s["bestBvs"], s["bestStnb"]
            card.set_data({"count": f"{s['games']} 局", "rows": [
                ("最快单局", f"{fmt_time(bt['time'])} s @BV{bt['bv']}"),
                ("最高 3BV/s", f"{fmt_bvs(bb['bvs'])} @BV{bb['bv']}"),
                ("最高 stnb", f"{fmt_stnb(bs['stnb'])} @BV{bs['bv']}"),
            ]})

    # ---------- 最近完成局 ----------
    RECENT_MIN = 100        # 至少列出的局数
    RECENT_DAYS = 3         # 时间窗口：近三天的局数比 100 多就都列出来
    RECENT_MAX = 2000       # 上限：一次性导入几万个录像时，「近三天」会退化成全部

    def render_recent(self):
        """最近完成局：最近 RECENT_MIN 局与近 RECENT_DAYS 天取较多者。

        每行给出「距当前 PB」还差多少。PB 会变，所以这里每次都按当前 PB 现算：
        同一局今天显示 +0.532，等 PB 提上去之后会变成 +0.900。

        破 PB 的那一局拿它替掉的旧 PB 做参照，跟其余行同一个式子（本局 −
        参照）：时间快了多少写成负数（PB −2.111，跟旁边的 +4.782 读法一致），
        bvs/stnb 高了多少写成正数。旧 PB 取同（榜, 级别, 3BV）下更早完成的最快
        一局，本地没有就用网站上的成绩；首个 PB 没有旧值可比，仍旧只写 PB。
        """
        rows = sorted(self.games, key=lambda g: g.get("t0") or g.get("mtime") or 0,
                      reverse=True)
        cut = time.time() - self.RECENT_DAYS * 86400
        n = min(self.RECENT_MIN, len(rows))
        while n < len(rows) and (rows[n].get("t0")
                                 or rows[n].get("mtime") or 0) >= cut:
            n += 1
        if n > self.RECENT_MAX:
            # 只有「把别人的存档包整个解压进来」才会走到这里：解压出来的文件
            # mtime 全是那一刻，两万局全落在三天窗口里。
            diag.log.info("最近完成局：近 %d 天有 %d 局，只列最新 %d 局",
                          self.RECENT_DAYS, n, self.RECENT_MAX)
            n = self.RECENT_MAX
        pb = {}
        for r in self.pb_rows or []:
            pb[(r.get("category") or "all", r.get("level"), r.get("bv"))] = r
        gains = self._recent_gains(rows[:n])
        prevs = self._prev_pb_of(rows[:n], pb)
        out = []
        for g in rows[:n]:
            item = dict(g)
            item["level_cn"] = core.LEVEL_CN[g["level"]]
            item["style_cn"] = core.STYLE_CN.get(g.get("style"), "?")
            # 时间列与日期列同源：都用录像内的录制时刻，不用文件 mtime
            item["clock"] = core.clock_text(self._row_when(g))
            gap = self._gaps_to_pb(g, pb)
            item["vs_pb"] = gap["time"]
            item["vs_pb_bvs"] = gap["bvs"]
            item["vs_pb_stnb"] = gap["stnb"]
            old = prevs.get(g.get("path")) if gap["time"] == "PB" else None
            if old is not None:
                # 破 PB 的那一局拿旧 PB 做参照，跟上面几列同一个式子
                # （本局 − 参照）：时间快了多少就是负数，bvs/stnb 高了多少
                # 就是正数。「PB」前缀只用来标明这一行就是当前 PB。
                d_t, d_b, d_s = self._improve_over(g, old)
                item["vs_pb"] = "PB " + _delta_text(-d_t, 3)
                item["vs_pb_bvs"] = _delta_text(d_b, 2)
                item["vs_pb_stnb"] = _delta_text(d_s, 2)
            item["gain"] = gains.get(g.get("path") or "")
            out.append(item)
        self.tbl_recent.set_rows(out)

    def _recent_gains(self, rows):
        """最近这批局在各自打出来那一刻涨了多少分。

        与 PB 无关：没破 PB 的局只要挤进某一项前五，一样涨分。
        锚点不到（没登录过、也从来没存下过）就算不出来，这时整列留空。
        """
        if not rows or not self._lsf_anchors_eff():
            return {}
        window = [g for g in rows if g.get("path")]
        # 一键导入（解压出来的 mtime 全在同一秒）时顺序没有意义，
        # 涨分只能算出一个假的先后，宁可不显示
        if len(window) > 5 and len({g.get("mtime") or 0 for g in window}) < 3:
            return {}
        key = (id(self.games), len(self.games), len(window),
               id(self._lsf_anchors_eff()))
        if self._gain_cache and self._gain_cache[0] == key:
            return self._gain_cache[1]
        t0 = time.perf_counter()
        gains = core.lsf_gains(self.games, self._lsf_anchors_eff(), window)
        n = sum(1 for v in gains.values() if v > 0.005)
        diag.log.info("最近 %d 局的涨分：%d 局有涨分，用时 %.0f ms",
                      len(window), n, (time.perf_counter() - t0) * 1000)
        self._gain_cache = (key, gains)
        return gains

    @staticmethod
    def _pb_cat(row):
        """这行属于哪张榜：NF 局只跟 NF 比，其余都进全榜。"""
        return "nf" if row.get("style") == "nf" else "all"

    @staticmethod
    def _row_ms(row):
        v = row.get("timems")
        if v is None:
            v = int(round(float(row.get("time") or 0) * 1000))
        return int(v)

    @staticmethod
    def _row_when(row):
        """完成时刻：录像内录制时间优先，没有就退回文件 mtime。"""
        return row.get("t0") or row.get("mtime") or 0

    def _prev_pb_of(self, rows, pb):
        """这些行里刷新 PB 的那几行，各自替掉的旧 PB。

        「原 PB」= 同一（榜, 级别, 3BV）下、比本局更早完成的最快一局。本地找
        不到更早的局（比如录像只拷了一部分）就退回网站 PB —— 说明本局刷掉的
        原本是网站上的成绩。返回 {path: 旧 PB 行}；首个 PB（之前没成绩）不在
        里面。只算真正破了 PB 的行，其余行不算。
        """
        keys = {}
        for g in rows:
            k = (self._pb_cat(g), g.get("level"), g.get("bv"))
            p = pb.get(k)
            if p and p.get("path") and p.get("path") == g.get("path"):
                keys[g.get("path")] = k
        if not keys:
            return {}

        cand = {}
        key_set = set(keys.values())
        for g in self.games:
            k = (self._pb_cat(g), g.get("level"), g.get("bv"))
            if k in key_set:
                cand.setdefault(k, []).append(g)

        out = {}
        for g in rows:
            k = keys.get(g.get("path"))
            if k is None:
                continue
            t_self = self._row_when(g)
            best, best_ms = None, None
            for o in cand.get(k, ()):
                if o.get("path") and o.get("path") == g.get("path"):
                    continue
                t = self._row_when(o)
                if not t or not t_self or t >= t_self:
                    continue
                ms = self._row_ms(o)
                if ms > 0 and (best_ms is None or ms < best_ms):
                    best, best_ms = o, ms
            if best is None:
                best = self._site_prev_pb(k, self._row_ms(g))
            if best is not None:
                out[g.get("path")] = best
        return out

    def _site_prev_pb(self, key, ms_self):
        """本地没有更早的局时，网站 PB 就是本局刷掉的那个旧成绩。"""
        cat, lv, bv = key
        best = None
        for rec in self.site_pb or []:
            if not isinstance(rec, dict):
                continue
            if str(rec.get("level") or "").lower() != lv:
                continue
            try:
                if int(rec.get("bv") or 0) != bv:
                    continue
                ms = int(rec.get("timems") or 0)
            except (TypeError, ValueError):
                continue
            if ms <= 0 or ("nf" if rec.get("nf") else "all") != cat:
                continue
            if ms_self and ms <= ms_self:
                continue            # 网站更快，本局就不是 PB 行了
            if best is None or ms < best:
                best = ms
        if best is None:
            return None
        sec = best / 1000.0
        return {"timems": best, "time": sec,
                "bvs": bv / sec if sec > 0 else 0.0,
                "stnb": core.stnb_of(bv, sec, lv)}

    @classmethod
    def _improve_over(cls, g, old):
        """这局相对旧 PB 的提升：时间快了多少秒、3BV/s 和 stnb 高了多少。

        同 BV 下 PB 必然最快，所以三项都应当 >= 0；真出现负数说明数据
        异常（旧 PB 不是同 BV 最快），按 0 显示，不写个负的「提升」。
        """
        d_t = (cls._row_ms(old) - cls._row_ms(g)) / 1000.0
        d_b = float(g.get("bvs") or 0) - float(old.get("bvs") or 0)
        d_s = float(g.get("stnb") or 0) - float(old.get("stnb") or 0)
        return max(0.0, d_t), max(0.0, d_b), max(0.0, d_s)

    @staticmethod
    def _gaps_to_pb(g, pb):
        """这局离当前 PB 差多少：时间、3BV/s、stnb 三项一起算。

        三列都写「本局 − PB」，正负号就是方向。时间越大越差，所以是正数
        （+4.782 秒）；3BV/s 和 stnb 在同一 BV 下都只随用时单调递减，PB 又是
        该 BV 最快的局，所以这两项恒不高于 PB、差值恒为负数（−0.94 就是比
        PB 低 0.94）。同局返回 "PB"，没基准或指标缺失返回 "—"。
        """
        p = pb.get((App._pb_cat(g), g.get("level"), g.get("bv")))
        if not p:
            return {"time": "—", "bvs": "—", "stnb": "—"}
        if p.get("path") and p.get("path") == g.get("path"):
            return {"time": "PB", "bvs": "PB", "stnb": "PB"}

        def num(row, key):
            try:
                return float(row.get(key) or 0)
            except (TypeError, ValueError):
                return 0.0

        def diff(key):
            # 缺指标（值为 0）就不报差，否则会拿 0 算出一个"差满"的假数
            a, b = num(g, key), num(p, key)
            return (a - b) if a > 0 and b > 0 else "—"

        return {
            "time": max(0, App._row_ms(g) - App._row_ms(p)) / 1000.0,
            "bvs": diff("bvs"),
            "stnb": diff("stnb"),
        }

    @staticmethod
    def _gap_to_pb(g, pb):
        """这一局比当前 PB 慢多少秒：同一局 → "PB"，没有基准 → "—"。

        按行的风格取对应的榜（NF 局比 NF PB，其余比全PB），与 PB 页签同口径，
        两个页面来回看不会出现两个数。用整数毫秒相减，免得浮点差出 1 ms。

        三项差距的完整版见 _gaps_to_pb；这里只取时间，给每日明细表用。
        """
        return App._gaps_to_pb(g, pb)["time"]

    def set_pb_style(self, key):
        self._pb_style = key
        for k, p in self.pb_style_seg.items():
            p.set_selected(k == key)
        self.render_pb()

    def set_pb_metric(self, key):
        self._pb_metric = key
        for k, p in self.pb_metric_seg.items():
            p.set_selected(k == key)
        self.render_pb()

    def _recompute_pb(self):
        """本地 PB 与网站 PB 取并集。两边谁先到都行，后到的负责重算。

        「最近完成局」那列写着「距当前 PB」，所以它跟着这里一起重画 ——
        网站 PB 是异步后到的，漏掉这一步，比较列会一直停在本地 PB 上，
        直到下一次扫描才变。重画一次 9~26 ms，可以忽略。
        """
        self.pb_rows = core.merge_site_pb(self.pb_local_rows, self.site_pb)
        self.render_pb()
        if getattr(self, "tbl_recent", None) is not None:
            self.render_recent()

    def render_pb(self):
        q = self.var_q.get().strip().lower()
        cat = self._pb_style
        rows = {}
        for r in self.pb_rows:
            # 两张榜各自独立：全PB 里会出现 NF 获胜局（风格列标 NF），
            # NF PB 里的每一行必然也是 NF，但它们是不同榜，不能混在一起。
            if (r.get("category") or "all") != cat:
                continue
            rows[(r["level"], int(r["bv"]))] = r

        self._pb_index = rows
        grid = self.pb_grid
        grid.set_counts(self.pb_counts)
        n, _cells = grid.bind_data(self._pb_metric, rows, cat)

        hits = set()
        if q:
            if q.isdigit():
                hits = {key for key in rows if str(key[1]) == q}
            else:
                hits = {key for key, r in rows.items()
                        if q in (r.get("file") or "").lower()}
            grid.set_hits(hits)

        self._pb_picked = None
        self._set_pb_detail("")
        self.btn_pb_play.set_enabled(False)
        self.btn_pb_reveal.set_enabled(False)
        self.lbl_pb_hint.config(
            text=("颜色按全国名次" if grid.mode == "rank" else "颜色按本级快慢")
            if n else "")
        if not n:
            self._set_pb_detail("三个级别都还没有成绩", dim=True)
        elif q:
            self._set_pb_detail("搜到 %d 个" % len(hits))

        bits = []
        if self.site_pb:
            n_site = sum(1 for r in self.pb_rows
                         if (r.get("category") or "all") == cat
                         and r.get("source") == "site")
            bits.append("网站 PB %d 条%s" % (
                len(self.site_pb),
                "（本榜 %d 条领先本地）" % n_site if n_site else ""))
        elif self.site_pb_err:
            bits.append(self.site_pb_err)
        elif not self.oms_user:
            bits.append("未登录，仅显示本地")
        self.lbl_pb_count.config(
            text=" · ".join(bits),
            fg=C_ERR if self.site_pb_err else C_DIM)
        self.after_idle(self._pb_fit)

    def _on_bvmin_change(self):
        if self._bv_guard:
            return
        try:
            v = max(0, int((self.var_bvmin.get() or "").strip()))
        except ValueError:
            return                      # 还在输，等收工时再归一
        self._bvmin_eff = v
        self.render_top()

    def _settle_bvmin(self, restore=False, quiet=False):
        """这一格收工：把内容归成有效数字（或退回上次生效的值），再交出焦点。

        逐字 render 是为了边输边看结果，但输入框本身不会被「提交」——用户点到
        按钮、表格上时 Tk 不会换焦点，光标就一直闪。所以回车、Esc、失焦三处都
        走这里，把内容和焦点一次收干净。
        """
        raw = (self.var_bvmin.get() or "").strip()
        ok = raw.isdigit()
        if restore or not ok:
            val = self._bvmin_eff
        else:
            val = max(0, int(raw))
        self._bv_guard = True
        self.var_bvmin.set(str(val))
        self._bv_guard = False
        if val != self._bvmin_eff:
            self._bvmin_eff = val
            self.render_top()
        self.bvmin_box.selection_clear()
        if not quiet:
            self.focus_set()

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
            self._bvmin_eff = core.BV_MIN_DEFAULT[lv]
        self.render_top()

    def _top_site_note(self):
        """前端成绩页顶部：网站 Top5 的载入状态。"""
        if self.site_top_err:
            return self.site_top_err, C_ERR
        if not self.oms_user:
            return "未登录，仅显示本地成绩", C_DIM
        if self.site_top_uid is None:
            return "网站 Top5 尚未读取", C_DIM
        n_boards = len(self.site_top)
        n_videos = len(core.site_top_video_ids(self.site_top))
        if not n_boards:
            return "网站暂无 Top5 成绩", C_DIM
        text = "网站 Top5 已载入：%d 个小榜 / %d 条录像" % (n_boards, n_videos)
        if self.site_top_detail_err:
            text += " · " + self.site_top_detail_err
        return text, (C_WARN if self.site_top_detail_err else C_DIM)

    def render_top(self):
        bvmin = getattr(self, "_bvmin_eff", 0)
        style = None if self._top_style == "all" else "nf"
        cat = "nf" if style == "nf" else "all"
        site_map = (core.site_top_rows(self.site_top, self.site_top_details)
                    if self._site_top_ready() else {})
        for metric, table in self.top_tables.items():
            local = core.top5(self.games, self._level, metric, bvmin, style=style)
            site_rows = [r for r in site_map.get((cat, self._level, metric), [])
                         if r.get("bv") is None or r["bv"] >= bvmin]
            rows = []
            for i, g in enumerate(core.merge_top_rows(local, site_rows, metric), 1):
                item = dict(g)
                item["rank"] = i
                item["style_cn"] = core.STYLE_CN.get(g.get("style"), "?")
                item["source_cn"] = core.TOP_SOURCE_CN.get(g.get("source"), "")
                rows.append(item)
            table.set_rows(rows)
        if hasattr(self, "lbl_top_site"):
            text, color = self._top_site_note()
            if self.var_lsf_fixed.get():
                text += " · LSF 用固定基准（赛季快照 %s）" % SEASON_ANCHORS_AT
            else:
                text += " · LSF 用实时锚点"
            if not getattr(self, "_local_rce", True):
                text += " · 本地 NF/FL 不可用，NF 仅网站"
                color = C_WARN
            self.lbl_top_site.config(text=text, foreground=color)
        self.render_lsf()

    def render_lsf(self):
        """个人 LSF：本地 + 网站合并口径，跟上面三张表同一批数据。

        锚点是全站前二十名的水平，没有它算不出来；没有时卡片留「—」。
        勾了「固定 LSF 基准」就用硬编码的赛季基准，不随网站更新。
        """
        card = getattr(self, "lsf_card", None)
        if card is None:
            return
        anchors = self._lsf_anchors_eff()
        if not anchors:
            card.set_data(None)
            return
        key = (id(self.games), len(self.games), id(self.site_top),
               id(self.site_top_details), id(anchors))
        if self._lsf_cache and self._lsf_cache[0] == key:
            local = self._lsf_cache[1]
        else:
            local = core.lsf_report(self.games, self.site_top,
                                    self.site_top_details, anchors)
            local["sens"] = core.lsf_sensitivity(
                self.games, self.site_top, self.site_top_details, anchors)
            self._lsf_cache = (key, local)
        card.set_data(local)

    def _lsf_anchors_eff(self):
        if self.var_lsf_fixed.get():
            return SEASON_ANCHORS
        return self.lsf_anchors

    def toggle_lsf_fixed(self):
        """固定基准：打开 = 用硬编码的赛季基准；关闭 = 回到实时。"""
        if self.var_lsf_fixed.get():
            save_config(lsf_fixed=True)
            self.set_status("LSF 基准已固定：赛季快照 %s" % SEASON_ANCHORS_AT,
                            "ok", hold=4000)
        else:
            save_config(lsf_fixed=False)
            self.set_status("LSF 基准回到实时", "ok", hold=3000)
        self._lsf_cache = None
        self._gain_cache = None
        self.render_lsf()
        self.render_top()
        self.render_recent()

    def set_status(self, text, kind="dim", hold=None, transient=False):
        """写状态栏。``hold`` 是毫秒：到点自动退回写之前那一行。

        一次性回执（「元扫雷已启动」这类）必须自己退场 —— 常驻下来就会变成
        一句跟当前界面对不上的话，用户关掉游戏之后还能看到它。

        ``transient`` 标的是「说了就过期」的过渡话（「正在启动元扫雷…」）：
        它自己不需要计时退场，但**不能被当成「原样」记下来**。否则后一条
        回执退场时会把「正在启动…」捞回来 —— 明明已经启动成功了，底下还
        写着正在启动。
        """
        color = {"dim": C_DIM, "ok": C_OK, "warn": C_WARN, "err": C_ERR}.get(kind, C_DIM)
        # 序号一变，排队中的「几秒后恢复原样」就作废 —— 期间有新消息时不能覆盖它
        self._status_seq = getattr(self, "_status_seq", 0) + 1
        seq = self._status_seq
        cur = self.lbl_status.cget("text")
        # 上一行如果本身就是一条会自己退场的消息，它不能当「原样」被还原 ——
        # 否则新消息退场时会把一句已经过期的话又捞回来。
        if cur and cur == getattr(self, "_status_temp", ""):
            self._status_prev = getattr(self, "_status_temp_prev", ("", C_DIM))
        else:
            self._status_prev = (cur, self.lbl_status.cget("foreground"))
        self._status_temp = text if (hold or transient) else ""
        self._status_temp_prev = self._status_prev
        self.lbl_status.config(text=text, foreground=color)
        if hold:
            self.after(hold, lambda s=seq: self._status_back(s))

    def _status_back(self, seq=None):
        """退回写之前那一行。给了 seq 就表示「这条消息还是最新的才退」。"""
        if seq is not None and getattr(self, "_status_seq", 0) != seq:
            return
        text, color = getattr(self, "_status_prev", ("", C_DIM))
        self._status_seq = getattr(self, "_status_seq", 0) + 1
        self.lbl_status.config(text=text, foreground=color)

    def _status_later(self, ms, text, color=None):
        """过一会儿把状态栏还原成 text；这期间有新消息就不动它。"""
        seq = getattr(self, "_status_seq", 0)
        color = color or C_DIM

        def _back():
            if getattr(self, "_status_seq", 0) == seq:
                self.lbl_status.config(text=text, foreground=color)
                self._status_seq += 1
        self.after(ms, _back)

    # ---------- 读取进度条 ----------
    def _progress_begin(self):
        """扫描开始：先亮一条「不确定进度」的来回动画，等清点出总数再切成百分比。"""
        self._scan_total = 0
        self._scan_done = 0
        self._scan_pct = -1
        self._scan_t0 = time.monotonic()
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

    def _scan_eta_text(self):
        """按「已读 / 用时」估剩余时间；开局前几秒速率不稳，先不报。"""
        if not self._scan_total or self._scan_done <= 0:
            return ""
        elapsed = time.monotonic() - getattr(self, "_scan_t0", 0)
        if elapsed < 3:
            return ""
        remain = (self._scan_total - self._scan_done) * elapsed / self._scan_done
        if remain < 5:
            return ""
        if remain < 60:
            return "约 %d 秒" % int(remain)
        return "约 %d 分 %02d 秒" % (remain // 60, remain % 60)

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
        cnts = self.counts if isinstance(self.counts, dict) else {}
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["category", "category_cn", "level", "level_cn", "bv",
                        "style", "rce", "best_time", "timems", "bvs", "stnb",
                        "games_in_bv", "source", "site_video_id", "site_rank",
                        "date", "file", "path"])
            for r in self.pb_rows:
                cat = r.get("category") or "all"
                cnt = cnts.get(cat, {}) if isinstance(cnts.get(cat), dict) else {}
                w.writerow([cat, core.PB_CATEGORY_CN.get(cat, cat),
                            r["level"], core.LEVEL_CN[r["level"]], r["bv"],
                            r.get("style") or "", r.get("rce", ""),
                            f"{r['time']:.3f}", r.get("timems", ""),
                            f"{r['bvs']:.3f}", f"{r['stnb']:.2f}",
                            cnt.get((r["level"], r["bv"]), 0),
                            core.PB_SOURCE_CN.get(r.get("source"), ""),
                            r.get("site_video_id", ""), r.get("site_rank", ""),
                            r["date"], r["file"], r["path"]])
        self.set_status("已导出：" + path, "ok")


def main():
    if "--selftest" in sys.argv[1:]:
        # 打包后自检用：证明这份 exe 能启动、能解包，然后立刻退出。
        # 不建窗口、不扫录像，只往 %TEMP% 写一行回执；build.cmd 靠这个文件
        # 判断产物有没有被安全软件当场拦掉（被拦时进程起不来，回执也不会出现）。
        try:
            out = os.path.join(os.environ.get("TEMP") or ".", "ytb-selftest.txt")
            # 顺带画一张总结图：PIL / 字体 / 剪贴板这条路只有在打包后才可能
            # 出问题（少打一个模块、少打一个字体），回执里必须能看出来
            png = "summary_png=?"
            mk = "mascot=?"
            try:
                probe = {"date": "2026-11-17", "games": 1,
                         "t_from": 0.0, "t_till": 60.0, "span": 60.0,
                         "best": 1.0, "avg": 1.0, "worst": 1.0,
                         "bvs_max": 1.0, "bvs_avg": 1.0, "bvs_min": 1.0,
                         "stnb_max": 1.0, "stnb_avg": 1.0, "stnb_min": 1.0,
                         "nf": 1, "fl": 1}
                img = summary.daily_png(probe, "e", "高级", APP_VER)
                png = "summary_png=%dx%d" % img.size
            except Exception as e:              # noqa: BLE001
                png = "summary_png=ERR %r" % (e,)
            # 贴纸是后加的 --add-data，漏打时图照样能出（只是下面空着一块），
            # 所以必须单独报一行，否则「图能画」会被当成「一切都对」
            try:
                img2 = summary.mascot(120 * summary.SCALE)
                mk = "mascot=%dx%d" % img2.size if img2 is not None else "mascot=missing"
            except Exception as e:              # noqa: BLE001
                mk = "mascot=ERR %r" % (e,)
            # 每日页右键的「全部级别」走 levels_png：三级块 + 贴纸 + 字体一起用，
            # 只有打包后才可能缺东西，单独报一行
            lvl = "levels_png=?"
            try:
                img3 = summary.levels_png(
                    [("b", "初级", dict(probe, games=2)),
                     ("e", "高级", probe)], APP_VER, "2026-11-17")
                lvl = "levels_png=%dx%d" % img3.size
            except Exception as e:              # noqa: BLE001
                lvl = "levels_png=ERR %r" % (e,)
            # 详细版（每日页右键「详细总结」）走复盘卡版式，打包后同样要能跑
            det = "detail_png=?"
            try:
                g6 = [{"time": 10.0 + i, "bv": 30 + i, "style": "nf",
                       "stnb": 30.0 + i * 2.0, "t0": 1763200000.0 + i * 60}
                      for i in range(6)]
                img4 = summary.levels_detail_png(
                    [("e", "高级", probe)], {"e": g6}, APP_VER, "2026-11-17")
                det = "detail_png=%dx%d" % img4.size
            except Exception as e:              # noqa: BLE001
                det = "detail_png=ERR %r" % (e,)
            # 本地 NF/FL 全靠 ms-toollib 重演；打包后它若加载失败会静默降级成
            # 「仅按文件名」（整批本地局风格「?」、NF 只剩网站），回执必须报出来
            try:
                lib = "ms_toollib=%s" % ("ok" if core.MSLIB
                                         else "MISSING " + (core.LOAD_ERR or ""))
            except Exception as e:              # noqa: BLE001
                lib = "ms_toollib=ERR %r" % (e,)
            # 兜底路径也要在冻结产物里真跑一次：构造最小 evf v4 头部读一遍
            try:
                import tempfile
                hb = bytearray()
                hb += bytes([4, 0xF0, 0, 8, 8]) + (10).to_bytes(2, "big")
                hb += bytes([16]) + (0).to_bytes(2, "big") + (4).to_bytes(2, "big")
                hb += (1234).to_bytes(4, "big") + b"CN"
                hb += (1700000000000000).to_bytes(8, "big") * 2
                hb += b"selftest\0" + b"p\0" + b"\0" + b"\0" + (0).to_bytes(2, "big")
                hb += b"\xff" * 8 + (0).to_bytes(2, "big") + b"\0" + (0).to_bytes(2, "big")
                lp = os.path.join(tempfile.gettempdir(), "ytb-selftest.evf")
                with open(lp, "wb") as fh:
                    fh.write(hb)
                ld = core._evf_lite(lp)
                okl = bool(ld) and (ld["rce"] == 0 and ld["bv"] == 4
                                    and ld["timems"] == 1234 and ld["level"] == "b"
                                    and ld["ident"] == "p")
                lite = "evf_lite=%s" % ("ok" if okl else "BAD %r" % (ld,))
                os.remove(lp)
            except Exception as e:              # noqa: BLE001
                lite = "evf_lite=ERR %r" % (e,)
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("version=%s\nfrozen=%s\n%s\n%s\n%s\n%s\n%s\n%s\n"
                         % (APP_VER, bool(getattr(sys, "frozen", False)), png, mk,
                            lvl, det, lib, lite))
        except Exception:
            pass
        return
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
    # 打开工具箱时自动拉起上次选中的程序（设置里默认关）。--probe 是布局自检，
    # --no-autolaunch 给截图/冒烟测试用：都不该在无人值守时顺手开个游戏。
    if "--probe" not in args and "--no-autolaunch" not in args:
        app.after(900, app.auto_launch_game)
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

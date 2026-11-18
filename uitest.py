"""跑界面自动化时用的小工具：窗口不出现在用户屏幕上，截图不依赖屏幕。

为什么要：以前跑一次冒烟测试就在用户桌面上弹十几个窗口，挡住他正在做的事，
还会抢走键盘焦点。`import uitest` 之后（必须在 `import app` 之前）所有窗口都
被挪到屏幕外、从任务栏隐掉、禁止被激活，见 app.py 里 MS_PB_QUIET 那一段。

截图也不能再抓屏幕坐标（窗口在屏幕外，那个位置什么都没有），改成用
PrintWindow 直接把窗口自己的像素画到位图里 —— 窗口不可见也能抓。
"""

import ctypes
import os
from ctypes import wintypes

os.environ.setdefault("MS_PB_QUIET", "1")

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

_user32.GetDC.restype = ctypes.c_void_p
_user32.GetDC.argtypes = [ctypes.c_void_p]
_user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
_user32.PrintWindow.restype = wintypes.BOOL
_gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
_gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
_gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_int]
_gdi32.SelectObject.restype = ctypes.c_void_p
_gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_uint]
_gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
_gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
_gdi32.CreateSolidBrush.restype = ctypes.c_void_p
_gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
_user32.FillRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
_user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.GetAncestor.restype = ctypes.c_void_p
_user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def hwnd_of(win):
    """Tk 窗口的外层句柄（带标题栏那一层）。"""
    return _user32.GetAncestor(ctypes.c_void_p(win.winfo_id()), 2)   # GA_ROOT


def _capture(hwnd, w, h, flag):
    """按指定的 PrintWindow 标志抓一次客户区，返回 PIL 图。"""
    from PIL import Image

    hdc = _user32.GetDC(hwnd)
    mdc = _gdi32.CreateCompatibleDC(hdc)
    bmp = _gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = _gdi32.SelectObject(mdc, bmp)
    try:
        _user32.PrintWindow(hwnd, mdc, flag)
        bi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        _gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    finally:
        _gdi32.SelectObject(mdc, old)
        _gdi32.DeleteObject(bmp)
        _gdi32.DeleteDC(mdc)
        _user32.ReleaseDC(hwnd, hdc)


def grab(win, path):
    """把窗口自己的像素存成 PNG，窗口对用户不可见也能抓。返回 (宽, 高)。

    抓的是**客户区**句柄（`winfo_id`）：外层带标题栏那个走 PrintWindow 只画
    得出标题栏，客户区一片白。所以截图里没有标题栏，内容是全的。

    标志位：2 = PW_RENDERFULLCONTENT，读 DWM 的合成面，Tk 的表格、画布都在
    里面；窗口一旦被挪到屏幕外 DWM 就不给合成面了，那时退回 0（WM_PRINT，
    只有 Tk 自己实现的控件，Treeview 会是空白）。
    """
    win.update_idletasks()
    win.update()
    hwnd = ctypes.c_void_p(win.winfo_id())
    rect = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise RuntimeError("窗口还没布局：%dx%d" % (w, h))

    img = _capture(hwnd, w, h, 2)
    gray = img.convert("L")
    letters = gray.getextrema()
    if letters == (255, 255):                  # 全白 = 没合成面，退回 WM_PRINT
        img = _capture(hwnd, w, h, 0)
    img.save(path)
    return w, h


def is_quiet():
    return os.environ.get("MS_PB_QUIET") == "1"

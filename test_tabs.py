"""三个页签各截一张图（含 PB 的 NF/FL 过滤），用于验收与更新 README 预览图。

用法：python test_tabs.py            # 用 dist\\元神工具箱.exe
      python test_tabs.py --src      # 用源码跑（同一套逻辑，方便改样式后快速看）
"""
import ctypes
import os
import subprocess
import sys
import time

from PIL import Image

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)   # 不声明就会拿到 1/1.5 的虚拟坐标
except Exception:
    pass

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "元神工具箱.exe")
PYC = r"C:\Users\TANGWENJIAN\.workbuddy\binaries\python\envs\packtk\Scripts\python.exe"
REPLAY = os.path.join(os.environ["APPDATA"], "Metasweeper3.3.4", "replay")


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


def find_window(sub="元神工具箱"):
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, b, n + 1)
            if sub in b.value and user32.IsWindowVisible(hwnd):
                out.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return out


def capture(hwnd, path):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)          # PW_RENDERFULLCONTENT
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    img.save(path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return w, h


def run(tab, out, wait=6.0):
    if "--src" in sys.argv:
        cmd = [PYC, os.path.join(HERE, "app.py"), "--root", REPLAY, "--tab", tab]
        cwd = HERE
    else:
        cmd = [EXE, "--root", REPLAY, "--tab", tab]
        cwd = os.path.dirname(EXE)
    p = subprocess.Popen(cmd, cwd=cwd)
    deadline = time.time() + wait
    hwnd = None
    while time.time() < deadline:
        time.sleep(0.3)
        wins = find_window()
        if wins:
            hwnd = wins[0]
            break
    if not hwnd:
        print("  [FAIL] 没有窗口")
        p.terminate()
        return
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
    time.sleep(1.6)
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    sz = capture(hwnd, os.path.join(HERE, out))
    print("  %-8s rect=%dx%d  shot=%dx%d" % (tab, r.right - r.left, r.bottom - r.top, *sz))
    user32.PostMessageW(hwnd, 0x0010, 0, 0)
    try:
        p.wait(timeout=6)
    except subprocess.TimeoutExpired:
        p.terminate()


if __name__ == "__main__":
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    tabs = [("overview", "tab_overview.png"), ("pb", "tab_pb.png"),
            ("top", "tab_top.png"), ("upload", "tab_upload.png")]
    for tab, out in tabs:
        if only and tab not in only:
            continue
        run(tab, out)
    print("done")

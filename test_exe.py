"""打包后 exe 的真实启动测试：找窗口、截图、验数据、退出。"""
import ctypes
import json
import os
import subprocess
import sys
import time

from PIL import Image, ImageGrab

# 本脚本也要声明 DPI 感知，否则 GetWindowRect 拿到的是 1/1.5 的虚拟坐标
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


def capture(hwnd, path):
    """PrintWindow 直接抓窗口表面，不受其它窗口遮挡影响。"""
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
    bi.bmiHeader.biHeight = -h                # top-down
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    img.save(path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return img.size


EXE = r"D:\metasweeper-pb\dist\元神工具箱.exe"
HERE = os.path.dirname(os.path.abspath(__file__))


def find_window(substr="元神工具箱"):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if substr in buf.value and user32.IsWindowVisible(hwnd):
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def shot(hwnd, name):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    print("  rect", r.left, r.top, r.right - r.left, r.bottom - r.top)
    return capture(hwnd, os.path.join(HERE, name))


def run_case(label, exe, workdir, cfg_root=None, wait=14):
    print("\n===", label)
    cfg_dir = os.path.join(os.environ["APPDATA"], "元神工具箱")
    cfg_path = os.path.join(cfg_dir, "config.json")
    if cfg_root is None:
        if os.path.exists(cfg_path):
            os.remove(cfg_path)
    else:
        os.makedirs(cfg_dir, exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"root": cfg_root}, fh, ensure_ascii=False)

    p = subprocess.Popen([exe], cwd=workdir)
    wins = []
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.4)
        wins = find_window()
        if wins:
            break
    print("  windows:", wins)
    if not wins:
        print("  [FAIL] 没找到窗口")
        p.terminate()
        return p
    hwnd, title = wins[0]
    # 置顶，避免被其它窗口盖住导致截图是别人的界面
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.8)
    print("  shot:", shot(hwnd, f"exe_{label}.png"))
    user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
    try:
        p.wait(timeout=6)
    except subprocess.TimeoutExpired:
        p.terminate()
    print("  exit code:", p.returncode)
    return p


if __name__ == "__main__":
    real = os.path.join(os.environ["APPDATA"], "Metasweeper3.3.4", "replay")
    run_case("auto", EXE, os.path.dirname(EXE), cfg_root=real)
    # 无配置 + 放在元扫雷目录：应自动认出 replay，不弹选择框
    run_case("inplace", os.path.join(os.environ["APPDATA"], "Metasweeper3.3.4",
                                     "元神工具箱.exe"),
             os.path.join(os.environ["APPDATA"], "Metasweeper3.3.4"), cfg_root=None)
    run_case("firstrun", EXE, os.path.dirname(EXE), cfg_root=None, wait=8)
    print("\nDONE")

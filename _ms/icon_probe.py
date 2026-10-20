"""探针：一档一色，看 Windows 任务栏/标题栏实际取 ico 的哪一档。

用法：python _ms/icon_probe.py
输出：_probe_ico.png（任务栏图标放大）、_probe_colors.txt（各档颜色表）
"""
import ctypes
import os
import subprocess
import sys
import time

from PIL import Image, ImageDraw, ImageGrab

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_icon2 as M          # 复用 write_ico

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 一档一色（纯色 + 白底数字，便于肉眼与像素双重判定）
COLORS = {
    16: (230, 30, 30), 20: (240, 130, 20), 24: (240, 210, 20),
    30: (40, 170, 60), 32: (40, 90, 230), 36: (150, 40, 220),
    40: (240, 60, 190), 42: (30, 200, 200), 48: (250, 250, 250),
    64: (128, 128, 128), 96: (120, 0, 0), 128: (0, 100, 0), 256: (0, 0, 120),
}


def main():
    frames = []
    for s, c in COLORS.items():
        im = Image.new("RGBA", (s, s), c + (255,))
        M.write_ico(os.path.join(HERE, "_probe.ico"), frames + [(s, im)])
        frames.append((s, im))
    M.write_ico(os.path.join(HERE, "_probe.ico"), frames)

    app = os.path.join(HERE, "_probe_app.py")
    with open(app, "w", encoding="utf-8") as fh:
        fh.write(
            "import tkinter as tk\n"
            "r = tk.Tk()\n"
            "r.title('图标探针 iconprobe')\n"
            "r.geometry('420x240+900+400')\n"
            "r.iconbitmap(r'%s')\n"
            "r.after(6000, r.destroy)\n"
            "r.mainloop()\n" % os.path.join(HERE, "_probe.ico").replace("\\", "\\\\"))

    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    py = sys.executable
    p = subprocess.Popen([py, app])
    time.sleep(3.0)
    img = ImageGrab.grab(all_screens=True)
    img.save(os.path.join(HERE, "_probe_ico_full.png"))

    # 任务栏那条
    img.crop((0, img.height - 70, img.width, img.height)).save(
        os.path.join(HERE, "_probe_taskbar.png"))
    time.sleep(3.5)
    try:
        p.wait(timeout=8)
    except subprocess.TimeoutExpired:
        p.terminate()

    with open(os.path.join(HERE, "_probe_colors.txt"), "w", encoding="utf-8") as fh:
        for s in sorted(COLORS):
            fh.write("%3d  %s\n" % (s, COLORS[s]))
    print("done")


main()

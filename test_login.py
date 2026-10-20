"""登录窗渲染检查：起窗 → 等验证码图片 → 截图 → 关闭。不提交登录。"""
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ctypes.windll.shcore.SetProcessDpiAwareness(1)

import app  # noqa: E402
import test_tabs  # noqa: E402

REPLAY = os.path.join(os.environ["APPDATA"], "Metasweeper3.3.4", "replay")


class Probe(app.App):
    def __init__(self):
        super().__init__(REPLAY)

    def _boot(self):
        self.start_scan()
        self.after(1200, self._open)

    def _open(self):
        self.dlg = app.LoginDialog(self, self.oms)
        self.after(3500, self._shoot)

    def _shoot(self):
        dlg = self.dlg
        ctypes.windll.user32.SetForegroundWindow(int(dlg.frame(), 16))
        time.sleep(0.6)
        hwnd = int(dlg.frame(), 16)
        r = test_tabs.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        print("dialog rect", r.right - r.left, r.bottom - r.top,
              "hashkey", bool(dlg.hashkey), "img", bool(dlg._img))
        test_tabs.capture(hwnd, os.path.join(HERE, "dlg_login.png"))
        dlg.destroy()
        self.after(300, self.destroy)


if __name__ == "__main__":
    a = Probe()
    a.after(15000, a.destroy)
    a.mainloop()

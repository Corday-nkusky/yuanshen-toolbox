"""上传页交互测试：走完整 UI 路径（选范围 → 选行 → 上传 → 行状态更新）。

用法：python test_upload.py [--live] [N]
      默认只做「选范围 + 选行」的界面验证，不真传；
      --live N 会真的上传前 N 条（谨慎：会写进你的账号）。
"""
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ctypes.windll.shcore.SetProcessDpiAwareness(1)

import app  # noqa: E402
import openms  # noqa: E402

REPLAY = os.path.join(os.environ["APPDATA"], "Metasweeper3.3.4", "replay")
LIVE = "--live" in sys.argv
N = 2
for a in sys.argv[1:]:
    if a.isdigit():
        N = int(a)


class Probe(app.App):
    def __init__(self):
        super().__init__(REPLAY)

    def _boot(self):
        self.start_scan()
        self.show_tab("upload")
        self.after(2500, self._step1)

    def _step1(self):
        print("games:", len(self.games), "acct:", bool(self.oms_user),
              "videos:", len(self.oms_videos))
        self.toggle_scope("pb_nf")
        self.after(300, self._step2)

    def _step2(self):
        print("scope pb_nf -> selected:", len(self.up_sel))
        self.clear_selection()
        self.select_all_pending()
        print("all pending -> selected:", len(self.up_sel))
        self.after(300, self._step3)

    def _step3(self):
        pending = [r["path"] for r in self.up_rows if not r.get("uploaded")]
        print("pending rows:", len(pending))
        if not LIVE:
            print("dry run ok; 截图")
            self.after(400, self._shoot)
            return
        # 只传前 N 条
        self.up_sel = set(pending[:N])
        self.tbl_up.select_paths(self.up_sel)
        self._update_up_stat()
        print("start upload", N)
        self._start_upload(pending[:N])
        self.after(1000, self._wait)

    def _wait(self):
        if self._up_running:
            self.after(600, self._wait)
        else:
            print("done: ok=%d dup=%d fail=%d" % (self._up_ok, self._up_dup,
                                                  self._up_fail))
            for r in self.up_rows:
                if r.get("status", "").startswith(("上传", "网站已有", "失败")):
                    print("   ", r["file"], "->", r["status"], r.get("site"))
                    break
            self.after(600, self._shoot)

    def _shoot(self):
        print("up_stat:", self.lbl_up_stat.cget("text"))
        print("log:", self.lbl_up_log.cget("text"))
        os.system('python -c "pass"')
        self.after(200, self.destroy)


if __name__ == "__main__":
    a = Probe()
    a.after(12000, a.destroy)      # 兜底
    a.mainloop()

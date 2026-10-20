"""断点续传 / 单条失败不中断 / 重试失败 的界面级测试。

用假客户端替代真实站点（不会往 openms.top 传任何东西），跑法：
    python test_resume_ui.py     全部通过退出码 0，否则 1
依赖本机录像目录（%APPDATA%\\Metasweeper3.3.4\\replay），找不到就跳过。
"""

import os
import sys
import time

import app as A
import journal
import openms

REPLAY = os.path.join(os.environ.get("APPDATA", ""), "Metasweeper3.3.4", "replay")


def pump(win, cond, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        win.update()
        if cond():
            return True
        time.sleep(0.03)
    return False


class FakeClient:
    """前 fail_first 条抛网络错误，其余成功。"""

    def __init__(self, fail_first=1):
        self.fail_first = fail_first
        self.n = 0

        class _Store:                    # 只实现界面会碰到的几个方法
            def username(self):
                return "tester"

            def password(self):
                return ""

            def session(self):
                return ""

            def clear_session(self):
                pass

        self.store = _Store()

    def upload(self, path, retries=3):
        self.n += 1
        if self.n <= self.fail_first:
            raise openms.OmsError("网络错误：模拟断线")
        return {"ok": True, "state": "c", "vid": self.n, "reason": "", "fatal": None}

    def whoami(self):
        return {"id": 1, "username": "tester", "realname": "x"}

    def videos(self, uid):
        return []

    def identifiers(self, uid):
        return ["tester"]


class DeadClient(FakeClient):
    def upload(self, path, retries=3):
        self.n += 1
        raise openms.OmsError("网络错误：模拟断网")

    def whoami(self):
        return None


def main():
    if not os.path.isdir(REPLAY):
        print("跳过：找不到录像目录 %s" % REPLAY)
        return 0

    A.messagebox.showwarning = lambda *x, **k: None      # 别让弹窗卡住测试
    A.messagebox.showinfo = lambda *x, **k: None
    journal.clear()

    win = A.App(REPLAY)
    win.withdraw()
    assert pump(win, lambda: not win._scanning and bool(win.games)), "首次扫描没完成"

    win.oms = FakeClient(fail_first=1)
    win.oms_user = {"id": 1, "username": "tester", "realname": "x"}
    win.oms_synced = False
    win._rebuild_up_rows()

    batch = [r["path"] for r in win.up_rows if not r.get("uploaded")][:3]
    assert len(batch) == 3, "录像不够 3 条，无法测试"

    win._start_upload(batch)
    assert pump(win, lambda: not win._up_running), "上传没结束"
    win.update()

    checks = [
        ("单条失败不中断：成功 2 条", (win._up_ok, win._up_fail) == (2, 1)),
        ("失败项记在集合里", len(win._last_failed) == 1),
        ("台账只记成功的那 2 条", journal.count() == 2),
        ("重试按钮变成 (1)", "1" in win.btn_retry._text and win.btn_retry._enabled),
    ]

    # 重试失败项：这次全部成功
    win.oms.n = 99
    win.retry_failed()
    pump(win, lambda: not win._up_running)
    win.update()
    checks += [
        ("重试后全部成功", (win._up_ok, win._up_fail) == (1, 0)),
        ("台账累计 3 条", journal.count() == 3),
        ("重试按钮回到禁用", not win.btn_retry._enabled),
    ]

    # 模拟重启且还没联网：台账兜底，不该再显示「未上传」
    win.oms_synced = False
    win.oms_videos = []
    win._rebuild_up_rows()
    marked = [r for r in win.up_rows if r["status"].startswith("已传过")]
    checks.append(("离线重开：3 条标为已传过", len(marked) == 3))

    # 网站列表权威时以服务器为准
    win.oms_synced = True
    win._rebuild_up_rows()
    marked = [r for r in win.up_rows if r["status"].startswith("已传过")]
    checks.append(("同步后以网站为准：0 条", len(marked) == 0))

    # 连续断网 → 试 3 条就停批
    win.oms = DeadClient()
    win.oms_user = {"id": 1, "username": "tester", "realname": "x"}
    win._rebuild_up_rows()
    pending = [r["path"] for r in win.up_rows if not r.get("uploaded")][:8]
    win._start_upload(pending)
    pump(win, lambda: not win._up_running)
    win.update()
    checks.append(("连续断网只试 3 条就停", win._up_fail == 3))

    win.destroy()
    journal.clear()

    bad = 0
    for label, ok in checks:
        print("%s %s" % ("OK  " if ok else "FAIL", label))
        bad += 0 if ok else 1
    print("== %s ==" % ("全部通过" if not bad else "%d 项失败" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

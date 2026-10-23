"""上传页交互测试：扫描 → 选范围 → 清空 → 全选未传。

默认用临时数据目录，不联网、不碰真实账号与台账；退出码：全过 0，否则 1。
--live N 改用真实数据目录并真传前 N 条（谨慎：会写进账号）。

用法：python test_upload.py [--live] [N]
"""
import os
import shutil
import sys
import tempfile
import time

LIVE = "--live" in sys.argv
N = 2
for arg in sys.argv[1:]:
    if arg.isdigit():
        N = int(arg)

TMP = None
if not LIVE:
    TMP = tempfile.mkdtemp(prefix="ms-pb-upload-")
    os.environ["MS_PB_DATA_DIR"] = os.path.join(TMP, "data")

import ctypes  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ctypes.windll.shcore.SetProcessDpiAwareness(1)

import app as A  # noqa: E402
import openms  # noqa: E402

if not LIVE:
    A.App._restore_session = lambda self: None  # dry-run 不碰账号/网络

REPLAY = os.path.join(os.environ.get("APPDATA", ""), "Metasweeper3.3.4", "replay")
checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


def pump(win, cond, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        win.update()
        if cond():
            return True
        time.sleep(0.03)
    return False


def main():
    if not os.path.isdir(REPLAY):
        print("跳过：找不到录像目录 %s" % REPLAY)
        return 0

    A.messagebox.showwarning = lambda *a, **k: None
    A.messagebox.showinfo = lambda *a, **k: None
    win = A.App(REPLAY)
    win.withdraw()
    try:
        win.start_scan()
        ok = pump(win, lambda: not win._scanning and bool(win.games))
        check("扫描完成且有录像（%d 局）" % len(win.games), ok)
        if not ok:
            return 1

        win.show_tab("upload")
        win.update()
        check("读到了录像内容（rce）", bool(win.info.get("with_rce")))
        check("上传列表跟着扫描结果生成（%d 行）" % len(win.up_rows),
              len(win.up_rows) == len(win.games))
        pending = {r["path"] for r in win.up_rows if not r.get("uploaded")}
        if not LIVE:
            check("临时目录下没有网站/台账记录，全部为未上传",
                  len(pending) == len(win.up_rows))

        win.toggle_scope("pb_nf")
        want = set(openms.scope_paths(win.games, "pb_nf")) & pending
        check("选范围 PB·NF 选中 %d 条" % len(want), win.up_sel == want)
        check("统计行显示已选数", ("已选 %d" % len(want)) in win.lbl_up_stat.cget("text"))

        win.clear_selection()
        check("清空选择", not win.up_sel)
        check("清空后统计为 0", "已选 0" in win.lbl_up_stat.cget("text"))

        win.select_all_pending()
        check("全选未传选中 %d 条" % len(pending), win.up_sel == pending)
        check("表格选中状态同步", set(win.tbl_up.selected_paths()) == pending)
        check("选中项就是待传项", set(win._pending_paths()) == pending)

        if LIVE:
            pump(win, lambda: win.oms_user is not None, timeout=10)
            if not win.oms_user:
                print("跳过 live：当前数据目录没有登录账号")
                return 0
            batch = [r["path"] for r in win.up_rows
                     if not r.get("uploaded")][:N]
            if not batch:
                print("跳过 live：没有待上传录像")
                return 0
            win.up_sel = set(batch)
            win.tbl_up.select_paths(win.up_sel)
            win._update_up_stat()
            win._start_upload(batch)
            if not pump(win, lambda: not win._up_running):
                check("上传流程在超时前结束", False)
                return 1
            win.update()
            print("live: ok=%d dup=%d fail=%d" % (win._up_ok, win._up_dup,
                                                  win._up_fail))
            check("live 上传条数对得上",
                  win._up_ok + win._up_dup + win._up_fail == len(batch))
            check("live 没有失败", win._up_fail == 0)
    finally:
        try:
            win.destroy()
        except Exception:
            pass
        if TMP:
            shutil.rmtree(TMP, ignore_errors=True)

    bad = sum(1 for _l, ok in checks if not ok)
    print("== %s（%d 项）==" % ("全部通过" if not bad else "%d 项失败" % bad,
                               len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

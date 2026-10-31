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
        scope_set = set(openms.scope_paths(win.games, "pb_nf"))
        want = scope_set & pending
        check("选范围 PB·NF PB 选中 %d 条" % len(want), win.up_sel == want)
        check("统计行显示已选数", ("已选 %d" % len(want)) in win.lbl_up_stat.cget("text"))
        check("表里只剩这个范围的录像（%d 行）" % len(scope_set),
              {r["path"] for r in win.up_view} == scope_set)
        check("表格显示的行数跟着范围走",
              len(win.tbl_up.tv.get_children()) == len(scope_set))
        check("up_rows 仍是全集（逻辑不受过滤影响）",
              len(win.up_rows) == len(win.games))
        check("统计行写明当前范围", "当前范围" in win.lbl_up_stat.cget("text"))

        win.clear_selection()
        check("清空选择", not win.up_sel)
        check("清空后统计为 0", "已选 0" in win.lbl_up_stat.cget("text"))
        check("清空范围后表里恢复全部",
              len(win.up_view) == len(win.up_rows)
              and len(win.tbl_up.tv.get_children()) == len(win.up_rows))

        # 网站 PB 更快：对应本地 PB 不进入上传范围（不联网，直接注入一条记录）
        local_pb = win.pb_local_rows[0]
        scope = "pb_nf" if local_pb["category"] == "nf" else "pb"
        win.site_pb = [{
            "level": local_pb["level"], "bv": local_pb["bv"],
            "nf": local_pb["category"] == "nf",
            "timems": local_pb["timems"] - 1,
            "video_id": 9001, "rank": 1,
        }]
        win.site_pb_uid = 1
        win.oms_user = {"id": 1, "username": "test"}
        win._site_pb_changed()
        win.toggle_scope(scope)
        check("网站 PB 更快时，本地 PB 不进入上传范围",
              local_pb["path"] not in win._scope_path_set())
        check("统计行注明排除条数",
              "已排除 1 条本地 PB" in win.lbl_up_stat.cget("text"))
        win.clear_selection()
        win.site_pb = []
        win.site_pb_uid = None
        win.oms_user = None
        win._site_pb_changed()

        # 网站 Top5 已满五条且更快：对应级别的本地 Top5 不进入上传范围
        local_top = A.core.top5(win.games, "b", "time",
                                A.core.BV_MIN_DEFAULT["b"])
        top_records = {}
        for field, vals in (
                ("std:bt", [1, 2, 3, 4, 5]),
                ("std:bb", [1000, 999, 998, 997, 996]),
                ("std:bs", [1000, 999, 998, 997, 996])):
            top_records[field] = {"videos": [
                {"video_id": 9000 + i, "value": v}
                for i, v in enumerate(vals)]}
        win.site_top = top_records
        win.site_top_details = {}
        win.site_top_uid = 1
        win.site_top_err = ""
        win.site_top_detail_err = ""
        win.oms_user = {"id": 1, "username": "test"}
        win._site_top_changed()
        win.toggle_scope("top")
        check("网站 Top5 更快时，本地 Top5 不进入上传范围",
              bool(local_top) and local_top[0]["path"] not in win._scope_path_set())
        check("统计行注明被网站 Top5 排除",
              "已排除" in win.lbl_up_stat.cget("text")
              and "Top5" in win.lbl_up_stat.cget("text"))

        # Top5 通常比账号信息先到：账号晚到后顶部提示要跟着改
        win.oms_user = None
        win._site_top_changed()
        check("Top5 先到、账号未到时提示未登录",
              "未登录" in win.lbl_top_site.cget("text"))
        check("账号未落定时不并入网站 Top5",
              all(r.get("source") != "site"
                  for table in win.top_tables.values()
                  for r in table.rows))
        win._on_account(win._site_seq, {"id": 1, "username": "test"},
                        [], [], "")
        win.set_top_level("b")
        win.set_top_style("all")
        check("账号到达后顶部提示改为已载入",
              "已载入" in win.lbl_top_site.cget("text"))
        check("账号到达后网站成绩并入前端 Top5",
              any(r.get("source") in ("site", "both")
                  for table in win.top_tables.values()
                  for r in table.rows))
        win.toggle_scope("top")
        win.clear_selection()
        win.site_top = {}
        win.site_top_details = {}
        win.site_top_uid = None
        win.oms_user = None
        win._site_top_changed()

        win.select_all_pending()
        check("全选未传选中 %d 条" % len(pending), win.up_sel == pending)
        check("表格选中状态同步", set(win.tbl_up.selected_paths()) == pending)
        check("选中项就是待传项", set(win._pending_paths()) == pending)

        # 二次确认：点「取消」不能开传，且确认框拿到的是选中那一批
        seen = {}

        def fake_confirm(rows, action="上传", hint=""):
            seen["rows"], seen["action"] = rows, action
            return False

        win._confirm_batch = fake_confirm
        win.upload_selected()
        check("取消确认就不上传", not win._up_running)
        check("确认框拿到的是选中那 %d 条" % len(pending),
              {r["path"] for r in seen.get("rows") or []} == pending)
        check("确认框标题带动作名", seen.get("action") == "上传")

        # 确认框本身能建起来（配色/表格/按钮都走一遍，防止布局代码写崩）
        dlg = None
        try:
            dlg = A.UploadConfirmDialog(win, list(win.up_rows)[:5], action="上传",
                                        hint="测试用")
            dlg.withdraw()
            win.update()
            check("确认框默认不确认", dlg.result is False)
            check("确认框按钮带条数", "5" in dlg.btn_ok._text)
            check("确认框列出全部 5 行",
                  len(dlg.tbl.tv.get_children()) == min(5, len(win.up_rows)))
        finally:
            if dlg is not None:
                try:
                    dlg.grab_release()
                    dlg.destroy()
                except Exception:
                    pass

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

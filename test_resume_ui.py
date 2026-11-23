"""断点续传 / 单条失败不中断 / 重试失败 的界面级测试。

用假客户端替代真实站点（不会往 openms.top 传任何东西），跑法：
    python test_resume_ui.py     全部通过退出码 0，否则 1
依赖本机录像目录（%APPDATA%\\Metasweeper3.3.4\\replay），找不到就跳过。
"""

import ctypes
import os
import sys
import time
from ctypes import windll

import uitest  # 后台跑界面：窗口对用户透明，见 app.py 里 MS_PB_QUIET
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

    def has_session(self):
        return True

    def whoami(self):
        return {"id": 1, "username": "tester", "realname": "x"}

    def videos(self, uid, progress=None):
        if progress:
            progress(0, 0)
        return []

    def identifiers(self, uid):
        return ["tester"]

    def pb_records(self, uid):
        return []


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
    # 二次确认窗口是模态的，脚本里没法点；这里直接放行
    A.App._confirm_batch = lambda self, rows, action="上传", hint="": bool(rows)
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
        ("只记下成功的那 2 条", journal.count() == 2),
        ("重试按钮变成 (1)", "1" in win.btn_retry._text and win.btn_retry._enabled),
        ("有失败时总结不自己退场",
         win.lbl_status.cget("text").startswith("上传结束")),
    ]

    # 重试失败项：这次全部成功（先把总结的停留时间调短，不然要等 6 秒）
    A.App.UP_STATUS_HOLD_MS = 150
    win.oms.n = 99
    win.retry_failed()
    pump(win, lambda: not win._up_running)
    win.update()
    checks += [
        ("重试后全部成功", (win._up_ok, win._up_fail) == (1, 0)),
        ("累计记下 3 条", journal.count() == 3),
        ("重试按钮回到禁用", not win.btn_retry._enabled),
        ("传完进度行已收回", not win.row_up_prog.winfo_ismapped()),
        ("传完右下角那行清空", win.lbl_up_log.cget("text") == ""),
    ]
    # 全部成功：总结挂一会儿就退回开传前那一行，不能一直占着状态栏
    gone = pump(win, lambda: not win.lbl_status.cget("text").startswith("上传结束"), 3)
    checks.append(("全部成功时「上传结束」自己退场", gone))

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

    # 上传期间的互斥：重扫 / 退出登录 / 改路径应被挡下，相关按钮应禁用
    win._up_running = True
    win.start_scan()
    win.do_logout()
    guards = ("上传进行中" in win.lbl_status.cget("text") and not win._scanning)
    win.open_paths()
    paths_guard = "上传进行中" in win.lbl_status.cget("text")
    win._set_up_enabled(False)
    buttons = not win.btn_logout._enabled
    win._set_up_enabled(True)
    win._up_running = False
    checks += [("上传中禁止重扫/退出登录", guards),
               ("上传中设置菜单里的改路径同样被挡", paths_guard),
               ("上传中禁用退出登录按钮", buttons)]

    # 横幅上的「启动元扫雷」：点击有绑定；已经在跑时点了不重复启动
    bound = bool(win.banner.tag_bind("b_play", "<Button-1>"))
    win._ms_running = True
    before = win.lbl_status.cget("text")
    win.launch_game()
    checks += [("横幅启动按钮已绑定点击", bound),
               ("已在跑时点启动不重复启动",
                win.lbl_status.cget("text") == before)]

    # 状态机：点过启动 -> 进程确认起来 -> 状态栏必须走出「正在启动」
    win._ms_probe = lambda: (True, "")
    win._ms_running = False
    win._ms_launch_at = time.time()
    win._auto_on = False
    win._poll_ms()
    settled = ("正在启动" not in win.lbl_status.cget("text")
               and win._ms_running and win._ms_launch_at == 0.0)

    # 用户自己把元扫雷关掉：静默回弹，不能误报「没能启动起来」
    win._ms_probe = lambda: (False, "")
    win._ms_running = True
    win._ms_launch_at = 0.0
    win._poll_ms()
    quiet = (not win._ms_running
             and "没能启动" not in win.lbl_status.cget("text"))

    # 点了启动、宽限期过了仍没见到进程：这才是真没起来
    win._ms_launch_at = time.time() - A.mslaunch.START_GRACE - 5
    win._poll_ms()
    expired = "没能启动起来" in win.lbl_status.cget("text")

    checks += [("确认启动后状态栏不再停在「正在启动」", settled),
               ("用户自己关掉时不误报启动失败", quiet),
               ("宽限期过了仍没起来才报错", expired)]

    # 回执退场不能把「正在启动元扫雷…」捞回来 —— 已经启动成功了，底下还写着
    # 正在启动，界面上下一对不上，用户以为程序没跟上。
    win.set_status("扫描 2 个文件 → 2 个完成局", "dim")
    win.set_status("正在启动元扫雷…", "dim", transient=True)
    win.set_status("元扫雷已启动", "ok", hold=80)
    backed = pump(win, lambda: win.lbl_status.cget("text") != "元扫雷已启动", 2)
    back_text = win.lbl_status.cget("text")
    checks.append(("回执退场不会退回「正在启动元扫雷」",
                   backed and "正在启动" not in back_text))

    # 「每次打开工具箱都自动开始上传」：勾上要落盘，并且当场就把上传开起来
    cfg_before = A.load_config().get("auto_start", None)
    real_tick = win._auto_tick
    win._auto_tick = lambda: None          # 别让测试真的往上网站传东西
    win.set_auto_upload(False)
    win.var_auto_start.set(True)
    win.toggle_auto_start()
    start_on = win._auto_on and win._auto_start
    start_saved = A.load_config().get("auto_start") is True
    win.var_auto_start.set(False)
    win.toggle_auto_start()
    start_off = (not win._auto_start
                 and A.load_config().get("auto_start") is False)
    still_on = win._auto_on                # 取消勾选只是「以后不自动开」，不暂停
    win._auto_tick = real_tick
    win.set_auto_upload(False)
    if cfg_before is None:
        A.save_config(auto_start=False)
    else:
        A.save_config(auto_start=cfg_before)
    checks += [("勾上「自动开始」会记住", start_saved),
               ("勾上时当场就开始上传", start_on),
               ("取消勾选会记住", start_off),
               ("取消勾选不暂停当前上传", still_on)]

    # 启动回执：不能出现「这局」这种没头没尾的指代，也不能关掉游戏之后还挂着
    win._auto_on = False
    win._ms_probe = lambda: (True, "")
    win._ms_running = False
    win._ms_launch_at = time.time()
    win._poll_ms()
    receipt = win.lbl_status.cget("text")
    win._ms_probe = lambda: (False, "")
    win._poll_ms()
    dropped = win.lbl_status.cget("text") != receipt

    checks += [("启动回执说的是「新成绩」而不是「这局」",
                "元扫雷已启动" in receipt and "这局" not in receipt),
               ("关掉元扫雷后启动回执不再挂在底下", dropped)]

    # 置前状态机：抢到一次就收工；窗口还没建出来就留到下一拍再试；
    # 用户已经去干别的了就别再打扰他
    real_focus, real_self = A.mslaunch.focus, A.mslaunch.foreground_is_self
    calls = []
    A.mslaunch.foreground_is_self = lambda: True
    A.mslaunch.focus = lambda: calls.append(1) or True
    win._ms_want_front()
    win._ms_try_focus()
    front_ok = len(calls) == 1 and win._ms_focus_until == 0.0

    A.mslaunch.focus = lambda: calls.append(1) or False       # 窗口还没建出来
    win._ms_want_front()
    win._ms_try_focus()
    front_retry = win._ms_focus_until > 0 and len(calls) == 2

    A.mslaunch.foreground_is_self = lambda: False             # 用户切到别处了
    win._ms_try_focus()
    front_leave = win._ms_focus_until == 0.0 and len(calls) == 2
    A.mslaunch.focus, A.mslaunch.foreground_is_self = real_focus, real_self
    win._ms_focus_until = 0.0

    checks += [("抢到前台就收工", front_ok),
               ("窗口还没出现时留到下一拍再试", front_retry),
               ("用户切到别处就不再抢前台", front_leave)]

    # 双击分派：录像行交给元扫雷，非录像行（目录之类）仍然复制路径
    rec = win.tbl_recent
    opened, copied = [], []
    real_open, real_copy = win.open_replay, rec._copy_path_text
    real_is = A.mslaunch.is_replay
    win.open_replay = lambda p: opened.append(p)
    rec._copy_path_text = lambda p: copied.append(p)
    try:
        kids = rec.tv.get_children()
        if kids:
            rec.tv.selection_set(kids[0])
            rec._on_double()
        A.mslaunch.is_replay = lambda p: False     # 冒充一行目录
        if kids:
            rec._on_double()
    finally:
        win.open_replay, rec._copy_path_text = real_open, real_copy
        A.mslaunch.is_replay = real_is
    checks += [("双击录像行交给元扫雷播放",
                bool(opened) and A.mslaunch.is_replay(opened[0])),
               ("非录像行仍然复制路径", bool(copied))]

    # 右键菜单（同风格 StyleMenu，不再是原生 tk.Menu）：项齐不齐；贴出这一步换成
    # 空实现，免得在测试里真弹窗口抢 grab
    real_popup, pops = A.StyleMenu.popup, []
    A.StyleMenu.popup = lambda self, x, y: pops.append(self)

    class _Evt:
        x_root = y_root = 100
        y = 0

    real_identify = rec.tv.identify_row
    try:
        kids = rec.tv.get_children()
        if kids:
            # 不赌 bbox 的坐标换算（测试窗口可能还没布局完），直接让它命中第一行
            rec.tv.identify_row = lambda _y: kids[0]
            rec._on_right_click(_Evt)
    finally:
        rec.tv.identify_row = real_identify
        A.StyleMenu.popup = real_popup
    made = pops
    alive = bool(made) and all(m.winfo_exists() for m in made)
    for m in made:                      # 收起：菜单应自毁
        m.close()
    checks += [("右键菜单贴出后不秒删（命令才点得到）", alive),
               ("右键菜单收起即销毁（不漏窗口）",
                bool(made) and all(not m.winfo_exists() for m in made))]
    import inspect
    sig = inspect.signature(A.DailyDialog.set_status)
    checks += [("明细对话框 set_status 接得住 hold / transient（复制路径不崩）",
                "hold" in sig.parameters and "transient" in sig.parameters)]
    labels = made[0].labels() if made else []
    seps = [it for it in (made[0]._items if made else []) if it[0] == "sep"]
    checks += [("右键菜单：详细信息 / 播放 / 复制路径 / 打开所在文件夹",
                labels == ["详细信息", "用元扫雷播放", "复制路径", "打开所在文件夹"]),
               ("右键菜单分隔线不重复、不在首尾", len(seps) == 1)]

    # 真弹一次（窗口对用户透明）：贴出即 grab、回车执行悬停项、收起释放 grab
    win.update()
    hits = []
    m = A.StyleMenu(win)
    m.add_command("甲", command=lambda: hits.append("甲"))
    m.add_separator()
    m.popup(100.5, 100.25)
    win.update()
    popped = (m.winfo_exists() and win.grab_current() is m
              and A.StyleMenu._current is m and (m.winfo_x(), m.winfo_y()) == (100, 100))
    m._set_hover(0)
    m._run(m._hover)                   # <Return> 绑的就是它：后台跑时窗口拿不到键盘焦点
    win.update()
    checks += [("弹出菜单贴出在取整后的坐标并接管点击", popped),
               ("弹出菜单执行悬停项后自毁",
                hits == ["甲"] and not m.winfo_exists()
                and win.grab_current() is None and A.StyleMenu._current is None)]

    # 空菜单不弹；上一条分隔线留在结尾时自动去掉
    m2 = A.StyleMenu(win)
    m2.add_separator()
    m2.popup(100, 100)
    m3 = A.StyleMenu(win)
    m3.add_command("乙")
    m3.add_separator()
    m3.popup(100, 100)
    win.update()
    checks += [("没有可点项的菜单不弹", not m2.winfo_exists()),
               ("结尾的分隔线自动去掉", len(m3._rows) == 1 and len(m3._items) == 1)]
    m3.close()

    # 设置弹窗：操作药丸 / 强无猜三档 / LSF 两态胶囊（文案与说明随状态变）
    def _pills(w):
        out = []
        for c in w.winfo_children():
            if isinstance(c, A.Pill):
                out.append(c)
            out += _pills(c)
        return out

    real_scan = win.start_scan
    scan_calls = []
    win.start_scan = lambda: scan_calls.append(1)
    win.open_settings()
    dlg = win._settings_dlg
    dlg.update_idletasks()
    dlg.update()
    scan_pill = [p for p in _pills(dlg) if p._text == "重新扫描"][0]
    no_paths = not any(p._text.startswith("路径") for p in _pills(dlg))
    scan_pill._on_click()
    win.update()
    act_ok = scan_calls == [1] and not dlg.winfo_exists()
    win.start_scan = real_scan

    win.open_settings()
    dlg = win._settings_dlg
    dlg.update_idletasks()
    dlg.update()
    ng_labels = [p._text for p in dlg._ng_pills.values()]
    win.ng_scope_var.set("top")
    dlg._refresh()
    ng_sel = dlg._ng_pills["top"]._sel and not dlg._ng_pills[""]._sel
    win.ng_scope_var.set("")
    dlg._refresh()
    win.var_lsf_fixed.set(False)
    dlg._refresh()
    live_txt = dlg._lsf_pill._text
    live_note = dlg._lsf_note.cget("text")
    dlg._lsf_pill._on_click()          # 实时 -> 固定
    win.update()
    fix_txt = dlg._lsf_pill._text
    fix_note = dlg._lsf_note.cget("text")
    two_state = ("实时" in live_txt and "固定" in fix_txt
                 and live_txt != fix_txt and dlg._lsf_pill._sel)
    dlg._lsf_pill._on_click()          # 固定 -> 实时
    win.update()
    back_txt = dlg._lsf_pill._text
    pos_int = isinstance(dlg.winfo_x(), int) and isinstance(dlg.winfo_y(), int)
    dlg.close()
    win.update()
    checks += [("设置是弹窗，点命令会触发并关闭", act_ok),
               ("路径已移出设置（单独上横幅）", no_paths),
               ("横幅路径按钮已绑定点击",
                bool(win.banner.tag_bind("b_paths", "<Button-1>"))),
               ("强无猜三档药丸齐", ng_labels == ["关闭", "进榜才传", "全部上传"]),
               ("强无猜选中态跟随", ng_sel),
               ("LSF 两态胶囊文案随状态变", two_state),
               ("LSF 说明随状态变（不定死）", live_note != fix_note),
               ("LSF 胶囊再点回到实时", "实时" in back_txt),
               ("设置弹窗收起即销毁（不漏窗口）", not dlg.winfo_exists()),
               ("设置弹窗坐标是整数", pos_int)]

    # 今日总结下拉：同风格菜单，弹出坐标必须是整数
    pops2 = []
    real_popup = A.StyleMenu.popup
    A.StyleMenu.popup = lambda self, x, y: pops2.append((self, x, y))
    try:
        win._today_menu()
    finally:
        A.StyleMenu.popup = real_popup
    checks += [("今日总结菜单：总结 / 详细总结两项",
                len(pops2) == 1 and pops2[0][0].labels() == ["复制今日总结", "复制今日详细总结"]),
               ("今日总结菜单弹出坐标是整数（浮点坐标已取整）",
                len(pops2) == 1 and all(isinstance(v, int) for v in pops2[0][1:]))]
    for m, _x, _y in pops2:
        m.destroy()

    # 所有弹窗都带应用图标（标题栏不再是 Tk 默认的羽毛）
    win.update()
    win.open_settings()
    dlg = win._settings_dlg
    for _ in range(40):                 # 等映射后那一下补设图标
        win.update()
        time.sleep(0.01)
    try:
        dlg_hwnd = windll.user32.GetAncestor(dlg.winfo_id(), 2)
        has_icon = bool(windll.user32.SendMessageW(
            ctypes.c_void_p(dlg_hwnd), 0x007F, 0, None))      # WM_GETICON ICON_SMALL
    except Exception:
        has_icon = False
    dlg.close()
    bases = {c.__name__: c.__bases__[0].__name__ for c in vars(A).values()
             if isinstance(c, type) and c.__module__ == A.__name__
             and c.__name__ in ("SettingsDialog", "LsfDetailDialog", "DailyDialog",
                                "LoginDialog", "DiagnosticsDialog",
                                "UploadConfirmDialog", "IdentsDialog", "GameDetail",
                                "PathsDialog")}
    checks += [("设置弹窗带应用图标", has_icon),
               ("九个弹窗类都继承带图标的基类",
                len(bases) == 9 and set(bases.values()) == {"Dialog"})]

    # 强无猜范围：选择要落盘，且不动标准模式的范围
    ng_before = A.load_config().get("ng_scope", None)
    real_tick2 = win._auto_tick
    win._auto_tick = lambda: None
    win.ng_scope_var.set("all")
    win.toggle_ng_scope()
    ng_all = win._ng_scope == "all" and A.load_config().get("ng_scope") == "all"
    win.ng_scope_var.set("top")
    win.toggle_ng_scope()
    ng_top = win._ng_scope == "top" and A.load_config().get("ng_scope") == "top"
    win.ng_scope_var.set("")
    win.toggle_ng_scope()
    ng_off = win._ng_scope == "" and A.load_config().get("ng_scope") == ""
    win._auto_tick = real_tick2
    if ng_before is None:
        A.save_config(ng_scope="")
    else:
        A.save_config(ng_scope=ng_before)
    checks += [("强无猜「全部上传」会记住", ng_all),
               ("强无猜「进榜才传」会记住", ng_top),
               ("强无猜「关闭」会记住", ng_off)]

    # 固定 LSF 基准 = 硬编码赛季锚点：不依赖实时锚点，也不随其变化
    win.lsf_anchors = {}
    win.var_lsf_fixed.set(True)
    fixed_ok = (win._lsf_anchors_eff() is A.SEASON_ANCHORS
                and len(A.SEASON_ANCHORS) == 18)
    win.lsf_anchors = {"std:bt": {"value": 1.0, "sample_count": 20}}
    fixed_stable = win._lsf_anchors_eff() is A.SEASON_ANCHORS
    win.var_lsf_fixed.set(False)
    live_ok = win._lsf_anchors_eff() == win.lsf_anchors
    checks += [("固定基准用硬编码赛季锚点（18 格）", fixed_ok),
               ("固定基准不随实时锚点变化", fixed_stable),
               ("不勾时回到实时锚点", live_ok)]

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

"""自动上传的「第二拍」与状态栏还原回归测试。

覆盖 2026-11-15 晚上暴露的三个问题（都是「界面什么都没发生」类）：
  1. 一局打完到文件写稳之间没人再看一眼 —— 自动上传要等到下一局落地才动；
  2. 传完之后把「正在扫描…已读 22400 / 22555（99%）」当常态还原，状态栏卡在假进度；
  3. 自动上传成功后不重读网站，前端成绩 / 网站 PB 停在旧数据。
另外覆盖：垃圾 .evf 不能送进 ms-toollib（它会直接把进程带下去）。

不联网、不碰真实数据：配置目录与录像目录都是临时目录，录像用真实文件的一份副本。
跑法：python test_auto_flow.py    全部通过退出码 0，否则 1
"""

import glob
import os
import shutil
import sys
import tempfile
import time

SRC_REPLAY = os.path.join(os.environ.get("APPDATA", ""),
                          "Metasweeper3.3.4", "replay")
TMP = tempfile.mkdtemp(prefix="ytb-autoflow-")
os.environ["MS_PB_DATA_DIR"] = os.path.join(TMP, "data")
REPLAY = os.path.join(TMP, "replay")
os.makedirs(REPLAY, exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uitest  # 后台跑界面：窗口对用户透明，见 app.py 里 MS_PB_QUIET
import app as A          # noqa: E402
import autoup            # noqa: E402
import core              # noqa: E402

A.messagebox.showinfo = lambda *x, **k: None
A.messagebox.showwarning = lambda *x, **k: None
A.messagebox.showerror = lambda *x, **k: None
A.messagebox.askyesno = lambda *x, **k: False

checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


def pump(win, cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        win.update()
        if cond():
            return True
        time.sleep(0.02)
    return False


def main():
    sample = sorted(glob.glob(os.path.join(SRC_REPLAY, "b_*.evf")))[:1]
    if not sample:
        print("跳过：找不到一条可用的 .evf 样本（%s）" % SRC_REPLAY)
        return 0
    shutil.copy2(sample[0], REPLAY)

    win = A.App(REPLAY)
    win.withdraw()
    # 启动扫描在 after 里排着，_scanning 一开始还是 False，直接等会抢跑
    time.sleep(0.8)
    assert pump(win, lambda: not win._scanning and bool(win.games), 20), \
        "首次扫描没出结果"

    sent = []
    win._start_upload = lambda paths, **k: sent.append(list(paths))
    # 判定与门槛不是这份测试的主题（test_autoup 已覆盖），这里只关心「有没有第二拍」。
    # 真 ready 留一份：最后那条「三项还是要求成绩就绪」得用它。
    real_ready = autoup.ready
    A.autoup.ready = lambda *a, **k: True
    A.autoup.blocked = lambda *a, **k: ""
    A.autoup.uploaded_before = lambda *a, **k: False
    A.autoup.judge = lambda games, scopes, new_paths, site_pb=None, site_top=None: [
        {"path": g["path"], "game": g, "why": ["测试"]}
        for g in games if g["path"] in set(new_paths)]
    win.oms_user = {"id": 1, "username": "tester", "realname": "x"}
    win.site_pb = [{"level": 0, "bv": 1, "timems": 99999}]
    win.site_top = {"b": {"time": [99999]}}
    win._watch_enabled = True
    for var in win.auto_vars.values():
        var.set(True)
    win.toggle_auto_scope()
    win.set_auto_upload(True)            # 开关本身（150ms 后还会自己判一次）
    win._auto_tracker = autoup.Tracker(0)
    win._auto_tracker.prime([])          # 空基线：现有这一局就算「新局」

    # ---- 1. 一局可见之后，只扫描过一次，也必须自己再判一次 ----
    win._auto_tick()                     # 相当于「扫描结束时那一拍」
    check("刚看到的局不会被当场判定（还在写）", sent == [])
    check("但排了一次「写稳之后再判」的闹钟",
          getattr(win, "_auto_job", None) is not None)
    got = pump(win, lambda: sent, timeout=6)
    check("不依赖下一次扫描，自动传起来", got and sent and len(sent[0]) == 1)

    # 候选处理完就不要再排闹钟
    sent.clear()
    win._auto_job = None
    win._auto_tick()
    check("候选处理完就不再排闹钟", getattr(win, "_auto_job", None) is None)

    # ---- 2. 状态栏：作废的扫描进度不能被还原 ----
    win.set_status("扫描 22555 个文件 → 22554 个完成局，用时 0.2 秒", "dim")
    win.set_status("正在扫描…已读 22400 / 22555 个录像（99%）", "dim",
                   transient=True)
    check("扫描进度不被当成「开传前那一行」",
          win._status_before_upload() is None)
    win.set_status("自动上传：成功 1，已有 0，失败 0", "ok")
    win._status_back()
    win.update()
    check("还原后不是那句 99% 的进度",
          "22400" not in win.lbl_status.cget("text"))

    # 进度行本身也不该被后来的消息记成「原样」
    win.set_status("扫描 22555 个文件 → 22554 个完成局，用时 0.2 秒", "dim")
    win.set_status("正在扫描…已读 800 / 22555 个录像（3%）", "dim",
                   transient=True)
    win.set_status("元扫雷已启动", "ok", hold=50)
    win.set_status("扫描 22556 个文件 → 22555 个完成局，用时 0.2 秒", "dim")
    win._status_back()
    win.update()
    check("进度行不会在中间消息退场时被捞回来",
          "已读 800" not in win.lbl_status.cget("text"))

    # ---- 3. 自动上传成功后要重读网站 ----
    refreshed = []
    win.refresh_site = lambda: refreshed.append(1)
    win._auto_tick = lambda: None
    win._up_silent = True
    win._up_ok, win._up_dup, win._up_fail = 1, 0, 0
    win._up_stop = False
    win._on_upload_end({"stopped": False})
    pump(win, lambda: refreshed, timeout=3)
    check("自动上传成功后重读站点数据", bool(refreshed))

    # ---- 4. 坏文件不能把进程带下去 ----
    bad = os.path.join(REPLAY, "b_0_9.999_9_9.999_bad.evf")
    with open(bad, "wb") as fh:
        fh.write(b"\0" * 64)
    check("垃圾 .evf 在送进 ms_toollib 之前就被挡下",
          core.read_video(bad) is None)
    check("头部正常的录像仍然照常解析",
          core.read_video(os.path.join(REPLAY, os.path.basename(sample[0]))) is not None)
    os.remove(bad)

    # evf v4 第 3 字节的高 3 位是三个独立游戏设置，低 5 位保留。
    # 穷举全部 256 种取值：8 种合法组合都必须走完整的 ms-toollib 主解析，
    # 其余保留位非零的取值在主解析和 lite 兜底中都不能漏进去。
    with open(sample[0], "rb") as fh:
        settings_raw = bytearray(fh.read())
    settings_path = os.path.join(REPLAY, "settings.evf")
    wrong_acceptance = []
    wrong_route = []
    for setting in range(256):
        settings_raw[2] = setting
        with open(settings_path, "wb") as fh:
            fh.write(settings_raw)
        vid = core.read_video(settings_path)
        valid = not (setting & 0x1F)
        if (vid is not None) != valid:
            wrong_acceptance.append(setting)
        if valid and vid is not None and vid.get("via") != "lib":
            wrong_route.append(setting)
    check("evf 游戏设置字节：8 种合法组合全部接受、非法保留位全部拒绝",
          not wrong_acceptance)
    check("8 种合法游戏设置组合都走 ms-toollib 主解析，不降级 lite",
          not wrong_route)
    os.remove(settings_path)

    # ---- 4. 配置：空范围不能被 `or` 吞回默认三项 ----
    check("配置缺字段 -> 默认三项",
          A.config_auto_scopes({}) == ["top", "top_nf", "pb"])
    check("三项全取消（存空列表）-> 重启仍是空",
          A.config_auto_scopes({"auto_scopes": []}) == [])
    check("类型不对才回落默认",
          A.config_auto_scopes({"auto_scopes": "pb"}) == ["top", "top_nf", "pb"]
          and A.config_auto_scopes({"auto_scopes": [1, 2]}) == [])

    # ---- 5. 全部完成局：控件联动、配置、就绪判定 ----
    # 上面为了让三项控件全亮已经存过一次范围（三个开关对应四个 scope），
    # 拿它当「三项偏好」的基线，验证切全量不会把它改掉。
    three_scopes = A.load_config().get("auto_scopes")
    win.var_auto_all.set(False)
    win.toggle_auto_all()
    check("关掉全量：三项控件可用",
          all(cb.cget("state") == "normal" for cb in win.auto_checks.values()))
    check("关掉全量：三项勾选照旧还在",
          all(v.get() for v in win.auto_vars.values()))
    check("关掉全量：有效范围回到那三项",
          win._auto_scopes() == autoup.scopes_of(win.auto_flags))
    win.var_auto_all.set(True)
    win.toggle_auto_all()
    check("勾上全量：有效范围就是 all",
          win._auto_scopes() == (autoup.ALL,))
    check("勾上全量：三项变灰",
          all(cb.cget("state") == "disabled"
              for cb in win.auto_checks.values()))
    check("勾上全量：三项勾选标记留着（不是被点掉）",
          all(v.get() for v in win.auto_vars.values()))
    check("勾上全量：没去改三项的保存值",
          A.load_config().get("auto_scopes") == three_scopes)
    check("勾上全量：配置里落盘 auto_all",
          A.load_config().get("auto_all") is True)
    check("全量开着时程序调 toggle_auto_scope 不会改偏好",
          (win.toggle_auto_scope() or True) and
          A.load_config().get("auto_scopes") == three_scopes)

    # 就绪：全量只等「当前这一代」的录像列表，不看 PB / Top5
    win.oms_user = {"id": 1, "username": "tester"}
    win._auto_on = True
    win._watch_enabled = True
    win._auto_stop = ""
    win._auto_backoff = 0.0
    win._site_seq = 5
    win._auto_acct_seq = 4
    win.oms_loaded = True
    win.oms_synced = True
    check("全量：上一代拉到的列表不算数",
          "录像记录" in win._auto_ready_reason())
    win._auto_acct_seq = 5
    check("全量：这一代列表到齐 -> 就绪", win._auto_ready_reason() == "")
    win.oms_synced = False
    check("全量：列表没拉到 -> 说还在读 / 会重试",
          "录像记录" in win._auto_ready_reason())
    win.oms_synced = True
    win.oms_loaded = False
    check("全量：列表还没开始读 -> 不就绪",
          "录像记录" in win._auto_ready_reason())
    win.oms_loaded = True
    win.site_pb, win.site_top = [], {}
    win.var_auto_all.set(False)
    win.toggle_auto_all()
    A.autoup.ready = real_ready        # 上面为了测「第二拍」把它假成了恒真
    check("三项范围仍然要求网站成绩就绪",
          win._auto_ready_reason() != "")
    A.autoup.ready = lambda *a, **k: True

    # ---- 6. 状态行跟着网站成绩走：没有新局可判也要重算 ----
    # 启动时网站数据还没到，那句「还没读到你在网站上的成绩」会留着；数据到了
    # 之后这一拍即使没有新局可判，也得把它抹掉，不能等用户切走再切回来。
    win.oms_user = {"id": 1, "username": "tester"}
    win._auto_on = True
    win._watch_enabled = True
    win._auto_stop = ""
    win._auto_backoff = 0.0
    win.oms_loaded = True
    win.oms_synced = True
    win._auto_acct_seq = win._site_seq
    win.var_auto_all.set(False)
    A.autoup.ready = real_ready
    win.site_pb, win.site_top = [], {}
    win._auto_tracker = autoup.Tracker(0)
    win._auto_tracker.prime(win.games)      # 基线拉满：这一拍没有任何新局
    win._refresh_auto_status()
    before = win.lbl_auto_status.cget("text")
    win.site_pb = [{"level": 0, "bv": 1, "timems": 99999}]
    win.site_top = {"b": {"time": [99999]}}
    # 与 _on_site_pb / _on_site_top 的落盘契约一致：读成功才有 uid 且 err 空
    win.site_pb_uid = win.site_top_uid = 1
    win.site_pb_err = win.site_top_err = ""
    win._auto_tick_once()
    check("成绩到了，状态行自己消失（不用切页）",
          bool(before) and not win.lbl_auto_status.cget("text"))
    win.site_pb, win.site_top = [], {}
    win._auto_tick_once()
    check("新账号成绩合法为空：算读到，不永久卡「还没读到」",
          not win.lbl_auto_status.cget("text"))

    win.destroy()
    print()
    bad_checks = [l for l, ok in checks if not ok]
    if bad_checks:
        print("失败 %d 项：" % len(bad_checks))
        for l in bad_checks:
            print("  -", l)
        return 1
    print("== 全部通过（%d 项）==" % len(checks))
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)

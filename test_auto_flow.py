"""自动上传的「第二拍」与状态栏还原回归测试。

覆盖 2026-11-15 晚上暴露的三个问题（都是「界面什么都没发生」类）：
  1. 一局打完到文件写稳之间没人再看一眼 —— 自动上传要等到下一局落地才动；
  2. 传完之后把「正在扫描…已读 22400 / 22555（99%）」当常态还原，状态栏卡在假进度；
  3. 自动上传成功后不重读网站，前端成绩 / 网站 PB 停在旧数据。
另外覆盖：垃圾 .evf 不能送进 ms-toollib（它会直接把进程带下去）。
范围切换与配置恢复由 test_auto_all_new.py 覆盖。

不联网、不碰真实数据：配置目录与录像目录都是临时目录。有真实 .evf 样本时
复制一份；没有时用合成占位文件跑完整主流程（占位文件头部全 0，扫描层会在
送进 ms-toollib 之前挡下），只把「头部正常的录像照常解析」这一条标为跳过。

「不联网」不只是约定：启动期的会话恢复 / 全站锚点 / 任务栏轮询都不跑，HTTP
出口 Client.request 换成记录器，跑完断言一次请求都没发。
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

import app as A          # noqa: E402
import autoup            # noqa: E402
import core              # noqa: E402
import openms            # noqa: E402

A.messagebox.showinfo = lambda *x, **k: None
A.messagebox.showwarning = lambda *x, **k: None
A.messagebox.showerror = lambda *x, **k: None
A.messagebox.askyesno = lambda *x, **k: False

# 站点的所有 HTTP 都走 Client.request，这里换成记录器：真发出去时抛
# AssertionError（工作线程会吞掉），路径留在 _net_calls 里，末尾统一断言。
_net_calls = []


def _no_net(self, path, *a, **k):
    _net_calls.append(path)
    raise AssertionError("测试期间不应发起网络请求：%s" % path)


openms.Client.request = _no_net

# 启动期的网站任务与本测试无关，全部屏蔽：
#   _lsf_start      开机 900ms 后取 /api/speedranking/top/anchors，公开接口，
#                   不等登录就会真的打到站点 —— 之前就是它漏了网络。
#   _restore_session 读保存的会话，有 Cookie 就会走整条网站刷新。
#   _poll_ms        任务栏进程轮询，为一份纯逻辑回归白跑定时器。
A.App._restore_session = lambda self: None
A.App._lsf_start = lambda self: None
A.App._poll_ms = lambda self: None

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
    synthetic = not sample
    if synthetic:
        # 头部全 0：core.looks_like_video() 会挡住它，绝不会把垃圾送进
        # ms-toollib；文件名仍按标准模式解析，扫描层仍算一局完成局。
        with open(os.path.join(REPLAY, "b_0_1.774_9_5.073_synthetic.evf"),
                  "wb") as fh:
            fh.write(b"\0" * 64)
        print("提示：没找到真实 .evf 样本（%s），本轮用合成占位文件跑主流程；"
              "「头部正常的录像照常解析」单独跳过" % SRC_REPLAY)
    else:
        shutil.copy2(sample[0], REPLAY)

    win = A.App(REPLAY)
    win.withdraw()
    # 启动扫描在 after 里排着，_scanning 一开始还是 False，直接等会抢跑
    time.sleep(0.8)
    assert pump(win, lambda: not win._scanning and bool(win.games), 20), \
        "首次扫描没出结果"

    sent = []
    win._start_upload = lambda paths, **k: sent.append(list(paths))
    # 判定与门槛不是这份测试的主题（test_autoup 已覆盖），这里只关心「有没有第二拍」
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
    if synthetic:
        print("跳过：头部正常的录像仍然照常解析（缺真实 .evf 样本）")
    else:
        check("头部正常的录像仍然照常解析",
              core.read_video(os.path.join(
                  REPLAY, os.path.basename(sample[0]))) is not None)
    os.remove(bad)

    # ---- 5. 全程没有对外请求 ----
    check("全程没有发起网络请求", not _net_calls)
    if _net_calls:
        print("  实际请求：%r" % (_net_calls,))

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

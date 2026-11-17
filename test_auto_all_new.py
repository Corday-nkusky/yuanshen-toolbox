"""「全部新增完成局」的配置、控件、门控与「扫描 → 判定 → 上传」合成回归。

不联网、不碰作者本机录像：数据目录与录像目录都是临时目录；录像用合成的
占位文件由 core.scan(deep=False) 产出候选；站点用假事件和假 oms.upload。
模式过滤由 test_mode.py 覆盖，范围计算与 Tracker 规则由 test_autoup.py 覆盖。

跑法：python test_auto_all_new.py    全部通过退出码 0，否则 1
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="ytb-allnew-")
DATA = os.path.join(TMP, "data")
REPLAY = os.path.join(TMP, "replay")
os.makedirs(DATA, exist_ok=True)
os.makedirs(REPLAY, exist_ok=True)
os.environ["MS_PB_DATA_DIR"] = DATA

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as A            # noqa: E402
import autolog             # noqa: E402
import autoup              # noqa: E402
import core                # noqa: E402
import journal             # noqa: E402
import openms              # noqa: E402

# 界面起来就行，不启动扫描 / 刷新 / 网站请求：候选全部由本文件注入。
A.messagebox.showinfo = lambda *a, **k: None
A.messagebox.showwarning = lambda *a, **k: None
A.messagebox.showerror = lambda *a, **k: None
A.messagebox.askyesno = lambda *a, **k: False
A.App._restore_session = lambda self: None
A.App._lsf_start = lambda self: None
A.App._poll_ms = lambda self: None
A.App._boot = lambda self: None
A.App.refresh_site = lambda self: None
A.App._watch_rebuild = lambda self: None
A.diag.log.setLevel(logging.CRITICAL)   # 故意触发的退避 / 暂停别刷屏

# 「离线」要断言，不能只靠上面几个补丁：站点的 HTTP 全走 Client.request，
# 换成记录器，末尾统一断言一条都没发（防止以后再冒出开机请求）。
_net_calls = []


def _no_net(self, path, *a, **k):
    _net_calls.append(path)
    raise AssertionError("测试期间不应发起网络请求：%s" % path)


openms.Client.request = _no_net

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


def cfg_path():
    return os.path.join(DATA, "config.json")


def write_cfg(obj):
    with open(cfg_path(), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def read_cfg():
    with open(cfg_path(), "r", encoding="utf-8") as fh:
        return json.load(fh)


def make_app(cfg=None):
    if cfg is not None:
        write_cfg(cfg)
    win = A.App(REPLAY)
    win.withdraw()
    return win


def close_app(win):
    """关掉窗口前撤掉所有待办闹钟，免得 Tk 在退出后报 invalid command name。"""
    try:
        for job in list(win.tk.call("after", "info")):
            try:
                win.after_cancel(job)
            except Exception:
                pass
    except Exception:
        pass
    win.destroy()


def marks(win):
    return {k: bool(v.get()) for k, v in win.auto_vars.items()}


def states(win):
    return {k: cb.cget("state") for k, cb in win.auto_checks.items()}


# ---------------------------------------------------------------- 合成录像
# 文件名遵循元扫雷命名：{级别}_{mode}_{秒}_{3BV}_{3BVs}_{玩家}.evf。
# 大小各不相同，避免 dedupe_games 把不同局当成同一份的逐字节副本。
FILES = [
    ("b_0_1.774_9_5.073_X.evf", 64),                 # 普通局，BV 低于前端门槛
    ("i_0_30.000_60_2.000_X.evf", 96),               # 后面标成 valid=1
    ("i_0_5.400_34_6.296_Y.evf", 128),               # 后面标成标识不符
    ("b_0_1.200_3_2.500_Z.evf", 160),                # 后面标成网站已有
    ("e_0_60.000_150_2.500_W.evf", 192),             # 后面标成台账已传
    ("b_0_2.500_11_4.400_X.evf", 224),               # 普通局，不破 PB
    ("HI-SCORE Beg_2.00_3BV=12_3BVs=6.00_A.avf", 256),   # 阿比特
]
for i, (fn, size) in enumerate(FILES):
    with open(os.path.join(REPLAY, fn), "wb") as fh:
        fh.write(bytes([i + 1]) * size)

games, info = core.scan(REPLAY, deep=False)
by_name = {os.path.basename(g["path"]): g for g in games}
for g in games:
    g["ident"] = "X"
    g["valid"] = 0
by_name["i_0_30.000_60_2.000_X.evf"]["valid"] = 1
by_name["i_0_5.400_34_6.296_Y.evf"]["ident"] = "别人(北京)"

G_PLAIN = by_name["b_0_1.774_9_5.073_X.evf"]
G_PLAIN2 = by_name["b_0_2.500_11_4.400_X.evf"]
G_FROZEN = by_name["i_0_30.000_60_2.000_X.evf"]
G_IDENT = by_name["i_0_5.400_34_6.296_Y.evf"]
G_SITE = by_name["b_0_1.200_3_2.500_Z.evf"]
G_JOURNAL = by_name["e_0_60.000_150_2.500_W.evf"]
G_ARB = by_name["HI-SCORE Beg_2.00_3BV=12_3BVs=6.00_A.avf"]


def fresh_games():
    return [dict(g) for g in games]


def acct_ready(win, user=None):
    """把账号 / 录像列表置成「完整事件已到齐」的状态。"""
    win.oms_user = user or {"id": 1, "username": "tester", "realname": "x"}
    win.oms_videos = []
    win.oms_idents = []
    win.oms_err = ""
    win.oms_loaded = True
    win.oms_synced = True
    win._auto_acct_seq = win._site_seq


# ------------------------------------------------------- 1. 纯配置兼容函数
check("缺 auto_scopes 时用默认范围",
      A.config_auto_scopes({}) == ["top", "top_nf", "pb"])
check("合法空 auto_scopes 保留为空", A.config_auto_scopes({"auto_scopes": []}) == [])
check("auto_scopes 类型异常才回退默认",
      A.config_auto_scopes({"auto_scopes": "top"}) == ["top", "top_nf", "pb"]
      and A.config_auto_scopes({"auto_scopes": [1, 2]}) == ["top", "top_nf", "pb"])
check("缺 auto_all_new 时默认关闭", A.config_auto_all_new({}) is False)
check("字符串 \"false\" 不当成 true",
      A.config_auto_all_new({"auto_all_new": "false"}) is False)
check("数字 1 不当成 true", A.config_auto_all_new({"auto_all_new": 1}) is False)
check("JSON 布尔 true 才打开", A.config_auto_all_new({"auto_all_new": True}) is True)


# ------------------------------------------------------- 2. 控件与配置往返
write_cfg({"auto_scopes": ["pb", "pb_nf"]})
win = make_app()
check("旧配置无新字段：全量默认关闭", win._auto_all_new is False)
check("旧配置无新字段：原有有效范围保留", win._auto_scopes() == ("pb", "pb_nf"))
check("初始化控件不触发保存（没有写 auto_all_new）",
      "auto_all_new" not in read_cfg())
check("旧配置下三项控件可操作", all(s == "normal" for s in states(win).values()))
close_app(win)
write_cfg({"auto_scopes": []})
win = make_app()
check("auto_scopes=[] 重启：三项仍全未选", not any(marks(win).values()))
check("auto_scopes=[] 重启：有效范围为空", win._auto_scopes() == ())
close_app(win)
write_cfg({"auto_all_new": "false", "auto_scopes": ["top", "top_nf"]})
win = make_app()
check("异常类型的新字段不会打开全量",
      win._auto_all_new is False and win._auto_scopes() == ("top", "top_nf"))
close_app(win)
# 三组偏好：开启 → 保存 → 重启（全量仍开）→ 关闭，逐项不丢失
combos = [
    {"rank": True, "pb": False, "pb_nf": True},
    {"rank": False, "pb": True, "pb_nf": False},
    {"rank": False, "pb": False, "pb_nf": False},
]
for i, flags in enumerate(combos, 1):
    scopes = [s for k, ss, _l in autoup.GROUPS for s in ss if flags.get(k)]
    expect = autoup.flags_of(scopes)
    write_cfg({"auto_scopes": scopes})
    win = make_app()
    check("组合 %d 重启后三项偏好逐项保持" % i, marks(win) == expect)
    before = read_cfg().get("auto_scopes")
    win.var_auto_all_new.set(True)
    win.toggle_auto_all_new()
    check("组合 %d 开启全量：有效范围只有 all" % i, win._auto_scopes() == ("all",))
    check("组合 %d 开启全量：只存 auto_all_new" % i,
          read_cfg().get("auto_all_new") is True
          and read_cfg().get("auto_scopes") == before)
    check("组合 %d 开启全量：三项禁用且勾选标记保留" % i,
          all(s == "disabled" for s in states(win).values())
          and marks(win) == expect)
    close_app(win)
    win2 = make_app()          # 全量开启状态重启
    check("组合 %d 全量重启：开关与禁用关系恢复" % i,
          win2._auto_all_new is True
          and all(s == "disabled" for s in states(win2).values()))
    check("组合 %d 全量重启：三项偏好仍在" % i, marks(win2) == expect)
    check("组合 %d 全量重启：有效范围仍为 all（含三项全未勾）" % i,
          win2._auto_scopes() == ("all",))
    check("组合 %d 全量重启：自己与启动时自动开始仍可操作" % i,
          win2.chk_auto_all_new.cget("state") == "normal"
          and win2.chk_auto_start.cget("state") == "normal")
    win2.var_auto_all_new.set(False)
    win2.toggle_auto_all_new()
    check("组合 %d 关闭全量：范围逐项恢复" % i,
          win2._auto_scopes() == tuple(s for s in autoup.AUTO_SCOPES
                                       if s in scopes))
    check("组合 %d 关闭全量：三项控件恢复可操作" % i,
          all(s == "normal" for s in states(win2).values()))
    close_app(win2)
write_cfg({"auto_all_new": True, "auto_scopes": ["top", "pb"]})
win = make_app()
before = read_cfg().get("auto_scopes")
win.auto_vars["pb"].set(False)
win.toggle_auto_scope()
check("全量开启时偏好回调不改写保存的偏好",
      read_cfg().get("auto_scopes") == before
      and read_cfg().get("auto_all_new") is True)
check("全量开启时偏好回调不改写内存偏好",
      win.auto_flags == autoup.flags_of(before))
check("全量开启时偏好勾选标记被拨回", win.auto_vars["pb"].get() is True)
close_app(win)
# ------------------------------------------------------- 3. 基础数据门控
win = make_app({"auto_all_new": True, "auto_scopes": [],
                "auto_start": False, "auto_refresh": True})
win._auto_on = True
win._auto_tick = lambda: None          # 排定的闹钟不干扰本节
win._watch_enabled = True
win.games = [dict(G_PLAIN)]
win.upload_arb = True
win.site_pb = []
win.site_top = {}
win.oms_user = {"id": 1, "username": "tester"}
win.oms_loaded = False
win.oms_synced = False
win.oms_videos = []
win.oms_idents = []
win.oms_err = ""
sent = []
win._start_upload = lambda paths, **kw: sent.append((list(paths), dict(kw)))
seq = win._site_seq

check("账号已到、列表未到：等待",
      "正在读取网站录像记录" in win._auto_ready_reason())
win._auto_tracker = autoup.Tracker(0, settle_s=0)
win._auto_tracker.prime([])
wm = win._auto_watermark
win._auto_tick_once()
win._auto_tick_once()
check("门控未就绪：不上传", sent == [])
check("门控未就绪：候选不 commit", bool(win._auto_tracker.pending()))
check("门控未就绪：不推进水位", win._auto_watermark == wm)

win._on_account(seq, win.oms_user, [], [], "")
check("完整账号事件 + 成功加载的空列表：就绪", win._auto_ready_reason() == "")
win._auto_tick_once()
check("列表就绪后，等待中的候选被判定上传", bool(sent) and len(sent[0][0]) == 1)
check("等待中的候选理由仍是「新增完成局」",
      sent and sent[0][1].get("why", {}).get(G_PLAIN["path"]) == ["新增完成局"])

win._on_account(seq, win.oms_user, [], [], "读取录像列表失败")
check("列表读取失败：继续等待", win._auto_ready_reason() != "")
win._site_seq += 1                       # 相当于点了「刷新网站记录」
seq2 = win._site_seq
check("刷新期间旧代次不算就绪", win._auto_ready_reason() != "")

wake = []
win._auto_tick = lambda: wake.append(1)
win._on_account(seq2, win.oms_user, [], [], "")
check("列表失败后刷新成功：恢复就绪", win._auto_ready_reason() == "")
check("完整账号事件安排了一次自动判定",
      pump(win, lambda: bool(wake), timeout=3))
win._auto_tick = lambda: None

# 换号：旧事件晚到必须丢弃，等当前账号的完整数据
win._site_seq += 1
cur = win._site_seq
win.oms_user = {"id": 2, "username": "other"}
win.oms_loaded = False
win.oms_synced = False
win.oms_videos = []
check("换号后等待当前账号数据", win._auto_ready_reason() != "")
win._on_account(cur - 1, {"id": 1, "username": "tester"}, [], [], "")
check("旧账号晚到的完整事件被丢弃", win._auto_ready_reason() != "")
win._on_account(cur, win.oms_user, [], [], "")
check("当前账号完整事件恢复就绪", win._auto_ready_reason() == "")

win._site_seq += 1
win.oms_user = None
win.oms_loaded = False
win.oms_synced = False
check("退出登录后停在未登录", "登录" in win._auto_ready_reason())

acct_ready(win)
win.site_pb = []
win.site_top = {}
win.var_auto_all_new.set(False)
win.toggle_auto_all_new()
win.auto_flags = {"rank": True, "pb": True, "pb_nf": True}
check("原三项范围在 PB/Top5 全空时仍要求网站成绩",
      "成绩" in win._auto_ready_reason())
close_app(win)
# --------------------------------------------- 4. 判定 / 预检 / 来源开关
journal.clear()
autolog.clear()
winC = make_app({"auto_all_new": True, "auto_scopes": [],
                 "auto_start": False, "auto_refresh": True})
winC._auto_tick = lambda: None
winC._auto_on = True
winC._watch_enabled = True
winC.games = fresh_games()
acct_ready(winC)
winC.oms_idents = ["X"]
winC.oms_videos = [{"timems": 1200, "bv": 3, "file_size": 160}]  # G_SITE 网站已有
journal.mark(openms.game_key(G_JOURNAL))                          # 台账已传
winC.site_pb = [{"level": "i", "bv": 34, "timems": 4000,
                 "nf": False, "video_id": 1, "rank": 1}]
winC.site_top = {"std:it": {"videos": [
    {"video_id": 100 + i, "value": v}
    for i, v in enumerate((1000, 1100, 1200, 1300, 1400))]}}
winC.upload_arb = False
sentC = []
winC._start_upload = lambda paths, **kw: sentC.append((list(paths), dict(kw)))


def rejudge():
    winC._auto_tracker = autoup.Tracker(0, settle_s=0)
    winC._auto_tracker.prime([])
    winC._auto_tick_once()
    winC._auto_tick_once()


rejudge()
check("全量：普通完成局不破 PB、不入 Top5、低于 BV 门槛也入队",
      bool(sentC) and set(sentC[0][0]) == {G_PLAIN["path"], G_PLAIN2["path"]})
check("全量：静默上传通道 + 独立理由",
      sentC[0][1].get("silent") is True
      and all(sentC[0][1]["why"][p] == ["新增完成局"] for p in sentC[0][0]))
check("判定完才 commit，候选池清空", winC._auto_tracker.pending() == [])
winC._auto_tick_once()
check("同一批不会重复启动上传", len(sentC) == 1)

results = [x["result"] for x in autolog.items()]
check("冻结预检跳过并保留可读原因", any("会被冻结" in r for r in results))
check("标识不匹配跳过并保留可读原因", any("标识不在账号里" in r for r in results))
check("网站已有 / 台账已传都跳过（两条）",
      sum("网站上已经有这条了" in r for r in results) == 2)
check("关掉「含阿比特」时不上传阿比特",
      G_ARB["path"] not in sentC[0][0])

winC.upload_arb = True
sentC.clear()
rejudge()
check("开启「含阿比特」时按同一规则参与",
      set(sentC[0][0]) == {G_PLAIN["path"], G_PLAIN2["path"], G_ARB["path"]})

# 已完成判定的局，不因切换范围重新入队。
sentC.clear()
tracker = winC._auto_tracker
winC._auto_on = False
winC.var_auto_all_new.set(False)
winC.toggle_auto_all_new()
winC.var_auto_all_new.set(True)
winC.toggle_auto_all_new()
winC._auto_tick_once()
winC._auto_tick_once()
check("切范围不自动开始、不重建 Tracker",
      not winC._auto_on and winC._auto_tracker is tracker
      and winC.var_auto_start.get() is False)
winC._auto_on = True
winC._auto_tick_once()
winC._auto_tick_once()
check("切范围不补传已 commit 的局", sentC == [])

close_app(winC)
# --------------------------------------------- 5. 假站点 / 假上传端到端
journal.clear()
autolog.clear()
winU = make_app({"auto_all_new": True, "auto_scopes": [],
                 "auto_start": False, "auto_refresh": True})
winU._auto_tick = lambda: None
winU._auto_on = True
winU._watch_enabled = True
winU.games = [dict(G_PLAIN), dict(G_PLAIN2)]
acct_ready(winU)
winU.oms_idents = []
winU.site_pb = []
winU.site_top = {}
winU.upload_arb = True
uploaded = []


def fake_upload(path):
    uploaded.append(path)
    return {"ok": True, "state": "a", "vid": 900 + len(uploaded),
            "collision": False}


winU.oms.upload = fake_upload
winU._auto_tracker = autoup.Tracker(0, settle_s=0)
winU._auto_tracker.prime([])
tracker = winU._auto_tracker
winU._up_running = True
winU.var_auto_all_new.set(False)
winU.toggle_auto_all_new()
winU.var_auto_all_new.set(True)
winU.toggle_auto_all_new()
winU._auto_tick_once()
check("上传中切范围不另开批次且保留 Tracker",
      uploaded == [] and winU._auto_tracker is tracker)
winU._up_running = False
winU._auto_tick_once()
winU._auto_tick_once()
check("假站点批次已串行启动", winU._up_running is True)
check("假站点上传批次跑完",
      pump(winU, lambda: not winU._up_running, timeout=12))
check("两条新局都被假站点接收",
      set(uploaded) == {G_PLAIN["path"], G_PLAIN2["path"]}
      and len(uploaded) == 2)
check("上传成功写入去重台账",
      journal.has(openms.game_key(G_PLAIN))
      and journal.has(openms.game_key(G_PLAIN2)))
log = autolog.items()
check("记录里写明「新增完成局」与结果",
      len(log) == 2
      and all(x["why"] == "新增完成局" and x["ok"] for x in log))
winU._up_running = True
winU._start_upload([G_PLAIN["path"]], silent=True)
check("上传中不允许并行再开一批", len(uploaded) == 2)
winU._up_running = False

# 上传后再扫描（模拟 tracker 重建）：网站列表 + 台账都不该让它重复上传
uploaded.clear()
winU._auto_tracker = autoup.Tracker(0, settle_s=0)
winU._auto_tracker.prime([])
winU._up_running = False
winU._auto_tick_once()
winU._auto_tick_once()
pump(winU, lambda: False, timeout=0.6)
check("上传后重扫不重复上传成功局", uploaded == [])
check("成功后内存里补了站点记录（供去重）",
      len(winU.oms_videos) == 2)
close_app(winU)
check("全程没有发起网络请求", not _net_calls)
if _net_calls:
    print("  实际请求：%r" % (_net_calls,))
bad = [l for l, ok in checks if not ok]
print("\n%d 项检查，%d 项失败" % (len(checks), len(bad)))
for l in bad:
    print("  FAIL", l)
shutil.rmtree(TMP, ignore_errors=True)
raise SystemExit(1 if bad else 0)

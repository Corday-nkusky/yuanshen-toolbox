"""阿比特 .avf 兼容 / 多目录 / 阿比特上传开关 的测试。

不联网、不碰真实站点，也不改用户的配置（数据目录被指到临时文件夹）。
跑法：
    python test_arb.py       全部通过退出码 0，否则 1
样本来源：
    .evf ← %APPDATA%\\Metasweeper3.3.4\\replay
    .avf ← D:\\扫雷\\gsc\\27415gsc16
"""

import os
import shutil
import sys
import tempfile
import time

# 必须先于 import app：paths.data_dir() 一旦定下来就会缓存
TMP = tempfile.mkdtemp(prefix="ms-pb-test-")
os.environ["MS_PB_DATA_DIR"] = os.path.join(TMP, "data")

import app as A          # noqa: E402
import core              # noqa: E402
import journal           # noqa: E402

REPLAY = os.path.join(os.environ.get("APPDATA", ""), "Metasweeper3.3.4", "replay")
AVF_SRC = r"D:\扫雷\gsc\27415gsc16"

checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


def pump(win, cond, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        win.update()
        if cond():
            return True
        time.sleep(0.03)
    return False


def stage():
    """把几份样本拷进临时目录，避免碰用户原始录像。"""
    ms_dir = os.path.join(TMP, "replay")
    arb_dir = os.path.join(TMP, "arb")
    os.makedirs(ms_dir, exist_ok=True)
    os.makedirs(arb_dir, exist_ok=True)
    evf = sorted(f for f in os.listdir(REPLAY) if f.lower().endswith(".evf"))[:3]
    for fn in evf:
        shutil.copy2(os.path.join(REPLAY, fn), os.path.join(ms_dir, fn))
    avf = sorted(f for f in os.listdir(AVF_SRC) if f.lower().endswith(".avf"))[:3]
    for fn in avf:
        shutil.copy2(os.path.join(AVF_SRC, fn), os.path.join(arb_dir, fn))
    return ms_dir, arb_dir, len(evf), len(avf)


def test_filename():
    cases = [
        ("HI-SCORE Beg_1.00_3BV=6_3BVs=6_Anonymous.avf",
         {"level": "b", "time": 1.0, "bv": 6, "flag2": "avf"}),
        ("Beg_NF_1.06_3BV=5_3BVs=4.71_Anonymous.avf",
         {"level": "b", "time": 1.06, "bv": 5, "flag2": "nf"}),
        ("#Exp_4.73_3BV=191_3BVs=4.01_Anonymous.avf",
         {"level": "e", "time": 4.73, "bv": 191, "flag2": "avf"}),
    ]
    for fn, want in cases:
        got = core.parse_avf_filename(fn) or {}
        ok = all(abs(got.get(k, -1) - v) < 1e-6 if isinstance(v, float)
                 else got.get(k) == v for k, v in want.items())
        check("解析文件名 %s" % fn[:22], ok)

    # 玩家名在前、不带 3BV= 的命名：解析不出就返回 None（交给录像内容兜底）
    check("非标准命名返回 None",
          core.parse_avf_filename("Ju Ze En_Exp_26.1(3bv123).avf") is None)


def test_scan(ms_dir, arb_dir, n_evf, n_avf):
    games, info = core.scan([ms_dir, arb_dir], deep=True)
    by_src = info["by_src"]
    check("两类录像都被扫到（%d / %d）" % (by_src["ms"], by_src["arb"]),
          by_src["ms"] == n_evf and by_src["arb"] == n_avf)
    check("每局都带 src 标记",
          all(g.get("src") in ("ms", "arb") for g in games))
    arb = [g for g in games if g["src"] == "arb"]
    check("阿比特局有 rce → 能判 NF/FL",
          all(g.get("rce") is not None and g.get("style") in ("nf", "fl")
              for g in arb))
    check("阿比特局的时间和 BV 来自录像内容（不为 0）",
          all(g["time"] > 0 and g["bv"] > 0 and g["level"] in core.LEVELS
              for g in arb))
    check("阿比特不做「文件名与录像不符」比对",
          not any(f.lower().endswith(".avf") for f in info["tampered"]))

    # 同一个目录既单独加、又通过父目录加 → 不能算两次
    games2, info2 = core.scan([TMP, arb_dir], deep=True)
    check("重复目录会去重（%d 个）" % info2["duplicates"], info2["duplicates"] > 0)
    check("去重后局数不变", len(games2) == len(games))

    peek = core.peek_dir(arb_dir)
    check("peek_dir 数出 %d 个 .avf" % peek["arb"], peek["arb"] == n_avf)

    # 第二遍应该全部命中缓存，且结果完全一致
    games3, info3 = core.scan([ms_dir, arb_dir], deep=True)
    check("第二遍全部走缓存（hit=%d）" % info3["cache"]["hits"],
          info3["cache"]["hits"] == n_evf + n_avf and info3["cache"]["misses"] == 0)
    check("缓存结果与首次一致",
          sorted((g["path"], g["bv"], round(g["time"], 3), g.get("rce")) for g in games3) ==
          sorted((g["path"], g["bv"], round(g["time"], 3), g.get("rce")) for g in games))

    # 改一个文件的 mtime → 只有它重新解析
    victim = os.path.join(arb_dir, sorted(os.listdir(arb_dir))[0])
    os.utime(victim, (time.time() + 5, time.time() + 5))
    _g4, info4 = core.scan([ms_dir, arb_dir], deep=True)
    check("录像改动后只重解析 1 个（miss=%d）" % info4["cache"]["misses"],
          info4["cache"]["misses"] == 1)

    pb = core.build_pb(games, split_style=True)
    check("PB 表把阿比特算进来", any(r["path"].lower().endswith(".avf") for r in pb))


def test_dedupe(arb_dir):
    """同一局存了多份（阿比特 HI-SCORE 与 replays 里的正本）只算一局。"""
    d1 = os.path.join(TMP, "dup_a")
    d2 = os.path.join(TMP, "dup_b")
    os.makedirs(d1, exist_ok=True)
    os.makedirs(d2, exist_ok=True)
    fn = sorted(f for f in os.listdir(arb_dir) if f.lower().endswith(".avf"))[0]
    src = os.path.join(arb_dir, fn)
    shutil.copy2(src, os.path.join(d1, fn))
    shutil.copy2(src, os.path.join(d2, "HI-SCORE " + fn))

    games, info = core.scan([d1, d2], deep=True)
    check("同字节的 HI-SCORE 副本被合并（剩 %d 局）" % len(games), len(games) == 1)
    check("dup_removed 记了 1 份", info["dup_removed"] == 1)
    check("留下的是正本（文件名不带 HI-SCORE）",
          bool(games) and not games[0]["file"].lower().startswith("hi-score"))
    check("by_src 按去重后的结果重算", info["by_src"]["arb"] == 1)

    raw, info_raw = core.scan([d1, d2], deep=True, dedupe=False)
    check("dedupe=False 时两份都留着", len(raw) == 2 and info_raw["dup_removed"] == 0)

    # 同 size、同指标，但内容不同 → 必须都留下。
    # 阿比特录像的 rtime 只有 10ms 精度，指标相同不代表是同一局。
    p1 = os.path.join(TMP, "same_a.avf")
    p2 = os.path.join(TMP, "same_b.avf")
    for p, blob in ((p1, b"0000"), (p2, b"1111")):
        with open(p, "wb") as f:
            f.write(blob)

    def fake(path):
        return {"path": path, "file": os.path.basename(path), "size": 4,
                "level": "b", "bv": 3, "timems": 970}

    kept, dropped = core.dedupe_games([fake(p1), fake(p2)])
    check("同大小但字节不同的两局都保留", len(kept) == 2 and dropped == 0)

    # 优先级：非 HI-SCORE 优先于「层级浅」
    deep_dir = os.path.join(TMP, "nested", "deeper")
    os.makedirs(deep_dir, exist_ok=True)
    q1 = os.path.join(deep_dir, "Beg_NF_9.99_3BV=5_3BVs=0.5_Anonymous.avf")
    q2 = os.path.join(TMP, "HI-SCORE Beg_NF_9.99_3BV=5_3BVs=0.5_Anonymous.avf")
    for p in (q1, q2):
        with open(p, "wb") as f:
            f.write(b"sameness")

    def fake2(path):
        return {"path": path, "file": os.path.basename(path), "size": 8,
                "level": "b", "bv": 5, "timems": 9990}

    kept, dropped = core.dedupe_games([fake2(q2), fake2(q1)])
    check("正本优先于层级浅的子目录（留下 %s）" % os.path.basename(kept[0]["path"]),
          dropped == 1 and os.path.dirname(kept[0]["path"]) == deep_dir)


def test_dedupe_real():
    """真实数据的规模检查（不联网，只读 D:\\扫雷）。"""
    root = r"D:\扫雷"
    if not os.path.isdir(root):
        return
    games, info = core.scan([root], deep=True)
    raw, _ri = core.scan([root], deep=True, dedupe=False)
    check("真实目录 %d 份录像 → %d 局，去掉 %d 份重复副本"
          % (len(raw), len(games), info["dup_removed"]),
          info["dup_removed"] > 0 and len(raw) - len(games) == info["dup_removed"])
    again, _i2 = core.scan([root], deep=True)
    check("重复扫描结果稳定（%d == %d）" % (len(games), len(again)),
          len(again) == len(games))


def test_ui(ms_dir, arb_dir):
    A.messagebox.showwarning = lambda *a, **k: None
    A.messagebox.showinfo = lambda *a, **k: None
    A.messagebox.askyesno = lambda *a, **k: True

    win = A.App()
    win.withdraw()
    win.apply_roots([{"path": ms_dir, "on": True},
                     {"path": arb_dir, "on": True}], rescan=False)
    win.start_scan()
    ok = pump(win, lambda: not win._scanning and bool(win.games))
    check("界面按多目录扫描完成", ok)
    check("导航显示「+1 个目录」", "+1 个目录" in win.lbl_meta.cget("text")
          or "+1 个目录" in win.roots_label())

    win.oms_user = {"id": 1, "username": "tester", "realname": "x"}
    win.oms_videos = []
    win.oms_synced = True
    win.upload_arb = True
    win.var_arb.set(True)
    win._rebuild_up_rows()
    n_all = len(win.up_rows)
    n_arb = sum(1 for r in win.up_rows if r["src_cn"] == core.SRC_CN["arb"])
    check("勾选时阿比特出现在上传列表（%d 条）" % n_arb, n_arb > 0)
    check("每行都有来源列", all(r.get("src_cn") for r in win.up_rows))

    win.var_arb.set(False)
    win.toggle_upload_arb()
    check("取消勾选后列表里没有阿比特",
          not any(r["src_cn"] == core.SRC_CN["arb"] for r in win.up_rows))
    check("取消勾选后条数变少（%d → %d）" % (n_all, len(win.up_rows)),
          len(win.up_rows) == n_all - n_arb)
    check("开关写进了配置", A.load_config().get("upload_arb") is False)
    check("统计行说明了阿比特被排除",
          "已排除" in win.lbl_up_stat.cget("text"))

    win.var_arb.set(True)
    win.toggle_upload_arb()
    check("重新勾选能恢复", len(win.up_rows) == n_all)

    # 停用一个目录 → 只扫另一个
    win.apply_roots([{"path": ms_dir, "on": True},
                     {"path": arb_dir, "on": False}], rescan=True)
    pump(win, lambda: not win._scanning)
    win.update()
    check("停用阿比特目录后只剩元扫雷局",
          all(g["src"] == "ms" for g in win.games) and bool(win.games))
    check("停用的目录仍留在配置里（on=False）",
          any(not r["on"] for r in win.roots))

    # 目录对话框：类型标注对不对、启用能不能生效
    dlg = A.PathsDialog(win)
    dlg.withdraw()
    check("对话框列出 %d 个目录" % len(dlg.roots), len(dlg.roots) == 2)
    kinds = {r["src_cn"] for r in dlg.tbl.rows}
    check("对话框标出「元扫雷」「阿比特」", {"元扫雷", "阿比特"} <= kinds)
    off = [r["path"] for r in dlg.roots if not r["on"]]
    dlg.tbl.tv.selection_set(*off)
    dlg.toggle_on()
    pump(win, lambda: not win._scanning)
    win.update()
    check("在对话框里启用后阿比特局回来了",
          any(g["src"] == "arb" for g in win.games))
    dlg.destroy()

    win.destroy()


def test_default_roots(arb_dir):
    """默认只认元扫雷；阿比特目录必须手动加，且加之前要确认「全是自己打的」。"""
    auto = core.default_roots()
    check("默认目录不为空", bool(auto))
    check("默认目录里没有阿比特的 replays（%d 个）" % len(auto),
          all(not p.rstrip("\\/").lower().endswith("replays") for p in auto))
    check("默认目录都是含 .evf 的目录",
          all(core.has_evf(p) for p in auto))
    check("discover_root 不会把手选的阿比特目录认成录像目录",
          core.discover_root(arb_dir) != os.path.abspath(arb_dir))

    # 造一个只有 1 个 .avf 的临时目录，用来测「手动添加」这条路
    ghost = os.path.join(TMP, "ghost")
    os.makedirs(ghost, exist_ok=True)
    src = next((os.path.join(arb_dir, f) for f in sorted(os.listdir(arb_dir))
                if f.lower().endswith(".avf")), None)
    if src:
        shutil.copy2(src, os.path.join(ghost, os.path.basename(src)))

    A.messagebox.askokcancel = lambda *a, **k: False
    win = A.App()
    win.withdraw()
    win.apply_roots([{"path": auto[0], "on": True}], rescan=False)
    pump(win, lambda: not win._scanning)
    before = [r["path"] for r in win.roots]
    real_ask = A.filedialog.askdirectory
    A.filedialog.askdirectory = lambda **k: ghost
    win.pick_dir()
    check("拒绝确认后目录没变（%d 个）" % len(win.roots),
          [r["path"] for r in win.roots] == before)

    A.messagebox.askokcancel = lambda *a, **k: True
    win.pick_dir()
    pump(win, lambda: not win._scanning)
    check("确认后主目录换成手动选的目录",
          os.path.normcase(win.roots[0]["path"]) == os.path.normcase(ghost))
    A.filedialog.askdirectory = real_ask
    win.destroy()


def main():
    missing = []
    if not os.path.isdir(REPLAY):
        missing.append(REPLAY)
    if not os.path.isdir(AVF_SRC):
        missing.append(AVF_SRC)
    if missing:
        print("跳过：找不到样本目录 %s" % ", ".join(missing))
        return 0

    try:
        ms_dir, arb_dir, n_evf, n_avf = stage()
        test_filename()
        test_scan(ms_dir, arb_dir, n_evf, n_avf)
        test_dedupe(arb_dir)
        test_dedupe_real()
        test_default_roots(arb_dir)
        test_ui(ms_dir, arb_dir)
    finally:
        journal.clear()
        shutil.rmtree(TMP, ignore_errors=True)

    bad = sum(1 for _l, ok in checks if not ok)
    print("== %s（%d 项）==" % ("全部通过" if not bad else "%d 项失败" % bad, len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

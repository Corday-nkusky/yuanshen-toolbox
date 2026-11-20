"""强无猜（mode=6）：不进统计，但能单独自动上传。

事实依据：
- 元扫雷文件名第二段是 mode，6 = 强无猜（见 test_mode.py 的来源）。
- 网站上传接口的 7 道检查不按 mode 过滤，强无猜录像可传（docs/openms-upload-research.md）。
- 强无猜在站点只有 6 格 + 总和榜，没有逐 BV 的 PB 榜，所以这里只做「新局全传」，
  复用 autoup 的全量（ALL）路径，不重写一套判定。

纯逻辑：不碰界面、不发网络。用 deep=False 扫描，空文件即可造样本。
"""
import os
import shutil
import tempfile

import core
import autoup

checks = []


def check(name, ok):
    checks.append((name, bool(ok)))


def ng_game(path, level="b", bv=10, sec=1.0, valid=0, ident="tester", mt=1000):
    return {"path": path, "file": os.path.basename(path), "src": "ms",
            "level": level, "bv": bv, "time": sec, "timems": int(sec * 1000),
            "bvs": bv / sec, "stnb": 0.0, "valid": valid, "ident": ident,
            "size": 32, "mtime": mt, "mtime_ns": mt * 1_000_000,
            "t0": mt, "style": "fl", "nf": False}


# ---------- 扫描：强无猜另收一桶，标准统计一条不受影响 ----------
tmp = tempfile.mkdtemp(prefix="ng-test-")
for fn in ("b_0_1.000_10_10.000_X.evf",      # 标准
           "b_6_1.000_10_10.000_X.evf",      # 强无猜
           "i_6_30.000_60_2.000_X.evf",      # 强无猜·中级
           "b_5_1.000_10_10.000_X.evf"):     # 竞速无猜（仍算 non_std）
    with open(os.path.join(tmp, fn), "wb") as fh:
        fh.write(b"\0" * 32)
games, info = core.scan(tmp, deep=False)
check("标准局照常进统计", len(games) == 1)
check("强无猜两局收进 ng_games", info["ng_count"] == 2)
check("强无猜不在统计池里",
      all(g["mode"] != "6" for g in games if g.get("mode")))
check("竞速无猜仍计入 non_std", info["non_std"] == 1)
ng_paths = {os.path.basename(g["path"]) for g in info["ng_games"]}
check("ng 桶里正是那两个强无猜文件",
      ng_paths == {"b_6_1.000_10_10.000_X.evf", "i_6_30.000_60_2.000_X.evf"})
shutil.rmtree(tmp, ignore_errors=True)


# ---------- 判定：复用全量路径，新局全传 ----------
g1 = ng_game("/r/b_6_1.000_10_10.000_X.evf")
g2 = ng_game("/r/i_6_30.000_60_2.000_X.evf", level="i", bv=60, sec=30.0)
pool = [g1, g2]
cands = autoup.judge(pool, (autoup.ALL,), {g1["path"], g2["path"]})
check("全量判定放行两条强无猜新局",
      {c["path"] for c in cands} == {g1["path"], g2["path"]})
check("没有新局时一条不传", autoup.judge(pool, (autoup.ALL,), set()) == [])
check("只放行新出现的那条",
      [c["path"] for c in autoup.judge(pool, (autoup.ALL,), {g2["path"]})]
      == [g2["path"]])


# ---------- 预检：不合法 / 标识不符照样拦 ----------
check("valid=1 会被冻结", autoup.blocked(ng_game("/x", valid=1)) == "会被冻结")
check("标识不在账号里被拦",
      autoup.blocked(ng_game("/x", ident="someone"), ["tester"]) == "标识不在账号里")
check("标识相符放行", autoup.blocked(ng_game("/x"), ["tester"]) is None)


# ---------- 新局识别：历史不补传，关机期间补传 ----------
old = ng_game("/r/old.evf", mt=1000)
fresh = ng_game("/r/new.evf", mt=5000)
tr = autoup.Tracker(watermark=2000 * 1_000_000, settle_s=0.0)
tr.prime([old, fresh], now=50.0)
ready = tr.observe([old, fresh], now=100.0)
check("水位以下的旧局不补传", old["path"] not in ready)
check("水位以上（关机期间）的局进候选", fresh["path"] in ready)
tr.commit(ready)
check("commit 之后不再算新局", tr.observe([old, fresh], now=200.0) == [])


# ---------- 进榜才传：刷新自己那一格才传 ----------
site_ng = {
    "strict_ng": {"bt": 1000, "bb": 5.0, "it": None, "ib": None,
                  "et": None, "eb": None},
    "strict_ng_nf": {"bt": 2000, "bb": 4.0, "it": None, "ib": None,
                     "et": None, "eb": None},
}


def ng(path, level="b", bv=10, sec=1.0, nf=False):
    g = ng_game(path, level=level, bv=bv, sec=sec)
    g["nf"] = nf
    g["style"] = "nf" if nf else "fl"
    return g


def top_paths(games, ng_dict=None):
    return {c["path"] for c in autoup.judge_ng(
        games, {g["path"] for g in games}, "top",
        site_ng if ng_dict is None else ng_dict)}


# 初级 time 900ms < 个人最佳 1000ms → 刷新 bt
check("进榜才传：更快 time 放行",
      top_paths([ng("/a", sec=0.9)]) == {"/a"})
# 把 bvs 最佳设到不可达，单独看 time：同值不算（站点同分优先更早上传）
time_ng = {"strict_ng": {"bt": 1000, "bb": 99.0, "it": None, "ib": None,
                         "et": None, "eb": None},
           "strict_ng_nf": {"bt": 1000, "bb": 99.0}}
check("进榜才传：同值不传", top_paths([ng("/a", sec=1.0)], time_ng) == set())
# 更慢且不改善 bvs → 不传
check("进榜才传：更慢不传", top_paths([ng("/a", sec=1.2)], time_ng) == set())
# 初级 time 最低 3BV=2，bv=1 进不了 bt
check("进榜才传：初级 time 低于 3BV 门槛不传",
      top_paths([ng("/a", bv=1, sec=0.5)]) == set())
# 初级 bvs 最低 3BV=4：把个人 time 最佳压到极低（time 无从改善），
# bv=3 即便 bvs 很高也进不了 bb —— 单独验证 bvs 门槛。
gate_ng = {"strict_ng": {"bt": 100, "bb": 5.0, "it": None, "ib": None,
                         "et": None, "eb": None},
           "strict_ng_nf": {"bt": 100, "bb": 5.0}}
check("进榜才传：初级 bvs 低于 3BV 门槛不传",
      top_paths([ng("/a", bv=3, sec=0.5)], gate_ng) == set())
# 中级 it 还空着 → 填上即进榜
check("进榜才传：空格首次进榜",
      top_paths([ng("/a", level="i", bv=60, sec=30.0)]) == {"/a"})
# bvs 6.0 > 个人最佳 5.0（bv=10≥4）→ 刷新 bb
check("进榜才传：更高 bvs 放行",
      top_paths([ng("/a", bv=10, sec=10 / 6.0)]) == {"/a"})
# NF 局：普通榜 bb 进不了，但 NF 榜 bb(4.0) 能进 → 标 NF
g_nf = ng("/a", bv=10, sec=2.0, nf=True)   # bvs=5.0：普通 bb5.0 同值不进，NF bb4.0 可进
c_nf = autoup.judge_ng([g_nf], {"/a"}, "top", site_ng)
check("进榜才传：NF 局走 NF 榜", len(c_nf) == 1
      and any("NF" in w for w in c_nf[0]["why"]))
# 全上传不看成绩
check("全上传：无视榜成绩全放行",
      {c["path"] for c in autoup.judge_ng([ng("/a", sec=9.0)], {"/a"}, "all", {})}
      == {"/a"})
check("进榜才传：没有新局一条不传",
      autoup.judge_ng([ng("/a")], set(), "top", site_ng) == [])


ok = 0
for name, good in checks:
    print(("OK   " if good else "FAIL ") + name)
    ok += 1 if good else 0
print("%d/%d" % (ok, len(checks)))
print("== 全部通过 ==" if ok == len(checks) else "!! 有失败项 !!")
raise SystemExit(0 if ok == len(checks) else 1)
"""只统计标准模式：无猜 / 可猜 / win7 / 自定义盘面一律不进统计。

事实依据（不是猜的）：
- 元扫雷 `cal_evf_filename` 生成的文件名是
  ``{级别前缀}{mode}_{rtime}_{bbv}_{bbvs}_{玩家}``，第二段就是 mode 的十进制值
  （eee555/Metasweeper，src/app/game_engine.py）。
- 级别前缀标准三档是 ``b_`` / ``i_`` / ``e_``，自定义盘面是 ``c_``
  （src/config/constants.py）。
- mode 编码：0 标准、1 upk、4 win7、5 竞速无猜、6 强无猜、7 弱无猜、8 准无猜、
  9 强可猜、10 弱可猜、11 标准递归
  （openms 站点源码 back_end/saolei/config/text_choices.py 的 Mode 枚举）。

用 ``deep=False`` 扫描：不读录像内容，只走文件名，所以可以用空文件造样本。

另外把 ``core.scan()`` 的输出直接喂给 ``autoup.judge(..., "all")``：全量范围
只放行扫描层确认过的标准完成局，判定层绝不重新实现一套模式 / 完成状态检查。
阿比特的 mode 来自录像内容，这里用合成解析结果验证扫描阶段就排除。
"""
import os
import shutil
import tempfile

import autoup
import core

checks = []


def check(name, ok):
    checks.append((name, bool(ok)))


# ---------- 文件名解析：mode 就是第二段 ----------
std = core.parse_filename("b_0_1.774_9_5.073_Corday(Tongcheng).evf")
check("标准局：mode 读成 0", std and std["mode"] == "0")

for mode, label in (("5", "竞速无猜"), ("6", "强无猜"), ("7", "弱无猜"),
                    ("8", "准无猜"), ("9", "强可猜"), ("1", "upk"),
                    ("4", "win7"), ("11", "标准递归")):
    rec = core.parse_filename("b_%s_1.000_10_10.000_X.evf" % mode)
    check("%s（mode=%s）解析出 mode" % (label, mode), rec and rec["mode"] == mode)

check("自定义盘面 c_ 前缀认不出级别",
      core.parse_filename("c_0_1.000_10_10.000_X.evf") is None)


# ---------- 扫描：非标准局一条都不留 ----------
tmp = tempfile.mkdtemp(prefix="mode-test-")
files = [
    ("b_0_1.000_10_10.000_X.evf", True),      # 标准，保留
    ("i_0_30.000_60_2.000_X.evf", True),      # 标准，保留
    ("b_5_1.000_10_10.000_X.evf", False),     # 竞速无猜
    ("b_6_1.000_10_10.000_X.evf", False),     # 强无猜
    ("i_7_30.000_60_2.000_X.evf", False),     # 弱无猜
    ("e_8_60.000_150_2.500_X.evf", False),    # 准无猜
    ("b_9_1.000_10_10.000_X.evf", False),     # 强可猜
    ("b_4_1.000_10_10.000_X.evf", False),     # win7
    ("e_11_60.000_150_2.500_X.evf", False),   # 标准递归
    ("c_0_1.000_10_10.000_X.evf", False),     # 自定义盘面
    ("b_0_1.000_10_10.000_X_fail.evf", False),  # 没打完（另算 fail）
]
for fn, _keep in files:
    with open(os.path.join(tmp, fn), "wb") as fh:
        fh.write(b"\0" * 32)

games, info = core.scan(tmp, deep=False)
kept = sorted(os.path.basename(g["path"]) for g in games)

check("只留下两条标准局", kept == ["b_0_1.000_10_10.000_X.evf",
                              "i_0_30.000_60_2.000_X.evf"])
check("8 条非标准局计入 non_std", info["non_std"] == 8)
check("没打完的仍单独计数", info["fail_skipped"] == 1)
check("非标准局不算「读不出」", not info["unparsed"])

# ---------- 全量范围的候选必须来自 core.scan() ----------
# 不是给 judge() 塞一条带 mode 字段的记录就算验过扫描规则：这里用上面真实
# 扫出来的 games，确认非标准局 / 失败局 / 自定义盘面一条都进不了全量候选。
cands = autoup.judge(games, (autoup.ALL_SCOPE,),
                     {g["path"] for g in games})
check("全量候选恰好是扫描放行的两条标准完成局",
      {c["path"] for c in cands} == {g["path"] for g in games})
check("全量候选用「新增完成局」作理由",
      all(c["why"] == ["新增完成局"] for c in cands))
excluded_names = {fn for fn, keep in files if not keep}
check("非标准 / 失败 / 自定义盘面都不在全量候选里",
      not (excluded_names & {os.path.basename(c["path"]) for c in cands}))

shutil.rmtree(tmp, ignore_errors=True)


# ---------- 阿比特：内容里的 level / mode 才作数 ----------
tmp_avf = tempfile.mkdtemp(prefix="mode-avf-")
avf_files = {
    # 文件名  ->  (mode, level, 完成状态, 秒, BV, 字节数)
    "Beg_2.00_3BV=12_3BVs=6.00_A.avf": (0, "b", True, 2.0, 12, 64),
    "Int_3.00_3BV=13_3BVs=4.33_B.avf": (5, "i", True, 3.0, 13, 96),
    "Exp_4.00_3BV=14_3BVs=3.50_C.avf": (0, "e", False, 4.0, 14, 128),
}
for fn, (_mode, _lv, _done, _sec, _bv, nbytes) in avf_files.items():
    with open(os.path.join(tmp_avf, fn), "wb") as fh:
        fh.write(bytes([len(fn)]) * nbytes)

_real_read_video = core.read_video


def fake_read_video(path):
    """合成解析结果，不碰 ms-toollib；mode / 完成状态由这里给出。"""
    fn = os.path.basename(path)
    if fn not in avf_files:
        return None
    mode, lv, done, sec, bv, _n = avf_files[fn]
    return {"rce": 0, "left": 0, "flag": 0, "solved": bv,
            "bv": bv, "time": sec, "timems": int(sec * 1000),
            "level": lv, "mode": mode, "completed": done,
            "ident": "X", "valid": 0, "src": "arb",
            "t0": 1_700_000_000.0, "t1": 1_700_000_000.0 + sec}


try:
    core.read_video = fake_read_video
    avf_games, avf_info = core.scan(tmp_avf, deep=True, dedupe=False)
finally:
    core.read_video = _real_read_video

check("阿比特 mode 非 0 在扫描阶段就排除（non_std=1）",
      avf_info["non_std"] == 1)
check("阿比特未完成局在扫描阶段就排除（fail_skipped=1）",
      avf_info["fail_skipped"] == 1)
check("阿比特只剩 mode 0 的完成局",
      [os.path.basename(g["path"]) for g in avf_games]
      == ["Beg_2.00_3BV=12_3BVs=6.00_A.avf"])
check("阿比特标准完成局能进全量候选",
      [c["path"] for c in autoup.judge(
          avf_games, (autoup.ALL_SCOPE,), {g["path"] for g in avf_games})]
      == [g["path"] for g in avf_games])
check("关掉「含阿比特」时这些局不进候选池",
      autoup.pool(avf_games, False) == [])
check("开启「含阿比特」时按同一规则进候选池",
      autoup.pool(avf_games, True) == list(avf_games))
shutil.rmtree(tmp_avf, ignore_errors=True)

check("level 6（自定义盘面）在 read_video 里就是 None 级别",
      core.VIDEO_LEVEL.get(6) is None)
check("标准模式常量是 0", core.STD_MODE == 0)


ok = 0
for name, good in checks:
    print(("OK   " if good else "FAIL ") + name)
    ok += 1 if good else 0
print("%d/%d" % (ok, len(checks)))
print("== 全部通过 ==" if ok == len(checks) else "!! 有失败项 !!")
raise SystemExit(0 if ok == len(checks) else 1)

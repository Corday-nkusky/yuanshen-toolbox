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
"""
import os
import shutil
import tempfile

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

shutil.rmtree(tmp, ignore_errors=True)


# ---------- 阿比特：内容里的 level / mode 才作数 ----------
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

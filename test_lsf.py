"""LSF 三档窗口敏感度 + PB 保持天数 回归。

跑法：python test_lsf.py —— 全部通过退出码 0，否则 1。
不联网、不碰真实数据：全部用构造局。
"""

from datetime import date, datetime

import core

checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


def ts(day):
    """2026-01-01 起第 day 天的正午 epoch 秒。"""
    return datetime(2026, 1, 1, 12).timestamp() + day * 86400


def game(level, bv, sec, day, nf=False):
    return {"level": level, "bv": bv, "time": sec, "timems": int(sec * 1000),
            "bvs": bv / sec, "stnb": core.stnb_of(bv, sec, level),
            "nf": nf, "style": "nf" if nf else "fl",
            "t0": ts(day), "mtime": ts(day), "path": "x/%s_%d_%d.evf" % (level, bv, day)}


ANCHORS = {"%s:%s%s" % (mode, lv, stat): v
           for mode in ("std", "nf") for lv in core.LEVELS
           for stat, v in (("t", 10000), ("b", 5.0), ("s", 1.0))}
ANCHORS["std:et"] = 40000
ANCHORS["nf:et"] = 45000

# ---------------------------------------------------------------- 敏感度
check("缺锚点 -> 空 dict", core.lsf_sensitivity([game("i", 40, 12, 0)], anchors={}) == {})

gs = [game("i", 40 + k, 12.0 + k * 0.7, k) for k in range(30)]
sens = core.lsf_sensitivity(gs, anchors=ANCHORS)
check("三档窗口都在", set(sens) == {1, 5, 25})
check("上限 >= 五局 >= 25 局（单调）",
      sens[1]["lsf"] >= sens[5]["lsf"] >= sens[25]["lsf"])
check("有离散度时上限严格更高", sens[1]["lsf"] > sens[25]["lsf"])
rep = core.lsf_report(gs, anchors=ANCHORS)
check("窗口 5 与 lsf_report 同口径",
      abs(sens[5]["lsf"] - rep["soft"]["lsf"]) < 1e-9)
check("每档都带定阶与级别明细",
      all(sens[n].get("grade") and set(sens[n]["levels"]) == set(core.LEVELS)
          for n in sens))

one = [game("i", 40, 12.0, 0)]
s1 = core.lsf_sensitivity(one, anchors=ANCHORS)
check("只有一局：top1 不补位、top5 补位，top1 更高",
      s1[1]["lsf"] > s1[5]["lsf"])
check("空局集也算得出（全补位）",
      core.lsf_sensitivity([], anchors=ANCHORS)[5]["lsf"] >= 0)
check("自定义窗口", set(core.lsf_sensitivity(gs, anchors=ANCHORS, windows=(1, 3))) == {1, 3})
check("非法窗口剔掉", core.lsf_sensitivity(gs, anchors=ANCHORS, windows=(0, -2)) == {})

nf = [game("i", 40 + k, 12.0 + k * 0.7, k, nf=True) for k in range(30)]
sn = core.lsf_sensitivity(nf, anchors=ANCHORS)
check("NF 局进 NF 窗口", sn[1]["nf_levels"]["i"]["soft"] > 0)

tops5 = core.lsf_top5(gs)
tops25 = core.lsf_top5(gs, top_n=25)
check("lsf_top5 默认仍是前五", all(len(v) <= 5 for v in tops5.values()))
check("top_n=25 取到 25 局",
      max(len(v) for v in tops25.values()) == 25)
check("放大窗口不改默认口径",
      core.lsf_means(gs) == {k: core._lsf_mean(v, k[1], k[2])
                             for k, v in tops5.items()})

# ---------------------------------------------------------------- PB 保持天数
TODAY = date(2026, 2, 1)          # ts(0) = 2026-01-01

check("没有局 -> None", core.pb_streak([], "i", 40) is None)

r = core.pb_streak([game("i", 40, 12.0, 0)], "i", 40, today=TODAY)
check("首条纪录：gap=None", r["gap"] is None)
check("首条纪录：保持 31 天", r["held"] == 31 and r["set_date"] == "2026-01-01")

gs2 = [game("i", 40, 15.0, 0), game("i", 40, 12.0, 10), game("i", 40, 14.0, 20)]
r = core.pb_streak(gs2, "i", 40, today=TODAY)
check("破纪录后：保持从创纪录日算", r["held"] == 21)
check("gap = 距上一条 PB 的天数", r["gap"] == 10)
check("之后的慢局不影响", r["best"] == 12.0)

gs3 = [game("i", 40, 15.0, 0), game("i", 40, 13.0, 5), game("i", 40, 12.0, 10)]
r = core.pb_streak(gs3, "i", 40, today=TODAY)
check("上一条 PB 取更早那次的创造日（不是任意旧局）", r["gap"] == 5)

gs4 = [game("i", 40, 12.0, 3), game("i", 40, 12.0, 8)]
r = core.pb_streak(gs4, "i", 40, today=TODAY)
check("同成绩并列：创纪录者取最早那局", r["set_date"] == "2026-01-04")

gs5 = [game("i", 40, 12.0, 0, nf=False), game("i", 40, 11.0, 5, nf=True)]
check("nf_only 只看 NF 局",
      core.pb_streak(gs5, "i", 40, nf_only=True, today=TODAY)["best"] == 11.0)
check("全PB 口径两条都算",
      core.pb_streak(gs5, "i", 40, today=TODAY)["best"] == 11.0)

g6 = game("i", 40, 12.0, 0)
del g6["t0"]
r = core.pb_streak([g6], "i", 40, today=TODAY)
check("缺 t0 退回 mtime", r["set_date"] == "2026-01-01")

check("别的 BV / 级别不串",
      core.pb_streak([game("i", 40, 12.0, 0)], "i", 41) is None
      and core.pb_streak([game("i", 40, 12.0, 0)], "b", 40) is None)

bad = [c for c in checks if not c[1]]
print("\n%d 项检查，%d 项失败" % (len(checks), len(bad)))
for label, _ok in bad:
    print("  FAIL", label)
raise SystemExit(1 if bad else 0)
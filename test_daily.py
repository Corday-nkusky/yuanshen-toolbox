"""每日页的数据层回归：录像内时间戳的读法 + 按天聚合的口径。

不起窗口，全部是 core 的纯函数，外加两个真实录像样本（文件不在就跳过）。
界面本身在 test_smoke.py 里查。退出码 0 全过，1 有失败。

最要紧的是第 2、3 条：.avf 的时间戳读法一改错就是整整 +8 小时的静默偏移，
界面上看不出来，只有日期断言能守住。
"""
import calendar
import os
import sys
from datetime import datetime

import core

HERE = os.path.dirname(os.path.abspath(__file__))
REPLAY = os.path.join(os.environ.get("APPDATA", ""), "Metasweeper3.3.4", "replay")
AVF = os.path.join("D:\\", "扫雷", "#Exp_4.73_3BV=191_3BVs=4.01_Anonymous.avf")
EVF = os.path.join(REPLAY, "i_0_38.353_66_1.721_Corday(Tongcheng).evf")

checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


def game(t0, t, bv, level="b", nf=True, path=None, stnb=None):
    return {"t0": t0, "t1": t0 + t, "time": t, "bv": bv, "bvs": bv / t,
            "stnb": core.stnb_of(bv, t, level) if stnb is None else stnb,
            "level": level, "nf": nf, "path": path or "rec-%d" % t0,
            "date": core.day_text(t0)}


def test_wall_epoch():
    """合成样本，不依赖时区：墙钟写成 UTC，读回来必须还是那个墙钟。"""
    wall = datetime(2024, 8, 27, 17, 33, 18)
    want = wall.timestamp()                      # 本地时区下的同一时刻
    us = calendar.timegm(wall.timetuple()) * 1000000   # 墙钟被当成 UTC 编码
    check("avf 的墙钟读回来不偏移（%s）" % wall.strftime("%H:%M:%S"),
          core.clock_text(core._wall_epoch(us, "arb")) == "17:33:18"
          and abs(core._wall_epoch(us, "arb") - want) < 1)
    check("evf 的 UTC 时间戳读回来是本地墙钟",
          core.clock_text(core._wall_epoch(int(want * 1e6), "ms")) == "17:33:18")
    check("坏值一律 None（0 / None / 非数字）",
          [core._wall_epoch(v, "arb") for v in (0, None, "", "x")] == [None] * 4)


def test_samples():
    """真实样本：日期写死成字面量。这是防「+8 小时」回归的那颗钉子。"""
    if core.MSLIB is None:
        print("跳过：本机没装 ms-toollib，录像读不出来（pip install ms-toollib）")
        return
    if os.path.isfile(AVF):
        v = core.read_video(AVF)
        got = core.day_text(v["t0"]) if v else ""
        check("阿比特样本归日 = 2024-08-27（不是 08-28）", got == "2024-08-27")
        check("阿比特样本的结束时刻晚于开始",
              bool(v) and v["t1"] > v["t0"])
    else:
        print("跳过：没有阿比特样本 %s" % AVF)
    if os.path.isfile(EVF):
        v = core.read_video(EVF)
        got = core.day_text(v["t0"]) if v else ""
        check("元扫雷样本归日 = 2026-11-12", got == "2026-11-12")
    else:
        print("跳过：没有元扫雷样本 %s" % EVF)


def test_daily_rows():
    d1 = datetime(2026, 11, 15, 9, 0, 0).timestamp()
    d2 = datetime(2026, 11, 16, 23, 50, 0).timestamp()
    d3 = datetime(2026, 11, 17, 0, 30, 0).timestamp()      # 跨午夜：算 16 号
    games = [
        game(d1, 10, 20), game(d1 + 3600, 20, 40),
        game(d1 + 7200, 30, 60, nf=False),
        game(d2, 12, 24), game(d3, 8, 16),
        game(d1, 5, 10, level="e"),                        # 别的级别，不能混进来
    ]
    rows = core.daily_rows(games, "b")
    check("只留指定级别（3 天）",
          [r["date"] for r in rows] == ["2026-11-17", "2026-11-16", "2026-11-15"])
    day = rows[2]
    check("一天一行、局数对（3 局）", day["games"] == 3)
    check("起 = 最早一局的开始 / 止 = 最晚一局的结束",
          core.clock_text(day["t_from"]) == "09:00:00"
          and core.clock_text(day["t_till"]) == "11:00:30")
    check("平均是算术平均（10/20/30 → 20.000）", abs(day["avg"] - 20.0) < 1e-9)
    check("最快 / 最慢（10 / 30）", day["best"] == 10 and day["worst"] == 30)
    check("3BV/s 的最高 / 平均 / 最低（2/2/2）",
          abs(day["bvs_max"] - 2.0) < 1e-9 and abs(day["bvs_min"] - 2.0) < 1e-9
          and abs(day["bvs_avg"] - 2.0) < 1e-9)
    # stnb = C*bv/time^1.7，bv 越大、time 越小越高 → 三项必须与 time 反向
    want = [core.stnb_of(20, 10, "b"), core.stnb_of(40, 20, "b"),
            core.stnb_of(60, 30, "b")]
    check("stnb 的最高 / 平均 / 最低（越大越好，与 time 反向）",
          abs(day["stnb_max"] - max(want)) < 1e-9
          and abs(day["stnb_min"] - min(want)) < 1e-9
          and abs(day["stnb_avg"] - sum(want) / 3) < 1e-9)
    check("NF 局数只数 rce=0 的（2 局）", day["nf"] == 2)
    check("FL 局数是 NF 的对立面（1 局），两者互斥", day["fl"] == 1)
    check("判不出模式的局单独计数（这里没有）", day["style_unknown"] == 0)
    check("nf + fl 不一定等于总局数（没判出来的两边都不算）",
          core.daily_rows([game(d1, 10, 20, nf=None)], "b")[0]["style_unknown"] == 1
          and core.daily_rows([game(d1, 10, 20, nf=None)], "b")[0]["nf"] == 0
          and core.daily_rows([game(d1, 10, 20, nf=None)], "b")[0]["fl"] == 0)
    check("最快那局的路径能取到（给「播放最快」用）",
          day["best_path"].startswith("rec-"))
    check("跨午夜的局归开始那天，不会拆成两天",
          rows[0]["games"] == 1 and rows[1]["games"] == 1
          and core.clock_text(rows[0]["t_from"]) == "00:30:00")
    check("空级别返回空表", core.daily_rows(games, "i") == [])

    detail = core.daily_games(games, "2026-11-15", "b")
    check("明细按时刻倒序（3 局）",
          [r["clock"] for r in detail] == ["11:00:00", "10:00:00", "09:00:00"])
    check("明细条数与每日行对得上",
          len(detail) == rows[2]["games"])
    check("明细只收这一天、这个级别", core.daily_games(games, "2026-11-16", "e") == [])

    # 聚合不改原记录：同一批 games 连算两次结果一致
    again = core.daily_rows(games, "b")
    check("聚合是纯函数（两次结果相同）",
          [r["avg"] for r in again] == [r["avg"] for r in rows])


def main():
    test_wall_epoch()
    test_samples()
    test_daily_rows()
    bad = sum(1 for _l, ok in checks if not ok)
    print("== %s（%d 项）==" % ("全部通过" if not bad else "%d 项失败" % bad,
                               len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

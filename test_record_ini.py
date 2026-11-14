"""record.ini 对账测试：落后时不该把「刚破 PB」报成数值冲突。

元扫雷只在退出时写 record.ini，所以它开着时 ini 必然比最新录像旧。
纯逻辑测试，不读真实 record.ini、不建窗口。退出码：全过 0，否则 1。

用法：python test_record_ini.py
"""
import sys

import core

checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


def diff(mine, ini, level="b", bv=100):
    return {"level": level, "bv": bv, "mine": mine, "ini": ini}


def main():
    # --- 1. 落后判定 ---
    t = 1_700_000_000.0                       # ini 写入时刻（秒）
    check("录像比 ini 新 → 落后",
          core.record_ini_stale(t, int((t + 60) * 1e9)))
    check("录像都比 ini 旧 → 不落后",
          not core.record_ini_stale(t, int((t - 60) * 1e9)))
    check("没有 ini（mtime=0）→ 不落后",
          not core.record_ini_stale(0, int((t + 60) * 1e9)))
    check("没有元扫雷录像（newest=0）→ 不落后",
          not core.record_ini_stale(t, 0))
    check("同一时刻不算落后",
          not core.record_ini_stale(t, int(t * 1e9)))

    # --- 2. 未落后：任何数值不一致都算冲突（老行为不能变）---
    diffs = [diff(10.0, 12.0), diff(20.0, 18.0)]
    j = core.judge_record_diffs(diffs, 5, stale=False)
    check("未落后时 2 条都算冲突：%r" % j, j["conflict"] == 2 and j["faster"] == 0)

    # --- 3. 落后：本地更快是必然，本地更慢才是异常 ---
    diffs = [diff(10.0, 12.0),      # 本地更快（刚破 PB）→ 正常
             diff(20.0, 18.0),      # 本地更慢 → 异常
             diff(30.0, None),      # 只有录像（新 BV）→ 正常
             diff(None, 40.0)]      # 只有 ini（录像不在了）→ 异常
    j = core.judge_record_diffs(diffs, 6, stale=True)
    check("落后时本地更快被归为正常：faster=%d" % j["faster"], j["faster"] == 1)
    check("落后时只剩 1 条真冲突：%d" % j["conflict"], j["conflict"] == 1)
    check("仅 ini 有仍照报：%d" % j["only_ini"], j["only_ini"] == 1)
    check("仅录像有不报（但计数保留）：%d" % j["only_mine"], j["only_mine"] == 1)
    check("一致数 = 总键 - 仅录像 - 冲突项：%d" % j["same"],
          j["same"] == 6 - 1 - 2)

    # --- 4. 落后但没有真异常：应该一条都不报 ---
    j = core.judge_record_diffs([diff(10.0, 12.0), diff(15.0, None)], 3, stale=True)
    check("落后且只有正常差异 → 无警报项",
          j["conflict"] == 0 and j["only_ini"] == 0)

    # --- 5. cross_check 本身：相等的不进 diff ---
    pb = [{"level": "b", "bv": 100, "time": 10.0},
          {"level": "b", "bv": 101, "time": 20.0}]
    ini = {("b", 100): 10.0, ("b", 102): 30.0}
    d = core.cross_check(pb, ini)
    keys = {(x["level"], x["bv"]) for x in d}
    check("相等项不进 diff", ("b", 100) not in keys)
    check("导出两条差异（新 BV / 仅 ini）", keys == {("b", 101), ("b", 102)})


try:
    main()
finally:
    bad = [c for c in checks if not c[1]]
    print("\n== %d/%d 通过 ==" % (len(checks) - len(bad), len(checks)))
    sys.exit(1 if bad else 0)

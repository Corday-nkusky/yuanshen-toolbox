"""自动上传判定回归。

跑法：python test_autoup.py —— 全部通过退出码 0，否则 1。

覆盖：三个开关与范围的互转、all 范围白名单与归一化、网站数据未就绪时拒绝判定、
      全量范围（独立理由 / 不依赖排名数据 / 混合输入 / 未知范围忽略）、
      新局识别（基线 / 水位 / 两次一致 / 文件还在写 / 已处理不再提）、
      PB 榜（更快 / 更慢 / 首次 / NF 独立）、前端榜（第五名阈值 / 同值 / 前五之外）、
      预检（不合法 / 标识不符 / 标识未读到）、站点去重键。
      模式与完成状态的扫描口径由 test_mode.py 另经 core.scan() 验证。
"""

import os
import tempfile

import autoup
import core
import openms

checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


def game(level, bv, ms, style="fl", path=None, size=7300, mtime_ns=1_700_000_000_000_000_000):
    sec = ms / 1000.0
    return {
        "level": level, "bv": bv, "time": sec, "timems": ms,
        "bvs": bv / sec, "stnb": core.stnb_of(bv, sec, level),
        "date": "2026-11-14",
        "file": "%s_%d_%.3f_%s.evf" % (level, bv, sec, style),
        "path": path or "C:/replay/%s_%d_%d_%s.evf" % (level, bv, ms, style),
        "style": style, "nf": style == "nf",
        "rce": 0 if style == "nf" else 3,
        "size": size, "mtime_ns": mtime_ns, "valid": 0, "ident": "科代(安徽)",
    }


def site_pb(level, bv, ms, nf=False, vid=1):
    return {"level": level, "bv": bv, "timems": ms, "nf": nf,
            "video_id": vid, "rank": 1}


def site_top(field, values):
    """field 形如 'std:bt'；values 是榜上第 1..5 名的值（毫秒或 bvs/iqg）。"""
    return {field: {"videos": [{"video_id": 100 + i, "value": v}
                               for i, v in enumerate(values)]}}


# ---------------------------------------------------------------- 开关 / 范围
check("三个开关 -> 范围：涨分含 std 与 NF",
      autoup.scopes_of({"rank": True}) == ("top", "top_nf"))
check("三个开关 -> 范围：涨分 + 全PB",
      autoup.scopes_of({"rank": True, "pb": True}) == ("top", "top_nf", "pb"))
check("三个开关 -> 范围：三个全勾",
      autoup.scopes_of({"rank": True, "pb": True, "pb_nf": True})
      == autoup.AUTO_SCOPES)
check("三个开关 -> 范围：一个都不勾 = 空",
      autoup.scopes_of({}) == ())
check("范围 -> 开关：来回一致",
      autoup.flags_of(autoup.scopes_of({"rank": True, "pb_nf": True}))
      == {"rank": True, "pb": False, "pb_nf": True})
check("范围不含「全部完成局」", "all" not in autoup.AUTO_SCOPES)

# ---------------------------------------------------------------- 数据就绪
check("涨分局缺网站 Top5 -> 拒绝判定",
      autoup.ready(("top",), site_pb=[], site_top={}) is False)
check("涨分局有网站 Top5 -> 可以判定",
      autoup.ready(("top",), site_top=site_top("std:bt", [5000, 5100, 5200, 5300, 5400])))
check("只传全PB 缺网站 PB -> 拒绝判定",
      autoup.ready(("pb",), site_pb=[], site_top={}) is False)
check("只传全PB 有网站 PB -> 可以判定",
      autoup.ready(("pb",), site_pb=[site_pb("i", 34, 5310)]))
check("全量不需要网站 PB / Top5",
      autoup.ready((autoup.ALL_SCOPE,), site_pb=[], site_top={}) is True)
check("全量与涨分混合时仍不受网站数据阻塞",
      autoup.ready((autoup.ALL_SCOPE, "top"), site_pb=[], site_top={}) is True)

# ---------------------------------------------------------------- 新局识别
g_old = game("i", 34, 5500)
g_new = game("i", 34, 5203)

t = autoup.Tracker(settle_s=1.5)
t.prime([g_old])
check("基线建立后不做候选", t.observe([g_old], now=0.0) == [])
check("第一次见到新局只记下，不放行", t.observe([g_old, g_new], now=1.0) == [])
check("第二次看到同样的 (size, mtime) 才放行",
      t.observe([g_old, g_new], now=3.0) == [g_new["path"]])
check("未 commit 前每拍都会再放行（幂等）",
      t.observe([g_old, g_new], now=4.0) == [g_new["path"]])
t.commit([g_new["path"]])
check("commit 之后不再算新局", t.observe([g_old, g_new], now=6.0) == [])
check("commit 后候选池清空", t.pending() == [])


t2 = autoup.Tracker(settle_s=1.5)
t2.prime([g_old])
writing = dict(g_new)
t2.observe([g_old, writing], now=0.0)
writing2 = dict(g_new, size=4096, mtime_ns=writing["mtime_ns"] + 1)
check("文件还在写（大小/时间变了）不放行",
      t2.observe([g_old, writing2], now=5.0) == [])
check("写完之后重新计时，再一致才放行",
      t2.observe([g_old, writing2], now=7.0) == [g_new["path"]])

t3 = autoup.Tracker(watermark=0)
t3.prime([g_old, g_new])
check("水位为 0（首次启用）时历史成绩全部算旧局",
      t3.observe([g_old, g_new], now=10.0) == [])

t4 = autoup.Tracker(watermark=g_old["mtime_ns"])
fresh = dict(g_new, mtime_ns=g_old["mtime_ns"] + 60_000_000_000)
t4.prime([g_old, fresh], now=0.0)
check("水位把「关机期间打的局」放进候选池",
      t4.observe([g_old, fresh], now=2.0) == [fresh["path"]])
check("水位之上的历史局不会被补传", t4.observe([g_old], now=3.0) == [])

# 程序刚起来、还没 prime 就调 observe：一次性把关机期间的局捞出来
t4b = autoup.Tracker(watermark=g_old["mtime_ns"])
check("未 prime 时头一拍就能捞到关机期间打的局",
      t4b.observe([g_old, fresh], now=100.0) == [fresh["path"]])
check("未 prime 时头一拍不会把历史局当新局",
      autoup.Tracker().observe([g_old, fresh], now=100.0) == [])

t5 = autoup.Tracker()
t5.prime([g_old])
t5.observe([g_old, g_new], now=0.0)
t5.observe([g_old, g_new], now=2.0)
check("文件被删掉后不再等它", t5.observe([g_old], now=3.0) == [])
check("水位取见过的最大 mtime",
      t5.newest([g_old, g_new]) == g_new["mtime_ns"])

t6 = autoup.Tracker()
t6.prime([g_old])
t6.observe([g_old, g_new], now=0.0)
t6.observe([g_old, g_new], now=2.0)
t6.commit([g_new["path"]])
t6.requeue(g_new["path"])
check("requeue 之后重新计时，不立刻放行",
      t6.observe([g_old, g_new], now=4.0) == [])
check("requeue 之后稳定了就再次放行",
      t6.observe([g_old, g_new], now=6.0) == [g_new["path"]])

# ---------------------------------------------------------------- PB 榜
slow = game("i", 34, 5400)
fast = game("i", 34, 5203)
spb = [site_pb("i", 34, 5310)]

got = autoup.judge([slow, fast], ("pb",), {fast["path"]}, site_pb=spb)
check("比网站 PB 更快的本地局要传", [x["path"] for x in got] == [fast["path"]])
check("理由写清原记录", "全PB" in got[0]["why"][0]
      and "原 5.310 秒" in got[0]["why"][0])

got = autoup.judge([slow, fast], ("pb",), {slow["path"]}, site_pb=spb)
check("比网站 PB 慢的本地局不传", got == [])

got = autoup.judge([fast], ("pb",), {fast["path"]}, site_pb=[])
check("网站上没有这个 3BV 时标「首次」", "首次" in got[0]["why"][0])

nf_fast = game("i", 34, 5203, style="nf")
got = autoup.judge([nf_fast], ("pb_nf",), {nf_fast["path"]}, site_pb=[])
check("NF PB 收 NF 局", len(got) == 1)
got = autoup.judge([fast], ("pb_nf",), {fast["path"]}, site_pb=[])
check("NF PB 不收 FL 局", got == [])
check("全PB 榜不区分 NF/FL",
      len(autoup.judge([nf_fast], ("pb",), {nf_fast["path"]}, site_pb=[])) == 1)

# ---------------------------------------------------------------- 前端榜
older = [game("i", 30 + i, 5500 + 100 * i) for i in range(5)]
board = site_top("std:it", [5000, 5100, 5200, 5300, 5400])
got = autoup.judge(older + [fast], ("top",), {fast["path"]}, site_top=board)
check("挤进网站前五的局要传", [x["path"] for x in got] == [fast["path"]])
check("理由带「网站第五」", "网站第五 5.400 秒" in " ".join(got[0]["why"]))
check("理由带涨分榜名", any("涨分" in w for w in got[0]["why"]))

# 三个指标都卡死：时间刚好同值、3BV/s 与 stnb 都够不着，才算「同值不涨分」。
# 注意站点榜会按指标重新排序，第五名是排序后的末位（bvs/stnb 降序 → 最小那条），
# 所以这五条都要比本地局高，阈值才会卡在本地局前面。
stnb_i = core.stnb_of(fast["bv"], fast["time"], "i")
board_full = {}
board_full.update(site_top("std:it", [5000, 5100, 5150, 5180, 5203]))
board_full.update(site_top("std:ib", [7.0, 6.9, 6.8, 6.7, 6.6]))
board_full.update(site_top("std:is", [stnb_i * k / core.STNB_CONST["i"]
                                     for k in (1.1, 1.2, 1.3, 1.4, 1.5)]))
got = autoup.judge(older + [fast], ("top",), {fast["path"]}, site_top=board_full)
check("同值不算涨分（站点同分不替换末项）", got == [])

board_short = site_top("std:it", [5000, 5100])
got = autoup.judge(older + [fast], ("top",), {fast["path"]}, site_top=board_short)
check("不满五条时本地成绩可补位", len(got) == 1)
check("补位写「新上榜」", "新上榜" in " ".join(got[0]["why"]))

# 本地前五之外：6 条更快的局
quicker = [game("i", 30 + i, 4000 + 100 * i) for i in range(6)]
got = autoup.judge(quicker + [fast], ("top",), {fast["path"]}, site_top=board_short)
check("进不了本地前五就不传", got == [])

fl = game("i", 34, 5203, style="fl")
got = autoup.judge(older + [fl], ("top_nf",), {fl["path"]}, site_top=board)
check("NF 前端榜不收 FL 局", got == [])

# ---------------------------------------------------------------- 新局 / 预检 / 去重
got = autoup.judge([fast], ("pb",), set(), site_pb=[])
check("不是这次新出现的局不传", got == [])
check("没勾任何范围就不传",
      autoup.judge([fast], (), {fast["path"]}, site_pb=[]) == [])

bad = dict(fast, valid=1)
check("机器判定不合法的局拦下", "冻结" in (autoup.blocked(bad) or ""))
other = dict(fast, ident="别人(北京)")
check("标识不在账号里拦下", "标识" in (autoup.blocked(other, ["科代(安徽)"]) or ""))
check("标识未读到时不做判断", autoup.blocked(other, []) is None)
check("正常局不拦", autoup.blocked(fast, ["科代(安徽)"]) is None)

fd, tmp = tempfile.mkstemp(suffix=".evf")
try:
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"x" * 7300)
    rec = game("i", 34, 5203, path=tmp, size=7300)
    key = openms.game_key(rec)
    check("站点去重键能被识别", key is not None)
    check("传过的局不重复传", autoup.uploaded_before(rec, {key}) is True)
    check("没传过的局不拦", autoup.uploaded_before(rec, set()) is False)
finally:
    os.remove(tmp)

arb = dict(game("i", 40, 4000), path="C:/arb/i_40_4000.evf", src="arb")
ms = game("i", 41, 4100)
check("勾了「含阿比特」时阿比特录像进候选池",
      len(autoup.pool([ms, arb], True)) == 2)
check("不勾时阿比特录像不进候选池",
      [g["path"] for g in autoup.pool([ms, arb], False)] == [ms["path"]])
check("老录像没有 src 字段时按元扫雷算",
      [g["path"] for g in autoup.pool([ms], False)] == [ms["path"]])

# ------------------------------------------------------- 全量判定与独立理由
# 纯逻辑测试直接给 judge() 已过滤的记录；模式 / 完成状态由 test_mode.py
# 另经 core.scan() 验证，不在这里塞一个 mode 字段冒充扫描规则测试。
slow_plain = game("i", 34, 5400)
low_bv = game("b", 3, 1200)          # BV 低于前端 Top5 的官方门槛
quicker = [game("i", 34 + i, 4000 + 100 * i) for i in range(6)]
spb_fast = [site_pb("i", 34, 5000)]
board_fast = site_top("std:it", [4000, 4100, 4200, 4300, 4400])

got = autoup.judge([slow_plain], (autoup.ALL_SCOPE,), {slow_plain["path"]},
                   site_pb=[], site_top={})
check("全量：普通完成局不破纪录也入队",
      [x["path"] for x in got] == [slow_plain["path"]])
check("全量理由固定为「新增完成局」", got and got[0]["why"] == ["新增完成局"])
check("explain 对全量也只给独立理由",
      autoup.explain(slow_plain, [slow_plain], (autoup.ALL_SCOPE,))
      == ["新增完成局"])

check("全量：慢于网站 PB 的局也入队",
      [x["path"] for x in autoup.judge(
          [slow_plain], (autoup.ALL_SCOPE,), {slow_plain["path"]},
          site_pb=spb_fast)] == [slow_plain["path"]])
check("对照：同样这局在 PB 范围里被排除",
      autoup.judge([slow_plain], ("pb",), {slow_plain["path"]},
                   site_pb=spb_fast) == [])
check("全量：低于 Top5 BV 门槛的完成局也入队",
      [x["path"] for x in autoup.judge(
          [low_bv], (autoup.ALL_SCOPE,), {low_bv["path"]})] == [low_bv["path"]])
check("对照：低于门槛的局进不了涨分范围",
      autoup.judge([low_bv], ("top",), {low_bv["path"]},
                   site_top=board_fast) == [])
check("全量：进不了本地前五的局也入队",
      [x["path"] for x in autoup.judge(
          quicker + [slow_plain], (autoup.ALL_SCOPE,),
          {slow_plain["path"]})] == [slow_plain["path"]])
check("对照：同一批里这局进不了涨分范围",
      autoup.judge(quicker + [slow_plain], ("top",), {slow_plain["path"]},
                   site_top=board_fast) == [])

mixed = autoup.judge(quicker + [slow_plain],
                     ("all", "top", "pb", "top_nf", "pb_nf"),
                     {slow_plain["path"]}, site_pb=spb_fast, site_top=board_fast)
check("全量与 PB/Top5 混合输入不重复产生候选",
      [x["path"] for x in mixed] == [slow_plain["path"]])
check("混合输入仍用全量理由", mixed and mixed[0]["why"] == ["新增完成局"])
check("未知范围输入被忽略，不退化为全量上传",
      autoup.judge([slow_plain], ("wat",), {slow_plain["path"]}) == [])
check("全量范围里只有新局才进候选",
      autoup.judge([slow_plain], (autoup.ALL_SCOPE,), set()) == [])

bad = [c for c in checks if not c[1]]
print("\n%d 项检查，%d 项失败" % (len(checks), len(bad)))
for label, _ok in bad:
    print("  FAIL", label)
raise SystemExit(1 if bad else 0)

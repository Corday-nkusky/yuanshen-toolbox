"""总结图（总结 / 详细总结 / 剪贴板）的回归。

分两层：
  1. 图片：尺寸、白卡片位置、没有字溢到卡片外（按像素查，不看图）；
     详细版还要保证「比总结高、宽不变」，否则手机上又得放大；
  2. 剪贴板：图写一次再读回来，CF_DIB 那条路最容易在 64 位下悄悄截断
     句柄。剪贴板被别的程序占着时跳过，不算失败。
"""
import os
import sys
import tkinter as tk
from datetime import datetime

import core
import summary

checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


def row(games=3, **kw):
    """造一行 daily_rows 的输出（字段与 core 完全一致）。"""
    base = {"date": "2026-11-15", "games": games,
            "t_from": datetime(2026, 11, 15, 21, 2, 11).timestamp(),
            "t_till": datetime(2026, 11, 15, 23, 58, 3).timestamp(),
            "best": 16.741, "avg": 19.482, "worst": 24.120,
            "bvs_max": 3.383, "bvs_avg": 2.981, "bvs_min": 2.412,
            "stnb_max": 35.392, "stnb_avg": 23.392, "stnb_min": 10.908,
            "nf": 1, "fl": 2, "style_unknown": 0, "best_path": "x.avf"}
    base["span"] = base["t_till"] - base["t_from"]
    base.update(kw)
    return base


def test_png():
    img = summary.daily_png(row(), "i", "中级", "0.28.0")
    w, h = img.size
    plain = summary.daily_png(row(), "i", "中级", "0.28.0", with_mascot=False)
    pw, ph = plain.size
    m = summary._Sheet.MARGIN * summary.SCALE
    gap = summary.MASCOT_GAP * summary.SCALE
    card_h = ph - 2 * m
    check("单日图是竖的（宽小于高），不再是一条横带", h > w * 1.15)
    check("加贴纸只变高、不加宽（贴纸在卡片下方，不占右边）",
          w == pw and h > ph + 200)
    check("宽度 = 卡片宽 + 两侧留边",
          w == int((summary.D_W + 2 * summary._Sheet.MARGIN) * summary.SCALE))
    check("左上与左下角是底色（白卡片外留了一圈边）",
          img.getpixel((0, 0)) == _rgb(summary.PAGE_BG)
          and img.getpixel((0, h - 1)) == _rgb(summary.PAGE_BG))
    check("卡片内部是白底",
          img.getpixel((m + 20, m + 20)) == _rgb(summary.CARD_BG))
    check("卡片外沿 3 px 内没有深色像素（左右与顶边）",
          not _ink_near_edge(img, 3))

    # 贴纸：在卡片下方、居中、贴住图片下沿
    y0 = m + card_h + gap
    check("卡片与贴纸之间留了一条页底色的缝（不是糊在一起）",
          _row_is_bg(img, m + card_h + gap // 2, 0, w))
    art, total = _block_stats(img, m, y0, w - m, h - 1)
    # 只查「有内容」和「四角仍是页底」：这一块被角色填掉七成是正常的，
    # 真正要防的是整块变成一个白色方块（白底没被乘掉）。
    check("贴纸区域真的有画（占 20%–90%）", 0.20 < art / float(total) < 0.90)
    check("贴纸底边贴住图片下沿（最后几行有内容）",
          _row_has_ink(img, h - 2, m, w - m))
    check("贴纸左右留白对称（左右边 20 px 内那一行仍是页底）",
          _row_is_bg(img, y0 + 20, 0, m) and _row_is_bg(img, y0 + 20, w - m, w))

    one = summary.levels_png([("e", "高级", row())], "0.28.0")
    three = summary.levels_png([("b", "初级", row(games=7)),
                               ("i", "中级", row(games=9)),
                               ("e", "高级", row())], "0.28.0")
    check("今日图：一个级别也能画", one is not None and one.size[0] > 800)
    # 只有一级时改走单日卡版式（矩阵退化成一行，卡片太矮就成一条长带）
    check("今日图：只有一个级别时套用单日卡版式",
          one.size == summary.daily_png(row(), "e", "高级", "0.28.0").size)
    check("今日图：三个级别也还是竖图（不是横表）",
          three.size[1] > three.size[0])
    two = summary.levels_png([("i", "中级", row(games=9)),
                             ("e", "高级", row())], "0.28.0")
    m2 = summary._Sheet.MARGIN * summary.SCALE
    check("今日图：三个级别比两个级别的卡片高",
          _card_height(three, m2 + 20) > _card_height(two, m2 + 20))
    check("今日图：没有级别时返回 None", summary.levels_png([]) is None)
    check("今日图：图宽与单日图一致（同一套列宽）",
          summary.levels_png([("b", "初级", row(games=7)),
                             ("i", "中级", row(games=9)),
                             ("e", "高级", row())],
                            with_mascot=False).size[0]
          == int((summary.D_W + 2 * summary._Sheet.MARGIN) * summary.SCALE))
    check("图里不含时间戳之外的随机内容（同参数两次尺寸相同）",
          summary.daily_png(row(), "i", "中级", "0.28.0").size
          == summary.daily_png(row(), "i", "中级", "0.28.0").size)


def test_mascot():
    path = os.path.join(os.path.dirname(os.path.abspath(summary.__file__)),
                        summary.MASCOT)
    check("贴纸图随仓库带着（mascot.png）", os.path.isfile(path))
    mk = summary.mascot(300)
    check("按高度取到贴纸，宽高比不变（约 1.44）",
          mk is not None and mk.height == 300
          and 1.35 < mk.width / float(mk.height) < 1.55)
    check("同一高度取第二次走缓存（同一个对象）", summary.mascot(300) is mk)

    # 图丢了不能把整张卡片带崩：只是下面空着、图矮一截
    old, cached = summary.MASCOT, dict(summary._MASCOT_CACHE)
    summary.MASCOT = "no-such-mascot.png"
    summary._MASCOT_CACHE.clear()
    try:
        img = summary.daily_png(row(), "i", "中级", "0.28.0")
        plain = summary.daily_png(row(), "i", "中级", "0.28.0",
                                  with_mascot=False)
        check("贴纸缺失时照常出图，只是没有下面那一段",
              summary.mascot(300) is None
              and img.size[0] == plain.size[0]
              and img.size[1] < plain.size[1] + 100)
    finally:
        summary.MASCOT = old
        summary._MASCOT_CACHE.clear()
        summary._MASCOT_CACHE.update(cached)


def _card_height(img, x):
    """某一列上连续白卡片的高度（用来比较卡片本身有多高）。"""
    px = img.load()
    return sum(1 for y in range(img.height) if px[x, y] == _rgb(summary.CARD_BG))


def _block_stats(img, x0, y0, x1, y1, step=3):
    """统计区域里有多少像素不是页底色（用来判断「有没有画东西」）。"""
    bg = _rgb(summary.PAGE_BG)
    px = img.load()
    art = total = 0
    for x in range(x0, x1, step):
        for y in range(y0, y1, step):
            total += 1
            if px[x, y] != bg:
                art += 1
    return art, total


def _row_has_ink(img, y, x0, x1):
    px = img.load()
    return any(sum(px[x, y]) < 600 for x in range(x0 + 2, x1 - 2))


def _row_is_bg(img, y, x0, x1):
    """这一行（区间内）是不是全是页底色 —— 用来查贴纸左右有没有溢出。"""
    bg = _rgb(summary.PAGE_BG)
    px = img.load()
    return all(px[x, y] == bg for x in range(x0, x1))


def _rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _ink_near_edge(img, margin):
    """卡片边缘 margin 像素以内有没有深色像素（用于查文字溢出）。

    只查左右与顶边：贴纸贴在图片下沿，底边本来就该是画。
    """
    w, h = img.size
    px = img.load()
    bad = [(x, y) for x in range(margin) for y in range(h)
           if sum(px[x, y]) < 500]
    bad += [(x, y) for x in range(w - margin, w) for y in range(h)
            if sum(px[x, y]) < 500]
    bad += [(x, y) for y in range(margin) for x in range(w)
            if sum(px[x, y]) < 500]
    return bad


def test_accent():
    """summary 里的级别配色必须与 app 一致，否则图上的紫不是表格上的紫。"""
    try:
        import app
    except Exception as e:                       # noqa: BLE001
        print("跳过：导入 app 失败（%s）" % e)
        return
    check("级别配色与 app.LEVEL_ACCENT 一致",
          app.LEVEL_ACCENT == summary.LEVEL_ACCENT)


def test_clipboard():
    img = summary.daily_png(row(), "e", "高级", "0.26.0")
    ok = summary.copy_image(img)
    if not ok:
        print("跳过剪贴板图片：写不进去（被别的程序占着？）")
    else:
        try:
            from PIL import ImageGrab
            back = ImageGrab.grabclipboard()
            check("图片进剪贴板再读回来，尺寸一致",
                  back is not None and tuple(back.size) == img.size)
        except Exception as e:                   # noqa: BLE001
            print("跳过剪贴板回读：%s" % e)
def _games(n=12, base=18.0, step=0.8):
    """造 n 局（time / bv / style 三个字段就够详细版画图）。"""
    out = []
    for i in range(n):
        out.append({"time": base + step * i, "bv": 20 + 3 * i,
                    "stnb": 40.0 + 2.5 * (n - i),
                    "style": "nf" if i % 3 else "fl",
                    "t0": 1763200000.0 + i * 60})
    return out


def test_detail():
    k, cn = "e", "高级"
    r = row(games=12)
    games = {k: _games(12)}
    img = summary.levels_detail_png([(k, cn, r)], games, "0.28.0", "2026-11-15")
    plain = summary.levels_png([(k, cn, r)], "0.28.0", "2026-11-15")
    check("详细版能出图", img is not None)
    check("详细版与总结卡同宽（手机上缩放比一致）",
          img.size[0] == plain.size[0] ==
          int((summary.D_W + 2 * summary._Sheet.MARGIN) * summary.SCALE))
    check("详细版比总结卡高（多了状态与散点等块）",
          img.size[1] > plain.size[1] + 300)
    m = summary._Sheet.MARGIN * summary.SCALE
    check("详细版四角仍是页底色", img.getpixel((0, 0)) == _rgb(summary.PAGE_BG)
          and img.getpixel((img.size[0] - 1, img.size[1] - 1))
          == _rgb(summary.PAGE_BG))
    check("详细版卡片边缘没有字溢出去", not _ink_near_edge(img, 3))

    three = summary.levels_detail_png(
        [("b", cn, row(games=5)), (k, cn, row(games=12))],
        {"b": _games(5), k: _games(12)}, "0.28.0", "2026-11-15")
    check("两级详细版比单级高（每级一块）", three.size[1] > img.size[1])

    one = summary.levels_detail_png([(k, cn, row(games=1))],
                                    {k: _games(1)}, "0.28.0", "2026-11-15")
    check("只有一局时照样出图（不画分布、不列两栏极值）",
          one is not None and one.size[1] >= 900
          and one.size[1] < img.size[1])
    check("没有级别时返回 None", summary.levels_detail_png([], {}) is None)


def test_meta_text():
    """小字那一行：只有起止与 FL / NF。

    这里钉的是「不许再算跨度」—— 加过一次「跨度 12 小时 48 分」，被要求删掉后
    又混进详细版。小字归 _meta_text 一家生产，两张卡都不许自己拼。
    """
    t0 = datetime(2026, 11, 15, 21, 2, 11).timestamp()
    t1 = datetime(2026, 11, 15, 23, 58, 3).timestamp()
    line = summary._meta_text(t0, t1, 0, 37)
    check("小字 = 起止 + FL / NF", line == "21:02:11 – 23:58:03 · FL 0 · NF 37")
    check("小字里没有「小时 / 分 / 跨度 / 历时」",
          not any(w in line for w in ("小时", "分", "跨度", "历时")))
    check("FL 为 0 也照写（全 NF 的日子要看得出是 0）", "FL 0" in line)
    check("判不出完成方式时只剩起止",
          summary._meta_text(t0, t1, 0, 0) == "21:02:11 – 23:58:03")
    check("多级别小块里用时分",
          summary._meta_text(t0, t1, 3, 4, brief=True)
          == "21:02 – 23:58 · FL 3 · NF 4")


def test_helpers():
    check("中位数取中间两个的平均", abs(summary._median([1, 2, 3, 10]) - 2.5) < 1e-9)
    check("空列表的中位数是 None（调用方写「—」）", summary._median([]) is None)
    check("中位数不过滤 0（相对平时的 0 是合法值）",
          summary._median([-0.1, 0.0, 0.5]) == 0.0)
    # 逐局列表与节奏必须按时间正序，而 daily_games 给的是倒序
    g = [{"t0": 300.0}, {"t0": 100.0}, {"t0": 200.0}]
    check("走势按开始时刻升序", [x["t0"] for x in summary._by_time(g)]
          == [100.0, 200.0, 300.0])


def _wrap_calls(names, fn):
    """数绘图件被调了几次：版式改了也不会误红（不数像素）。"""
    calls = dict((n, 0) for n in names)
    orig = dict((n, getattr(summary, n)) for n in names)
    for n in names:
        def wrap(*a, _n=n, **kw):
            calls[_n] += 1
            return orig[_n](*a, **kw)
        setattr(summary, n, wrap)
    try:
        fn()
    finally:
        for n in names:
            setattr(summary, n, orig[n])
    return calls


def _seen_texts(fn):
    """收集一张卡上写过的所有字符串（查块在不在、口径红线用）。"""
    seen = []
    orig = summary._Sheet.text

    def rec(self, x, y, s, *a, **kw):
        seen.append(s)
        return orig(self, x, y, s, *a, **kw)
    summary._Sheet.text = rec
    try:
        fn()
    finally:
        summary._Sheet.text = orig
    return seen


def _day_games(n, day="2026-11-15", bv0=100, base=20.0, step=0.5):
    """造 n 局当天局（e 级门槛 100 BV 起）。"""
    t0 = datetime.strptime(day, "%Y-%m-%d").timestamp() + 21 * 3600
    return [{"time": base + step * i, "bv": bv0 + 3 * i, "style": "nf",
             "t0": t0 + i * 60, "level": "e"} for i in range(n)]


def _hist_games(gs, day="2026-11-01", copies=(1.15, 1.25, 1.3)):
    """每个 BV 造几局更慢的历史局：中位线 / PB / 纪录都有得算。"""
    t0 = datetime.strptime(day, "%Y-%m-%d").timestamp() + 21 * 3600
    out = []
    for i, g in enumerate(gs):
        for k, mul in enumerate(copies):
            out.append(dict(g, time=g["time"] * mul,
                            t0=t0 + (i * len(copies) + k) * 60))
    return out


def test_detail_blocks():
    """v4 的块：散点 / 最快榜 / 指标最佳 / 逐局 / 节奏，按局数退化。"""
    names = ("_scatter", "_rows_fast", "_highlights", "_rows_all", "_segments")
    games = _day_games(12)
    calls = _wrap_calls(names, lambda: summary.levels_detail_png(
        [("e", "高级", row(games=12))], {"e": games}, "0.36.0", "2026-11-15"))
    check("12 局无历史：散点 + 逐局列表，没有榜与节奏",
          calls["_scatter"] == 1 and calls["_rows_all"] == 1
          and calls["_rows_fast"] == 0 and calls["_highlights"] == 0
          and calls["_segments"] == 0)

    games = _day_games(16)
    hist = games + _hist_games(games)
    calls = _wrap_calls(names, lambda: summary.levels_detail_png(
        [("e", "高级", row(games=16))], {"e": games}, "0.36.0", "2026-11-15",
        all_games=hist))
    check("16 局有历史：散点 + 最快榜 + 指标最佳 + 节奏，不逐局列",
          calls["_scatter"] == 1 and calls["_rows_fast"] == 1
          and calls["_highlights"] == 1 and calls["_segments"] == 1
          and calls["_rows_all"] == 0)

    games = _day_games(1)
    calls = _wrap_calls(names, lambda: summary.levels_detail_png(
        [("e", "高级", row(games=1))], {"e": games}, "0.36.0", "2026-11-15"))
    check("只有一局：不画散点，只逐局列一次",
          calls["_scatter"] == 0 and calls["_rows_all"] == 1
          and calls["_rows_fast"] == 0 and calls["_highlights"] == 0)


def test_detail_v4():
    """v4 的口径：三指标、扫雷日窗口、门槛、往日不偷看未来、红线。"""
    pool = [{"bv": 100, "time": 10.0, "stnb": 50.0},
            {"bv": 120, "time": 15.0, "stnb": 40.0}]
    check("_best：time 取最小，bvs / stnb 取最大",
          summary._best(pool, "time") == 10.0
          and summary._best(pool, "bvs") == 10.0
          and summary._best(pool, "stnb") == 50.0)
    check("_best 空池返回 None（调用方写「—」）", summary._best([], "time") is None)
    check("_mean 跳过 None、空列表返回 None",
          summary._mean([1.0, None, 3.0]) == 2.0 and summary._mean([]) is None)
    check("_metric：bvs = BV ÷ 用时", summary._metric(pool[0], "bvs") == 10.0)
    gs = [{"bv": 100, "time": 10.0 + d,
           "t0": datetime(2026, 11, 1 + d, 21, 0, 0).timestamp()}
          for d in range(9)]
    w = summary._play_window(gs)
    check("窗口只取最近 7 个扫雷日（第 8、9 天不进）",
          len(w) == 7 and min(core.day_text(g["t0"]) for g in w) == "2026-11-03")
    check("中位线按 BV 取中位", summary._med_curve(
        [{"bv": 100, "time": t} for t in (10.0, 12.0, 14.0)])[100] == 12.0)
    # 纪录门槛：该 BV 累计不足 3 局不算纪录（第一次打这个 BV 不算破纪录）
    today = [{"bv": 100, "time": 9.0}]
    past2 = [{"bv": 100, "time": 10.0}]
    hist2 = today + past2
    hist3 = hist2 + [{"bv": 100, "time": 11.0}]
    check("BV 累计不足 3 局不算纪录",
          not summary._record_bvs(today, summary._pb_curve(past2), hist2))
    check("BV 累计 ≥3 局且今天最快才算纪录",
          summary._record_bvs(today, summary._pb_curve(past2), hist3)
          == {id(today[0])})
    same_bv = [{"bv": 100, "time": t} for t in (9.0, 10.0, 11.0)]
    past = [{"bv": 100, "time": t} for t in (12.0, 13.0)]
    check("同 BV 多局只认当天最快的那局，不认整组 BV",
          summary._record_bvs(same_bv, summary._pb_curve(past), same_bv + past)
          == {id(same_bv[0])})
    tie = [{"bv": 100, "time": 10.0}]
    check("平历史最快不算刷新纪录",
          not summary._record_bvs(tie, summary._pb_curve(past2),
                                  tie + past2 + past2))
    first = [{"bv": 100, "time": 9.0 + i} for i in range(3)]
    check("首日打这个 BV 满 3 局也不算纪录（没有历史可破）",
          not summary._record_bvs(first, {}, first))
    check("中位局保留 0（正好等于平时）",
          summary._typical([{"bv": 100, "time": t}
                            for t in (9.0, 10.0, 15.0)], {100: 10.0}) == 0.0)

    # 退化边界：2/7 逐局、8/14 散点无节奏、15 全块
    for n, want_scatter, want_rhythm in ((2, False, False), (7, False, False),
                                         (8, True, False), (14, True, False),
                                         (15, True, True)):
        gs = _day_games(n)
        seen = _seen_texts(lambda: summary.levels_detail_png(
            [("e", "高级", row(games=n))], {"e": gs}, "0.36.0", "2026-11-15"))
        check("%d 局：散点%s、节奏%s" % (n, "出" if want_scatter else "不出",
                                        "出" if want_rhythm else "不出"),
              ("BV × 用时" in seen) == want_scatter
              and ("节奏" in seen) == want_rhythm)
        check("%d 局：hero 位一直在" % n, "今天最好一局" in seen)

    # 无历史 vs 有历史：状态表与摘要差的有无
    gs = _day_games(12)
    seen = _seen_texts(lambda: summary.levels_detail_png(
        [("e", "高级", row(games=12))], {"e": gs}, "0.36.0", "2026-11-15"))
    check("不传全量历史：不出状态表，也不炸", "近 7 扫雷日" not in seen)
    hist = gs + _hist_games(gs)
    seen = _seen_texts(lambda: summary.levels_detail_png(
        [("e", "高级", row(games=12))], {"e": gs}, "0.36.0", "2026-11-15",
        all_games=hist))
    check("有历史：三指标表与中位局摘要差都出",
          "近 7 扫雷日" in seen and "3BV/s" in seen and "stnb" in seen
          and any("中位局比近 7 扫雷日" in s for s in seen))
    check("有纪录时散点图例带空心环说明",
          any("○ 新纪录" in s for s in seen))
    check("小字红线：整卡没有「跨度 / 小时 / 分 / 历时」",
          not any(w in s for s in seen for w in ("跨度", "小时", "分", "历时")))

    # 慢榜已删：16 局出指标最佳，不再列「离自己 PB 最远」
    gs = _day_games(16)
    seen = _seen_texts(lambda: summary.levels_detail_png(
        [("e", "高级", row(games=16))], {"e": gs}, "0.36.0", "2026-11-15",
        all_games=gs + _hist_games(gs)))
    check("16 局：bvs / stnb 今日最佳在，慢榜不在",
          "bvs 最佳" in seen and "stnb 最佳" in seen
          and not any("离自己 PB 最远" in s for s in seen)
          and not hasattr(summary, "_rows_slow"))

    # 指标 PB：三个指标各算各的，摘要行报刷新了哪几个
    today = [dict(_day_games(1, bv0=150, base=55.0)[0], stnb=60.0)]
    hist = today + [dict(today[0], time=60.0, stnb=50.0,
                         t0=today[0]["t0"] - 86400 * 14)]
    seen = _seen_texts(lambda: summary.levels_detail_png(
        [("e", "高级", row(games=1))], {"e": today}, "0.36.0", "2026-11-15",
        all_games=hist))
    check("三指标 PB 各算各的（time / bvs / stnb 都刷新就都报）",
          "刷新全期 PB：time / bvs / stnb" in seen)

    # 往日的卡不偷看未来：明天更快的局不许进 PB 徽章 / 窗口
    future = dict(today[0], time=50.0, t0=today[0]["t0"] + 86400)
    seen = _seen_texts(lambda: summary.levels_detail_png(
        [("e", "高级", row(games=1))], {"e": today}, "0.36.0", "2026-11-15",
        all_games=hist + [future]))
    check("往日卡 PB 徽章用今天之前的原 PB（60.000，不是未来的 50.000）",
          "新级别 PB · 原 60.000" in seen
          and not any("50.000" in s for s in seen))

    # 级别 PB 门槛：4 BV 的 0.8 s 不许当参照
    today = _day_games(1, bv0=150, base=55.0)
    hist = today + [dict(today[0], bv=4, time=0.8,
                         t0=today[0]["t0"] - 86400 * 14),
                    dict(today[0], time=60.0,
                         t0=today[0]["t0"] - 86400 * 14)]
    seen = _seen_texts(lambda: summary.levels_detail_png(
        [("e", "高级", row(games=1))], {"e": today}, "0.36.0", "2026-11-15",
        all_games=hist))
    check("新级别 PB 徽章用 ≥门槛的原 PB（60.000，不是 0.800）",
          "新级别 PB · 原 60.000" in seen and "0.800" not in seen)
    today = _day_games(1, bv0=50, base=20.0)
    hist = today + [dict(today[0], bv=150, time=60.0,
                         t0=today[0]["t0"] - 86400 * 14)]
    seen = _seen_texts(lambda: summary.levels_detail_png(
        [("e", "高级", row(games=1))], {"e": today}, "0.36.0", "2026-11-15",
        all_games=hist))
    check("最好一局低于门槛时只标 PB 与门槛，不报跨盘面差",
          "级别 PB 60.000（≥100 BV）" in seen)

    # 节奏段间比中位（不是平均）：一段里 [-10%, -10%, +90%] 应报快 10%
    sh = summary._Sheet(summary.D_W, with_mascot=False)
    gs = []
    t0 = datetime(2026, 11, 15, 21, 0, 0).timestamp()
    ratios = [-0.1, -0.1, 0.9] + [0.0] * 9
    for i, d in enumerate(ratios):
        gs.append({"bv": 100, "time": 10.0 * (1 + d), "t0": t0 + i * 60})
    seen = _seen_texts(lambda: summary._segments(
        sh, 30, 0, 480, gs, "#8f6ce8", {100: 10.0}))
    check("节奏段内取中位（离群局不带偏整段）",
          "比平时快 10%" in seen and "比平时慢 23%" not in seen)

    # 有历史的整卡：宽度不变、字不溢边
    gs = _day_games(16)
    img = summary.levels_detail_png(
        [("e", "高级", row(games=16))], {"e": gs}, "0.36.0", "2026-11-15",
        all_games=gs + _hist_games(gs), with_mascot=False)
    check("v4 整卡与总结卡同宽",
          img.size[0] == int((summary.D_W + 2 * summary._Sheet.MARGIN)
                             * summary.SCALE))
    check("v4 整卡边缘没有字溢出去", not _ink_near_edge(img, 3))


def test_pb_line():
    """散点上的 PB 折线：只含今天之前的局，新纪录点落在线下方。"""
    def scatter_args(fn):
        got = {}
        orig = summary._scatter

        def wrap(*a, **kw):
            got["a"] = a
            return orig(*a, **kw)
        summary._scatter = wrap
        try:
            fn()
        finally:
            summary._scatter = orig
        return got.get("a")

    gs = _day_games(16)
    hist = gs + _hist_games(gs)
    seen = []
    orig_text = summary._Sheet.text

    def rec(self, x, y, s, *a, **kw):
        seen.append(s)
        return orig_text(self, x, y, s, *a, **kw)
    summary._Sheet.text = rec
    try:
        args = scatter_args(lambda: summary.levels_detail_png(
            [("e", "高级", row(games=16))], {"e": gs}, "0.36.0", "2026-11-15",
            all_games=hist))
    finally:
        summary._Sheet.text = orig_text
    pb_ref, recs = args[9], args[8]
    legend = next(s for s in seen if s.startswith("● 今天"))
    check("有历史：图例写「PB 线」（不写「今天之前」）",
          "━ PB 线" in legend and "今天之前" not in legend)
    check("PB 线逐 BV 等于今天之前的全期最快",
          pb_ref and all(
              abs(pb_ref[int(g["bv"])][0] - g["time"] * 1.15) < 1e-9
              for g in gs))
    check("新纪录点都在 PB 线下方（破墙看得见）",
          recs and all(pb_ref[int(g["bv"])][0] > g["time"]
                       for g in gs if id(g) in recs))
    check("脚注写明 PB 线口径", any("PB 线 = 今天之前" in s for s in seen))

    # 同一天同 BV 的普通局不能跟着破纪录局一起画成空心环。
    same_bv = [{"bv": 100, "time": t} for t in (9.0, 10.0, 11.0)]
    past = [{"bv": 100, "time": t} for t in (12.0, 13.0)]
    recs = summary._record_bvs(same_bv, summary._pb_curve(past), same_bv + past)
    dots = []

    class ScatterSheet:
        def line(self, *a, **kw):
            pass

        def text(self, *a, **kw):
            pass

        def dot(self, *a):
            dots.append(a)

    summary._scatter(ScatterSheet(), 0, 0, 300, 100, same_bv, {},
                     "#8f6ce8", recs, summary._pb_curve(past))
    rings = [d for d in dots if d[2] == 3.4]
    check("同 BV 多局只有真正破纪录的一局画空心环",
          len(rings) == 1 and rings[0][1] == max(d[1] for d in dots))

    seen = _seen_texts(lambda: summary.levels_detail_png(
        [("e", "高级", row(games=16))], {"e": gs}, "0.36.0", "2026-11-15"))
    args = scatter_args(lambda: summary.levels_detail_png(
        [("e", "高级", row(games=16))], {"e": gs}, "0.36.0", "2026-11-15"))
    legend = next(s for s in seen if s.startswith("● 今天"))
    check("无历史：不画 PB 线，图例也不提",
          not args[9] and "PB 线" not in legend)

    future = dict(gs[0], time=1.0, t0=gs[0]["t0"] + 86400 * 2)
    args = scatter_args(lambda: summary.levels_detail_png(
        [("e", "高级", row(games=16))], {"e": gs}, "0.36.0", "2026-11-15",
        all_games=hist + [future]))
    check("未来的更快局不进 PB 线",
          abs(args[9][int(gs[0]["bv"])][0] - gs[0]["time"] * 1.15) < 1e-9)


def test_clipboard_free():
    """剪贴板失败路径必须 GlobalFree，否则一次漏整张 DIB。"""
    import ctypes
    from types import SimpleNamespace
    img = summary.daily_png(row(), "e", "高级", "t")
    real_windll, real_memmove = ctypes.WinDLL, ctypes.memmove
    for lock_ok, set_ok, want_free in ((False, True, 1), (True, False, 1),
                                       (True, True, 0)):
        calls = []

        def fn(tag, ret):
            def f(*a):
                calls.append(tag)
                return ret
            return f
        k = SimpleNamespace(GlobalAlloc=fn("alloc", 7),
                            GlobalLock=fn("lock", 1 if lock_ok else 0),
                            GlobalUnlock=fn("unlock", 1),
                            GlobalFree=fn("free", 1))
        u = SimpleNamespace(OpenClipboard=fn("open", 1),
                            EmptyClipboard=fn("empty", 1),
                            SetClipboardData=fn("set", 1 if set_ok else 0),
                            CloseClipboard=fn("close", 1))
        ctypes.WinDLL = lambda name, use_last_error=False: (
            k if name == "kernel32" else u)
        ctypes.memmove = fn("memmove", 0)
        try:
            ok = summary.copy_image(img)
        finally:
            ctypes.WinDLL, ctypes.memmove = real_windll, real_memmove
        check("lock=%s set=%s：返回%s且 GlobalFree %d 次"
              % (lock_ok, set_ok, lock_ok and set_ok, want_free),
              ok == (lock_ok and set_ok) and calls.count("free") == want_free)


def test_sheet_off():
    """排版坐标相对卡片左上角：x 与 y 一样要加左边距，不能整卡左移。"""
    sh = summary._Sheet(summary.D_W, with_mascot=False)
    sh.rect(0, 0, 10, 10, (255, 0, 0))
    img = sh.render(40)
    m = summary._px(summary._Sheet.MARGIN)
    from PIL import ImageColor
    check("卡片原点落在左边距上（x 不漏 off）",
          img.getpixel((m + 2, m + 2)) == (255, 0, 0)
          and img.getpixel((2, m + 2)) == ImageColor.getrgb(summary.PAGE_BG))


def test_none_hooks():
    check("copy_image(None) 返回 False，不炸", summary.copy_image(None) is False)


def main():
    test_png()
    test_detail()
    test_meta_text()
    test_helpers()
    test_mascot()
    test_accent()
    test_detail_blocks()
    test_detail_v4()
    test_pb_line()
    test_clipboard_free()
    test_sheet_off()
    test_none_hooks()
    test_clipboard()
    bad = sum(1 for _l, ok in checks if not ok)
    print("== %s（%d 项）==" % ("全部通过" if not bad else "%d 项失败" % bad,
                               len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

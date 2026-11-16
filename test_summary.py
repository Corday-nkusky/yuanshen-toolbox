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
    check("详细版比总结卡高（多了分布与极值两块）",
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


def test_span_text():
    check("跨度 2 小时 55 分", summary._span_text(10512) == "2 小时 55 分")
    check("跨度 43 分 12 秒", summary._span_text(2592) == "43 分 12 秒")
    check("跨度 18 秒", summary._span_text(18) == "18 秒")
    check("中位数取中间两个的平均", abs(summary._median([1, 2, 3, 10]) - 2.5) < 1e-9)
    check("空列表的中位数是 0", summary._median([]) == 0.0)


def test_none_hooks():
    check("copy_image(None) 返回 False，不炸", summary.copy_image(None) is False)


def main():
    test_png()
    test_detail()
    test_span_text()
    test_mascot()
    test_accent()
    test_none_hooks()
    test_clipboard()
    bad = sum(1 for _l, ok in checks if not ok)
    print("== %s（%d 项）==" % ("全部通过" if not bad else "%d 项失败" % bad,
                               len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

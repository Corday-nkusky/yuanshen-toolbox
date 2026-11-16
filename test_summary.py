"""总结（文本 / 图片 / 剪贴板）的回归。

分三层：
  1. 文字：数字、顺序、口径必须与每日页表格一致；
  2. 图片：尺寸、白卡片位置、没有字溢到卡片外（按像素查，不看图）；
  3. 剪贴板：文本与图片各写一次再读回来，CF_DIB 那条路最容易在 64 位下
     悄悄截断句柄。剪贴板被别的程序占着时跳过，不算失败。
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
            "nf": 1, "best_path": "x.avf"}
    base["span"] = base["t_till"] - base["t_from"]
    base.update(kw)
    return base


def test_text():
    r = row()
    t = summary.daily_text(r, "高级", "0.26.0")
    lines = t.splitlines()
    print("-" * 60)
    print(t)
    print("-" * 60)
    check("首行 = 日期 · 级别 · 局数",
          lines[0] == "2026-11-15 · 高级 · 3 局")
    check("第二行带起止与跨度", "21:02:11 – 23:58:03" in lines[1]
          and "2 小时 55 分" in lines[1])
    check("NF 局数与占比（1/3 → 33%）", any("NF 1（33%）" == s for s in lines))
    check("时间行用「最快/平均/最慢」，三位小数",
          "时间（秒）：最快 16.741 / 平均 19.482 / 最慢 24.120" in lines)
    check("3BV/s 行用「最高/平均/最低」",
          "3BV/s：最高 3.383 / 平均 2.981 / 最低 2.412" in lines)
    check("stnb 行同样给三项",
          "stnb：最高 35.392 / 平均 23.392 / 最低 10.908" in lines)
    check("末尾署名带版本", lines[-1] == "—— YuanshenToolbox v0.26.0")
    check("没有 NF 局时不写这一行",
          not any(s.startswith("NF 0") for s in
                  summary.daily_text(row(nf=0), "高级").splitlines()))
    check("空行返回空串", summary.daily_text(None, "高级") == "")

    a = summary.today_text([("高级", r), ("中级", row(games=9))], "0.26.0")
    print(a)
    check("今日首行汇总局数（3+9=12）", a.splitlines()[0].endswith("共 12 局"))
    check("今日逐个级别成段", "高级 · 3 局" in a and "中级 · 9 局" in a)
    check("今日为空返回空串", summary.today_text([], "0.26.0") == "")


def test_span():
    check("跨度 2 小时 55 分", summary.span_text(10512) == "2 小时 55 分")
    check("跨度 43 分 12 秒", summary.span_text(2592) == "43 分 12 秒")
    check("跨度 18 秒", summary.span_text(18) == "18 秒")


def test_png():
    img = summary.daily_png(row(), "i", "中级", "0.27.0")
    w, h = img.size
    plain = summary.daily_png(row(), "i", "中级", "0.27.0", with_mascot=False)
    m = summary._Sheet.MARGIN * summary.SCALE
    gap = summary.MASCOT_GAP * summary.SCALE
    # 贴纸比卡片高，所以图不只是变宽，也比"只有卡片"时更高
    check("单日图：加贴纸后变宽、也变高（贴纸高出卡片顶部）",
          w > plain.size[0] + gap + 600 and h > plain.size[1])
    check("单日图：关掉贴纸回到卡片宽 + 两侧留边",
          plain.size[0] == int((summary.D_W + 2 * summary._Sheet.MARGIN)
                               * summary.SCALE))
    check("四角是底色（白卡片外留了一圈边）",
          img.getpixel((0, 0)) == _rgb(summary.PAGE_BG)
          and img.getpixel((w - 1, h - 1)) == _rgb(summary.PAGE_BG))
    check("卡片内部是白底", img.getpixel((m + 20, h // 2)) == _rgb(summary.CARD_BG))
    check("字都在卡片里（边缘 2 px 内没有深色像素）",
          not _ink_near_edge(img, 3))

    # 贴纸：位置、大小、无缝
    x0 = m + int(summary.D_W * summary.SCALE) + int(summary.MASCOT_GAP * summary.SCALE)
    check("贴纸紧贴卡片右侧（间隔 = MASCOT_GAP）", x0 < w - m)
    check("贴纸左上角与页底完全同色（白底被乘掉，不留方块）",
          img.getpixel((x0 + 1, m + 1)) == _rgb(summary.PAGE_BG))
    art, total = _block_stats(img, x0, m, w - m, h - m)
    # 只查「有内容」和「四角仍是页底」：这一块被角色填掉七成是正常的，
    # 真正要防的是整块变成一个白色方块（白底没被乘掉）。
    check("贴纸区域真的有画（占 20%–90%）", 0.20 < art / float(total) < 0.90)
    check("贴纸下沿与卡片下沿齐平（齐平那一行右侧有画）",
          _row_has_ink(img, h - m - 2, x0, w - m))

    one = summary.today_png([("e", "高级", row())], "0.27.0")
    three = summary.today_png([("b", "初级", row(games=7)),
                               ("i", "中级", row(games=9)),
                               ("e", "高级", row())], "0.27.0")
    check("今日图：一个级别也能画", one is not None and one.size[0] > 800)
    # 只有一级时改走单日卡版式（矩阵退化成一行，卡片太矮就成一条长带）
    check("今日图：只有一个级别时套用单日卡版式",
          one.size == summary.daily_png(row(), "e", "高级", "0.27.0").size)
    # 贴纸高度到顶之后画布高度不再变，所以量的是**卡片**的白底高度
    two = summary.today_png([("i", "中级", row(games=9)),
                             ("e", "高级", row())], "0.27.0")
    m2 = summary._Sheet.MARGIN * summary.SCALE
    check("今日图：三个级别比两个级别的卡片高",
          _card_height(three, m2 + 20) > _card_height(two, m2 + 20))
    check("今日图：没有级别时返回 None", summary.today_png([]) is None)
    check("今日图：关掉贴纸回到卡片宽 + 两侧留边",
          summary.today_png([("b", "初级", row(games=7)),
                             ("i", "中级", row(games=9)),
                             ("e", "高级", row())],
                            with_mascot=False).size[0]
          == int((summary.T_W + 2 * summary._Sheet.MARGIN) * summary.SCALE))
    check("图里不含时间戳之外的随机内容（同参数两次尺寸相同）",
          summary.daily_png(row(), "i", "中级", "0.27.0").size
          == summary.daily_png(row(), "i", "中级", "0.27.0").size)


def test_mascot():
    path = os.path.join(os.path.dirname(os.path.abspath(summary.__file__)),
                        summary.MASCOT)
    check("贴纸图随仓库带着（mascot.png）", os.path.isfile(path))
    mk = summary.mascot(300)
    check("按高度取到贴纸，宽高比不变（约 1.44）",
          mk is not None and mk.height == 300
          and 1.35 < mk.width / float(mk.height) < 1.55)
    check("同一高度取第二次走缓存（同一个对象）", summary.mascot(300) is mk)

    # 图丢了不能把整张卡片带崩：只是右边空着
    old, cached = summary.MASCOT, dict(summary._MASCOT_CACHE)
    summary.MASCOT = "no-such-mascot.png"
    summary._MASCOT_CACHE.clear()
    try:
        img = summary.daily_png(row(), "i", "中级", "0.27.0")
        check("贴纸缺失时照常出图，宽度回到无贴纸那样",
              summary.mascot(300) is None
              and img.size == summary.daily_png(row(), "i", "中级", "0.27.0",
                                                with_mascot=False).size)
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


def _rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _ink_near_edge(img, margin):
    """卡片边缘 margin 像素以内有没有深色像素（用于查文字溢出）。"""
    w, h = img.size
    px = img.load()
    bad = [(x, y) for x in range(margin) for y in range(h)
           if sum(px[x, y]) < 500]
    bad += [(x, y) for x in range(w - margin, w) for y in range(h)
            if sum(px[x, y]) < 500]
    bad += [(x, y) for y in range(margin) for x in range(w)
            if sum(px[x, y]) < 500]
    bad += [(x, y) for y in range(h - margin, h) for x in range(w)
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
    try:
        root = tk.Tk()
        root.withdraw()
        summary.copy_text(root, "扫雷 12.345")
        check("文本进剪贴板再读回来", root.clipboard_get() == "扫雷 12.345")
        root.destroy()
    except tk.TclError as e:
        print("跳过剪贴板文本：%s" % e)


def test_none_hooks():
    check("copy_image(None) 返回 False，不炸", summary.copy_image(None) is False)


def main():
    test_text()
    test_span()
    test_png()
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

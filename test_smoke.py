"""GUI 冒烟测试：扫描、概览、PB 筛选/搜索/排序、前端成绩页渲染。

用本机录像目录（%APPDATA%\\Metasweeper3.3.4\\replay），找不到就跳过。
截图仍输出 shot_*.png 供人工看布局，结论看退出码：全过 0，否则 1。
"""
import os
import sys
import time

import app as A
from PIL import ImageGrab

A.App._restore_session = lambda self: None      # 冒烟测试不需要账号，别碰网络

HERE = os.path.dirname(os.path.abspath(__file__))
REPLAY = os.path.join(os.environ.get("APPDATA", ""), "Metasweeper3.3.4", "replay")
checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


def pump(win, cond, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        win.update()
        if cond():
            return True
        time.sleep(0.03)
    return False


def shot(win, name):
    win.update()
    time.sleep(0.25)
    win.update()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    img.save(os.path.join(HERE, name))
    print("shot", name, img.size)


def main():
    if not os.path.isdir(REPLAY):
        print("跳过：找不到录像目录 %s" % REPLAY)
        return 0

    win = A.App(REPLAY)
    win.attributes("-topmost", True)
    try:
        ok = pump(win, lambda: not win._scanning and bool(win.games))
        check("扫描完成且有录像（%d 局）" % len(win.games), ok)
        if not ok:
            return 1

        total = sum(s["games"] for s in win.summary.values())
        check("概览统计覆盖全部录像（%d）" % total, total == len(win.games))
        status = win.lbl_status.cget("text")
        check("状态栏没有扫描错误",
              not status.startswith("扫描失败") and "处理结果出错" not in status)
        shot(win, "shot_overview.png")

        # PB 页：级别单选 / 网格落位 / 搜索 / 指标 / 点格 / 双击播放
        win.show_tab("pb")
        check("PB 级别是单选，没有「全部」",
              set(win.pb_seg) == set(A.core.LEVELS))

        win.set_pb_level("b")
        win.update()
        grid = win.pb_grid
        idx = win._pb_index
        expect_b = {int(r["bv"]): r for r in win.pb_rows
                    if r["level"] == "b"
                    and (r.get("category") or "all") == win._pb_style}
        check("PB 网格只收 b 级（%d 格）" % len(idx),
              set(idx) == set(expect_b) and grid.level == "b" and bool(idx))
        shot(win, "shot_pb.png")

        # 网格的硬约束：BV n 必须落在第 n//10 行、第 n%10 列 —— 位置错了就
        # 会「哪一段断着一眼看见」这个前提失效，界面上看不出来
        cells = {}
        for i, line in enumerate(grid._cells):
            for c, cell in enumerate(line):
                bv = grid._bv_of.get(cell)
                if bv is not None:
                    cells[bv] = (i, c)
        check("每个 BV 都落在（十位段, 个位）对应的格子里",
              set(cells) == set(idx)
              and all(i * 10 + c + grid._row_start == bv
                      for bv, (i, c) in cells.items()))
        lo = (min(idx) // 10) * 10
        check("第一行标签是 %d–%d" % (lo, lo + 9),
              grid._rowlab[0].cget("text") == "%d–%d" % (lo, lo + 9))

        # 搜索：只给命中格子描边，不把别的格子藏掉（藏了会造出假断层）
        bv0 = max(idx)
        cell0 = cells[bv0]
        win.var_q.set(str(bv0))
        win.render_pb()
        check("搜 BV=%d 只命中这一格" % bv0, grid.hits == {bv0})
        check("搜索不从网格里删格子",
              set(win._pb_index) == set(expect_b) and len(cells) == len(idx))
        check("命中格描了高亮边",
              grid._cells[cell0[0]][cell0[1]].cget("highlightbackground")
              == A.C_ACCENT3)
        check("页面上写出搜到几格", "搜到 1 个" in win.pb_detail.cget("text"))
        win.var_q.set("")
        win.render_pb()

        # 指标只换数字：格子位置、颜色都不该跟着动
        row0 = idx[bv0]
        before_bg = grid._cells[cell0[0]][cell0[1]].cget("bg")
        win.set_pb_metric("bvs")
        text_bvs = grid._cells[cell0[0]][cell0[1]].cget("text")
        check("切到 3BV/s 后格子显示 %.2f" % float(row0.get("bvs") or 0),
              text_bvs == "%.2f" % float(row0.get("bvs") or 0))
        check("切指标不改格子底色",
              grid._cells[cell0[0]][cell0[1]].cget("bg") == before_bg)
        win.set_pb_metric("time")

        # 单击看数据，双击播录像
        grid.pick(bv0)
        check("单击格子把成绩写进详情条",
              ("BV %d" % bv0) in win.pb_detail.cget("text"))
        played = []
        orig_open = win.open_replay
        win.open_replay = lambda p: played.append(p)

        class _Ev(object):
            widget = None

        ev = _Ev()
        ev.widget = grid._cells[cell0[0]][cell0[1]]
        grid._on_double(ev)
        check("双击格子用元扫雷播这一局",
              bool(row0.get("path")) and played == [row0.get("path")])
        win.open_replay = orig_open

        # NF PB 榜：网格里不能混进全PB 的局
        win.set_pb_style("nf")
        win.update()
        check("NF PB 榜只收 NF 局（%d 格）" % len(win._pb_index),
              all((r.get("category") or "all") == "nf"
                  for r in win._pb_index.values()))
        win.set_pb_style("all")

        # 网站 PB 合并：离线注入一条网站独有记录，验证网格与计数（不联网）
        win.site_pb = [{"level": "b", "bv": 999, "nf": False,
                        "timems": 1000, "video_id": 777, "rank": 1}]
        win._recompute_pb()
        check("网站独有的 PB 会进网格（BV 999）",
              999 in win._pb_index
              and win._pb_index[999].get("source") == "site")
        check("PB 右上角标出网站 PB 条数",
              "网站 PB 1 条" in win.lbl_pb_count.cget("text"))
        win.site_pb = []
        win._recompute_pb()

        # 已退出登录（本机无会话）时，迟到的网站 PB 不能复活
        orig_has = win.oms.has_session
        win.oms.has_session = lambda: False
        win._on_site_pb(win._site_seq, 3,
                        [{"level": "b", "bv": 999, "nf": False,
                          "timems": 1000, "video_id": 777, "rank": 1}], "")
        check("退出登录后迟到的网站 PB 被拒绝", not win.site_pb)
        win.oms.has_session = orig_has

        # 最近完成局的涨分列：有涨分局才出现，没有就连表头收起
        disp = win.tbl_recent.tv.cget("displaycolumns")
        if isinstance(disp, (tuple, list)):
            shown = {str(x) for x in disp}
        else:
            shown = set(str(disp).replace(",", " ").split())
        # displaycolumns 默认是 "#all"，意思是「一列都没藏」，等价于全部显示
        if "#all" in shown:
            shown = {c[0] for c in win.tbl_recent.columns_def}
        gains = [r.get("gain") for r in win.tbl_recent.rows]
        hot = sum(1 for g in gains if isinstance(g, float) and g > 0.05)
        check("涨分列的出现/收起跟内容一致（%d 行有 %d 局涨分，显示=%s）"
              % (len(gains), hot, "gain" in shown),
              ("gain" in shown) == (hot > 0))

        # 前端成绩页：三张表都按 b 级渲染
        win.show_tab("top")
        win.set_top_level("b")
        check("前端成绩页有 3 张表", len(win.top_tables) == 3)
        for metric, table in win.top_tables.items():
            rows = table.rows
            check("Top5[%s] 不超过 5 行、序号连续、都是 b 级" % metric,
                  len(rows) <= 5
                  and [r.get("rank") for r in rows] == list(range(1, len(rows) + 1))
                  and all(r.get("level") == "b" for r in rows))
        check("Top5 至少有一项有成绩",
              any(table.rows for table in win.top_tables.values()))

        # 个人 LSF：有锚点就必须算出数，没有锚点只能留「—」
        if win.lsf_anchors:
            value = win.lsf_card.lbl_value.cget("text")
            check("LSF 卡片有分数（%s）" % value, value not in ("", "—"))
            cells = [v for line in win.lsf_card.cells for v, _sub in line]
            check("LSF 卡片八项软实力已填", len(cells) == 8 and all(
                c.cget("text") not in ("", "—") for c in cells))
        else:
            print("跳过：没有锚点（首次离线启动只能显示「—」）")
        shot(win, "shot_top.png")
    finally:
        try:
            win.destroy()
        except Exception:
            pass

    bad = sum(1 for _l, ok in checks if not ok)
    print("== %s（%d 项）==" % ("全部通过" if not bad else "%d 项失败" % bad,
                               len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

"""GUI 冒烟测试：扫描、概览、PB 筛选/搜索/排序、前端成绩页渲染。

用本机录像目录（%APPDATA%\\Metasweeper3.3.4\\replay），找不到就跳过。
截图仍输出 shot_*.png 供人工看布局，结论看退出码：全过 0，否则 1。
"""
import os
import sys
import time
import tkinter as tk

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

        # 每日页：一天一行，按开始时刻归日
        win.show_tab("daily")
        win.update()
        check("每日页有级别单选（三个）", set(win.daily_seg) == set(A.core.LEVELS))
        check("默认看高级，且按钮已经点亮（不是三个全灰）",
              win._daily_level == "e" and win.daily_seg["e"]._sel
              and not win.daily_seg["b"]._sel)
        rows = win.daily_rows
        check("每日页列出有局的日子（%d 天）" % len(rows), bool(rows))
        lv_total = sum(1 for g in win.games if g["level"] == win._daily_level)
        check("每日局数合计 = 该级别完成局数（%d）" % lv_total,
              sum(r["games"] for r in rows) == lv_total)
        days = [r["date"] for r in rows]
        check("日期倒序且不重复", days == sorted(set(days), reverse=True))
        check("每局的时间都来自录像内（不是文件 mtime）",
              all(g.get("t0") for g in win.games))
        check("起止时刻是 HH:MM:SS",
              all(len(A.core.clock_text(r["t_from"])) == 8
                  and len(A.core.clock_text(r["t_till"])) == 8 for r in rows))
        check("表格行数 = 天数", len(win.tbl_daily.rows) == len(rows))
        # 组头必须压在各自那三列上方：列是 stretch 的，位置只能按实际列宽算，
        # 用设计宽度硬算会偏移（改过一次，所以钉在这里）
        # 先让布局落定再重画一次：组头是 <Configure> 里画的，窗口在测试中途
        # 变过尺寸时，画的那一次和量尺寸的这一次不是同一套列宽（会随机红）。
        win.update_idletasks()
        win._draw_daily_groups()
        kids = win.tbl_daily.tv.get_children()
        labels = {win.daily_strip.itemcget(i, "text"): win.daily_strip.coords(i)[0]
                  for i in win.daily_strip.find_all()}
        check("三组组头都在（time / 3BV/s / stnb）",
              set(labels) == {g[2] for g in A.App.DAILY_GROUPS})
        for first, last, name in A.App.DAILY_GROUPS:
            a = win.tbl_daily.tv.bbox(kids[0], first)
            b = win.tbl_daily.tv.bbox(kids[0], last)
            if not a or not b or name not in labels:
                print("跳过：组头 %s 没量到（表格还没布局）" % name)
                continue
            mid = (a[0] + b[0] + b[2]) / 2.0
            check("组头「%s」居中自己那三列" % name,
                  abs(labels[name] - mid) <= A.sc(6))
        # 抽一天手算：局数与算术平均都要对得上，否则口径就是错的
        r0 = rows[0]
        day_games = A.core.daily_games(win.games, r0["date"], win._daily_level)
        want_avg = sum(g["time"] for g in day_games) / max(1, len(day_games))
        check("抽 %s 手算：%d 局、平均 %.3f 与表里一致"
              % (r0["date"], len(day_games), want_avg),
              len(day_games) == r0["games"] and abs(want_avg - r0["avg"]) < 1e-9)
        check("止 >= 起（同日内的时刻顺序）",
              all(r["t_till"] >= r["t_from"] for r in rows))
        win.tbl_daily.tv.selection_set(r0["date"])
        win._on_daily_pick()
        check("选中一天，明细条写出摘要",
              r0["date"] in win.lbl_daily_detail.cget("text"))
        # 双击某天 → 当天明细对话框
        win._open_daily()
        dlg = win._daily_dialog
        check("双击弹出当天明细（%d 行 = 当天局数）"
              % len(day_games),
              dlg is not None and dlg.winfo_exists()
              and len(dlg.tab.rows) == len(day_games))
        check("明细按时刻倒序",
              [r["clock"] for r in dlg.tab.rows]
              == sorted([r["clock"] for r in dlg.tab.rows], reverse=True))
        check("明细每行都带本地录像路径",
              all(r.get("path") for r in dlg.tab.rows))
        # 对话框里的播放要转发给主窗口，否则双击只会复制路径
        played = []
        orig = win.open_replay
        win.open_replay = lambda p: played.append(p)
        dlg.open_replay(dlg.tab.rows[0]["path"])
        check("对话框的播放转给主窗口", played == [dlg.tab.rows[0]["path"]])
        win.open_replay = orig
        dlg.destroy()
        # 双击要把被点那一行的日期直接传下去：Treeview 的选中变化是异步事件，
        # 靠「先选中再读选中」会打开上一天
        if len(rows) > 1:
            win.tbl_daily.tv.selection_set(rows[1]["date"])
            win._on_daily_pick()
            want = len(A.core.daily_games(win.games, rows[0]["date"],
                                          win._daily_level))
            win.tbl_daily.on_double(rows[0]["date"])
            check("双击打开的是被点的那一天（不是选中的那天）",
                  win._daily_dialog is not None
                  and len(win._daily_dialog.tab.rows) == want
                  and win._daily_dialog.tab.rows[0]["date"] == rows[0]["date"])
            win._daily_dialog.destroy()
        # 右键某天：总结 / 详细总结，各自再分这一级与全部级别
        menu = tk.Menu(win, tearoff=0)
        win._daily_menu(r0["date"], menu)
        items = [menu.entrycget(i, "label")             # 分隔符没有 -label 选项
                 for i in range(menu.index("end") + 1)
                 if menu.type(i) != "separator"]
        cn0 = A.core.LEVEL_CN[win._daily_level]
        check("右键菜单：本级总结/详细 + 全部级别总结/详细（没有文本）",
              items == ["复制总结 · %s" % cn0,
                        "复制详细总结 · %s" % cn0,
                        "复制总结 · 全部级别",
                        "复制详细总结 · 全部级别"])
        win._copy_level(r0["date"], False)
        check("当日总结复制成功", "图片" in win.lbl_status.cget("text"))
        win._copy_level(r0["date"], True)
        hist = win.lbl_status.cget("text")
        check("当日详细总结复制成功", "详细总结" in hist and "图片" in hist)

        # 当天全部级别：任意一天都能出（不必等到今天）
        lv_items = win._day_level_rows(r0["date"])
        check("当天有 %d 个级别，且包含正在看的那一级" % len(lv_items),
              bool(lv_items)
              and win._daily_level in [lv for lv, _c, _r in lv_items]
              and [lv for lv, _c, _r in lv_items]
              == [lv for lv in A.core.LEVELS
                  if lv in [x for x, _c, _r in lv_items]])
        win._copy_levels(r0["date"], False)
        check("当天全部级别（总结）复制成功",
              "图片" in win.lbl_status.cget("text"))
        win._copy_levels(r0["date"], True)
        check("当天全部级别（详细总结）复制成功",
              "详细总结" in win.lbl_status.cget("text"))
        shot(win, "shot_daily.png")

        # 切级别要共用「天」：三级行数不同，按百分比保留滚动位置会跳到别的
        # 日期。切过去应停在同一天，那天没有就取日期最近的一天。
        # 选天数最多的那级作源（默认的高级可能只有一局），保证滚动有得测。
        near = A.App._nearest_day
        check("取最近一天：精确命中",
              near(["2026-11-10", "2026-11-15", "2026-11-20"], "2026-11-15")
              == "2026-11-15")
        check("取最近一天：落在两个之间，取更近的",
              near(["2026-11-10", "2026-11-20"], "2026-11-16") == "2026-11-20")
        check("取最近一天：一样近取较新",
              near(["2026-11-10", "2026-11-20"], "2026-11-15") == "2026-11-20")
        check("取最近一天：早于全部/晚于全部取端点",
              near(["2026-11-10", "2026-11-20"], "2026-01-01") == "2026-11-10"
              and near(["2026-11-10", "2026-11-20"], "2026-12-31") == "2026-11-20")

        ranked = sorted(A.core.LEVELS,
                        key=lambda k: -len(win.daily_by_level[k]))
        src = ranked[0]
        others = [k for k in ranked[1:] if win.daily_by_level[k]]
        if len(win.daily_by_level[src]) > 1 and others:
            dst = others[0]
            win.set_daily_level(src)
            win.update()
            tv = win.tbl_daily.tv
            tv.yview_moveto(0.45)           # 滚到中段，确保第一行不是最新那天
            win.update()
            anchor = win._daily_top_date()
            win.set_daily_level(dst)
            win.update()
            after_days = [r["date"] for r in win.daily_by_level[dst]]
            want = anchor if anchor in after_days else near(after_days, anchor)
            got = win._daily_top_date()
            visible = bool(tv.bbox(want)) if want in tv.get_children() else False
            # 末尾几天下面行数不够置顶时，只要求目标那天在视口里可见
            tail = after_days[-8:]
            ok = got == want or (want in tail and visible)
            check("切到%s仍停在同一天（或最近一天 %s），实际 %s"
                  % (A.core.LEVEL_CN[dst], want, got), ok)
            win.set_daily_level(src)        # 恢复，别影响后面的测试
            win.update()
        else:
            print("跳过：切级别滚动测试需要至少两个级别各有多天数据")

        # 真机置顶：真实录像每级只有几天，全可见、滚不动，上面那条走的是宽松
        # 分支。这里注入合成的多天数据，确认「滚动到中段→切级别→置顶同一天」
        # 真的成立（置顶算法以后改坏了，这条会红）。测完恢复原 games。
        from datetime import datetime as _dt
        saved_games = win.games
        base = _dt(2026, 1, 1).timestamp()
        def _synthetic(level, ndays, start):
            out = []
            for j in range(ndays):
                t0 = base + (start + j) * 86400 + 9 * 3600
                out.append({"t0": t0, "t1": t0 + 10, "time": 10.0, "bv": 40,
                            "bvs": 4.0, "stnb": 1.0, "level": level,
                            "nf": True, "path": "x", "category": "all"})
            return out
        win.games = _synthetic("b", 60, 0) + _synthetic("i", 55, 3)
        win.set_daily_level("b")
        win.update()
        tv = win.tbl_daily.tv
        tv.yview_moveto(0.45)
        win.update()
        anchor = win._daily_top_date()
        win.set_daily_level("i")
        win.update()
        after_days = [r["date"] for r in win.daily_by_level["i"]]
        want = anchor if anchor in after_days else A.App._nearest_day(after_days, anchor)
        check("多天数据：滚到中段切级别，第一行精确停在 %s" % want,
              win._daily_top_date() == want)
        win.games = saved_games
        win.set_daily_level(src)            # 重算 daily_by_level 并恢复级别
        win.update()

        # 概览页的「今日总结」按钮：今天的几个级别并在一张图 / 一段文字里
        win.show_tab("overview")
        win.update()
        check("概览页有「今日总结」按钮",
              win.btn_today.winfo_exists() == 1)
        today, titems = win._today_rows()
        win._copy_today(False)
        if titems:
            check("今日总结复制成功（%d 个级别）" % len(titems),
                  "图片" in win.lbl_status.cget("text"))
            win._copy_today(True)
            check("今日详细总结复制成功",
                  "详细总结" in win.lbl_status.cget("text"))
        else:
            check("今天没局时给提示，不弹空图",
                  "还没有完成局" in win.lbl_status.cget("text"))

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
        # 网格的起止：下限固定在级别起点，上限跟着已有最大 BV 走
        floor = A.PB_BV_FLOOR["b"]
        lo = (floor // 10) * 10
        check("第一行从初级起点 %d–%d 开始" % (lo, lo + 9),
              grid._rowlab[0].cget("text") == "%d–%d" % (lo, lo + 9))
        check("初级 BV 0 / 1 两格留空",
              all(grid._bv_of.get(grid._cells[0][c]) is None
                  for c in range(floor)))
        hi = (max(idx) // 10) * 10
        last = (hi - lo) // 10
        check("最后一行是 %d–%d（网格到最大 BV 那一段为止）" % (hi, hi + 9),
              grid._rowlab[last].cget("text") == "%d–%d" % (hi, hi + 9))
        # 下限是死数，低于起点的成绩会被网格遮掉 —— 真出现就是丢数据
        for lv in A.core.LEVELS:
            got = {int(r["bv"]) for r in win.pb_rows if r["level"] == lv}
            check("%s 最低 BV %d 不低于网格起点 %d"
                  % (A.core.LEVEL_CN[lv], min(got), A.PB_BV_FLOOR[lv]),
                  all(bv >= A.PB_BV_FLOOR[lv] for bv in got))

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
        check("详情条不再报「几个位置」",
              "个位置" not in win.pb_detail.cget("text"))

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

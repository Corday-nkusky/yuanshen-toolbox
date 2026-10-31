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

        # PB 页：级别筛选 / 搜索 / 排序
        win.show_tab("pb")
        win.set_pb_level("b")
        rows_b = win.tbl_pb.rows
        expect_b = [r for r in win.pb_rows
                    if r["level"] == "b"
                    and (r.get("category") or "all") == win._pb_style]
        check("PB 级别筛选只剩 b（%d 行）" % len(rows_b),
              len(rows_b) == len(expect_b) and all(r["level"] == "b" for r in rows_b))
        shot(win, "shot_pb.png")

        win.set_pb_level("")
        all_rows = win.tbl_pb.rows
        check("PB 表有数据（%d 行）" % len(all_rows), bool(all_rows))
        cats = {r.get("category") for r in win.pb_rows}
        check("PB 只有全PB / NF PB 两类", cats <= {"all", "nf"} and "all" in cats)
        check("全PB 行都标了获胜局风格",
              all(r.get("style_cn") in ("NF", "FL", "—", "同速", "?")
                  for r in win.pb_rows if r.get("category") == "all"))

        win.set_pb_style("nf")
        nf_rows = win.tbl_pb.rows
        check("切到 NF PB 后只显示 NF 榜（%d 行）" % len(nf_rows),
              all((r.get("category") or "all") == "nf" for r in nf_rows)
              and len(nf_rows) == len([r for r in win.pb_rows
                                       if (r.get("category") or "all") == "nf"]))
        win.set_pb_style("all")

        # 网站 PB 合并：离线注入一条网站独有记录，验证表格与来源列（不联网）
        win.site_pb = [{"level": "b", "bv": 999, "nf": False,
                        "timems": 1000, "video_id": 777, "rank": 1}]
        win._recompute_pb()
        site_rows = [r for r in win.tbl_pb.rows if r["bv"] == 999]
        check("网站独有 PB 会出现在全PB 表里",
              len(site_rows) == 1 and site_rows[0].get("source") == "site"
              and site_rows[0].get("source_cn") == "网站")
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
        if all_rows:
            q = str(all_rows[0]["bv"])
            base = len(all_rows)
            win.var_q.set(q)
            win.render_pb()
            hits = win.tbl_pb.rows
            check("搜索 BV=%s 有结果且全部命中（%d 行）" % (q, len(hits)),
                  bool(hits) and len(hits) <= base
                  and all(q in ("%s %s" % (r["bv"], r["file"])).lower() for r in hits))
            win.var_q.set("")
            win.render_pb()

            win.tbl_pb._sort_by("time")
            times = [r["time"] for r in win.tbl_pb.rows]
            check("PB 按时间排序单调（%s）"
                  % ("降序" if win.tbl_pb._sort_rev else "升序"),
                  times == sorted(times, reverse=win.tbl_pb._sort_rev))

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

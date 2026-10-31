"""PB 两榜口径回归：全PB / NF PB，以及网站 PB 与本地 PB 的合并。

跑法：python test_site_pb.py —— 全部通过退出码 0，否则 1。
覆盖：两榜各自取最快、FL 不单独成榜、全PB 标出获胜风格、
      合并按整数毫秒比较、同速优先本地、网站行无本地路径、无效记录过滤、
      上传范围不再有 FL 榜、pb_records 的路径与返回。
"""

import os
import tempfile

import core
import openms

checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


def game(level, bv, ms, style):
    sec = ms / 1000.0
    return {
        "level": level, "bv": bv, "time": sec, "timems": ms,
        "bvs": bv / sec, "stnb": core.stnb_of(bv, sec, level),
        "date": "2026-10-01", "file": "%s_%d_%s.evf" % (level, bv, style),
        "path": "C:/replay/%s_%d_%s.evf" % (level, bv, style),
        "style": style, "nf": style == "nf",
        "rce": 0 if style == "nf" else 3,
    }


def local_row(level, bv, ms, style, cat):
    row = game(level, bv, ms, style)
    row["category"] = cat
    row["style_cn"] = core.STYLE_CN.get(style, "?")
    return row


def site(level, bv, nf, ms, vid, rank=None):
    return {"level": level, "bv": bv, "nf": nf, "timems": ms,
            "video_id": vid, "rank": rank}


def by_key(rows):
    return {(r["level"], r["bv"], r["category"]): r for r in rows}


def test_two_boards():
    games = [game("b", 10, 2000, "fl"), game("b", 10, 3000, "nf"),
             game("b", 20, 5000, "fl")]
    rows = core.build_pb_rows(games)
    cats = {r["category"] for r in rows}
    check("PB 只有 all / nf 两张榜", cats == {"all", "nf"})
    d = by_key(rows)
    check("全PB 取不分风格最快", d[("b", 10, "all")]["timems"] == 2000
          and d[("b", 10, "all")]["style"] == "fl")
    check("NF PB 只在 NF 里取最快", d[("b", 10, "nf")]["timems"] == 3000
          and d[("b", 10, "nf")]["style"] == "nf")
    check("没有 NF 局的 BV 不产生 NF 行", ("b", 20, "nf") not in d)
    check("全PB 行带获胜局的 rce", d[("b", 10, "all")]["rce"] == 3)
    check("全PB 行的 timems 是整数毫秒",
          isinstance(d[("b", 10, "all")]["timems"], int))


def test_merge_basic():
    local = [
        local_row("b", 10, 2500, "fl", "all"),
        local_row("b", 10, 3000, "nf", "nf"),
        local_row("b", 11, 4000, "fl", "all"),
    ]
    site_rows = [
        site("b", 10, False, 2500, 101, 7),   # 与本地同速
        site("b", 10, True, 2800, 102, 9),    # 比本地 NF 快
        site("b", 11, False, 4200, 103, 4),   # 比本地慢
        site("b", 12, False, 3500, 104, 2),   # 只有网站有
        site("b", 12, True, 3900, 105, 3),
    ]
    rows = core.merge_site_pb(local, site_rows)
    d = by_key(rows)

    r = d[("b", 10, "all")]
    check("同速保留本地行（有文件/日期）", r["source"] == "both"
          and r["file"].endswith(".evf") and r["timems"] == 2500)
    check("同速也保留网站 id 与名次", r["site_video_id"] == 101 and r["site_rank"] == 7)

    r = d[("b", 10, "nf")]
    check("网站 NF 更快则采用网站行", r["source"] == "site" and r["timems"] == 2800)
    check("网站行没有本地路径且标明网站",
          r["path"] == "" and r["file"] == "网站 #102" and r["local_timems"] == 3000)

    r = d[("b", 11, "all")]
    check("本地更快则保留本地行", r["source"] == "local" and r["timems"] == 4000)
    check("本地行记录网站时间但不用它", r["site_timems"] == 4200 and r["site_video_id"] == 103)

    r = d[("b", 12, "all")]
    check("网站独有的全PB 也进表", r["source"] == "site" and r["timems"] == 3500)
    check("网站独有行的本地局数为 0（渲染时由 counts 决定）",
          r["local_timems"] is None)
    check("网站独有 NF PB 是单独一行",
          d[("b", 12, "nf")]["source"] == "site"
          and d[("b", 12, "nf")]["timems"] == 3900)
    check("两榜不互相覆盖",
          d[("b", 10, "all")]["timems"] == 2500
          and d[("b", 10, "nf")]["timems"] == 2800)


def test_merge_style_label():
    site_rows = [
        site("i", 30, False, 10000, 201),
        site("i", 31, False, 12000, 202),
        site("i", 31, True, 13000, 203),
        site("i", 32, False, 14000, 204),
        site("i", 32, True, 14000, 204),   # 同一条录像同时是全PB 与 NF PB
        site("i", 33, False, 16000, 205),
        site("i", 33, True, 15000, 206),   # 站点数据异常：全PB 慢于 NF
        site("i", 34, True, 17000, 207),   # 没有全PB，只有 NF
    ]
    rows = core.merge_site_pb([], site_rows)
    d = by_key(rows)
    check("只有全PB、没有 NF 记录 → 标 FL", d[("i", 30, "all")]["style_cn"] == "FL")
    check("全PB 快于 NF → 标 FL", d[("i", 31, "all")]["style_cn"] == "FL")
    check("全PB 与 NF 同速且同录像 → 标 NF",
          d[("i", 32, "all")]["style_cn"] == "NF")
    check("数据异常（全PB 慢于 NF）不猜风格",
          d[("i", 33, "all")]["style_cn"] == "—")
    check("只有 NF 记录时不伪造全PB", ("i", 34, "all") not in d)
    check("只有 NF 记录时 NF 行照常", d[("i", 34, "nf")]["timems"] == 17000)

    same = core.merge_site_pb([], [
        site("e", 100, False, 20000, 301),
        site("e", 100, True, 20000, 302),
    ])
    check("同速但录像不同 → 标同速",
          by_key(same)[("e", 100, "all")]["style_cn"] == "同速")


def test_merge_ms_and_filter():
    local = [local_row("b", 5, 1001, "nf", "nf")]
    # 同一 BV 的本地行 time 浮点相同、timems 差 1 ms —— 必须按 ms 判
    local[0]["time"] = 1.0010
    rows = core.merge_site_pb(local, [site("b", 5, True, 1000, 401)])
    r = by_key(rows)[("b", 5, "nf")]
    check("合并按整数毫秒比较（1000 < 1001）",
          r["source"] == "site" and r["timems"] == 1000)

    bad = core.merge_site_pb([{"level": "x", "bv": 1, "category": "all"}], [
        {"level": "b", "bv": 0, "nf": False, "timems": 100},
        {"level": "b", "bv": 6, "nf": False, "timems": 0},
        {"level": "b", "bv": 7, "nf": False, "timems": "oops"},
        {"level": "i", "bv": 8, "nf": False, "timems": 1000},
    ])
    check("非法本地/网站记录被过滤",
          len(bad) == 1 and bad[0]["level"] == "i" and bad[0]["bv"] == 8)


def test_scope_no_fl():
    games = [game("b", 10, 2000, "fl"), game("b", 10, 3000, "nf")]
    scopes = [k for k, _ in openms.SCOPES]
    check("上传范围里没有 FL 榜",
          "pb_fl" not in scopes and "top_fl" not in scopes
          and "pb" in scopes and "pb_nf" in scopes)
    check("范围文案是「PB·全PB」",
          dict(openms.SCOPES)["pb"] == "PB·全PB")
    pb_all = openms.scope_paths(games, "pb")
    pb_nf = openms.scope_paths(games, "pb_nf")
    check("全PB 范围含 FL 获胜局", len(pb_all) == 1)
    check("NF PB 范围只含 NF 局", len(pb_nf) == 1 and pb_nf != pb_all)


def test_pb_records_endpoint():
    tmp = tempfile.mkdtemp(prefix="ms-pb-test-")
    cli = openms.Client(store=openms.Store(path=os.path.join(tmp, "openms.json")),
                        proxy=False)
    seen = {}

    def fake(path, retries=3, timeout=None):
        seen["path"] = path
        return 200, [site("b", 10, False, 2500, 101, 7)]

    cli.get_json_retry = fake
    out = cli.pb_records(42)
    check("pb_records 打的是公开 PB 接口",
          seen.get("path") == "/api/speedranking/pb/player/42")
    check("pb_records 原样返回列表",
          isinstance(out, list) and out[0]["timems"] == 2500)
    cli.get_json_retry = lambda path, retries=3, timeout=None: (200, {})
    try:
        cli.pb_records(42)
        check("非列表响应会报错", False)
    except openms.OmsError:
        check("非列表响应会报错", True)


def main():
    test_two_boards()
    test_merge_basic()
    test_merge_style_label()
    test_merge_ms_and_filter()
    test_scope_no_fl()
    test_pb_records_endpoint()
    bad = sum(1 for _l, ok in checks if not ok)
    print("== %s（%d 项）==" % ("全部通过" if not bad else "%d 项失败" % bad,
                               len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

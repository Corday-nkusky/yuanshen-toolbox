"""PB 两榜 / 网站 PB / 网站 Top5 口径回归。

跑法：python test_site_pb.py —— 全部通过退出码 0，否则 1。
覆盖：两榜各自取最快、FL 不单独成榜、全PB 标出获胜风格、
      合并按整数毫秒比较、同速优先本地、网站行无本地路径、无效记录过滤、
      上传范围不再有 FL 榜、PB 范围排除慢于网站 PB 的本地局、
      网站 Top5 字段解析 / 排名阈值 / 本地与网站合并 / 范围排除、
      pb_records 与 top_records 的路径与返回。
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


def test_scope_site_filter():
    games = [
        game("b", 10, 2500, "fl"),
        game("b", 10, 3000, "nf"),
        game("b", 11, 4000, "fl"),
        game("b", 12, 5000, "nf"),
        game("b", 13, 6000, "fl"),
    ]
    paths = {g["file"]: g["path"] for g in games}
    site_rows = [
        site("b", 10, False, 2400, 501),   # 全PB 比本地快 → 排除
        site("b", 10, True, 3100, 502),    # NF 比本地慢 → 保留
        site("b", 11, False, 4000, 503),   # 同速 → 保留
        site("b", 12, False, 4800, 504),   # 全PB 更快 → 全PB 排除
        site("b", 12, True, 5200, 505),    # NF 更慢 → NF 保留
        site("b", 13, True, 5900, 506),    # 只有 NF 记录，不影响全PB
    ]

    skipped_all = set()
    pb_all = openms.scope_paths(games, "pb", site_pb=site_rows,
                                skipped=skipped_all)
    check("全PB 排除慢于网站的全PB 本地局",
          paths["b_10_fl.evf"] not in pb_all)
    check("全PB 同速保留本地局", paths["b_11_fl.evf"] in pb_all)
    check("全PB 排除慢于网站的 NF 获胜局",
          paths["b_12_nf.evf"] not in pb_all)
    check("网站只有 NF 记录时不影响全PB",
          paths["b_13_fl.evf"] in pb_all)
    check("skipped 记下全PB 排除的路径",
          skipped_all == {paths["b_10_fl.evf"], paths["b_12_nf.evf"]})

    skipped_nf = set()
    pb_nf = openms.scope_paths(games, "pb_nf", site_pb=site_rows,
                               skipped=skipped_nf)
    check("NF PB 只与网站 NF 比，不被网站全PB 压掉",
          paths["b_10_nf.evf"] in pb_nf and paths["b_12_nf.evf"] in pb_nf)
    check("NF PB 没有被排除的路径", not skipped_nf)

    both = set()
    all_paths = openms.scope_paths(games, "pb", site_pb=site_rows, skipped=both)
    nf_paths = openms.scope_paths(games, "pb_nf", site_pb=site_rows, skipped=both)
    check("跨范围合并时，NF 范围保留的路径不算排除",
          both - (all_paths | nf_paths) == {paths["b_10_fl.evf"]})

    check("没有网站 PB 时保持原范围",
          openms.scope_paths(games, "pb") ==
          {paths["b_10_fl.evf"], paths["b_11_fl.evf"],
           paths["b_12_nf.evf"], paths["b_13_fl.evf"]})
    check("「全部完成局」不受网站 PB 影响",
          openms.scope_paths(games, "all", site_pb=site_rows) ==
          {g["path"] for g in games})
    check("网站 PB 非法记录不参与排除",
          paths["b_10_fl.evf"] in openms.scope_paths(
              games, "pb", site_pb=[
                  {"level": "b", "bv": 10, "nf": False, "timems": 0},
                  {"level": "x", "bv": 10, "nf": False, "timems": 1000},
              ]))


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


def top_field(level, metric, nf=False):
    stat = {"time": "t", "bvs": "b", "stnb": "s"}[metric]
    return "%s:%s%s" % ("nf" if nf else "std", level, stat)


def top_record(level, metric, values, nf=False, start=1000):
    return top_field(level, metric, nf), {
        "videos": [{"video_id": start + i, "value": v}
                   for i, v in enumerate(values)]}


def top_game(level, bv, ms, style, size=1000):
    g = game(level, bv, ms, style)
    g["size"] = size
    return g


def test_site_top_rows():
    records = {}
    records.update(dict([top_record("b", "time", [1000, 1100, 1200, 1300, 1400],
                                    start=11)]))
    records.update(dict([top_record("b", "bvs", [10, 9, 8, 7, 6], start=21)]))
    records.update(dict([top_record("b", "stnb", [2.0, 1.9, 1.8, 1.7, 1.6],
                                    start=31)]))
    records.update(dict([top_record("b", "time", [1200, 1300], nf=True,
                                    start=41)]))
    records["std:xx"] = {"videos": [{"video_id": 99, "value": 1}]}
    details = {
        11: {"timems": 1000, "bv": 10, "file_size": 3026, "right_ce": 1,
             "end_time": "2022-04-12T12:50:50.158Z"},
        21: {"timems": 2770, "bv": 33, "file_size": 6388, "right_ce": 0,
             "end_time": "2022-10-15T20:17:11.233Z"},
        31: {"timems": 20000, "bv": 100, "file_size": 9000, "right_ce": 3},
    }
    rows = core.site_top_rows(records, details)
    check("站点 Top5 解析出 4 个合法小榜", len(rows) == 4)
    check("非法字段 std:xx 被忽略", ("all", "b", "time") in rows
          and all(k[2] != "time" or k[1] != "x" for k in rows))

    time_rows = rows[("all", "b", "time")]
    first = time_rows[0]
    check("time 榜按毫秒升序、最多五条",
          [r["timems"] for r in time_rows] == [1000, 1100, 1200, 1300, 1400])
    check("网站行补齐 BV / 3BV/s / stnb",
          first["bv"] == 10 and abs(first["bvs"] - 10.0) < 1e-9
          and abs(first["stnb"] - core.stnb_of(10, 1.0, "b")) < 1e-9)
    check("网站行标出来源与录像 id",
          first["source"] == "site" and first["source_cn"] == "网站"
          and first["site_video_id"] == 11 and first["file"] == "网站 #11"
          and first["path"] == "")
    check("网站行风格由 right_ce 判定",
          first["style"] == "fl" and rows[("all", "b", "bvs")][0]["style"] == "nf")

    stnb_rows = rows[("all", "b", "stnb")]
    check("iqg 换算成 stnb（乘级别常数）",
          abs(stnb_rows[0]["stnb"] - 2.0 * 36) < 1e-9)
    check("NF 榜用 nf 字段",
          [r["timems"] for r in rows[("nf", "b", "time")]] == [1200, 1300])

    cutoffs = core.site_top_cutoffs(records)
    check("满五条才产生第五名阈值",
          cutoffs[("all", "b", "time")] == 1400
          and cutoffs[("all", "b", "bvs")] == 6
          and abs(cutoffs[("all", "b", "stnb")] - 1.6 * 36) < 1e-9)
    check("不满五条没有阈值", ("nf", "b", "time") not in cutoffs)


def test_merge_site_top_rows():
    local = [
        top_game("b", 10, 900, "nf", size=3026),    # 本地更快、网站上没有
        top_game("b", 10, 1000, "nf", size=3026),   # 与网站第一条同录像
        top_game("b", 11, 1100, "fl", size=4001),
        top_game("b", 12, 1200, "nf", size=5001),
        top_game("b", 13, 1300, "fl", size=6001),
    ]
    records = dict([top_record("b", "time", [1000, 1100, 1200, 1300, 1400],
                               start=11)])
    details = {
        11: {"timems": 1000, "bv": 10, "file_size": 3026, "right_ce": 0},
        12: {"timems": 1100, "bv": 11, "file_size": 4000, "right_ce": 1},
        13: {"timems": 1200, "bv": 12, "file_size": 5000, "right_ce": 0},
        14: {"timems": 1300, "bv": 13, "file_size": 6000, "right_ce": 1},
        15: {"timems": 1400, "bv": 14, "file_size": 7000, "right_ce": 1},
    }
    site_rows = core.site_top_rows(records, details)[("all", "b", "time")]
    merged = core.merge_top_rows(local, site_rows, "time")
    check("本地与网站合并后仍只取前五", len(merged) == 5)
    check("本地更快的未上传局排在第一名",
          merged[0]["timems"] == 900 and merged[0]["source"] == "local")
    same = [r for r in merged if r.get("site_video_id") == 11]
    check("同一条录像只留一行并标「两边」",
          len(same) == 1 and same[0]["source"] == "both"
          and same[0]["source_cn"] == "两边" and same[0]["timems"] == 1000)
    check("网站独有的成绩也进表", any(r["source"] == "site" for r in merged))

    site_only = core.merge_top_rows([], site_rows, "time")
    check("没有本地局时全用网站行",
          len(site_only) == 5 and all(r["source"] == "site" for r in site_only))

    bare = core.site_top_rows(records)      # 明细读取失败：没有 file_size
    bare_merged = core.merge_top_rows([], bare[("all", "b", "time")], "time")
    check("明细缺失时网站成绩仍保留",
          len(bare_merged) == 5
          and all(r["source"] == "site" and r["timems"] for r in bare_merged))


def test_scope_site_top_filter():
    games = [top_game("b", 10 + i, 5000 + i * 100,
                      "nf" if i % 2 else "fl", size=8000 + i)
             for i in range(5)]
    paths = {g["path"] for g in games}
    records = {}
    records.update(dict([top_record("b", "time", [1000, 1100, 1200, 1300, 1400],
                                    start=11)]))
    records.update(dict([top_record("b", "bvs", [20, 19, 18, 17, 16], start=21)]))
    records.update(dict([top_record("b", "stnb", [10, 9, 8, 7, 6], start=31)]))

    skipped = set()
    got = openms.scope_paths(games, "top", site_top=records, skipped=skipped)
    check("网站三项都满五条更好时，本地 Top5 全被排除",
          not got and skipped == paths)

    short = dict(records)
    short.pop(top_field("b", "time"))
    short.update(dict([top_record("b", "time", [1000, 1100, 1200, 1300],
                                  start=11)]))
    got2 = openms.scope_paths(games, "top", site_top=short)
    check("网站不足五条时保留本地局", got2 == paths)

    nf_games = [top_game("b", 10 + i, 5000 + i * 100, "nf", size=9000 + i)
                for i in range(5)]
    got_nf = openms.scope_paths(nf_games, "top_nf", site_top=records)
    check("网站只有 std 榜时不压 NF Top5", got_nf == {g["path"] for g in nf_games})

    records_nf = dict([top_record("b", "time", [1000, 1100, 1200, 1300, 1400],
                                  nf=True, start=41)])
    records_nf.update(dict([top_record("b", "bvs", [20, 19, 18, 17, 16],
                                       nf=True, start=51)]))
    records_nf.update(dict([top_record("b", "stnb", [10, 9, 8, 7, 6],
                                       nf=True, start=61)]))
    got_nf2 = openms.scope_paths(nf_games, "top_nf", site_top=records_nf)
    check("网站 NF 榜满五条更好时排除 NF 本地局", not got_nf2)

    check("同值挤不进前五（time 用严格小于）",
          not core.top_beats({"timems": 1400}, "time", 1400)
          and core.top_beats({"timems": 1399}, "time", 1400))
    check("同值挤不进前五（bvs/stnb 用严格大于）",
          not core.top_beats({"bvs": 16.0}, "bvs", 16.0)
          and core.top_beats({"bvs": 16.001}, "bvs", 16.0))


def test_top_records_endpoint():
    tmp = tempfile.mkdtemp(prefix="ms-top-test-")
    cli = openms.Client(store=openms.Store(path=os.path.join(tmp, "openms.json")),
                        proxy=False)
    seen = {}

    def fake(path, retries=3, timeout=None):
        seen["path"] = path
        return 200, {"std:bt": {"videos": [{"video_id": 1, "value": 1000}]}}

    cli.get_json_retry = fake
    out = cli.top_records(42)
    check("top_records 打的是公开 Top5 接口",
          seen.get("path") == "/api/speedranking/top/player/42")
    check("top_records 原样返回字典",
          isinstance(out, dict) and out["std:bt"]["videos"][0]["value"] == 1000)
    cli.get_json_retry = lambda path, retries=3, timeout=None: (200, [])
    try:
        cli.top_records(42)
        check("Top5 非字典响应会报错", False)
    except openms.OmsError:
        check("Top5 非字典响应会报错", True)


def main():
    test_two_boards()
    test_merge_basic()
    test_merge_style_label()
    test_merge_ms_and_filter()
    test_scope_no_fl()
    test_scope_site_filter()
    test_pb_records_endpoint()
    test_site_top_rows()
    test_merge_site_top_rows()
    test_scope_site_top_filter()
    test_top_records_endpoint()
    bad = sum(1 for _l, ok in checks if not ok)
    print("== %s（%d 项）==" % ("全部通过" if not bad else "%d 项失败" % bad,
                               len(checks)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""读取网站记录的进度条：数据层回调 + 界面上的进度条。

跑法：python test_site_prog.py —— 全部通过退出码 0，否则 1。
覆盖：static_details 分批次回调 (已读 / 总数)、回调抛异常不影响读数、
      videos 把回调透传下去、界面上进度条在读的时候出现 / 切成百分比 /
      读完收回、标签写成「已读 / 总数」。

依赖本机录像目录（用默认数据源起一次窗口），找不到就跳过界面部分。
"""

import os
import time

import uitest  # 后台跑界面：窗口对用户透明，见 app.py 里 MS_PB_QUIET
import app as A
import cache
import openms
import paths

checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


# ---------- 数据层 ----------
def fake_oms():
    """只有 get_json_retry 的对象：static_details 只用到它。"""
    o = object.__new__(openms.Client)
    hits = []

    def fake_retry(path, retries=3, timeout=None):
        hits.append(path)
        ids = [int(x) for x in path.split("ids=")[1].split(",")]
        return 200, [{"id": i, "bv": 1, "timems": 1000} for i in ids]

    o.get_json_retry = fake_retry
    return o, hits


def test_static_details_progress():
    old_gap = openms.DETAIL_GAP
    openms.DETAIL_GAP = 0
    try:
        o, hits = fake_oms()
        seen = []
        n = openms.DETAIL_MAX_IDS * 2 + 50
        det = o.static_details(list(range(1, n + 1)),
                               progress=lambda d, t: seen.append((d, t)))
        check("明细都取回来了", len(det) == n)
        check("批次按 DETAIL_MAX_IDS 切", len(hits) == 3)
        check("先报一次 0 / 总数", seen[0] == (0, n))
        check("每批报一次已读数", [d for d, _ in seen] == [0, 100, 200, n])
        check("总数每次都一样", {t for _, t in seen} == {n})
        check("最后一批到 100%", seen[-1][0] == seen[-1][1])

        # 重复 ID 先去掉，进度按去重后的条数算
        o2, _ = fake_oms()
        seen2 = []
        o2.static_details([5, 5, 6], progress=lambda d, t: seen2.append((d, t)))
        check("重复 ID 只算一条", seen2[-1] == (2, 2))

        # 回调抛异常不能把读数拖死
        o3, _ = fake_oms()
        det3 = o3.static_details([1, 2],
                                 progress=lambda d, t: 1 / 0)
        check("回调抛异常照样读完", len(det3) == 2)

        # videos 要把它透传下去
        o4, _ = fake_oms()
        o4.get_json = lambda path, timeout=None: (200, [{"id": 7}, {"id": 8}])
        seen4 = []
        rows = o4.videos(9, progress=lambda d, t: seen4.append((d, t)))
        check("videos 透传回调", seen4 and seen4[-1] == (2, 2) and len(rows) == 2)
    finally:
        openms.DETAIL_GAP = old_gap


# ---------- 明细缓存 ----------
class DictCache:
    """cache 模块的替身：只实现 static_details 用到的两个方法。"""

    def __init__(self, known=None):
        self.rows = dict(known or {})
        self.puts = []

    def site_detail_get(self, ids):
        return {i: dict(self.rows[i]) for i in ids if i in self.rows}

    def site_detail_put(self, rows):
        self.puts.append(sorted(rows))
        self.rows.update(rows)
        return len(rows)


def test_detail_cache():
    old_gap = openms.DETAIL_GAP
    openms.DETAIL_GAP = 0
    try:
        # 命中缓存的不再发请求，进度从「已命中」开始
        o, hits = fake_oms()
        known = {i: {"id": i, "bv": 1, "timems": 1000}
                 for i in range(1, 201)}
        c = DictCache(known)
        seen = []
        det = o.static_details(list(range(1, 301)), cache=c,
                               progress=lambda d, t: seen.append((d, t)))
        check("缓存命中的不发请求", len(hits) == 1)
        check("只请求缺的那些", hits == ["/api/video/staticdetailbatch?ids=%s"
                                   % ",".join(str(i) for i in range(201, 301))])
        check("进度从已命中开始", seen[0] == (200, 300))
        check("最后一格到 100%", seen[-1] == (300, 300))
        check("结果里缓存和新取的都在", set(det) == set(range(1, 301)))
        check("新取的写回缓存", c.puts and c.puts[-1] == list(range(201, 301)))

        # 全命中：一个请求都不发
        o2, hits2 = fake_oms()
        c2 = DictCache(det)
        o2.static_details(list(range(1, 301)), cache=c2)
        check("全命中时零请求", hits2 == [])

        # 缓存坏了不能拖死读数：照样全量去取
        class Broken:
            def site_detail_get(self, ids):
                raise RuntimeError("坏了")

            def site_detail_put(self, rows):
                raise RuntimeError("还是坏的")

        o3, hits3 = fake_oms()
        det3 = o3.static_details([1, 2, 3], cache=Broken())
        check("缓存抛异常照样读完", len(det3) == 3 and len(hits3) == 1)
    finally:
        openms.DETAIL_GAP = old_gap


def test_realdb():
    """真库：写入 / 读出 / 存盘后重启还在 / 清空。"""
    import shutil
    import tempfile

    old_env = os.environ.get(paths.ENV_OVERRIDE)
    tmp = tempfile.mkdtemp(prefix="ms-site-cache-")
    try:
        os.environ[paths.ENV_OVERRIDE] = tmp
        paths._cache = None
        cache._site_conn = None
        cache._site_ram = None

        check("按 ID 取到", cache.site_detail_get([7, 8]) == {})
        n = cache.site_detail_put({7: {"id": 7, "timems": 1234},
                                   8: {"id": 8, "bv": 42}})
        check("写了两条", n == 2)
        got = cache.site_detail_get([7, 8, 9])
        check("读回来对得上", got == {7: {"id": 7, "timems": 1234},
                                      8: {"id": 8, "bv": 42}})
        check("读到的是副本", (got[7].update({"x": 1}), cache.site_detail_get([7])[7])
              == (None, {"id": 7, "timems": 1234}))

        # 模拟重启：连接和内存都丢掉，只看磁盘
        cache._site_conn.close()
        cache._site_conn = None
        cache._site_ram = None
        check("重启后还在", cache.site_detail_get([7])[7]["timems"] == 1234)
        check("行数对", cache.site_detail_rows() == 2)

        cache.site_detail_clear()
        check("清空后没了", cache.site_detail_get([7, 8]) == {})
    finally:
        try:
            if cache._site_conn is not None:
                cache._site_conn.close()
        except Exception:                            # noqa: BLE001
            pass
        cache._site_conn = None
        cache._site_ram = None
        if old_env is None:
            os.environ.pop(paths.ENV_OVERRIDE, None)
        else:
            os.environ[paths.ENV_OVERRIDE] = old_env
        paths._cache = None
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 界面 ----------
def pump(win, cond, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        win.update()
        if cond():
            return True
        time.sleep(0.02)
    return False


class SlowClient:
    """录像列表分三段回来，模拟几千条明细要串行拉的场景。"""

    def __init__(self):
        self.total = 260
        self.done = 0

        class _Store:
            def username(self):
                return "tester"

            def session(self):
                return ""

        self.store = _Store()

    def has_session(self):
        return True

    def whoami(self):
        return {"id": 1, "username": "tester", "realname": "x"}

    def videos(self, uid, progress=None, cache=None):
        self.cache_arg = cache          # 界面必须把明细缓存传下来
        for d in (0, 120, 260):
            if progress:
                progress(d, self.total)
            time.sleep(0.12)
        return [{"id": i} for i in range(self.total)]

    def identifiers(self, uid):
        return ["tester"]

    def pb_records(self, uid):
        return []

    def pb_counts(self):
        return {}

    def top_records(self, uid):
        return {}

    def top_anchors(self):
        return {}


def test_ui():
    win = A.App()
    try:
        assert pump(win, lambda: not win._scanning and bool(win.games)), "首次扫描没完成"
        win.oms = SlowClient()
        win.oms_user = {"id": 1, "username": "tester"}
        win._rebuild_up_rows()
        win.show_tab("upload")          # 进度条挂在上传页里，页面不显示就看不见
        win.update()
        # 启动时那次「恢复会话」是定时器里发起的，等它跑完再动手，
        # 否则它会把 _site_seq 推进一格，让下面的手写事件全被判成过期
        assert pump(win, lambda: win.oms_loaded, 30), "启动时的会话恢复没跑完"
        assert pump(win, lambda: not win._site_prog_on, 10), "启动时的进度条没收起"

        check("平时不占地方", not win.site_prog.winfo_ismapped())

        win._site_seq += 1
        seq = win._site_seq
        win._on_site_prog(seq, -1, 0)
        win.update()
        check("开读时进度条出现", win.site_prog.winfo_ismapped())
        check("总数未知时先来回跑", str(win.pb_site.cget("mode")) == "indeterminate")
        check("总数未知时不写数字", win.lbl_site_prog.cget("text") == "")

        win._on_site_prog(seq, 120, 260)
        win.update()
        check("知道总数后切成百分比", str(win.pb_site.cget("mode")) == "determinate")
        check("标签写「已读 / 总数」", win.lbl_site_prog.cget("text") == "120 / 260")
        pct = int(float(win.pb_site.cget("value")))
        check("百分比算对（120/260≈46）", pct == 46)

        win._on_site_prog(seq, -2, 0)
        win.update()
        check("读完收起来", not win.site_prog.winfo_ismapped())
        check("读完不留数字", win.lbl_site_prog.cget("text") == "")

        # 旧请求的收尾事件不能把新一轮的进度条关掉
        win._on_site_prog(seq - 1, -2, 0)
        win._on_site_prog(seq, -1, 0)
        win.update()
        check("过期事件不影响新一轮", win.site_prog.winfo_ismapped())
        win._on_site_prog(seq, -2, 0)

        # 走一遍真实链路：worker 线程 → 队列 → 界面
        win.refresh_site()
        check("读取中进度条亮着",
              pump(win, lambda: win.site_prog.winfo_ismapped(), 5))
        ok = pump(win, lambda: win.lbl_site_prog.cget("text") == "120 / 260", 8)
        check("后台线程的进度传到界面", ok)
        check("读完后收回",
              pump(win, lambda: not win.site_prog.winfo_ismapped(), 20))
        check("录像列表落到界面", len(win.oms_videos) == 260)
        check("界面上读网站记录会带上缓存", win.oms.cache_arg is cache)
    finally:
        try:
            win.destroy()
        except Exception:                            # noqa: BLE001
            pass


def main():
    test_static_details_progress()
    test_detail_cache()
    test_realdb()
    test_ui()

    bad = [c for c in checks if not c[1]]
    print("\n%d 项，%d 项失败" % (len(checks), len(bad)))
    for label, _ in bad:
        print("  FAIL", label)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

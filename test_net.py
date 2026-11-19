"""网站请求的连接方式：直连优先、连不上自动改走系统代理。

为什么有这一条：本机 `ProxyEnable=1`、代理是 127.0.0.1:10910（Clash 这类），
代理软件关掉或重启时空有配置、端口没人监听，urllib 照着注册表把请求发给它，
立刻 WinError 10061（目标计算机积极拒绝）。用户日志里 11-17、11-20 两次上传失败
都是这个。openms.top 是国内站，实测直连 0.15 s、走代理 0.21 s，直连本来就更好。

跑法：python test_net.py —— 全部通过退出码 0，否则 1。
覆盖：失败分类（拒绝连接 / 超时）、连不上换路并记住、GET 超时可换路而 POST 不行、
两条都不通才报错、proxy=False 只直连、proxy=None 只走代理、whoami 不再把网络
错误吞成「未登录」、videolist 遇 429 会重试。
"""

import http.server
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error

import openms

checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


def tmp_store():
    tmp = tempfile.mkdtemp(prefix="ms-net-test-")
    return openms.Store(path=os.path.join(tmp, "openms.json"))


class Fake(openms.Client):
    """不碰网络：按路线决定通还是不通。"""

    def __init__(self, fail=(), kind="conn", body=b'{"user": null}'):
        super().__init__(base="http://127.0.0.1:1", proxy="auto",
                         store=tmp_store())
        self.fail = set(fail)
        self.kind = kind
        self.body = body
        self.tried = []

    def _open(self, route, req, timeout):
        self.tried.append(route)
        if route in self.fail:
            reason = (ConnectionRefusedError(10061, "拒绝") if self.kind == "conn"
                      else socket.timeout("timed out"))
            raise openms.OmsError("网络错误：%s" % (reason,),
                                  kind=openms.Client._classify(reason))
        self._adopt(route)
        return 200, {}, self.body


# ---------- 失败分类 ----------
def test_classify():
    c = openms.Client._classify
    check("拒绝连接算 conn", c(ConnectionRefusedError(10061, "x")) == "conn")
    check("10061 的 OSError 算 conn", c(OSError(10061, "积极拒绝")) == "conn")
    check("DNS 失败算 conn", c(socket.gaierror("nope")) == "conn")
    check("源码里的 URLError 拆开看",
          c(urllib.error.URLError(ConnectionRefusedError(10061, "x"))) == "conn")
    check("超时算 timeout", c(socket.timeout("timed out")) == "timeout")
    check("TimeoutError 也算 timeout", c(TimeoutError("x")) == "timeout")
    check("其它 OSError 不换路", c(OSError(13, "denied")) == "other")


# ---------- 换路 ----------
def test_switch():
    cli = Fake(fail={"direct"})
    cli.whoami()                       # GET，直连不通 → 改走系统代理
    check("直连不通就试代理", cli.tried == ["direct", "system"])
    check("记住走得通的那条", cli.route == "system")
    check("记了一句说明", cli.route_note.startswith("网站请求改走系统代理"))

    cli.tried = []
    cli.whoami()
    check("下次直接从代理开始", cli.tried == ["system"])

    # 反过来：代理不通，直连通
    cli2 = Fake(fail={"system"})
    cli2.route = "system"              # 假装上次走的是代理
    cli2.whoami()
    check("代理不通退回直连", cli2.tried[-1] == "direct" and cli2.route == "direct")

    # 两条都不通才报错
    cli3 = Fake(fail={"direct", "system"})
    try:
        cli3.whoami()
        check("两条都不通要报错", False)
    except openms.OmsError as exc:
        check("两条都不通要报错", set(cli3.tried) == {"direct", "system"})
        check("报的是网络错误", "网络错误" in str(exc))


def test_timeout_rules():
    # GET 超时可以换路（站点没收到也没关系）
    cli = Fake(fail={"direct"}, kind="timeout")
    cli.whoami()
    check("GET 超时会换路", cli.tried == ["direct", "system"])

    # POST 超时不许换路：站点可能已经收下了，重发就是重复上传
    cli2 = Fake(fail={"direct"}, kind="timeout")
    try:
        cli2.request("/api/common/uploadvideo", data=b"x", method="POST")
        check("POST 超时不换路", False)
    except openms.OmsError as exc:
        check("POST 超时不换路", cli2.tried == ["direct"] and exc.kind == "timeout")

    # POST 被拒绝（请求没发出去）可以换路
    cli3 = Fake(fail={"direct"})
    cli3.request("/api/common/uploadvideo", data=b"x", method="POST")
    check("POST 被拒可以换路", cli3.tried == ["direct", "system"])


def test_modes():
    cli = Fake()
    cli.proxy_mode = False
    cli.whoami()
    check("proxy=False 只直连", cli.tried == ["direct"])

    cli2 = Fake()
    cli2.proxy_mode = None
    cli2.whoami()
    check("proxy=None 只走系统代理", cli2.tried == ["system"])

    cli3 = Fake()
    cli3.whoami()
    check("默认先直连", cli3.tried == ["direct"])


# ---------- 真网络（本地假站点） ----------
class H(http.server.BaseHTTPRequestHandler):
    plan = [(200, b"{}")]
    calls = []

    def do_GET(self):
        self.calls.append(self.path)
        i = min(len(self.calls) - 1, len(self.plan) - 1)
        code, body = self.plan[i]
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_real():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]

    # 匿名：loginauto 返回 {"user": null} → None，不报错
    H.plan, H.calls = [(200, b'{"user": null}')], []
    check("匿名返回 None 不报错",
          openms.Client(base=base, proxy=False, store=tmp_store()).whoami() is None)

    # 登录态：{"user": {...}} → 返回那个 dict
    H.plan, H.calls = [(200, b'{"user": {"id": 3, "username": "t"}}')], []
    u = openms.Client(base=base, proxy=False, store=tmp_store()).whoami()
    check("有登录态就返回用户", isinstance(u, dict) and u.get("id") == 3)

    # 断网：不再吞成 None，而要抛出来（否则界面会把网站记录清空、提示去登录）
    dead = openms.Client(base="http://127.0.0.1:1", proxy=False, store=tmp_store())
    t0 = time.monotonic()
    try:
        dead.whoami()
        check("断网时 whoami 要报错", False)
    except openms.OmsError as exc:
        check("断网时 whoami 要报错", "网络错误" in str(exc))
    # 本机对一个拒连端口单次尝试就要 2 s（有安全软件在中间），所以给宽一点，
    # 但不能超过「两条路各试一次」的量级
    check("断网不重试、不拖沓", time.monotonic() - t0 < 6)

    # videolist 遇 429 自动重试
    H.plan, H.calls = [(429, b""), (200, b'[{"id": 1}, {"id": 2}]')], []
    cli = openms.Client(base=base, proxy=False, store=tmp_store())
    rows = cli.videos(3)
    check("videolist 429 会重试", len(H.calls) >= 2 and len(rows) == 2)
    check("重试后拿到的行没被打乱", [r["id"] for r in rows] == [1, 2])

    srv.shutdown()


def main():
    test_classify()
    test_switch()
    test_timeout_rules()
    test_modes()
    test_real()

    bad = [c for c in checks if not c[1]]
    print("\n%d 项，%d 项失败" % (len(checks), len(bad)))
    for label, _ in bad:
        print("  FAIL", label)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

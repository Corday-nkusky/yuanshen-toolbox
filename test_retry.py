"""上传重试与错误分类的回归测试（本地假站点，不碰 openms.top）。

跑法：python test_retry.py  —— 全部通过退出码 0，否则 1。
覆盖：503/429/5xx 重试、403/402/实名/封禁的 fatal 标记、可重试错误重试耗尽后
      标 transient、collision 当成功、500 单条失败、断网抛 OmsError。
"""

import http.server
import os
import sys
import threading
import time

import openms

REPLAY = os.path.join(os.environ.get("APPDATA", ""), "Metasweeper3.3.4", "replay")


def pick_evf():
    if not os.path.isdir(REPLAY):
        print("跳过：找不到录像目录 %s" % REPLAY)
        sys.exit(0)
    for name in os.listdir(REPLAY):
        if name.lower().endswith(".evf"):
            return os.path.join(REPLAY, name)
    print("跳过：录像目录里没有 .evf")
    sys.exit(0)


class H(http.server.BaseHTTPRequestHandler):
    plan = [(200, b"{}")]
    calls = []

    def do_POST(self):
        # 必须把请求体读完再回应：否则客户端发到一半被断开，会以为是网络错误而重试
        try:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
        except Exception:
            pass
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


OK = (200, b'{"type":"success","data":{"state":"c","id":7}}')
E502 = (502, b"")
E503 = (503, b"")
E429 = (429, b"")
E403 = (403, b'{"type":"error","object":"userprofile","category":"not_login"}')
E402 = (402, b"")                 # 站点源码：额度满直接 HttpResponse(status=402)，无响应体
ERL = (200, b'{"type":"error","object":"userprofile","category":"realname_required"}')
EBAN = (200, b'{"type":"error","object":"userprofile","category":"banned"}')
ECOLL = (200, b'{"type":"error","object":"file","category":"collision"}')
E500 = (500, b"")

CASES = [
    # 名字,                响应序列,                 期望 ok, 期望 fatal, 期望请求数, 期望 transient
    ("503×2 后成功",       [E503, E503, OK],          True,  None,        3, False),
    ("502 后成功",         [E502, OK],                True,  None,        2, False),
    ("429 后成功",         [E429, OK],                True,  None,        2, False),
    ("一次成功",           [OK],                      True,  None,        1, False),
    ("重复 collision",     [ECOLL],                   True,  None,        1, False),
    ("403 登录失效",       [E403],                    False, "auth",      1, False),
    ("402 额度满（空响应）", [E402],                  False, "quota",     1, False),
    ("未实名",             [ERL],                     False, "realname",  1, False),
    ("被封禁",             [EBAN],                    False, "banned",    1, False),
    ("500 单条失败",       [E500],                    False, None,        1, True),
    ("429 重试耗尽",       [E429],                    False, None,        4, True),
    ("503 重试耗尽",       [E503],                    False, None,        4, True),
]


def main():
    evf = pick_evf()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]

    bad = 0
    for label, plan, want_ok, want_fatal, want_calls, want_transient in CASES:
        H.plan, H.calls = plan, []
        client = openms.Client(base=base, proxy=False)
        r = client.upload(evf, retries=3)
        got = (bool(r.get("ok")), r.get("fatal"), len(H.calls),
               bool(r.get("transient")))
        want = (want_ok, want_fatal, want_calls, want_transient)
        flag = "OK  " if got == want else "FAIL"
        if got != want:
            bad += 1
        print("%s %-16s 得到 %s 期望 %s" % (flag, label, got, want))

    # 断网：指向没人监听的端口，应抛 OmsError（不是静默返回失败）
    t = time.perf_counter()
    try:
        openms.Client(base="http://127.0.0.1:1", proxy=False).upload(evf, retries=1)
        print("FAIL 断网应抛 OmsError，实际没抛")
        bad += 1
    except openms.OmsError:
        print("OK   断网抛 OmsError（%.1fs）" % (time.perf_counter() - t))

    srv.shutdown()
    print("== %s ==" % ("全部通过" if not bad else "%d 项失败" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

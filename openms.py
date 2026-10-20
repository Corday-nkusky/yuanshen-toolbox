"""开源扫雷网（openms.top）上传客户端。

纯逻辑模块：不 import tkinter，可单独测试。

站点是 MIT 开源（github.com/eee555/saolei_website），接口经源码 + 实测双向确认：

    取验证码   GET  /userprofile/refresh_captcha/      -> {"hashkey": ...}
    验证码图   GET  /userprofile/captcha/image/<key>/  -> 100x30 PNG
    登录       POST /userprofile/login/                -> Set-Cookie: session_id
    登录态     GET  /api/common/loginauto              -> {"user": {...} | null}
    我的录像   GET  /api/userprofile/videolist?user_id=N
    我的标识   GET  /api/userprofile/identifier?user_id=N
    上传       POST /common/uploadvideo/               （注意不在 /api 下）

登录只在「登录那一刻」需要验证码；之后靠 session_id Cookie 维持，站点支持
最长 90 天且每次请求续期（SESSION_SAVE_EVERY_REQUEST）。所以长期免登录的正解是
**保存 Cookie**，密码只用于自动填表（存 DPAPI 密文）。
"""

import base64
import ctypes
import http.cookiejar
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "https://openms.top"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) ms-pb-viewer/0.5 Safari/537.36")

import paths


def store_path():
    """凭据文件位置：默认在 exe 旁边的数据目录，方便用户一键删除。"""
    return paths.store_path()

# 站点 state 码
STATE_CN = {
    "c": "已通过",
    "a": "待审核",
    "b": "已冻结",
    "d": "标识不符",
    "e": "外部审核",
}

# 上传失败原因（object/obj + category 组合）
REASON_CN = {
    ("file", "collision"): "网站已有同一录像",
    ("file", "incomplete"): "未完成局",
    ("file", "type"): "格式不支持",
    ("file", "level"): "级别异常",
    ("file", "review"): "审核码异常",
    ("file", "size"): "文件过大",
    ("identifier", "verify"): "标识含违规词",
    ("rust", "panic"): "录像解析崩溃",
    ("userprofile", "realname_required"): "账号未实名",
    ("userprofile", "banned"): "账号被封禁",
}


class OmsError(Exception):
    def __init__(self, msg, status=None, payload=None):
        super().__init__(msg)
        self.status = status
        self.payload = payload or {}


# ---------------------------------------------------------------- DPAPI
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_in(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def dpapi_encrypt(text):
    """用 Windows DPAPI 加密（绑定当前用户 + 本机，换机器解不开）。"""
    raw = text.encode("utf-8")
    blob, _keep = _blob_in(raw)
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob), "ms-pb-viewer", None, None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI 加密失败")
    try:
        return base64.b64encode(ctypes.string_at(out.pbData, out.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def dpapi_decrypt(b64):
    raw = base64.b64decode(b64)
    blob, _keep = _blob_in(raw)
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob), None, None, None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI 解密失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


# ---------------------------------------------------------------- 本地存储
class Store:
    """<数据目录>\\openms.json（默认在 exe 旁边的 元神工具箱-data\\）

    只落三样东西：用户名、DPAPI 密文密码、DPAPI 密文 session_id。
    没有明文凭据；密文拷到别的电脑也解不开。
    """

    def __init__(self, path=None):
        self.path = path or store_path()
        self.data = self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # --- 字段访问 ---
    def username(self):
        return self.data.get("username") or ""

    def password(self):
        enc = self.data.get("pass_enc")
        if not enc:
            return ""
        try:
            return dpapi_decrypt(enc)
        except Exception:
            return ""

    def session(self):
        enc = self.data.get("session_enc")
        if not enc:
            return ""
        try:
            return dpapi_decrypt(enc)
        except Exception:
            return ""

    def set_credentials(self, username, password):
        self.data["username"] = username or ""
        if password:
            try:
                self.data["pass_enc"] = dpapi_encrypt(password)
            except Exception:
                self.data.pop("pass_enc", None)
        self.save()

    def set_session(self, session_id, days=90):
        if session_id:
            try:
                self.data["session_enc"] = dpapi_encrypt(session_id)
            except Exception:
                self.data.pop("session_enc", None)
        else:
            self.data.pop("session_enc", None)
        self.data["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.data["expiry_days"] = days
        self.save()

    def clear_session(self):
        self.data.pop("session_enc", None)
        self.save()

    def clear_all(self):
        self.data = {}
        self.save()


# ---------------------------------------------------------------- 工具
def _ca_bundles():
    """按优先级找 CA 包：随程序打包的 → 程序目录 → 解释器自带 certifi。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        os.path.join(getattr(sys, "_MEIPASS", here), "cacert.pem"),
        os.path.join(os.path.dirname(sys.executable), "cacert.pem"),
        os.path.join(here, "cacert.pem"),
    ]
    try:
        import certifi
        cands.append(certifi.where())
    except Exception:
        pass
    return [p for p in cands if p and os.path.isfile(p)]


def make_ssl_context():
    """内置 CA 包优先。

    本机实测踩坑：Windows 根证书库里有一张已过期的交叉签证书，OpenSSL 会选中它，
    导致连 openms.top 报 "certificate has expired"（而系统浏览器/curl 都正常）。
    带一份 certifi 的 cacert.pem 就把这个问题绕开了，且不依赖用户机器的信任库状态。
    """
    for path in _ca_bundles():
        try:
            return ssl.create_default_context(cafile=path), path
        except Exception:
            continue
    return ssl.create_default_context(), "system"


def _json(raw):
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {}


def _multipart(fields, files):
    """手搓 multipart：不引入 requests，只依赖标准库。"""
    boundary = "----msPbViewer" + uuid.uuid4().hex
    out = bytearray()
    for k, v in (fields or {}).items():
        out += ("--%s\r\n" % boundary).encode()
        out += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode()
        out += str(v).encode() + b"\r\n"
    for k, (fname, content) in (files or {}).items():
        safe = str(fname).replace('"', "").replace("\r", "").replace("\n", "")
        out += ("--%s\r\n" % boundary).encode()
        out += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                % (k, safe)).encode("utf-8")
        out += b"Content-Type: application/octet-stream\r\n\r\n"
        out += content + b"\r\n"
    out += ("--%s--\r\n" % boundary).encode()
    return bytes(out), "multipart/form-data; boundary=%s" % boundary


def video_key(timems, bv, file_size):
    """站点去重用的三元组：timems + bv + file_size（再逐字节比对）。

    file_size 实测吻合：本地 7330 字节的录像，站点记录也是 7330。
    """
    try:
        return (int(timems), int(bv), int(file_size))
    except (TypeError, ValueError):
        return None


def game_key(game):
    """本地完成局 -> 去重键。"""
    try:
        size = os.path.getsize(game["path"])
    except OSError:
        return None
    # 优先用录像里读出的整数毫秒；退回 round(秒×1000) 会和站点差 ±1 ms
    tms = game.get("timems")
    if tms is None:
        tms = round(float(game["time"]) * 1000)
    return video_key(tms, game["bv"], size)


def server_key(video):
    return video_key(video.get("timems"), video.get("bv"), video.get("file_size"))


# ---------------------------------------------------------------- 客户端
class Client:
    def __init__(self, base=BASE, timeout=30, store=None, proxy=None):
        """proxy: None=沿用系统/环境变量代理；False=强制直连（排障用）。"""
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.store = store if store is not None else Store()
        self.jar = http.cookiejar.CookieJar()
        self.ssl_ctx, self.ca_source = make_ssl_context()
        handlers = [urllib.request.HTTPCookieProcessor(self.jar),
                    urllib.request.HTTPSHandler(context=self.ssl_ctx)]
        if proxy is False:
            handlers.insert(0, urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)
        self.user = None
        sid = self.store.session()
        if sid:
            self.set_session_cookie(sid)

    # --- Cookie ---
    def set_session_cookie(self, value):
        c = http.cookiejar.Cookie(
            version=0, name="session_id", value=value, port=None, port_specified=False,
            domain="openms.top", domain_specified=True, domain_initial_dot=False,
            path="/", path_specified=True, secure=True, expires=None, discard=False,
            comment=None, comment_url=None, rest={}, rfc2109=False)
        self.jar.set_cookie(c)

    def session_id(self):
        for c in self.jar:
            if c.name == "session_id":
                return c.value
        return ""

    def has_session(self):
        return bool(self.session_id())

    # --- 底层请求 ---
    def request(self, path, data=None, method=None, ctype=None, timeout=None):
        url = path if path.startswith("http") else self.base + path
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", UA)
        req.add_header("Referer", self.base + "/")
        req.add_header("Accept", "*/*")
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            try:
                body = e.read()
            except Exception:
                body = b""
            return e.code, dict(e.headers or {}), body
        except (urllib.error.URLError, OSError) as e:
            raise OmsError("网络错误：%s" % (getattr(e, "reason", e),)) from e

    def get_json(self, path, timeout=None):
        status, _h, raw = self.request(path, timeout=timeout)
        return status, _json(raw)

    # --- 登录链 ---
    def refresh_captcha(self):
        """返回 {"hashkey": str, "png": bytes}。"""
        status, p = self.get_json("/userprofile/refresh_captcha/")
        hk = p.get("hashkey")
        if not hk:
            raise OmsError("取验证码失败（HTTP %s）" % status, status, p)
        status, _h, png = self.request("/userprofile/captcha/image/%s/" % hk)
        if not png:
            raise OmsError("验证码图片为空（HTTP %s）" % status, status)
        return {"hashkey": hk, "png": png}

    def login(self, username, password, captcha, hashkey, days=90):
        """返回 (ok, payload)。失败时验证码已被消费，须重新取。"""
        body = urllib.parse.urlencode({
            "username": (username or "").strip(),
            "password": password or "",
            "captcha": (captcha or "").strip(),
            "hashkey": hashkey,
            "set_expiry": int(days),
        }).encode()
        status, hdrs, raw = self.request(
            "/userprofile/login/", data=body, method="POST",
            ctype="application/x-www-form-urlencoded")
        p = _json(raw)
        if p.get("type") == "success":
            self.user = p.get("user") or {}
            self.store.set_session(self.session_id(), days)
            self.store.set_credentials(username, password)
            return True, p
        if not p:
            p = {"type": "error", "object": "login",
                 "category": "http_%s" % status}
        return False, p

    def whoami(self):
        """登录态校验。匿名时返回 None（站点返回 {"user": null}）。"""
        try:
            status, p = self.get_json("/api/common/loginauto")
        except OmsError:
            return None
        user = p.get("user") if isinstance(p, dict) else None
        self.user = user or None
        return self.user

    def logout(self):
        try:
            self.request("/userprofile/logout/")
        except Exception:
            pass
        self.jar.clear()
        self.user = None
        self.store.clear_session()

    # --- 数据 ---
    def videos(self, user_id):
        status, p = self.get_json("/api/userprofile/videolist?user_id=%d" % int(user_id))
        if isinstance(p, list):
            return p
        raise OmsError("读取网站录像失败（HTTP %s）" % status, status, p)

    def identifiers(self, user_id):
        status, p = self.get_json("/api/userprofile/identifier?user_id=%d" % int(user_id))
        if isinstance(p, list):
            return p
        raise OmsError("读取标识失败（HTTP %s）" % status, status, p)

    # --- 上传 ---
    def upload(self, path, retries=3):
        """上传单个录像，返回归一化结果字典。

        归一化字段：
            ok      bool      是否成功（collision 也算成功）
            state   str|None  站点审核状态（c/a/b/d/e）
            vid     int|None  站点录像 id
            reason  str       失败原因（中文）
            fatal   str|None  auth / quota / banned / realname
                              命中这些是账号级问题，应停止整批
            transient bool    网络类失败（重试后仍失败）；单条问题，不影响整批

        网络异常在重试耗尽后仍会抛 OmsError —— 由调用方决定是继续还是整批停。
        """
        try:
            with open(path, "rb") as fh:
                content = fh.read()
        except OSError as e:
            return {"ok": False, "reason": "读取失败：%s" % e, "fatal": None}

        body, ctype = _multipart({"skipTournamentCheck": "false"},
                                 {"file": (os.path.basename(path), content)})
        # 单条上传内部重试：断网/超时按 1s、2s、4s 退避；503/429 听 Retry-After。
        # 重试只针对「可能自己好起来」的错误，403/402 这类直接交给上层判定。
        attempt = 0
        while True:
            attempt += 1
            try:
                status, hdrs, raw = self.request("/common/uploadvideo/", data=body,
                                                 method="POST", ctype=ctype, timeout=90)
            except OmsError:
                if attempt > retries:
                    raise
                time.sleep(min(2.0 ** (attempt - 1), 8.0))
                continue
            if status in (502, 503, 504, 429) and attempt <= retries:
                wait = 1.0
                try:
                    wait = max(0.5, float(hdrs.get("Retry-After") or 1))
                except (TypeError, ValueError):
                    pass
                time.sleep(min(wait, 8.0))
                continue
            break

        if status == 403:
            return {"ok": False, "reason": "登录已失效（403）", "fatal": "auth"}
        if status == 402:
            return {"ok": False, "reason": "录像配额已满（402）", "fatal": "quota"}
        if status == 429:
            return {"ok": False, "reason": "请求过快被限流（429）", "fatal": None}
        if status == 413:
            return {"ok": False, "reason": "文件过大（413）", "fatal": None}

        p = _json(raw)
        if not p:
            return {"ok": False, "reason": "HTTP %s，响应无法解析" % status,
                    "fatal": "auth" if status in (401,) else None, "status": status}

        if p.get("type") == "success":
            data = p.get("data") or {}
            return {"ok": True, "state": data.get("state"), "vid": data.get("id"),
                    "reason": "", "fatal": None, "payload": p}

        obj = p.get("object") or p.get("obj") or ""
        cat = p.get("category") or ""
        reason = REASON_CN.get((obj, cat)) or ("%s/%s" % (obj, cat) if obj or cat else "未知错误")
        if (obj, cat) == ("file", "collision"):
            return {"ok": True, "state": None, "vid": None, "reason": reason,
                    "fatal": None, "payload": p, "collision": True}
        fatal = None
        if cat in ("realname_required", "banned"):
            fatal = "banned" if cat == "banned" else "realname"
        return {"ok": False, "reason": reason, "fatal": fatal, "payload": p}


# ---------------------------------------------------------------- 选范围
SCOPES = [
    ("pb", "PB·全部风格"),
    ("pb_nf", "PB·NF"),
    ("pb_fl", "PB·FL"),
    ("top", "前端 Top5·全部"),
    ("top_nf", "前端·NF"),
    ("top_fl", "前端·FL"),
    ("all", "全部完成局"),
]


def scope_paths(games, scope, bvmin=None):
    """某范围对应的录像路径集合。bvmin=None 时用官方门槛。"""
    import core

    if scope.startswith("pb"):
        style = {"pb": None, "pb_nf": "nf", "pb_fl": "fl"}[scope]
        return {r["path"] for r in core.build_pb(games, style=style)}

    if scope.startswith("top"):
        style = {"top": None, "top_nf": "nf", "top_fl": "fl"}[scope]
        out = set()
        for lv in core.LEVELS:
            floor = core.BV_MIN_DEFAULT[lv] if bvmin is None else bvmin
            for metric in ("time", "bvs", "stnb"):
                for g in core.top5(games, lv, metric, floor, style=style):
                    out.add(g["path"])
        return out

    return {g["path"] for g in games}

"""日志、崩溃兜底、诊断包。

`--windowed` 打包后进程没有 stdout/stderr：脚本里一个未捕获异常，用户看到的是
「窗口闪一下就没了」，开发者手上什么都没有。所以这里做三件事：

1. 把日志写进 <数据目录>\\logs\\app.log（1 MB 滚动 3 份）；
2. 接管 sys.excepthook / threading.excepthook / Tk 的 report_callback_exception，
   崩溃时落一份 crash-<时间>.txt，再弹一个**带路径**的提示框；
3. 导出诊断包（zip）：日志 + 环境信息 + 脱敏后的配置，用户直接发过来就能查。

凭据只输出「有没有、用户名是谁」，绝不含密码密文 / session 密文。
"""

import io
import logging
import logging.handlers
import os
import platform
import sys
import threading
import time
import traceback
import zipfile

import paths

LOG_NAME = "app.log"
MAX_BYTES = 1 << 20
BACKUPS = 3

log = logging.getLogger("ms_pb")
_started = False


def setup():
    """安装日志。幂等，重复调用无副作用。"""
    global _started
    if _started:
        return log
    _started = True
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        path = os.path.join(paths.log_dir(), LOG_NAME)
        fh = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
        log.addHandler(fh)
    except OSError:
        pass
    if not getattr(sys, "frozen", False):
        # 源码运行时顺便打到控制台
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        log.addHandler(sh)
    return log


# ------------------------------------------------------------------ 环境信息
def system_info():
    import tkinter

    # 不 import app（会形成循环依赖），从已加载的模块里取标题和版本
    app_mod = sys.modules.get("app")
    title = getattr(app_mod, "APP_TITLE", "元神工具箱")
    ver = getattr(app_mod, "APP_VER", "")
    lines = [
        (f"{title} v{ver}" if ver else title),
        f"时间        {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"frozen      {bool(getattr(sys, 'frozen', False))}",
        f"exe         {sys.executable}",
        f"数据目录    {paths.data_dir()}",
        f"便携模式    {paths.is_portable()}",
        f"Python      {sys.version.replace(chr(10), ' ')}",
        f"Tk          {tkinter.TkVersion}",
        f"OS          {platform.platform()}",
        f"架构        {platform.machine()} / {platform.architecture()[0]}",
    ]
    try:
        import ctypes
        user32 = ctypes.windll.user32
        lines.append(f"屏幕        {user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}")
        try:
            dpi = user32.GetDpiForSystem()
        except Exception:
            hwnd = user32.GetForegroundWindow()
            dpi = user32.GetDpiForWindow(hwnd) if hwnd else 0
        if dpi:
            lines.append(f"DPI         {dpi}（{dpi / 96.0:.0%}）")
    except Exception:
        pass
    try:
        import ms_toollib                                   # noqa: F401
        from importlib.metadata import version
        try:
            lines.append(f"ms_toollib  {version('ms-toollib')}")
        except Exception:
            lines.append("ms_toollib  已加载（版本号未标注）")
    except Exception as exc:
        lines.append(f"ms_toollib  不可用（{type(exc).__name__}）")
    return "\n".join(lines)


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def credentials_summary():
    """凭据的「有没有」摘要，不含任何密文。"""
    import json
    path = paths.store_path()
    if not os.path.isfile(path):
        return "openms.json 不存在（未登录过）"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception as exc:
        return f"openms.json 读取失败：{type(exc).__name__}"
    out = [f"openms.json  {path}",
           f"  用户名      {d.get('username') or '（无）'}",
           f"  有密码密文  {'是' if d.get('pass_enc') else '否'}",
           f"  有会话密文  {'是' if d.get('session_enc') else '否'}"]
    return "\n".join(out)


# ------------------------------------------------------------------ 崩溃兜底
def _write_crash(where, exc_type, exc, tb):
    try:
        path = os.path.join(paths.log_dir(),
                            time.strftime("crash-%Y%m%d-%H%M%S.txt"))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(system_info() + "\n\n")
            fh.write(f"位置：{where}\n\n")
            fh.write("".join(traceback.format_exception(exc_type, exc, tb)))
        return path
    except Exception:
        return ""


def _alert(title, message):
    """崩溃提示。优先用 ctypes，不依赖 Tk 还能不能用。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)   # MB_ICONERROR
        return
    except Exception:
        pass
    try:
        from tkinter import messagebox
        messagebox.showerror(title, message)
    except Exception:
        pass


def _fire(where, exc_type, exc, tb):
    log.error("%s 未捕获异常：\n%s", where,
              "".join(traceback.format_exception(exc_type, exc, tb)))
    path = _write_crash(where, exc_type, exc, tb)
    msg = ("程序遇到未处理的错误，需要关闭。\n\n"
           f"{where}\n{exc_type.__name__}: {exc}\n\n"
           + (f"崩溃详情已写入：\n{path}\n\n" if path else "")
           + f"下次启动可在「诊断…」里导出诊断包，\n把整个文件夹发给作者即可定位问题。")
    _alert("程序错误", msg)


def install_hooks(get_title=lambda: "元神工具箱"):
    """接管主线程 / 子线程 / Tk 回调三处异常。

    返回给 Tk 用的 report_callback_exception 实现（由 App 绑定到实例上）。
    """
    old_hook = sys.excepthook

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            old_hook(exc_type, exc, tb)
            return
        _fire("主线程", exc_type, exc, tb)

    sys.excepthook = hook

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        _fire(f"线程 {getattr(args.thread, 'name', '?')}",
              args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = thread_hook

    def tk_callback(exc_type, exc, tb):
        # Tk 回调里的异常默认只打 stderr（windowed 下等于没有），这里落盘 + 提示
        _fire("界面回调", exc_type, exc, tb)

    return tk_callback


# ------------------------------------------------------------------ 诊断包
def export_bundle(target):
    """把日志 + 环境 + 脱敏配置打成一个 zip，返回路径。"""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("system.txt", system_info() + "\n\n" + credentials_summary() + "\n")
        cfg = paths.config_path()
        if os.path.isfile(cfg):
            z.writestr("config.json", _read_text(cfg))
        logs = paths.log_dir()
        try:
            names = sorted(os.listdir(logs))
        except OSError:
            names = []
        for name in names:
            full = os.path.join(logs, name)
            if not os.path.isfile(full):
                continue
            if name.endswith(".log") or (name.startswith("crash-") and name.endswith(".txt")):
                z.write(full, os.path.join("logs", name))
    return target


def recent_log(tail=200):
    """给「诊断」窗口显示最近的日志行。"""
    path = os.path.join(paths.log_dir(), LOG_NAME)
    text = _read_text(path)
    if not text:
        return "（还没有日志）"
    return "\n".join(text.splitlines()[-tail:])

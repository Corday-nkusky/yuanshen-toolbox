"""运行时数据目录（配置 / 登录凭据 / 日志 / 诊断包）。

设计目标：**用户能一键清干净**。所以默认全部落在 exe 同目录的一个文件夹里，
而不是散在 %APPDATA%。删掉那个文件夹 = 回到全新状态（含登录态、目录记忆、日志）。

    <exe 所在目录>\\元神工具箱-data\\
        config.json      录像目录等界面设置
        openms.json      openms.top 用户名 + DPAPI 密文（密码 / session_id）
        uploads.json     上传台账（断点续传用）
        scan-cache.db    录像解析结果缓存（几万个录像时靠它把重扫压到 1 秒）
        logs\\app.log     滚动日志（1 MB × 3）
        logs\\crash-*.txt  崩溃时的完整 traceback

三种情况按顺序判定：
  1. 环境变量 MS_PB_DATA_DIR 指定了目录 → 用它（测试和排障用）；
  2. exe 同目录可写 → 用 <exe 目录>\\元神工具箱-data；
  3. 不可写（典型：被放进 C:\\Program Files）→ 回落到 %APPDATA%\\元神工具箱。
"""

import os
import sys

DATA_DIR_NAME = "元神工具箱-data"
ROAMING_DIR_NAME = "元神工具箱"
ENV_OVERRIDE = "MS_PB_DATA_DIR"

_cache = None


def base_dir():
    """exe 所在目录；源码运行时是源码目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def portable_dir():
    return os.path.join(base_dir(), DATA_DIR_NAME)


def roaming_dir():
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(root, ROAMING_DIR_NAME)


def _writable(path):
    """真去写一个文件再删掉 —— 只看 os.access 在 Windows 上不可靠。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write-probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


def data_dir():
    """返回数据目录（首次调用时决定位置）。"""
    global _cache
    if _cache:
        return _cache
    env = os.environ.get(ENV_OVERRIDE)
    cand = os.path.abspath(env) if env else portable_dir()
    if os.path.isdir(cand) or _writable(cand):
        path = cand
    else:
        path = roaming_dir()
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = roaming_dir()
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
    _cache = path
    return path


def config_path():
    return os.path.join(data_dir(), "config.json")


def store_path():
    return os.path.join(data_dir(), "openms.json")


def log_dir():
    path = os.path.join(data_dir(), "logs")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def is_portable():
    """数据目录是否就在 exe 旁边（用于界面提示「删这个文件夹即可」）。"""
    try:
        return os.path.normcase(os.path.dirname(data_dir())) == \
            os.path.normcase(os.path.abspath(base_dir()))
    except Exception:
        return False

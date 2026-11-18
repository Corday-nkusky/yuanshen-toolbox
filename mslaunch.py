"""找到并启动元扫雷主程序（Windows）。

为什么不能只写死一个路径：元扫雷装在哪完全看用户的安装方式 —— 默认在
``C:\\Program Files\\Metasweeper\\metasweeper\\metasweeper.exe``，但也有人装在
``%LOCALAPPDATA%\\Programs``、或者干脆拷到 D 盘当绿色版用。

所以顺序是：
    配置里记过的 → 正在跑的进程反查 → 默认安装位置扫一遍 → 让用户自己指一次
「正在跑的进程反查」很有用：用户从开始菜单自己开了一次，我们就能顺着进程拿到
真实路径，下次不用再问。

本模块不弹任何窗、不依赖 tkinter：失败就返回原因，由界面决定怎么说。
"""

import ctypes
import os
import socket
import subprocess
from ctypes import wintypes as _w

# 认进程 / 认文件名都按这个前缀，大小写无关
_NAME_PREFIX = "metasweeper"
_EXE = "metasweeper.exe"

# 启动后多久之内还不算「没起来」—— 元扫雷冷启动可能要几秒
START_GRACE = 20.0


# ------------------------------------------------------------------ 找进程
class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", _w.DWORD),
        ("cntUsage", _w.DWORD),
        ("th32ProcessID", _w.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", _w.DWORD),
        ("cntThreads", _w.DWORD),
        ("th32ParentProcessID", _w.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", _w.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _kernel32():
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.OpenProcess.restype = ctypes.c_void_p
    return k


def _image_path(pid):
    """进程的完整 exe 路径；权限不够时返回空串（不影响「在不在跑」的判断）。"""
    k = _kernel32()
    h = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = _w.DWORD(1024)
        if k.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf,
                                        ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        k.CloseHandle(ctypes.c_void_p(h))


def find_running():
    """元扫雷在不在跑。返回 ``(pid, exe 全路径)`` 或 ``None``。

    路径可能拿不到（对方提权、或系统拒绝查询），这时第二项是空串，
    调用方仍可以据此判断「已经开着，别再开一个」。
    """
    if os.name != "nt":
        return None
    k = _kernel32()
    snap = k.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return None
    try:
        ent = _PROCESSENTRY32W()
        ent.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = k.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(ent))
        while ok:
            if ent.szExeFile.lower().startswith(_NAME_PREFIX):
                return (int(ent.th32ProcessID), _image_path(ent.th32ProcessID))
            ok = k.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(ent))
        return None
    finally:
        k.CloseHandle(ctypes.c_void_p(snap))


# ------------------------------------------------------------------ 找安装位置
def port_open(port, host="127.0.0.1", timeout=0.08):
    """本机这个端口有没有人在监听。

    用来判断「元扫雷是已经起来了（插件服务在监听）还是还在启动」：
    起来之后再起一个进程只会把路径转交给它然后退出，是安全的；
    还在启动的时候再起一个，两个会抢同一组插件端口，后来者直接崩。

    连不上是常态（端口没开），所以这里只做一次非阻塞式的尝试，不会卡住。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, int(port))) == 0
    except OSError:
        return False
    finally:
        s.close()


def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return ""


def search_bases():
    """可能装着元扫雷的父目录，按命中概率排。"""
    out = []
    for base in (_env("ProgramFiles", "ProgramW6432"),
                 _env("ProgramFiles(x86)"),
                 os.path.join(_env("LOCALAPPDATA"), "Programs"),
                 _env("LOCALAPPDATA"),
                 _env("ProgramData")):
        if base and os.path.isdir(base):
            key = os.path.normcase(os.path.abspath(base))
            if all(key != os.path.normcase(os.path.abspath(x)) for x in out):
                out.append(base)
    return out


def _child_dirs(base, prefix=_NAME_PREFIX):
    """base 下名字以 metasweeper 开头的子目录（大小写无关，排序保证确定性）。"""
    try:
        names = sorted(os.scandir(base), key=lambda e: e.name.lower())
    except OSError:
        return []
    return [e.path for e in names
            if e.is_dir() and e.name.lower().startswith(prefix)]


def _exe_in(folder):
    """这个目录里的元扫雷主程序；没有返回空串。

    两种常见摆法都要认：``<目录>\\metasweeper.exe``，以及官方安装包那种
    ``<目录>\\metasweeper\\metasweeper.exe``。
    """
    for cand in (os.path.join(folder, _EXE),
                 os.path.join(folder, _NAME_PREFIX, _EXE)):
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return ""


def discover(bases=None):
    """扫默认安装位置，返回找到的 exe 列表（去重）。

    只在「配置里没有、进程也没在跑」的第一次启动时执行，通常 1 毫秒级。
    """
    hits = []
    seen = set()
    for base in (search_bases() if bases is None else bases):
        # 先看 base 下那几个 metasweeper* 目录，再把 base 自己试一遍
        for folder in _child_dirs(base) + [base]:
            exe = _exe_in(folder)
            if not exe:
                continue
            key = os.path.normcase(exe)
            if key in seen:
                continue
            seen.add(key)
            hits.append(exe)
    return hits


def resolve(configured="", running_path="", search=True):
    """返回可用的主程序路径；一个都没有返回 ``""``。

    顺序就是用户能理解的顺序：上次用的 → 现在开着的那个 → 默认位置扫一遍。
    路径存在性是要核对的：用户可能换了安装位置或直接删了。
    """
    for p in (configured, running_path):
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    if search:
        hits = discover()
        if hits:
            return hits[0]
    return ""


def looks_like_metasweeper(path):
    """文件名看着像不像元扫雷 —— 用户手动挑文件时用，挡一下选错。"""
    name = os.path.basename(path).lower()
    return name.startswith(_NAME_PREFIX) and name.endswith(".exe")


# ------------------------------------------------------------------ 启动
def launch(path):
    """启动元扫雷，返回 ``(ok, 失败原因)``。不抛异常给界面。

    工作目录设成程序所在目录：它要在旁边读 gameSetting.ini 一类文件。
    主程序清单里是 asInvoker，所以不会弹 UAC。
    """
    if not path or not os.path.isfile(path):
        return False, "找不到这个文件"
    try:
        allow_foreground()      # 先放开前台限制，否则它的窗口会压在工具箱下面
        subprocess.Popen([path], cwd=os.path.dirname(path) or None,
                         close_fds=True)
    except OSError as exc:
        return False, str(exc)
    return True, ""


# 元扫雷能播的录像格式（它的 _create_video 里就是这五种）
REPLAY_EXT = (".evf", ".avf", ".rmv", ".mvf", ".evfs")


def is_replay(path):
    """这个文件是不是元扫雷能直接播的录像。"""
    return bool(path) and path.lower().endswith(REPLAY_EXT)


def open_file(exe, replay):
    """让元扫雷打开一条录像播放，返回 ``(ok, 失败原因)``。

    走的是它自己注册文件关联时用的命令行：``metasweeper.exe "<录像>"``。
    源码里只在参数**恰好一个**时才当录像打开（``len(sys.argv) == 2``），
    多带任何参数都会退化成只开主界面 —— 所以这里只能传两个元素。

    元扫雷已经在跑时不用特殊处理：新起的这个进程会把路径转交给已有窗口
    （本地 socket），然后自己退出，效果等同把文件拖进窗口。
    """
    for p in (exe, replay):
        if not p or not os.path.isfile(p):
            return False, "找不到这个文件" if p else "没找到元扫雷"
    try:
        allow_foreground()
        subprocess.Popen([exe, os.path.abspath(replay)],
                         cwd=os.path.dirname(exe) or None, close_fds=True)
    except OSError as exc:
        return False, str(exc)
    return True, ""


# ------------------------------------------------------------------ 窗口置前
# 工具箱拉起元扫雷之后，元扫雷的窗口默认会压在工具箱**下面**：Windows 只让
# 「当前前台进程」设置前台窗口，被启动的那个算后台进程，它的请求会被忽略
# （最多在任务栏闪一下）。表现出来的就是「录像确实在放，但被工具箱挡住了」。
#
# 两条路一起用：
#   1. 启动前 AllowSetForegroundWindow —— 让子进程自己也有资格抢，冷启动够用；
#   2. 启动后由工具箱主动把那个窗口提上来 —— 元扫雷已经在跑、只是把文件转交给
#      已有窗口时，根本不会有新窗口，第 1 条对它无效。
_USER32 = None
_ENUMPROC = ctypes.WINFUNCTYPE(_w.BOOL, _w.HWND, _w.LPARAM)

_ASFW_ANY = -1
_GW_OWNER = 4
_SW_RESTORE = 9


class _FLASHINFO(ctypes.Structure):
    _fields_ = [("cbSize", _w.UINT), ("hwnd", _w.HWND), ("dwFlags", _w.DWORD),
                ("uCount", _w.UINT), ("dwTimeout", _w.DWORD)]


def _user32():
    """声明过参数类型的 user32。

    句柄在 64 位下是 8 字节，ctypes 默认按 C int 传会把高位截掉 ——
    SetForegroundWindow 那种「静默失败」很容易被误当成权限问题。
    """
    global _USER32
    if _USER32 is not None:
        return _USER32
    u = ctypes.windll.user32
    u.EnumWindows.argtypes = [_ENUMPROC, _w.LPARAM]
    u.EnumWindows.restype = _w.BOOL
    u.IsWindowVisible.argtypes = [_w.HWND]
    u.IsWindowVisible.restype = _w.BOOL
    u.IsIconic.argtypes = [_w.HWND]
    u.IsIconic.restype = _w.BOOL
    u.GetWindow.argtypes = [_w.HWND, _w.UINT]
    u.GetWindow.restype = _w.HWND
    u.GetWindowRect.argtypes = [_w.HWND, ctypes.POINTER(_w.RECT)]
    u.GetWindowRect.restype = _w.BOOL
    u.GetWindowThreadProcessId.argtypes = [_w.HWND, ctypes.POINTER(_w.DWORD)]
    u.GetWindowThreadProcessId.restype = _w.DWORD
    u.GetForegroundWindow.restype = _w.HWND
    u.SetForegroundWindow.argtypes = [_w.HWND]
    u.SetForegroundWindow.restype = _w.BOOL
    u.ShowWindow.argtypes = [_w.HWND, ctypes.c_int]
    u.BringWindowToTop.argtypes = [_w.HWND]
    u.BringWindowToTop.restype = _w.BOOL
    u.AttachThreadInput.argtypes = [_w.DWORD, _w.DWORD, _w.BOOL]
    u.AttachThreadInput.restype = _w.BOOL
    u.AllowSetForegroundWindow.argtypes = [_w.DWORD]
    u.AllowSetForegroundWindow.restype = _w.BOOL
    u.FlashWindowEx.argtypes = [ctypes.POINTER(_FLASHINFO)]
    u.FlashWindowEx.restype = _w.BOOL
    _USER32 = u
    return u


def allow_foreground():
    """放开前台限制，让紧跟着启动的那个进程有资格把自己的窗口抢到最前。

    只对**下一次**抢前台有效，被谁用掉都行；用不掉也没有副作用。
    """
    if os.name != "nt":
        return False
    try:
        return bool(_user32().AllowSetForegroundWindow(_ASFW_ANY))
    except Exception:
        return False


def _is_candidate(visible, owner, exe_path):
    """枚举到的窗口该不该算元扫雷的主窗口。

    ``owner`` 非 0 的一律跳过：那是挂在主窗口上的对话框、右键菜单、输入法
    候选框之类，把它们的父窗口提上来自然会带着它们。
    """
    if not visible or owner:
        return False
    return os.path.basename(exe_path or "").lower().startswith(_NAME_PREFIX)


def _main_window():
    """元扫雷的主窗口句柄；没有返回 0。

    按 EnumWindows 的原始顺序取第一个命中的 —— 那是 Z 序最上面（最近用过）
    的那个窗口。元扫雷把播放窗口单开成一个顶层窗口时，用户此刻看的正是它。
    """
    if os.name != "nt":
        return 0
    u = _user32()
    hit = []

    def _cb(hwnd, _lp):
        if hit:
            return False                    # 已经找到了，别再往下枚举
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            pid = _w.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not _is_candidate(True, u.GetWindow(hwnd, _GW_OWNER),
                                 _image_path(pid.value)):
                return True
            hit.append(hwnd)
        except Exception:
            pass
        return True

    try:
        u.EnumWindows(_ENUMPROC(_cb), 0)
    except Exception:
        return 0
    return hit[0] if hit else 0


def _flash(hwnd):
    """实在抢不到时闪一下任务栏：让用户知道它开在下面，而不是以为没反应。"""
    try:
        fi = _FLASHINFO(ctypes.sizeof(_FLASHINFO), hwnd,
                        3 | 12, 3, 0)        # FLASHW_ALL | FLASHW_TIMERNOFG
        _user32().FlashWindowEx(ctypes.byref(fi))
    except Exception:
        pass


def foreground_is_self():
    """此刻最前面的窗口是不是本程序（用来判断该不该动手抢前台）。"""
    if os.name != "nt":
        return False
    try:
        u = _user32()
        pid = _w.DWORD()
        u.GetWindowThreadProcessId(u.GetForegroundWindow(), ctypes.byref(pid))
        return int(pid.value) == os.getpid()
    except Exception:
        return False


def focus_hwnd(hwnd):
    """把指定窗口提到最前；返回是否真的抢到了前台。

    最小化的窗口先还原；抢不到就挂到前台线程的输入队列上再试，最后还不行
    就闪一下任务栏。给打开文件夹等「把别人的窗口提上来」的场景复用。
    """
    if os.name != "nt" or not hwnd:
        return False
    u = _user32()
    ok = False
    try:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, _SW_RESTORE)
        if u.SetForegroundWindow(hwnd):
            return True
        # 退路：把自己的输入队列临时挂到「当前前台窗口的线程」上，权限检查
        # 会按那个线程（也就是用户正在操作的前台）来算；抢完立刻断开。
        fg = u.GetForegroundWindow()
        tid_fg = u.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_me = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = False
        try:
            if tid_fg and tid_fg != tid_me:
                attached = bool(u.AttachThreadInput(tid_me, tid_fg, True))
            u.BringWindowToTop(hwnd)
            ok = bool(u.SetForegroundWindow(hwnd))
        finally:
            if attached:
                u.AttachThreadInput(tid_me, tid_fg, False)
    except Exception:
        ok = False
    if not ok:
        _flash(hwnd)
    return ok


def focus():
    """把元扫雷的窗口提到最前；返回是否真的抢到了前台。

    窗口还没建出来（冷启动的头几百毫秒）时返回 False，调用方隔一会儿再试。
    """
    if os.name != "nt":
        return False
    hwnd = _main_window()
    if not hwnd:
        return False
    return focus_hwnd(hwnd)

"""启动元扫雷：找路径 / 判在不在跑 / 启动 的回归。

跑法：python test_mslaunch.py —— 全部通过退出码 0，否则 1。

覆盖：默认安装位置扫描（两种摆放方式、忽略无关目录）、
      路径优先级（配置 → 运行中的进程 → 扫盘）、
      路径失效时降级、全都没有时返回空、
      launch 的失败分支、find_running 的返回形状。
"""

import os
import shutil
import tempfile

import mslaunch

checks = []


def check(label, ok):
    checks.append((label, bool(ok)))
    print("%s %s" % ("OK  " if ok else "FAIL", label))


tmp = tempfile.mkdtemp(prefix="mslaunch-")


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"MZ")          # 只要是个存在的文件就够了
    return path


# ---------------------------------------------------------------- 扫盘
# 官方安装包的摆法：<base>\Metasweeper\metasweeper\metasweeper.exe
base_a = os.path.join(tmp, "pf")
official = touch(os.path.join(base_a, "Metasweeper", "metasweeper", "metasweeper.exe"))
hits = mslaunch.discover([base_a])
check("扫出官方安装包的摆法", hits == [os.path.abspath(official)])

# 绿色版摆法：<base>\Metasweeper\metasweeper.exe
base_b = os.path.join(tmp, "portable")
flat = touch(os.path.join(base_b, "Metasweeper", "metasweeper.exe"))
hits = mslaunch.discover([base_b])
check("扫出平铺的摆法", hits == [os.path.abspath(flat)])

# 无关目录不该被认成元扫雷
base_c = os.path.join(tmp, "other")
touch(os.path.join(base_c, "SomeGame", "metasweeper.exe"))
touch(os.path.join(base_c, "Metasweeper", "readme.txt"))
check("无关目录不误认", mslaunch.discover([base_c]) == [])

# 不存在的目录不抛异常
check("目录不存在时返回空", mslaunch.discover([os.path.join(tmp, "nope")]) == [])

# 两个 base 各有一份 → 两份都返回，顺序按传入顺序
both = mslaunch.discover([base_a, base_b])
check("多个安装位置都认", len(both) == 2 and both[0].endswith("metasweeper.exe"))

# ---------------------------------------------------------------- 优先级
cfg = touch(os.path.join(tmp, "cfg", "metasweeper.exe"))
run = touch(os.path.join(tmp, "run", "metasweeper.exe"))
check("配置里的路径优先", mslaunch.resolve(cfg, run, search=False) == os.path.abspath(cfg))
check("配置失效后用运行中的那个",
      mslaunch.resolve(os.path.join(tmp, "gone.exe"), run, search=False)
      == os.path.abspath(run))

# 配置和运行路径都没有 → 退回扫盘
mslaunch.search_bases = lambda: [base_b]
check("都没有时退回扫默认位置", mslaunch.resolve("", "", search=True) == os.path.abspath(flat))
check("关掉扫盘就返回空", mslaunch.resolve("", "", search=False) == "")

mslaunch.search_bases = lambda: [os.path.join(tmp, "empty-base")]
check("扫不到就是空（界面据此让用户指定）", mslaunch.resolve("", "", search=True) == "")

# ---------------------------------------------------------------- 启动
ok, err = mslaunch.launch(os.path.join(tmp, "no-such.exe"))
check("路径不存在时不抛异常", ok is False and bool(err))

not_exe = os.path.join(tmp, "readme.txt")
with open(not_exe, "w", encoding="utf-8") as fh:
    fh.write("not an exe")
ok, err = mslaunch.launch(not_exe)
check("不是可执行文件时给出原因", ok is False and bool(err))

# ---------------------------------------------------------------- 进程
running = mslaunch.find_running()
check("find_running 不抛异常且形状正确",
      running is None or (isinstance(running, tuple) and len(running) == 2
                          and isinstance(running[0], int)
                          and isinstance(running[1], str)))

# ---------------------------------------------------------------- 文件名
check("认得出元扫雷主程序",
      mslaunch.looks_like_metasweeper(r"C:\x\metasweeper.exe"))
check("认不出别的 exe", not mslaunch.looks_like_metasweeper(r"C:\x\notepad.exe"))

# ---------------------------------------------------------------- 打开录像
check("认得出 evf", mslaunch.is_replay("a.evf"))
check("后缀大小写都认", mslaunch.is_replay("A.EVF"))
check("认得出阿比特 avf", mslaunch.is_replay(r"D:\扫雷\x.avf"))
check("目录不当录像", not mslaunch.is_replay(r"D:\扫雷"))
check("普通文件不当录像", not mslaunch.is_replay(r"C:\x\a.txt"))

# 录像是参数**恰好一个**：元扫雷只在 len(sys.argv) == 2 时才当录像打开，
# 多带一个参数就退化成只开主界面 —— 这条约束必须守住
exe = touch(os.path.join(tmp, "metasweeper.exe"))
evf = touch(os.path.join(tmp, "replay", "a.evf"))
seen = {}
real_popen = mslaunch.subprocess.Popen


class _FakeProc:
    pass


def _fake_popen(args, **kw):
    seen["args"] = args
    seen["cwd"] = kw.get("cwd")
    return _FakeProc()


mslaunch.subprocess.Popen = _fake_popen
try:
    ok, err = mslaunch.open_file(exe, evf)
finally:
    mslaunch.subprocess.Popen = real_popen
check("打开录像不报错", ok and not err)
check("命令行恰好是 exe + 录像两个参数",
      seen.get("args") == [exe, os.path.abspath(evf)])
check("工作目录设成程序目录", seen.get("cwd") == os.path.dirname(exe))

ok, err = mslaunch.open_file(exe, os.path.join(tmp, "missing.evf"))
check("录像不存在时说清楚", ok is False and "找不到" in err)
ok, err = mslaunch.open_file("", evf)
check("没给程序时说清楚", ok is False and bool(err))


# ---------------------------------------------------------------- 窗口置前
# 换一个绝对不会命中的进程名：focus 只走「找不到窗口」那条分支 —— 既验了
# 返回值，也绝不会把用户正在用的窗口拽到前面来。
check("主窗口候选：可见、无 owner、进程是元扫雷",
      mslaunch._is_candidate(True, None, r"C:\Program Files\Metasweeper\metasweeper.exe")
      and not mslaunch._is_candidate(True, 0x1234, r"C:\x\metasweeper.exe")
      and not mslaunch._is_candidate(False, None, r"C:\x\metasweeper.exe")
      and not mslaunch._is_candidate(True, None, r"C:\x\chrome.exe")
      and not mslaunch._is_candidate(True, None, ""))

_real_prefix = mslaunch._NAME_PREFIX
mslaunch._NAME_PREFIX = "metasweeper-zzz-no-such-process"
try:
    check("没有元扫雷窗口时找不到主窗口", mslaunch._main_window() == 0)
    check("没有元扫雷窗口时置前返回 False", mslaunch.focus() is False)
finally:
    mslaunch._NAME_PREFIX = _real_prefix

check("allow_foreground 不抛异常", isinstance(mslaunch.allow_foreground(), bool))
check("foreground_is_self 不抛异常",
      isinstance(mslaunch.foreground_is_self(), bool))

shutil.rmtree(tmp, ignore_errors=True)

bad = [c for c in checks if not c[1]]
print("\n%d 项检查，%d 项失败" % (len(checks), len(bad)))
raise SystemExit(1 if bad else 0)

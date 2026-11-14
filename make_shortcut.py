"""在桌面生成指向部署目录那个 exe 的快捷方式。

exe 文件名固定（`YuanshenToolbox.exe`），所以快捷方式只建一次：
以后无论 exe 怎么升级，这条快捷方式都一直有效。

用系统的 WScript.Shell 创建（需要 pywin32：`pip install pywin32`），格式最标准。

用法：
    python make_shortcut.py             # 在桌面创建 / 覆盖快捷方式
    python make_shortcut.py --print     # 只打印位置和目标，不写文件
"""
import ctypes
import os
import sys

CSIDL_DESKTOPDIRECTORY = 0x0010
APP_EXE = "YuanshenToolbox.exe"
LNK_NAME = "YuanshenToolbox.lnk"
DESC = "扫雷录像 PB 统计工具箱"


def desktop_dir():
    buf = ctypes.create_unicode_buffer(260)
    rc = ctypes.windll.shell32.SHGetFolderPathW(
        None, CSIDL_DESKTOPDIRECTORY, None, 0, buf)
    if rc != 0:
        raise SystemExit("定位桌面目录失败（SHGetFolderPathW 返回 %d）" % rc)
    return buf.value


def deploy_dir():
    return os.path.join(os.environ["APPDATA"], "Metasweeper3.3.4")


def main():
    target = os.path.join(deploy_dir(), APP_EXE)
    lnk = os.path.join(desktop_dir(), LNK_NAME)

    if not os.path.isfile(target):
        raise SystemExit(
            "找不到 %s。\n先跑一次 build.cmd 把 exe 部署过去，再建快捷方式。" % target)

    if "--print" in sys.argv[1:]:
        print(lnk)
        print(target)
        return

    try:
        from win32com.client import Dispatch
    except ImportError:
        raise SystemExit("需要 pywin32，先执行：pip install pywin32")

    sh = Dispatch("WScript.Shell")
    sc = sh.CreateShortcut(lnk)
    sc.TargetPath = target
    sc.WorkingDirectory = os.path.dirname(target)
    sc.IconLocation = target + ",0"
    sc.Description = DESC
    sc.Save()

    print("快捷方式：%s" % lnk)
    print("指向：   %s" % target)
    print("（以后升级只换这个 exe 的内容，快捷方式不用重建）")


if __name__ == "__main__":
    main()

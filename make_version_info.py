"""从 app.py 的 APP_VER 生成 PyInstaller 用的 VERSIONINFO 资源。

build.cmd 在打包前调用；版本号只改 app.py 一处，exe 的文件属性跟着走。
**exe 文件名固定不带版本号**，这样桌面快捷方式指向的路径永远不变，升级时只换文件内容。
版本号另写到 build\app_ver.txt，供 build.cmd 给归档副本命名。
`--print-name` 只打印名字，便于命令行检查。
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app.py")
OUT = os.path.join(HERE, "version_info.txt")
NAME_FILE = os.path.join(HERE, "build", "app_name.txt")
VER_FILE = os.path.join(HERE, "build", "app_ver.txt")

PRODUCT = "YuanshenToolbox"

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={nums},
    prodvers={nums},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [StringStruct('CompanyName', 'Corday'),
         StringStruct('FileDescription', 'Metasweeper / Arbiter replay toolbox'),
         StringStruct('FileVersion', '{ver}'),
         StringStruct('InternalName', '{product}'),
         StringStruct('LegalCopyright', 'Copyright (c) 2026 Corday'),
         StringStruct('OriginalFilename', '{exe}'),
         StringStruct('ProductName', '{product}'),
         StringStruct('ProductVersion', '{ver}')])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0804, 1200])])
  ]
)
"""


def exe_name(_ver=None):
    """exe 文件名固定，不带版本号 —— 快捷方式的路径才不会过期。

    版本号仍然完整写进 VERSIONINFO，右键属性 → 详细信息能看到；
    归档副本由 build.cmd 用 app_ver.txt 拼出带版本号的名字。
    """
    return PRODUCT


def read_version():
    with open(APP, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), APP)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "APP_VER":
                value = ast.literal_eval(node.value)
                if isinstance(value, str):
                    return value
    raise SystemExit("app.py 里没找到字符串常量 APP_VER")


def version_tuple(ver):
    parts = ver.split(".")
    if not 1 <= len(parts) <= 4 or not all(re.fullmatch(r"\d+", p) for p in parts):
        raise SystemExit("APP_VER 必须是 1~4 段数字，当前为 %r" % ver)
    return tuple(int(p) for p in parts) + (0,) * (4 - len(parts))


def main():
    ver = read_version()
    if "--print-name" in sys.argv[1:]:
        print(exe_name(ver))
        return
    nums = version_tuple(ver)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(TEMPLATE.format(nums=nums, ver=ver, product=PRODUCT,
                                 exe=exe_name(ver) + ".exe"))
    os.makedirs(os.path.dirname(NAME_FILE), exist_ok=True)
    with open(NAME_FILE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(exe_name(ver) + "\n")
    with open(VER_FILE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(ver + "\n")
    print("version_info.txt: %s %s (%s.exe)" % (ver, nums, exe_name(ver)))


if __name__ == "__main__":
    main()

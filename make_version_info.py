"""从 app.py 的 APP_VER 生成 PyInstaller 用的 VERSIONINFO 资源。

build.cmd 在打包前调用；版本号只改 app.py 一处，exe 的文件属性跟着走。
"""
import ast
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app.py")
OUT = os.path.join(HERE, "version_info.txt")

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
         StringStruct('FileDescription', '元扫雷 / 阿比特录像工具箱'),
         StringStruct('FileVersion', '{ver}'),
         StringStruct('InternalName', 'YuanshenToolbox'),
         StringStruct('LegalCopyright', 'Copyright (c) 2026 Corday'),
         StringStruct('OriginalFilename', '元神工具箱.exe'),
         StringStruct('ProductName', '元神工具箱'),
         StringStruct('ProductVersion', '{ver}')])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0804, 1200])])
  ]
)
"""


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
    nums = version_tuple(ver)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(TEMPLATE.format(nums=nums, ver=ver))
    print("version_info.txt: %s %s" % (ver, nums))


if __name__ == "__main__":
    main()

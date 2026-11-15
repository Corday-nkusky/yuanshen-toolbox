# 打包与产物纪律

## 命令

```bat
set PYTHON=<带 tkinter 的 python.exe>
build.cmd
```

`build.cmd` 先调 `make_version_info.py` 从 `app.py` 的 `APP_VER` 生成
`version_info.txt`，再调 PyInstaller 出单文件 exe，**启动自检通过后**才自动归档 + 部署。

## 打完包会自动启动一次，起不来就重打

`build.cmd` 不把「PyInstaller 成功」当成打包成功，而是用 `--selftest` 真启动一次产物：
不建窗口、不扫录像，只往 `%TEMP%\ytb-selftest.txt` 写一行回执就退出。回执没出现
（进程根本没起来）就删掉重打，最多三次；部署到录像目录后还会再验一次。

**为什么需要这一步**：单文件 exe 是「自解压 + 自带 Python 运行时」，
杀毒软件的机器学习判定对它并不稳定。2026-11-16 撞过一次：同一份代码，
`archive\YuanshenToolbox-v0.23.0.exe` 静态扫描报「no threats」，但**一执行**就被判成
`Trojan:Win32/Wacatac.H!ml` 并立即隔离，连桌面快捷方式一起删了；重新打一次包
（字节不同）就正常。**这跟改了什么代码无关**，是那一份具体二进制被判定到了。

所以：

- 自检失败不是代码 bug，别去改代码，让它重打；
- 连续三次都失败才需要人工介入（那时才考虑提交误报或换打包方式）；
- 不能用 `md5sum` / `certutil -hashfile` 验收新包 —— 读这个文件本身就会触发一次扫描，
  可能把刚打好的包读没了。要比对就比**文件大小和修改时间**。

## exe 文件名固定为 `YuanshenToolbox.exe`

**版本号不进文件名。** 这样桌面快捷方式指向的那条路径永远不变，
升级只是换掉文件内容，快捷方式一次建立、永久有效。

版本号从三个地方都能看到，一个都没丢：

- 窗口标题：`YuanshenToolbox v0.20.0`
- 右键 → 属性 → 详细信息 → `FileVersion` / `ProductVersion`
- `archive\` 里归档副本的文件名：`YuanshenToolbox-v0.20.0.exe`

## 产物只有一个位置有效

| 位置 | 内容 | 规则 |
|---|---|---|
| `dist\` | 当前版本的 exe | **只允许 1 个 exe**（固定名）。`build.cmd` 打包完自动把其它 exe 挪进 `archive\` |
| `archive\`（仓库根） | 往期版本 | 随便堆，`.gitignore` 已忽略。每次打包自动留一份带版本号的副本 |
| `%APPDATA%\Metasweeper3.3.4\`（部署目录，跟录像同一个目录） | 当前版本的 exe + `YuanshenToolbox-data\` + 录像 | **只允许 1 个 exe**（固定名）。`build.cmd` 自动部署 |

部署目录是**用户实际在用的目录**。`build.cmd` 的部署步骤：

1. 旧 exe 若正在运行 —— Windows 不允许覆盖它，但**允许把它改名挪走**，
   于是先 `move` 到 `%TEMP%`；
2. 复制新的 `YuanshenToolbox.exe` 进去；
3. 顺手删 `%TEMP%` 里残留的旧副本（正在运行的删不掉，留着下一轮再删）。

所以**开着工具箱打包也不会失败**，下次双击快捷方式就是新版。

## 桌面快捷方式

只建一次，指向 `%APPDATA%\Metasweeper3.3.4\YuanshenToolbox.exe`。
之后无论 exe 怎么更新，都不用重新发送快捷方式。
建法：`python make_shortcut.py`（需要 `pip install pywin32`）。
换机器、重装系统后重跑一次就行；快捷方式失效时先确认部署目录里这个固定文件名还在。

快捷方式被删（比如被杀毒软件连带清掉）也重跑这个脚本，别再手工「发送到桌面」——
手工建的会指向一个带版本号或指向 `dist\` 的路径，下次升级就失效。

## 发版验收

1. `dist\` 与部署目录的 exe **大小、修改时间一致**（别用 md5 校验，见上一节）；
2. 文件属性 → 详细信息：`FileVersion` 与 `APP_VER` 相符，`OriginalFilename` 为 `YuanshenToolbox.exe`；
3. `YuanshenToolbox.exe --selftest` 在 `%TEMP%\ytb-selftest.txt` 写出
   `version=X.Y.Z` / `frozen=True`（打包脚本自己已经跑过一遍）；要排查 DPI / 布局时
   另用 `--probe`，它会写出窗口实际尺寸。
4. 测试全通过：`test_arb` / `test_site_pb` / `test_upload` / `test_smoke` / `test_retry` /
   `test_resume_ui` / `test_mode` / `test_autoup` / `test_mslaunch` / `test_record_ini` /
   `test_auto_flow`；
5. 部署目录里**只有 1 个 exe**，且文件名不带版本号；
6. `docs/CHANGELOG.md` 记一条，`README.md` 里的示例 exe 名同步。

## 版本号

只有一处权威来源：`app.py` 的 `APP_VER`。递增口径见 `docs/CHANGELOG.md` 开头。
已发布过的版本号不复用，哪怕只是「重打一次包」。

# 打包与产物纪律

## 命令

```bat
set PYTHON=<带 tkinter 的 python.exe>
build.cmd
```

`build.cmd` 先调 `make_version_info.py` 从 `app.py` 的 `APP_VER` 生成
`version_info.txt`，再调 PyInstaller 出单文件 exe，最后自动归档 + 部署。

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

## 发版验收

1. `dist\` 与部署目录的 exe **md5 一致**；
2. 文件属性 → 详细信息：`FileVersion` 与 `APP_VER` 相符，`OriginalFilename` 为 `YuanshenToolbox.exe`；
3. `YuanshenToolbox.exe --probe` 写出 `version=X.Y.Z` / `frozen=True`；
4. 六个测试全通过：`test_arb` / `test_site_pb` / `test_upload` / `test_smoke` / `test_retry` / `test_resume_ui`；
5. 部署目录里**只有 1 个 exe**，且文件名不带版本号；
6. `docs/CHANGELOG.md` 记一条，`README.md` 里的示例 exe 名同步。

## 版本号

只有一处权威来源：`app.py` 的 `APP_VER`。递增口径见 `docs/CHANGELOG.md` 开头。
已发布过的版本号不复用，哪怕只是「重打一次包」。

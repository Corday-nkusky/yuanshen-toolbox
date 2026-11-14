# 打包与产物纪律

## 命令

```bat
set PYTHON=<带 tkinter 的 python.exe>
build.cmd
```

`build.cmd` 先调 `make_version_info.py` 从 `app.py` 的 `APP_VER` 生成
`version_info.txt` 与文件名，再调 PyInstaller 出单文件 exe。

## 产物只有一个位置有效

| 位置 | 内容 | 规则 |
|---|---|---|
| `dist\` | 当前版本的 exe | **只允许 1 个 exe**。`build.cmd` 打包完自动把其它 exe 挪进 `archive\` |
| `archive\`（仓库根） | 往期版本 | 随便堆，这里是存档处，`.gitignore` 已忽略 |
| `%APPDATA%\Metasweeper3.3.4\`（部署目录，跟录像同一个目录） | 当前版本的 exe + `YuanshenToolbox-data\` + 录像 | **只允许 1 个 exe**。这条脚本管不到，发版时手动删/挪旧的 |

部署目录是**用户实际在用的目录**，多放一个旧 exe 就是多一份 27 MB 垃圾，
而且双击哪个版本全凭文件名猜。升级流程固定为：新 exe 复制进去 → 旧 exe 挪到 `archive\`。

## 发版验收

1. `dist\` 与部署目录的 exe **md5 一致**；
2. 文件属性 → 详细信息：`FileVersion` / `OriginalFilename` 与 `APP_VER` 相符；
3. `YuanshenToolbox-vX.Y.Z.exe --probe` 写出 `version=X.Y.Z` / `frozen=True`；
4. 六个测试全通过：`test_arb` / `test_site_pb` / `test_upload` / `test_smoke` / `test_retry` / `test_resume_ui`；
5. `docs/CHANGELOG.md` 记一条，`README.md` 里的示例 exe 名同步。

## 版本号

只有一处权威来源：`app.py` 的 `APP_VER`。递增口径见 `docs/CHANGELOG.md` 开头。
已发布过的版本号不复用，哪怕只是「重打一次包」。

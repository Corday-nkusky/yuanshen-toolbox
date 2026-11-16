# 技术笔记

README 放不下的实现细节和研究结论。改代码前先看这里；面向玩家的说明都在
[README](../README.md)。

## rce 与 NF / FL

`rce` = Right Click Effective，右键有效插旗数。口径与
[eee555/ms-toollib](https://github.com/eee555/ms-toollib) 一致：

```rust
// minesweeper_board.rs::right_click
if self.board[x][y] == -1 && !self.flaged_list.contains(&(x, y)) {
    self.rce += 1;                      // 只算「第一次给雷格插旗」
    self.flaged_list.push((x, y));
}
// base_video.rs
self.nf = b.rce == 0;                    // rce == 0 → NF
```

三条要点，实测确认过：

1. **只统计雷格**：右键插在非雷格（数字/空格）上，`flag` 会变但 `rce` 不加。
2. **同一格去重**：插旗 → 取消 → 再插，只算 1 次。
3. **只看「第一次标出」**：所以 `rce` 恒 ≤ 雷数。

程序读出 `.evf` 的棋盘与完整点击流，按 ms-toollib 的状态机重演整局，取累计 `rce`。

本机 35 个完成局（34 NF + 1 FL）逐局重演，`bbbv_solved` 与文件头 `bbbv` 全部吻合
（唯一例外是失败局：解了 4 个 BV 后踩雷，与文件名 `3.500 × 1.143 ≈ 4` 一致）。
那条 FL 局的右键落点逐条核对过：

```
t=1.198  rc  cell(2,0)  board=-1  useful_level=1   ← 雷，计入 rce
t=1.423  rc  cell(3,1)  board= 1  useful_level=0   ← 非雷，不计入
t=1.801  rc  cell(4,3)  board=-1  useful_level=1   ← 雷，计入 rce
```

最终 `rce = 2`、`flag = 2`。**错旗不影响 NF 判定**：只在非雷格乱插旗、从没标到过雷的局，
`rce` 仍是 0，依然算 NF —— 与「零旗」的直觉定义有细微差别。

## 文件名格式

实测样本（b/i/e 共 35 个）：

```
b_0_1.774_9_5.073_PlayerName(Region).evf
│ │  │     │  │      └ 玩家名
│ │  │     │  └ bvs（文件名保留 3 位小数）
│ │  │     └ bv（3BV）
│ │  └ time（rtime）
│ └ 第二字段：样本全为 0，含义未确认
└ 级别 b/i/e
```

失败局文件名以 `_fail` 结尾，且 `bvs` 字段是 `bvdone / time` 而非 `bv / time`
（`e_0_1.143_174_3.500_fail.evf` → `3.500 × 1.143 ≈ 4`，即只完成 4 个 BV）。

`record.ini` 的 `[BEGINNER]` / `[INTERMEDIATE]` / `[EXPERT]` 段本身就是「每个 BV 的最快时间」
表，扫描后逐条对账，结果写日志；只有发现「仅 record.ini 有」或「数值冲突」才在状态栏提示。
只拿元扫雷的局对账（阿比特的成绩不在 record.ini 里）。

**record.ini 只在元扫雷退出时才写**，它开着的时候这个文件必然落后于录像目录 ——
拿它对账会把「刚破的 PB」误判成冲突。

阿比特（`.avf`）实测两种写法，同一个人的存档里混着：

```
HI-SCORE Beg_1.00_3BV=6_3BVs=6_Anonymous.avf
Beg_NF_1.06_3BV=5_3BVs=4.71_Anonymous.avf
```

前缀 `HI-SCORE`、中缀 `_NF_`、`3BV=` / `3BVs=` 都不稳定，所以**文件名只用来兜底**：
先读录像内容拿级别 / 时间 / BV，读不出才退回文件名解析（`core.parse_avf_filename`）。

## 缓存与去重

- 每局解析结果（rce / BV / 时间 / 标识 / 审核码）按 `(修改时间, 大小)` 缓存在
  `scan-cache.db`；判重用的文件哈希存在同一个库（`file_sha` 表），第二次扫描连字节都不读。
  实测 23,183 个阿比特录像：首次 173.5 s → 之后 0.9 s（缓存库 8 MB）。
- 重复副本按**文件字节**判定，保留「文件名不带 `HI-SCORE` → 目录层级浅 → 路径字典序」
  里的第一份，所以双击复制到的是正本路径。
- 目录递归最多 20000 个子目录（实测 3–5 秒），防止误选 `C:\` 时卡死；到上限时状态栏写明。
- 缓存不做过期剪枝：录像删了/改名后留孤儿行，只占磁盘，不影响结果。

## 界面实现

选 tkinter 的原因：单文件约 27 MB、双击即开。同等界面用 Electron 要带整个 Chromium，
Qt 要拖 DLL 目录；这是纯本地只读工具，不需要 webview，也不需要起 HTTP 服务。

全部视觉都是**画出来的，不是贴图**：

| 效果 | 实现 |
|---|---|
| 横幅渐变（樱粉→薰衣草→湖蓝） | Canvas 逐行 `create_line` 插值填色 |
| 圆角按钮 / 圆角卡片 | `create_polygon(smooth=True)`，hover / active 换填充 |
| PB 网格每格单独着色 | `tk.Label` 复用池（Treeview 只能整行着色），切级别只改 text/bg |
| 表头淡紫底、选中行高亮 | `ttk.Style` 改 `Treeview` / `Treeview.Heading` 的 `background` 与 `map` |
| 高缩放下的清晰文字 | 字号统一乘 `SCALE`，用真实 DPI 而不是 Tk map 前的 96 |

**DPI 是这套界面的关键点**：`app.manifest` 声明 `dpiAware` / `dpiAwareness`，
运行时再按 `SetProcessDpiAwarenessContext` → `shcore(1)` → `user32()` 三级降级并检查返回值；
缩放比优先读注册表 `AppliedDPI`，可用区用 `SystemParametersInfoW(SPI_GETWORKAREA)`。
PyInstaller 默认清单不带 `dpiAware`，进程起来后再调会被系统拒绝（`E_ACCESSDENIED`），
窗口就被整体虚拟化拉伸 —— 字发糊、`SCALE` 掉回 1.0、坐标全错位。所以 manifest 不是可选项。

用到的 Win32 调用（全部 `ctypes`，不引 pywin32）：

| API | 用途 |
|---|---|
| `AddFontResourceExW` | 私有注册 MiSans，免安装字体 |
| `GetDpiForWindow` + `LoadImageW` + `WM_SETICON` | 按任务栏实际像素（24dp × 缩放）精确设 HICON |
| `SystemParametersInfoW(SPI_GETWORKAREA)` | 取可用工作区，窗口居中不超出屏幕 |
| `SetProcessDpiAwarenessContext` / `SetProcessDpiAwareness` | 声明 DPI 感知 |
| `CryptProtectData` / `CryptUnprotectData` | 密码与会话 Cookie 的本地加密 |

**表格对齐约定**（`Table` 的 `cols` 里那个 `anchor`）：数量 / 成绩列右对齐 `"e"`，
级别 / 风格 / 来源 / 状态 / 日期时间这类短标签居中 `"center"`，文件与路径左对齐 `"w"`。
表头跟着单元格同向。加列时按这三桶挑。

## 图标怎么来的（任务栏清晰度）

![任务栏图标：改前 / 改后](icon_taskbar.png)

最初做法是「1024 母版缩成 7 档塞进 ico」，任务栏上只有一个黑团。两个原因叠在一起：

1. **画面不适合小尺寸**：整张头像缩到 36px，脸只剩十来个像素，深紫黑发贴在深紫底上。
2. **尺寸被二次缩放**：Tk 的 `iconbitmap` 只往窗口塞一张 HICON，任务栏按 24dp × 缩放
   渲染（150% → 36px），把 32px 的位图硬拉到 36，边缘必糊。

修法：

- **小尺寸单独一套画面**（`_ms/make_icon2.py`）：16–48px 用「脸部紧裁 + 中调紫蓝底 +
  深紫描边」，靠「黑发剪影 + 白色眼罩横带」保辨识度；64 以上才用原设计。
  PIL 的 ICO 保存只能一份源图缩所有档，所以脚本自己按 ICO 格式写文件，各档塞各自的画面。
- **窗口图标按任务栏像素精确加载**：`GetDpiForWindow` 算出 24dp 对应的物理像素，
  `LoadImage(cx=cy=像素)` 取精确档，再 `WM_SETICON`。ico 里的 30/36/42 是非标准档，
  专门给 125% / 150% / 175% 缩放用。

验证方式：把 ico 每档涂成不同纯色，跑起来截任务栏看命中哪一档（150% 下走 36 档）。

## 已知边界 / 没验证的部分

- **文件名第二个字段（`0`）的含义**：35 个样本全是 0，仍无法推断。NF/FL 已由 `rce` 直接
  判定，不依赖这个字段。
- **失败局的 stnb**：本版本直接跳过非完成局，未实现。
- **rce 解析失败时降级**：录像损坏或格式不认时风格显示 `?`，并计入状态栏「rce 失败」数。
  本地分不出 NF/FL 时，lsf 页的 NF PB 退回全PB；PB 表不强制切换 —— 网站 PB 不依赖本地重演。
- **日期取文件修改时间**（mtime），不是录像内嵌时间；文件被复制/移动过日期会不准。
- **「文件名与录像不符」是检查而非拦截**：统计仍按文件名走（保持与 record.ini 对账口径一致），
  只在状态栏标出。
- **阿比特文件名解析只覆盖实测到的两种写法**，其他命名靠录像内容兜底。
- **阿比特上传只做过单条真传**（1 条 `.avf` 成功，站点记为 `d` 标识不匹配 —— 那条标识带
  比赛编号、账号里没登记）；批量混传还没在真实账号上跑过。
- **站点去重接口验过三层**（匿名叫公开录像 / 本地 38 个 `.evf` 比对 / 真实登录态 22 条全部
  拿到明细）；`a`（待审核）、`b`（冻结）两个状态本账号没有，仍未验。接口按站点 OpenAPI
  核对过字段，但站点可能再改，症状同样是「全判成未上传」。
- **自带 `cacert.pem`**：本机 Windows 根证书库里有一张过期交叉签证书，OpenSSL 会选中它，
  连 openms.top 直接报 `certificate has expired`（浏览器和 curl 正常）。程序优先用随包 CA，
  找不到才回落系统信任库。

## 相关文档

- [构建与发版](BUILD.md) —— 打包、自检、产物纪律
- [更新日志](CHANGELOG.md) —— 版本口径与每版改动
- [待办](TODO.md)
- 专题研究：[站点上传接口](openms-upload-research.md) · [LSF 口径](openms-lsf-report.md) ·
  [PB 网格改版](pb-redesign.md) · [涨分](lsf-gain.md) · [阿比特统计](arbiter-stats-study.md)

# 开源扫雷网 LSF 系统调研报告

> 调研时间：2026-11-15
> 对象：openms.top（开源扫雷网）与其后端 `eee555/open-minesweeper`（原名 `saolei_website`，MIT）
> 目的：弄清 LSF 是什么、后端怎么算、公开接口能拿到什么，以及**我们能拿这些接口做哪些和 LSF 有关的东西**
> 事实分级：**【源码】**= 读过仓库代码；**【实测】**= 本人实际发请求验证；**【文档】**= 站方文档原文；**【推断】**= 由以上推出的结论，未直接验证

---

## 0. 先说结论

1. **本机没有「后端镜像」**。有的是**源码克隆**：`D:\metasweeper-pb\_ms\saolei_website`（eee555/saolei_website，2026-10-24 的版本，还带一个 fork `Corday-nkusky/saolei_website`）。仓库里**没有 Dockerfile，也没有 docker-compose**，官方部署方式是 uwsgi + nginx + MySQL + Redis（`back_end/saolei/start.sh`）。【源码】
   Docker Desktop 装着但守护进程没启动，`docker_data.vhdx` 有 11 GB，说明磁盘上有镜像数据，**但无法在不启动 Docker 的情况下确认里面到底是什么镜像**。【实测】
2. LSF 是**纯派生指标**，没有独立数据表。它由「18 个 top5 小榜的均值」+「18 个锚点」加权算出，全部缓存在 Redis，**接口只返回排序和原始明细，不返回 LSF 数值**。任何第三方要用 LSF，必须自己实现公式。【源码】
3. 公式完全公开、可以逐位复现：我用两个匿名接口把 157 人的 9 个派生榜全部算了一遍，**排序与站点接口 157/157 完全一致**。【实测】
4. 所以最可行的方向不是「调一个 LSF 接口」，而是「**接口只给原料（top5 明细 + 锚点），计算放本地**」。上传前预测、短板诊断、目标反解、历史曲线，都能在客户端做完，不需要服务器也不需要登录。【推断，方案部分已给出】

---

## 1. 本机「后端」到底是什么

| 项 | 结论 | 依据 |
| --- | --- | --- |
| `D:\metasweeper-pb\_ms\saolei_website` | 完整源码克隆，含 `back_end/saolei/speedranking/top/`（LSF 实现），无 `.venv` | 【实测】 |
| git remote | origin `eee555/saolei_website`，fork `Corday-nkusky/saolei_website` | 【实测】 |
| 该克隆的时间点 | `79919b87`，2026-10-24；上游仓库已改名 `open-minesweeper`，今天仍在推送（2026-11-15） | 【实测】 |
| Docker 镜像 | 无法确认；daemon 未运行（`npipe:////./pipe/dockerDesktopLinuxEngine` 连不上） | 【实测】 |
| 仓库内的容器化文件 | 无 | 【源码】 |
| 本地跑后端的依赖 | MySQL + Redis + 三个进程（web / 前端 / `manage.py db_worker`） | 【文档】`本地启动.md` |

要确认镜像，得你自己在 PowerShell 里起 Docker Desktop，然后 `docker images`。我没有替你启动 GUI 程序。

---

## 2. LSF 是什么（规则层）

【文档】`vitepress_doc/zh/guide/top-lsf.md`，与【源码】`speedranking/top/utils.py` 一致。

### 2.1 原料：18 个 top5 小榜

- 三个级别（b/i/e）× 三个指标（Time / Bvs / Stnb）× 普通与 NF 两类 = **18 项**。
- 每项取**该玩家历史最好的五局**求均值。不是全部对局平均。
- 准入：`b/i/e` + `STD` + `OFFICIAL` + 未被比赛隐藏；3BV 下限 **10 / 30 / 100**（三项指标都适用）。普通榜含 NF，NF 榜额外要求 `right_ce == 0`。
- 不足五局：Time 每缺一局补 **999.999 秒**，Bvs/Stnb 补 **0**，仍除以 5。缺项不造假录像，只是拉低均值。

### 2.2 锚点：把不可比的指标变成可比

每个小榜取**前二十名玩家 top5 均值的算术平均**，叫锚点 A。设玩家该项均值为 V：

| 指标 | 单项软实力 | 说明 |
| --- | --- | --- |
| Time | `A / V` | 越大越好，1 = 达到锚点 |
| Bvs | `V / A` | 同上 |
| Stnb | `(V / A)^(1/1.7)` | 齐次化，使「速度同比例提高」三项涨幅一致 |

普通与 NF 各有自己的锚点，互不混用。

### 2.3 从单项到 LSF

```
单级别软实力 = Σ(权重 × 单项软实力) / 100      # 三项都到锚点 = 100
  b: t32 b48 s20    i: t36 b36 s28    e: t32 b32 s36
综合软实力   = 初级×6 + 中级×14 + 高级×20      # 三项都到锚点 = 4000
LSF          = 普通综合×0.97 + NF综合×0.03
```

普通、NF 各自独立算三级 + 综合，加上 LSF，共 **9 个派生榜**。

### 2.4 展示层：等价 et 与定阶

- 等价 et（秒）`= A_et / (S / C)`，其中 `A_et` 是该模式高级 Time 锚点（毫秒），`S` 是软实力，`C` 是基准（单项 1 / 单级别 100 / 综合 4000）。普通项用普通 et 锚点，NF 项用 NF et 锚点。
- 定阶按等价 et 分 9 档（新手 / 工兵 / 小雷神 / 大雷神 / 小雷圣 / 大雷圣 / 小雷帝 / 大雷帝 / 外星人），档内按**软实力线性插值**到 0–10 阶，允许超过 10 阶。
- **定阶只是展示，不改变排序。**

---

## 3. 后端怎么实现（数据流）

【源码】`back_end/saolei/speedranking/`

```
VideoModel(post_save/post_delete)
        │  transaction.on_commit
        ▼
speedranking/services.py   ←「共同读取 · 独立判断 · 共同写入」
        │  各大榜各自决定准入、选优、补位
        ▼
Redis
  speedranking:top:records                HASH  玩家 → 18 项各自的 video_id+原始值（最多 5 条）
  speedranking:top:{std|nf}:{b|i|e}{t|b|s} ZSET  18 个原始小榜（member=玩家id）
  speedranking:top:anchors                HASH  18 个锚点 {value, sample_count}
  speedranking:top:soft:{std|nf}:{b|i|e|sum} + :lsf  ZSET  9 个派生榜
  speedranking:top:soft:published         STRING 正式派生榜采用的锚点快照
```

几个必须知道的机制：

1. **zset 的 score 是变换值，不是原始成绩**。Time 用 `1/均值`，Stnb 用 `iqg均值^(1/1.7)`，Bvs 原值；统一降序。逆变换要 `1/score` 和 `score^1.7`。
2. **日常更新用「已发布锚点」**（`soft:published`），写进 9 个 soft zset；锚点一变（前 20 名变化）就发 `soft_refresh_requested`，后台任务用**最新锚点**重建全部 9 榜再统一发布。所以「排序用的锚点」和「页面显示的锚点」可能短暂不同版本 —— 文档原话是「允许展示计算与排序使用不同版本」。
3. **页面上的 LSF 数值是前端算的**，不是后端给的。`LSFRanking.vue` 拿 `/api/common/loginauto` 的实时锚点 + top5 明细现场计算。这意味着**站点自己也没有一个「LSF 数值」接口**。
4. 全量重建 `rebuild_top_ranks` 有独占锁；重建期间上传会返回 `503 + Retry-After: 1`。

---

## 4. 可调用接口清单（全部实测）

公开文档入口：`https://openms.top/api/docs`（Swagger UI），schema 在 `https://openms.top/api/openapi.json`（196 个端点）。这个入口站方没在前台挂，但可用。【实测】

### 4.1 LSF 直接相关（匿名 GET，无需登录）

| 接口 | 参数 | 单次上限 | 限流 | 跨域 | 返回 |
| --- | --- | --- | --- | --- | --- |
| `/api/speedranking/top/soft/rank` | `ranking`（lsf 或 8 个派生项）、`start`、`end` | `end-start ≤ 100` | 5/s | ✗ | `{count, players:[{player_id, records}]}`，按该派生榜排序 |
| `/api/speedranking/top/anchors` | — | — | 20/s | ✗ | 18 个 `{value, sample_count}` |
| `/api/speedranking/top/rank` | `level`、`stat`、`nf`、`start`、`end` | 100 | 5/s | ✗ | 原始 18 小榜之一，含完整 records |
| `/api/speedranking/top/player/{id}` | — | — | 5/s | ✗ | 单个玩家的完整 18 项明细（无则 `{}`） |
| `/api/common/loginauto` | — | — | — | ✗ | `{user, top_anchors}`；访客 user=null，锚点照给 |
| `/api/news/events` | `category`（pb/lsf/eff/all）、`limit` ≤ 200 | 200 | 5/s | ✗ | **含 LSF 精确数值**的变动事件，保留 7 天 / 200 条 |

`/api/news/events?category=lsf` 的事件长这样（【实测】原文）：

```json
{"category":"lsf","player_id":6,"payload":{"video_id":402814,
 "old":{"lsf":3879.365687943986,"rank":12},
 "new":{"lsf":3879.389363496836,"rank":12}}}
```

只有「LSF 真实变化 且 新名次进前 36」才会发事件，所以**不能当全量数据源**，但当「破纪录提醒」和「数值校验」很合适。

### 4.2 配套的公开数据接口（可跨域）

| 接口 | 用途 | 限流 | 跨域 |
| --- | --- | --- | --- |
| `/api/userprofile/infobulk?ids=1,2` | id → 用户名、签名、是否认证 | — | ✓ |
| `/api/video/staticdetailbatch?ids=...` | 录像解析字段（用时、3BV、点击等），≤100 个 | 5/s | ✓ |
| `/api/video/infobulk` / `detailbulk` / `abstractbulk` | 全量/增量爬录像，≤2000/500/10000 | 1/s、1/s、1/2s | ✓ |
| `/api/video/preview?id=` | 下载录像（可能是 MSRC 压缩） | 20/min | ✓ |
| `/api/monitor/summary` | 站点规模快照 | — | ✗ |
| `/api/userprofile/videolist?user_id=` | 某玩家全部录像 | — | ✓ |

`/api/monitor/summary` 当前值（【实测】2026-11-15 22:20）：**公开录像 402,531 条**，注册用户 **230** 人（`/api/userprofile/query` 的 `count`）。

### 4.3 写操作

- `POST /api/common/uploadvideo`：上传自己的录像（`openms.py` 已实现，需登录态）。
- `POST /api/speedranking/top/admin/rebuild_record`：`staff_required`，不是我们能用的。

---

## 5. 复现验证：LSF 能不能自己算出来

### 5.1 做了什么

`_probe/lsf_repro.py`：拉 `/top/soft/rank?ranking=lsf`（157 人，2 次分页）+ `/top/anchors`，按第 2 节公式算 9 项软实力、等价 et、定阶。`_probe/lsf_verify9.py`：对 9 个派生榜逐一比对「接口顺序 vs 本地算出的顺序」。

### 5.2 结果

```
LSF 榜人数 157，取回 157 条完整记录
  lsf       接口 157 人  本地排序一致: True
  std:b / std:i / std:e / std:sum        全部 True
  nf:b  / nf:i  / nf:e  / nf:sum         全部 True
```

九个榜、157 人，**顺序完全一致**。【实测】

数值层面抽样对比新闻事件（新闻里的 `new.lsf` 就是站方用当时锚点算出的真值）：

| 玩家 | 新闻里的 LSF | 我算的 LSF | 差 |
| --- | --- | --- | --- |
| 6 | 3879.389363 | 3879.385622 | 0.0037 |
| 139 | 4000.980551 | 4000.175425 | 0.805 |
| 42 | 3598.482895 | 3597.238698 | 1.244 |

差值来自**锚点漂移**（新闻用的是事件发生时的锚点，我用的是当前实时锚点）。157 人的排序一致 + 差值 < 1.3（约千分之三），说明公式与实现细节（iqg、1/1.7 次方、999999 补位、0.97/0.03 权重）全部正确。【实测 + 推断：差值成因未逐项证明，但排序一致排除了公式错误】

### 5.3 当前榜单概况（同一时刻算出来的）

```
157 人（注册用户 230，即 68% 的人有 LSF）
外星人 1 / 大雷帝 11 / 小雷帝 21 / 大雷圣 24 / 小雷圣 21 / 大雷神 21 / 小雷神 9 / 工兵 27 / 新手 22
P50 = 2678.7（等价 et 50.1s）   P90 = 3696.1（36.3s）   第 1 名 5144.4（26.1s）
std:et 锚点 33582.8 ms（33.58s）   nf:et 锚点 38432.7 ms（38.43s）
```

【实测】`_probe/lsf_stats.py`

---

## 6. 限制与坑（做产品前必须知道）

1. **没有 LSF 数值接口**。`soft/rank` 只给顺序 + 明细。想显示数值必须自己算，且用的是「当前实时锚点」，与站点排序用的「已发布锚点」可能差千分之几。
2. **排行接口不在跨域白名单**。我实测：`/api/video/infobulk` 返回 `Access-Control-Allow-Origin: *`，而 `/api/speedranking/top/*` **没有** ACAO 头。nginx 的 `$openms_public_cors` 白名单只覆盖 `userprofile` 的部分只读接口和 `video` 的下载/爬取接口（`default.conf`）。【源码 + 实测】
   → 纯浏览器网页直连 LSF 接口会被 CORS 拦；本地程序（Python/桌面端）和自建服务端代理不受影响。
3. **限流 5/s（按 IP）**。157 人拉两页就要 2 次请求；做每日全站快照也只有个位数请求，压力可忽略，但别并发爆破。
4. **没有历史数据**。LSF 只有当前值；新闻事件只留 7 天且只报前 36 名。想要曲线只能自己按天存。
5. **LSF 不是实时的**。上传后要走审核 → 进 top5 → 更新软实力；锚点变化还会触发后台重建。刚传完的录像不会立刻反映到 LSF。
6. **准入门槛**：3BV 下限 10/30/100、必须 STD + OFFICIAL、非比赛隐藏、审核通过。录像不合格就完全不进 LSF，不是「算得差」而是「不参与」。
7. **157 人的榜会全量暴露**。全站只有 230 个注册用户，LSF 榜一人两页就能拉完，做对比/分析没有隐私门槛，但也没有「大数据」可言。

---

## 7. 可以做什么（按可行性排序）

### A. 塞进 metasweeper 的「LSF 面板」（成本最低，最贴合现有产品）

现有 `openms.py` 已经能拿 `top_records`（18 项 top5）和登录态；只缺锚点。新增一次 `/top/anchors` 请求即可本地算全套：

- **9 项软实力 + LSF + 等价 et + 定阶**：直接套第 2 节公式。`_probe/lsf_repro.py` 里的 `soft_values` / `equivalent_et` / `grade` 可直接搬进 `core.py`（纯函数，无网络）。
- **名次**：`soft/rank?ranking=lsf` 每页 100，157 人两页拉完，本地找自己在第几位；顺带拿到「离上一名差多少 LSF」。比让用户自己去网页翻直观。
- **短板诊断**：8 个软实力列一起显示才有意义。当前榜单里第 10 名是 `std 90.9/99.2/101.6`、第 50 名是 `70.8/76.5/79.7`，一眼能看出差距集中在哪一级。
- **入榜资格提示**：如果用户某项为 0（没有合格录像），要说明「高级 3BV < 100 不计入」这类具体原因，而不是显示 0 分。

### B. 上传前预测（我认为最值得做的一个）

锚点固定时，LSF 对每项均值是**线性**的（Stnb 是幂函数但单调可解析）。所以本地就能算：

```
新增一条录像 → 重排该玩家某一项的 top5 → 重算均值 → 重算该项软实力 → 重算 LSF
增量 = Σ 权重系数 × Δ(单项软实力)
```

条件：一条 `/top/anchors` + 一条 `/top/player/{自己}`（或本地已缓存的 top5）。**不需要上传、不需要登录，纯本地模拟。**

可以直接回答玩家最关心的问题：
- 「这条 9.87s 的高级录像过审后，LSF 涨多少、能进大雷帝吗？」
- 「我现在离大雷帝 0.6 阶，最短路径是刷高级 Time 还是中级 Bvs？」（用每项的边际收益排序）
- 「先把哪一项补到 5 局最划算？」（缺项补位对 Time 影响巨大：每缺一局按 999.999 秒计入）

注意：预测误差来自锚点漂移（实测 < 1.3 LSF），要在界面上说清「锚点会变，所以是估算」。

### C. LSF 目标反解 / 定阶规划器

给定目标定阶，反推出需要多大 LSF → 需要多大综合软实力 → 各项需要达到多少。锚点已知，逆函数是解析的：

```
目标 et → 目标 LSF = 4000 × A_et / et目标(秒)
某级别所需软实力 S_level = LSF目标 / (100 × 级别权重 × 模式权重)
某单项所需软实力 s = S_level × 100 / 指标权重   # 再反解 V
  Time: V = A_t / s      Bvs: V = A_b × s      Stnb: V = A_s × s^1.7
```

再做一次「现实中能不能达到」的检验：拿该项前 20 名的均值当靶子（锚点本身就是这个），目标超过锚点意味着要进前 20。这比只给一个数字诚实。

### D. LSF 时间序列 / 变化榜

- 每日（或每次启动）存 `{date, lsf, rank, 9 项软实力, 18 个锚点}` 到本地 SQLite —— 站点不存历史，这是唯一办法。
- 变化榜：两次快照 diff，得到「谁涨了、谁跌了、新人进榜」。比 `/news/events` 全（新闻只报前 36 且要求真实变化），但需要自己持续跑。
- 锚点曲线本身也有意思：锚点上涨 = 全站水平上涨，等价 et 会「变难」。同一份成绩过半年 LSF 可能变低，这是 LSF 的相对性，值得在工具里解释清楚。

### E. 录像联动

每条入选 top5 的录像都有 `video_id`，`/api/video/preview?id=` 可下载（跨域允许），MSRC 解压后可回放。可以做：
- 「我的最强五局」直接点开回放（高级 Time 的 5 条、中级 Bvs 的 5 条……共 18 组）。
- 「这一项我被谁的哪条录像压着」：拉同榜玩家 top5，diff 出需要超越的 video_id。

### F. 独立小工具 / 网页

- **必须服务端代理**（CORS）。最小形态：本地 FastAPI/Flask 两个转发路由 + 前端算；或干脆做成桌面/命令行工具绕开。
- 可以做「全站 LSF 榜（含数值）」——站点只给顺序，不给数值，自己算就能画分布图、定阶分布、专长散点（Time vs Bvs 软实力）。
- 做社区对比雷达图：自己的 18 项 vs 锚点 vs 指定玩家。

### G. 数据分析向

- 402,531 条公开录像 + 157 人的 top5，可以回答「LSF 高的人是不是都刷高级」「Bvs 型与 Time 型在 LSF 上的收益差多少」这类问题。
- 注意站方的爬取规范（`vitepress_doc/zh/guide/development/data-crawling.md`）：**串行**、`abstractbulk` 间隔 ≥2s、`infobulk`/`detailbulk` ≥1s、`staticdetailbatch` 0.25s；429 要退避重试，不要把失败当空结果。

---

## 8. 做不到 / 不建议做的

| 想法 | 结论 |
| --- | --- |
| 直接调一个「返回 LSF 数值」的接口 | 不存在。必须自己算【源码 + 实测】 |
| 网页里 `fetch` LSF 接口 | 被 CORS 拦，除非自建代理【实测】 |
| 帮别人刷新 LSF / 重建榜 | 管理员接口，`staff_required` |
| 用 LSF 接口改任何站上数据 | 全部只读，写只有自己的 `uploadvideo` |
| 把 LSF 当绝对实力值 | 它是相对锚点的，锚点变则值变；同分可以来自完全不同的专长组合【文档】 |
| 高频轮询做实时看板 | 5/s 限流 + LSF 本身异步刷新，没有实时性可言 |

---

## 9. 我没能验证的部分

1. **Docker 里到底有没有扫雷网后端镜像** —— daemon 未启动，没起 GUI。`docker_data.vhdx` 11 GB 只能证明「有数据」。
2. **`speedranking:top:soft:published` 的内容** —— Redis 不对外，拿不到「排序用的那版锚点」，所以第 5 节的数值差只能归因到锚点漂移，不能逐项证明。
3. **上传 → LSF 更新的实际延迟** —— 只从代码看到「异步 + 锚点变化触发重建」，没有实测端到端时延（需要真传一条录像并轮询）。
4. **5/s 限流的实际惩罚** —— 没有故意打满去测 429 的行为。
5. **`/api/video/query` 的 filters 语法细节** —— 只读了 `query_videos` 的实现（`field` + `eq/in/gt/...`，`stnb` 需要带单一 level 过滤），没构造真实请求验证。
6. **站点 LSF 页面的显示数值** —— 没跑浏览器渲染，只通过对新闻事件里的真值反证公式正确。

---

## 附：本文用到的脚本

| 文件 | 作用 |
| --- | --- |
| `_probe/lsf_repro.py` | 拉 LSF 榜 + 锚点，本地算 9 项软实力/等价 et/定阶，输出 `lsf_calc.json` |
| `_probe/lsf_verify9.py` | 9 个派生榜逐一比对接口顺序与本地顺序 |
| `_probe/lsf_stats.py` | 定阶分布、分位数、构成对比 |

三个脚本都只用标准库（`urllib` + `ssl`），并且复用项目里的 `cacert.pem` —— 本机 Windows 根证书库有张过期交叉签证书，不加这个会报 `certificate has expired`（`openms.py` 里已经踩过同一个坑）。

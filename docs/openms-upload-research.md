# 开源扫雷网（openms.top）上传功能 · 调研报告

> 调研日期：2026-10-21　　状态：**仅调研，未实现**
> 依据：站点源码 `github.com/eee555/saolei_website`（MIT，分支 `main`）+ 对 openms.top 的真实请求实测。
> 表中 **[源码]** = 读代码确认，**[实测]** = 用 curl 打过真实站点确认。

---

## 0. 一句话结论

**可行，而且比预想简单：纯 HTTP 客户端就够，不需要浏览器自动化、不需要 Selenium。**

三个关键事实：

1. **登录 = 一次性图形验证码 + `session_id` Cookie**。验证码只在"登录那一刻"出现，之后靠 Cookie 维持。
2. **上传 = 一次一个文件的 multipart POST**，服务端自己用 ms_toollib 重新解析（跟我们本地用的是同一个库）。
3. **站点的 CSRF 中间件是注释掉的**（`settings.py` 第 83 行 `# 'django.middleware.csrf.CsrfViewMiddleware'`），
   所以不需要 CSRF token、不需要 Referer 校验、不需要模拟浏览器指纹。

反过来，有一件事必须先说清楚：

> **保存"账号密码"解决不了自动登录问题** —— 密码绕不过验证码。
> 真正能"长期不掉线 + 一键上传"的东西是 **`session_id` Cookie**（站点支持最长 90 天、且每次请求自动续期）。

---

## 1. 站点是什么技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 前端 | Vue 3 + TS + Element Plus + Vite + WASM | 单页应用，`/assets/index-*.js` 只是引导脚本 |
| 后端 | Django 4.2 + django-ninja | `/api/*` 是 ninja 的 JSON 接口（上传也已并入），`/userprofile/*` 是原生 Django 视图 |
| 解析 | **ms-toollib**（Rust，WASM 版给前端、CPython 版给后端） | 与本地 `core.py` 用的是同一套解析口径 |
| 部署 | Nginx + uwsgi + MySQL + Redis | Redis 做榜单缓存 |

> 这一点很重要：**站点前后端的解析规则、`rce` 定义、`is_valid()` 审核码，全部和我们本地已经验证过的一致**，
> 所以"本地预检 → 只传该传的"这件事我们是能做的。

---

## 2. 接口清单（全部实测/源码确认）

| 用途 | 方法 | 路径 | 鉴权 | 来源 |
|---|---|---|---|---|
| 取验证码 | GET | `/userprofile/refresh_captcha/` | 无 | [实测] |
| 验证码图片 | GET | `/userprofile/captcha/image/<hashkey>/` | 无 | [实测] |
| 登录 | POST | `/userprofile/login/` | 无 | [源码]+[实测路径] |
| 退出 | GET/POST | `/userprofile/logout/` | Cookie | [源码] |
| 查当前登录态 | GET | `/api/common/loginauto` | Cookie（可匿名） | [实测] |
| **上传录像** | POST | `/api/common/uploadvideo` | Cookie | [源码]+[实测] |
| 读我的标识列表 | GET | `/api/userprofile/identifier` | Cookie | [源码] |
| 接口文档 | GET | `/api/docs`（Redoc）、`/api/openapi.json` | 无 | [实测 200] |

两个容易踩的坑，实测排除过了：

- 上传在 `/api` 下，且**没有尾斜杠**：`POST /api/common/uploadvideo`。加了尾斜杠变 **404**
  （django-ninja 路由不做 `APPEND_SLASH`）。未登录时是 **500 + HTML**，不是 403 ——
  根因已定位：`ninja.decorators.decorate_view` 是**按书写顺序从内到外**叠装饰器
  （`for deco in decorators: operation.run = deco(operation.run)`，先写的最后才生效），
  所以 `decorate_view(login_required_error, banned_blocked, ratelimit(...))` 的真实执行
  顺序是 ratelimit → banned_blocked → login_required_error。匿名请求在 `banned_blocked`
  里取 `AnonymousUser.is_banned` 就抛 AttributeError，5xx 抢先返回，登录检查根本没轮到。
  **客户端不能把 5xx 一律当临时故障**，必须回头问 `loginauto` 才能分清「Cookie 过期」
  和「服务端真出错」。
- 登录**不在** `/api` 下：走 `/userprofile/login/`（前端 axios 的 `baseURL` 是根路径）。

---

## 3. 登录链路

### 3.1 三步走

```http
# 1. 拿 hashkey（实测返回 40 位十六进制）
GET /userprofile/refresh_captcha/
→ {"hashkey": "a55b35eb7e88136b6e5cf88b7f158205f7d66252", "status": 100}

# 2. 拿图片（实测 200 / image/png / 3937 字节 / 100×30）
GET /userprofile/captcha/image/a55b35eb.../

# 3. 登录（application/x-www-form-urlencoded）
POST /userprofile/login/
     username=xxx&password=xxx&captcha=WZTK&hashkey=a55b35eb...&set_expiry=90
→ 200 {"type":"success","user":{...}}
   Set-Cookie: session_id=...; HttpOnly; Secure; SameSite=Lax
```

字段约束来自 `userprofile/forms.py::UserLoginForm` [源码]：

| 字段 | 约束 | 备注 |
|---|---|---|
| `username` | 1 ~ 20 | 前后空格会被剥掉 |
| `password` | 1 ~ 20 | |
| `captcha` | 1 ~ 6 | **大小写不敏感**（服务端 `response == captcha.lower()`） |
| `hashkey` | 8 ~ 512 | 就是上面拿到的那串 |
| `set_expiry` | **0 ~ 90**（整数，单位天） | 必填；`0` = 关浏览器即失效 |

失败返回（HTTP 200，靠 `category` 区分）[源码]：

| 响应 | 含义 |
|---|---|
| `{"type":"error","object":"login","category":"captcha"}` | 验证码错（此时验证码已被消费，必须刷新） |
| `{"type":"error","object":"login","category":"password"}` | 账密错 |
| HTTP 400 | 表单字段不合法（缺字段、超长） |
| HTTP 429 | 触发限流 |

### 3.2 验证码机制（决定"能不能自动化"）

| 属性 | 值 | 依据 |
|---|---|---|
| 库 | django-simple-captcha | [源码] |
| 内容 | 4 位随机英文字母 | `CAPTCHA_LENGTH = 4` |
| 尺寸 | 100×30，白底蓝字，加噪点，±10° 倾斜 | `CAPTCHA_IMAGE_SIZE` / `NOISE_FUNCTIONS` |
| 有效期 | 15 分钟 | `CAPTCHA_TIMEOUT = 15` |
| 使用次数 | **一次**：成功或失败都会 `delete()` | `userprofile/utils.py::judge_captcha` |

实测图片长这样（100×30 原图放大）：

```
 WZTK   ← 白底 + 蓝色字母 + 稀疏噪点，人眼基本一次成功
```

**结论**：识别难度低（人眼），但**失败一次就得换一张**，且登录接口限流 60 次/小时/IP。
所以"暴力 OCR + 重试"这条路既不稳也没必要 —— 90 天只需要输一次。

### 3.3 会话能撑多久（这是"长期保持"的关键）

| 配置 | 值 | 依据 |
|---|---|---|
| Cookie 名 | `session_id` | `SESSION_COOKIE_NAME` |
| 属性 | `HttpOnly` + `Secure` + `SameSite=Lax` | [源码] |
| 默认寿命 | `SESSION_COOKIE_AGE = 1209600 * 2` = **28 天** | [源码] |
| 每次请求续期 | `SESSION_SAVE_EVERY_REQUEST = True` | [源码] |
| 登录时可指定 | `set_expiry` 0~90 天 | 表单约束 |

也就是说：**登录时填 90，之后只要 90 天内用过一次，就会一直续下去**。
这就是"长期保持登录"的正解：存 Cookie，而不是存密码。

会话存在服务端 DB（`django.contrib.sessions`），换机器只要 Cookie 有效就能用；
但**改密码/管理员封禁会让会话失效**，所以必须有"失效 → 重新登录"的回退路径。

### 3.4 限流（必须遵守，否则会被挡）

| 接口 | 限制 | 依据 |
|---|---|---|
| 登录 | 60 次 / 小时 / IP | `@ratelimit(key='ip', rate='60/h')` |
| 取验证码 | 60 次 / 小时 / IP | 同上 |
| 注册 | 6 次 / 小时 / IP | 同上 |
| **上传** | **5 次 / 秒 / IP** | `@ratelimit(key='ip', rate='5/s')` |
| 榜单重建锁 | 命中返回 **503 + `Retry-After: 1`** | `utils/ranking_lock.py` |

---

## 4. 上传链路（"复杂"到底复杂在哪）

### 4.1 请求本身很简单

```http
POST /api/common/uploadvideo
Content-Type: multipart/form-data; boundary=...
Cookie: session_id=...

file=@b_0_1.774_9_5.073_PlayerName(Region).evf
```

```json
{"type":"success","object":"videomodel","category":"upload",
 "data":{"id":12345,"state":"c"}}
```

- **一次只能传一个文件**，没有批量接口。前端自己也是 `for` 循环串行 + 200ms 间隔。
- 表单只有 `file` 一项（ninja 的 `file: UploadedFile = File(...)`）。旧接口那个
  `skipTournamentCheck` 还留在 `UploadVideoForm` 里，但视图是用 `data={}` 建表单的，
  **传了也不读**，发不发都一样。

### 4.2 服务端拿到文件后干了 7 件事

这才是真正"复杂"的部分（`common/api.py::upload_video` → `common/utils.py::new_video_by_file` → `utils/parser.py`）[源码]：

| # | 检查 | 不通过的表现 |
|---|---|---|
| 1 | 登录（`login_required_error`） | 未登录 **500 + HTML**（站点自身缺陷，见 §2 的坑） |
| 2 | 未被封禁（`banned_blocked`） | **403**（0 字节） |
| 3 | 录像总配额 `video_num_total >= video_num_limit` | **402**（空响应；额度由站点按账号设置，需联系管理员增加） |
| 4 | 准入 `has_identity_access`：**实名或玩家认证**满足其一 | 200 + `{"type":"error","object":"userprofile","category":"identity_access_required"}` |
| 5 | 表单：`file` 必填 | 400 |
| 6 | 按扩展名选解析器（avf/evf/rmv/mvf） | 200 + `object:"file", category:"type"` |
| 7 | `is_completed == False` | 200 + `object:"file", category:"incomplete"`（**失败局直接拒**） |
| 8 | 级别 3/4/5 → b/i/e；6 → 自定义；其他 | 200 + `object:"file", category:"level"` |

> 解析崩了**现在会给 500**：解析被挪进子进程（`utils/parser.py`），超时、退出码非零、
> 结果文件坏掉都 `raise ExceptionToResponse('rust','panic',500)`，body 仍是 JSON。
> 所以 5xx 不能一律当站点故障重试，要先看 body。
> 本地跑一遍预检能把这类文件提前筛掉，省一次往返。

然后还有三道与"能不能进榜"直接相关的关卡：

**A. 合法性审核码**（`v.is_valid()`，ms-toollib 算出来的）

| 码 | 站点状态 | 含义 |
|---|---|---|
| 0 | `c` 有效 | 通过机审，**进排行榜** |
| 1 | `b` 冻结 | 判定不合法（如触屏/递归双击等） |
| 3 | `a` 待审核 | 进人工审核队列 |
| 其他 | — | 200 + `object:"file", category:"review"` |

**B. 标识匹配**（最容易白传的一关）

```
录像里的 v.player_identifier  ←→  账号「标识管理」里注册过的标识
```
- 先做内容审查（百度大脑），含违规词 → `identifier/verify`
- 再精确匹配（**空格、全角/半角、不可见字符都算不同**）
- 不匹配 → **上传仍然成功**，但状态置为 `d 标识不匹配`，**不进任何榜单**

**C. 去重**

```python
# 进解析之前先算完整原始文件的摘要（sha256 前 12 字节 = 24 hex），
# VideoModel.original_hash 上有 unique=True
find_identical_video(original_hash)   # VideoModel.objects.filter(original_hash=...)
```
命中 → 200 + `{"type":"error","object":"file","category":"collision"}`
（**不是** 409，是 200 里的 error JSON，容易漏判）

> 对客户端的影响：站点只认「字节完全相同」。我们本地用 `timems + bv + file_size`
> 三元组做同判，**比站点宽**——三元组相同但内容差一个字节的录像，站点会收，
> 我们会跳。这是刻意的保守选择（宁可不传，也不制造重复）。

### 4.2.1 部署层的硬限制（客户端要知道的两条）

| 限制 | 值 | 来源 |
|---|---|---|
| 请求体上限（`/api/common/uploadvideo`） | **1 MB**，超了 nginx 直接 413 HTML | 仓库 `default.conf` 没有全局 `client_max_body_size`（默认 1m），[实测] 400 KB→进 Django，1024 KB→413 |
| 上游读超时 | **2 s**（`location /` 的 `uwsgi_read_timeout 2`），超了 504 | 仓库 `default.conf` [源码] |
| uWSGI harakiri | **10 s**（只有 MSGames 同步、WOM sync、媒体提交例外） | 仓库 `uwsgi.ini` 的 `route = ^(?!...) harakiri:10` [源码] |

注意这三条和站点自己声明的 5 MB（`MaxSizes.VIDEOFILE`、前端预检也是 5 MB）**矛盾**：
线上真正先卡住的是 nginx 的 1 MB。也就是说普通录像（几 KB ~ 几百 KB）没事，
但大录像会在网关层被拒，根本到不了 Django。

### 4.3 响应字典（建议直接做成映射表）

| 现象 | 判据 | 建议动作 |
|---|---|---|
| `500` + HTML | 登录态失效（站点缺陷：匿名也走 500） | 先问 `loginauto`：`user == null` → 停止整批、重新登录 |
| `500` + JSON `rust/panic` | 解析子进程超时 / 退出码非零 / 结果文件坏了 | **单条失败**，不要重试、不要当站点故障 |
| `403` | 被封禁（`banned_blocked`） | 停止整批 → 提示封禁 |
| `402` | 录像额度已满（空响应） | 停止整批 → 提示联系网站管理员增加额度 |
| `413` + HTML | nginx 的 1 MB 请求体上限 | 单条失败；本地就能按文件大小提前筛掉 |
| `503` | 榜单正在刷新（写锁） | 读 `Retry-After`（1 秒）后重试该文件 |
| `200` + `success` | 成功 | 记录 `id` 与 `state` |
| `200` + `file/collision` | 已经传过同一文件 | **视为成功**（跳过） |
| `200` + `file/incomplete` | 未完成局 | 跳过（我们本来就不传 `_fail`） |
| `200` + `file/type` / `level` / `review` | 格式/级别/审核码异常 | 记入失败清单 |
| `200` + `identifier/verify` | 标识含违规词 | 记失败，提示改标识 |
| `200` + `rust/panic` | 进程内 ms-toollib panic 被捕获 | 记失败，本地可先跑一遍预检筛掉 |
| `200` + `userprofile/identity_access_required` | 实名与玩家认证都没办 | 停止整批 → 提示去个人资料办其中一项 |

> 错误响应的对象键不统一：老接口用 **`obj`**，新接口的 `identity_access_required` 用 **`object`**。
> 我们解析时两个都读（`p.get("object") or p.get("obj")`）。

### 4.4 state 码对照

| state | 含义 | 是否进榜 |
|---|---|---|
| `c` | 已通过审核 | 是 |
| `a` | 已上传但未审核 | 否（等人工） |
| `b` | 审核未通过，被冻结 | 否 |
| `d` | 标识不匹配 | 否 |
| `e` | 外部网站审核 | 否 |

---

## 5. 上传前必须先解决的前置条件

这三条不解决，传上去也是白传：

1. **准入**：站点要求「实名」或「玩家认证」满足其一（`has_identity_access`），
   都不满足则拒绝上传（`identity_access_required`）。
2. **标识**：元扫雷 `gameSetting.ini` 里 `player_identifier = PlayerName(Region)`，
   必须在站点「个人主页 → 标识管理」里**一字不差**地存在。
   → 登录后可以 `GET /api/userprofile/identifier` 拉列表，本地就能比对并预警。
3. **额度**：账号的录像**总数**有上限，额度由站点按账号设置（源码默认 100，可调；
   `UserMS.video_num_limit`）。程序不写死数字，只处理空 402：停批并提示联系管理员增加额度。

---

## 6. "长期保持账号 + 尽量一键上传"怎么做

### 6.1 三种方案对比

| 方案 | 能否一键 | 验证码 | 风险 |
|---|---|---|---|
| 每次输账号密码 | 否 | 每次都要输 | 无 |
| 存密码自动填 | 半自动（仍要输验证码） | 每次都要输 | 密码落盘 |
| **存 `session_id`** | **是** | **90 天一次** | Cookie 泄露 = 账号可被冒用 |

**推荐第三种**，理由：验证码在服务端是"登录环节"的一次性消耗品，跟密码无关；
只有 Cookie 能把"登录"这一步的结果保存下来。

### 6.2 具体做法

```
%APPDATA%\YuanshenToolbox\openms.json
{
  "username": "xxx",
  "session_id_enc": "<DPAPI 密文，base64>",
  "saved_at": "2026-10-21T00:00:00",
  "expiry_days": 90,
  "last_ok": "2026-10-21T00:05:00"
}
```

- 加密用 **Windows DPAPI**（`CryptProtectData`，绑当前 Windows 用户 + 机器），
  不写明文、不用自造密钥。拷贝到别的电脑解不开 —— 这正是我们要的。
- 启动时：`GET /api/common/loginauto` 带上 Cookie → `user != null` 即有效（**实测匿名返回 `{"user": null, ...}`**）。
- 失效时：弹登录窗（验证码图片用 `tk.PhotoImage` 直接加载 PNG —— Tk 8.6 原生支持，**不需要 Pillow**），
  勾选"保持登录 90 天"（`set_expiry=90`）。
- 「退出登录」：`/userprofile/logout/` + 删除本地密文。
- 密码可以作为**可选**便利项（下次自动填），但要明确告知：**填了也还是要输验证码**。

### 6.3 验证码不做 OCR

- 4 位字母 + 噪点，人眼一次成功率很高，OCR 反而容易连续失败；
- 失败一次验证码就作废，且登录接口 60 次/小时/IP；
- 90 天输一次，收益极低、风险不低（可能被当成撞库）。

---

## 7. 上传策略设计（给实现阶段用）

1. **选范围**：`PB 表` / `前端 Top5` / 两者并集（默认并集，按文件路径去重）。
2. **本地预检**（零网络成本，`ms_toollib` 已在 exe 里）：
   - `_fail` 直接排除；`is_completed` 必须为真；
   - 读 `player_identifier`，与账号标识列表比对 → 不匹配就**黄标预警**（传上去也不进榜）；
   - 读 `is_valid()`：1 → 会被冻结，3 → 会进人工审核，提前说明。
3. **本地台账** `uploaded.json`：`sha256 → {video_id, state, time}`，已传过的直接跳过，
   避免靠服务端 `collision` 报错来兜底。
4. **串行上传 + 300ms 间隔**（限流 5/s，留余量；站点前端自己用的 200ms）。
5. **错误分级**：
   - `403` / `402` → **停止整批**；
   - `503` → 按 `Retry-After` 重试（最多 3 次）；
   - `collision` → 当作成功；
   - 其他 → 记失败原因，继续下一个。
6. **结果表**：文件名 / 结果 / 站点 id / `state` / 原因，可导出 CSV。

---

## 8. 风险与红线

- **不要刷接口**：登录 60/h、上传 5/s 都是硬限流，超了返回 429，可能连带影响 IP。
- **不要伪造/修改录像**：站点有审核队列，冻结是永久的。
- **Cookie 等同密码**：必须 DPAPI 加密存放，不能写进日志、不能随 CSV 导出。
- **不要用别人的标识**：标识匹配是账号归属的唯一凭据。
- 上传成功 ≠ 进榜：`a` 待审核、`b` 冻结、`d` 标识不匹配 都不进榜，界面上必须区分。

---

## 9. 需要你拍板的 4 件事

1. **账号是否已实名？标识 `PlayerName(Region)` 是否已在站点「标识管理」里？**
   （这条不确认，做出来可能传 40 个全部落在 `d 标识不匹配`）
2. **上传范围**：只传 PB 榜上的（去重后约 30~40 个），还是所有完成局（35 个）？
3. **能否接受"每 90 天手动登录一次"**？还是要我研究验证码自动识别（不推荐）？
4. **要不要把上传结果回写进界面**（每条录像的 `state`、审核状态、失败原因）？

---

## 10. 如果要做，建议分三步

| 阶段 | 内容 | 产出 |
|---|---|---|
| 1 | 只做"登录 + 显示登录态" | 登录窗（验证码图）+ 会话加密存储 + 启动校验 |
| 2 | 单文件上传打通 | 一条录像走通全链路，验证 state 与错误映射 |
| 3 | 批量 + 界面 | 范围选择、预检、台账、进度、结果表 |

---

## 附录 A：实测记录

**2026-11-14（针对接口迁移的复测，仓库 main = `c5a7839`，2026-11-13）**

| 请求 | 结果 |
|---|---|
| `POST /api/common/uploadvideo`（未登录/伪造 session_id，dummy 文件） | `500` + HTML 145 B（不是 403） |
| `POST /api/common/uploadvideo/` | `404`（ninja 不做 APPEND_SLASH） |
| `POST /common/uploadvideo/`（旧路径） | `404`（视图已下线） |
| `GET /api/common/uploadvideo` | `405 Method not allowed`（路由确实挂在那） |
| `POST` 400 KB body（未登录） | `500`（进到了 Django，说明网关已放行） |
| `POST` 1024 KB / 1200 KB / 4000 KB body | `413 Request Entity Too Large`（nginx 直接拒，没到 Django） |

**2026-10-21 首次摸底**

| 请求 | 结果 |
|---|---|
| `GET /userprofile/refresh_captcha/` | `200` `{"hashkey":"60fbad37...","status":100}` |
| `GET /userprofile/captcha/image/<hashkey>/` | `200` `image/png` 3937 B，100×30，内容 `WZTK` |
| `GET /api/common/loginauto` | `200` `{"user": null, "top_anchors": {...}}`（匿名，今天复测仍一致） |
| `GET /api/docs`、`/api/openapi.json` | `200` |

## 附录 B：读过的源码（已下载到 `_ms/oms_src/`；2026-11-14 重新克隆核对到 `back_end/`）

```
back_end/saolei/saolei/urls.py              根路由（/api、/userprofile、/common、/video）
back_end/saolei/saolei/settings.py          CSRF 关闭、Cookie、验证码、限流相关
back_end/saolei/saolei/api.py               ninja 路由挂载
back_end/saolei/userprofile/urls.py         登录/登出/验证码路径
back_end/saolei/userprofile/views.py        user_login / refresh_captcha
back_end/saolei/userprofile/forms.py        UserLoginForm 字段约束（set_expiry 0~90）
back_end/saolei/userprofile/utils.py        judge_captcha（一次性、15 分钟）
back_end/saolei/userprofile/decorators.py   login_required_error / banned_blocked → 403
back_end/saolei/common/api.py               上传视图 upload_video（ninja，/api/common/uploadvideo）
back_end/saolei/common/forms.py             UploadVideoForm（file + skipTournamentCheck，后者已不被读）
back_end/saolei/common/services.py           new_video_by_file（哈希去重、标识校验、建记录）
back_end/saolei/utils/exceptions.py          ExceptionToResponse（错误 body 用 "object" 键）
back_end/saolei/videomanager/utils.py        calculate_original_hash（sha256 前 12 字节）
back_end/saolei/videomanager/services.py     find_identical_video（original_hash 唯一）
back_end/saolei/videomanager/schema.py       VIDEO_INFO_BATCH_LIMIT=100、PARSED_VIDEO_FIELDS
back_end/saolei/default.conf                 nginx：无全局 client_max_body_size（=1m）、location / 读超时 2s
back_end/saolei/uwsgi.ini                    harakiri 90s，但 route 把普通请求压到 10s
back_end/saolei/utils/parser.py              MSVideoParser（子进程解析，失败 → 500 rust/panic）
back_end/saolei/utils/ranking_lock.py       503 + Retry-After
back_end/saolei/msuser/models.py            UserMS 配额字段（默认 100，可调）
ninja/decorators.py（上游库）                decorate_view 的叠放顺序（先写的最内层）
front_end/src/services/videoUploadService.ts  上传请求与错误映射
front_end/src/components/Login/LoginForm.vue  登录字段与"记住我"
front_end/src/http.ts                       axios 全局 form-urlencoded + withCredentials
front_end/src/components/VideoUpload/utils.ts 客户端预检（声明 ≤5MB，实际网关 1MB 先卡）
```

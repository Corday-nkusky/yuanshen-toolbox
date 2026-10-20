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
| 后端 | Django 4.2 + django-ninja | `/api/*` 是 ninja 的 JSON 接口，`/userprofile/*`、`/common/*` 是原生 Django 视图 |
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
| **上传录像** | POST | `/common/uploadvideo/` | Cookie | [实测] |
| 读我的标识列表 | GET | `/api/userprofile/identifier` | Cookie | [源码] |
| 接口文档 | GET | `/api/docs`（Redoc）、`/api/openapi.json` | 无 | [实测 200] |

两个容易踩的坑，实测排除过了：

- 上传**不在** `/api` 下：`POST /api/common/uploadvideo/` → **404**；正确的 `POST /common/uploadvideo/` → 未登录 **403（0 字节）**。
- 登录也**不在** `/api` 下：走 `/userprofile/login/`（前端 axios 的 `baseURL` 是根路径）。

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
POST /common/uploadvideo/
Content-Type: multipart/form-data; boundary=...
Cookie: session_id=...

file=@b_0_1.774_9_5.073_PlayerName(Region).evf
skipTournamentCheck=false
```

```json
{"type":"success","object":"videomodel","category":"upload",
 "data":{"id":12345,"state":"c"}}
```

- **一次只能传一个文件**，没有批量接口。前端自己也是 `for` 循环串行 + 200ms 间隔。
- `skipTournamentCheck` 可选，默认 false（比赛校验），一般不用管。

### 4.2 服务端拿到文件后干了 7 件事

这才是真正"复杂"的部分（`common/views.py` → `common/utils.py::new_video_by_file` → `utils/parser.py`）[源码]：

| # | 检查 | 不通过的表现 |
|---|---|---|
| 1 | 登录 & 未被封禁 | **403**（0 字节） |
| 2 | 录像配额 `video_num_total >= video_num_limit` | **402**（默认上限 **100 个**） |
| 3 | 是否实名 | 200 + `{"type":"error","obj":"userprofile","category":"realname_required"}` |
| 4 | 表单：`file` 必填 | 400 |
| 5 | 按扩展名选解析器（avf/evf/rmv/mvf） | 200 + `object:"file", category:"type"` |
| 6 | `is_completed == False` | 200 + `object:"file", category:"incomplete"`（**失败局直接拒**） |
| 7 | 级别 3/4/5 → b/i/e；6 → 自定义；其他 | 200 + `object:"file", category:"level"` |

> 解析器崩了不会 500：服务端捕获 `PanicException` 后返回 `object:"rust", category:"panic"`
> （本地也见过这个现象：损坏录像会让 ms-toollib 写 `crash.log` 并 panic）。
> 所以**上传前在本地跑一遍预检**能把这类文件提前筛掉，省一次往返。

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
VideoModel.objects.filter(timems=..., bv=...).filter(file_size=...)
# 且逐字节比对完全一致
```
命中 → 200 + `{"type":"error","object":"file","category":"collision"}`
（**不是** 409，是 200 里的 error JSON，容易漏判）

### 4.3 响应字典（建议直接做成映射表）

| 现象 | 判据 | 建议动作 |
|---|---|---|
| `403` | 未登录 / 被封 | 停止整批 → 重新登录 |
| `402` | 配额满（100） | 停止整批 → 提示删除旧录像或赞助提额 |
| `503` | 榜单正在刷新（写锁） | 读 `Retry-After`（1 秒）后重试该文件 |
| `200` + `success` | 成功 | 记录 `id` 与 `state` |
| `200` + `file/collision` | 已经传过同一文件 | **视为成功**（跳过） |
| `200` + `file/incomplete` | 未完成局 | 跳过（我们本来就不传 `_fail`） |
| `200` + `file/type` / `level` / `review` | 格式/级别/审核码异常 | 记入失败清单 |
| `200` + `identifier/verify` | 标识含违规词 | 记失败，提示改标识 |
| `200` + `rust/panic` | 录像损坏导致 ms-toollib 解析崩溃（服务端捕获了 Rust panic） | 记失败，本地可先跑一遍预检筛掉 |
| `200` + `obj:userprofile, category:realname_required` | 未实名 | 提示去个人资料实名 |

> 注意 `realname_required` 用的键是 **`obj`**（不是 `object`），站点自己的前端也要兼容两种写法，我们解析时两个都读。

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

1. **实名**：站点是实名制，未实名直接拒绝上传（`realname_required`）。
2. **标识**：元扫雷 `gameSetting.ini` 里 `player_identifier = PlayerName(Region)`，
   必须在站点「个人主页 → 标识管理」里**一字不差**地存在。
   → 登录后可以 `GET /api/userprofile/identifier` 拉列表，本地就能比对并预警。
3. **配额**：默认 100 个。本机完成局 35 个，PB + Top5 去重后大约 40~60 个文件，**够用但要注意**。

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
%APPDATA%\元神工具箱\openms.json
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

## 附录 A：实测记录（2026-10-21 00:16 ~ 00:25）

| 请求 | 结果 |
|---|---|
| `GET /userprofile/refresh_captcha/` | `200` `{"hashkey":"60fbad37...","status":100}` |
| `GET /userprofile/captcha/image/<hashkey>/` | `200` `image/png` 3937 B，100×30，内容 `WZTK` |
| `GET /api/common/loginauto` | `200` `{"user": null, "top_anchors": {...}}`（匿名） |
| `POST /common/uploadvideo/`（未登录，带 dummy 文件） | `403`，0 字节 |
| `POST /api/common/uploadvideo/` | `404`（证明上传不在 /api 下） |
| `GET /api/docs`、`/api/openapi.json` | `200` |

## 附录 B：读过的源码（已下载到 `_oms/`）

```
back_end/saolei/saolei/urls.py              根路由（/api、/userprofile、/common、/video）
back_end/saolei/saolei/settings.py          CSRF 关闭、Cookie、验证码、限流相关
back_end/saolei/saolei/api.py               ninja 路由挂载
back_end/saolei/userprofile/urls.py         登录/登出/验证码路径
back_end/saolei/userprofile/views.py        user_login / refresh_captcha
back_end/saolei/userprofile/forms.py        UserLoginForm 字段约束（set_expiry 0~90）
back_end/saolei/userprofile/utils.py        judge_captcha（一次性、15 分钟）
back_end/saolei/userprofile/decorators.py   login_required_error → 403
back_end/saolei/common/urls.py              uploadvideo 路径
back_end/saolei/common/views.py             上传视图（配额 402、实名、限流 5/s）
back_end/saolei/common/forms.py             UploadVideoForm（file + skipTournamentCheck）
back_end/saolei/common/utils.py             new_video_by_file（标识校验、去重）
back_end/saolei/utils/parser.py             MSVideoParser（扩展名、完成局、level、is_valid）
back_end/saolei/utils/ranking_lock.py       503 + Retry-After
back_end/saolei/msuser/models.py            配额默认 100
front_end/src/services/videoUploadService.ts  上传请求与错误映射
front_end/src/components/Login/LoginForm.vue  登录字段与"记住我"
front_end/src/http.ts                       axios 全局 form-urlencoded + withCredentials
front_end/src/components/VideoUpload/utils.ts 客户端预检（≤5MB、扩展名、is_valid）
```

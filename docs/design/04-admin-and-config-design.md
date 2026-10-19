# 04 · 管理员视角与配置体系

> 现有对话式控制台(`/admin`,`interactive-bot/admin/`)的"待办优先首页、待跟进队列、客户处理卡、订单、数据、找客户、设置向导、操作审计、SLA 提醒、每日摘要"骨架是对的,保留并扩展。本文回答三件事:**谁在用、他们怕什么;配置如何做到"几乎全开放但改不坏";数据如何让业主看到流失点。**

---

## 1. 管理员的真实处境

### 1.1 业主本人(owner)
- **高频**:早上看昨晚发生了什么(摘要);看有没有大单/冒充报告;改价格、改话术、加业务线;偶尔亲自接大客户。
- **最怕**:① 改了一个配置,机器人对所有客户沉默或说错价;② 收款地址被改(后台账号被盗/同事误操作)= 钱直接打到别人那里;③ 看不出钱从哪里漏掉。
- **设计回应**:发布前自检 + 一键回滚(§3.5);收款地址"高危协议"(§5);漏斗页与每日摘要的卡点报警(§7)。

### 1.2 深夜值班客服(agent)
- **高频**:看待跟进队列 → 点开客户 → 一屏看懂(简报)→ 接管回复 → 出报价 → 结案或恢复 AI。手机单手操作。
- **最怕**:① 漏掉 P0;② 报错价;③ 接管后 AI 还在插话;④ 不知道客户之前说过什么。
- **设计回应**:P0 置顶 + 二次提醒;报价向导只允许从商品目录或"自定义金额 + 二次确认"出单(§2.4);接管即 epoch bump(现有);客户卡里简报 + 最近 5 条对话 + 订单。

### 1.3 对账财务(finance)
- **高频**:看"待核对"订单;核对少付/多付/错网络;确认收款、发起退款登记;月底导出订单。
- **最怕**:重复确认、确认了不该确认的、找不到某笔交易对应哪个客户。
- **设计回应**:订单页按"需要我处理"排序;每笔显示链上 TxID 与核验结论;确认收款需填写依据(TxID 或备注),写入审计;导出 CSV(§2.6)。

### 1.4 新入职运营(manager 或 agent)
- **高频**:改文案、加 FAQ、调业务线关键词;想知道某个设置是干什么的。
- **最怕**:不知道改了会影响哪里;改完不敢发布。
- **设计回应**:每个配置项有中文说明与"影响范围";改动先成草稿,可以**用自己的账号以客户视角试走一遍**(§6);发布由 manager/owner 批准(角色决定)。

### 1.5 事后审计的人(viewer/auditor)
- **高频**:查"某天某客户为什么被报了这个价""谁改了收款地址""谁确认了这笔款"。
- **设计回应**:所有写操作进 `admin_audit`(现有)并扩展为"前值 → 后值"(配置类存 revision id);可按客户/订单/管理员/时间筛选;只读角色。

---

## 2. 控制台信息架构(对话式,保留现有 `adm:` 回调体系)

```
/admin 首页(待办优先)
├── 待跟进(队列:P0→P3,按等待时长)── 客户处理卡
│                                          ├── 简报 / 最近对话 / 订单
│                                          ├── 接管 · 结案 · 恢复 AI · 加额度
│                                          ├── 出报价(向导)
│                                          └── 打开话题
├── 订单(需要我处理 / 等待客户付款 / 进行中 / 最近完成)── 订单卡(核对 · 确认 · 交付 · 取消 · 登记退款)
├── 数据(今日 / 7 天漏斗 / 卡点 / 来源 / AI 成本)
├── 找客户(ID / @用户名 / 订单号 / TxID)
├── 设置中心(按领域分组,见 §3.3)── 配置项 → 查看 / 编辑草稿 / 预览 / 发布 / 历史与回滚
├── 操作记录(筛选)
└── 更多工具(广播、模型配置、管理员与角色、管理群、导入导出、客户视角模式)
```

### 2.1 首页(在现有 `views.home` 基础上改)
新增/调整:
- 待办区第一行:P0(红);第二行:**待报价**(quote_request 数量 + 最久等待);第三行:冒充报告(过去 24h 新增)。
- "今天"区:改为**漏斗一行**:`进线 42 → 有用回应 40 → 完成问答 18 → AI 有效对话 15 → 报价/入口 11 → 付款 3`。
- 系统区:AI 状态、收款通道状态(TRC20 已配置/未配置,链上查询服务最近一次成功时间)、配置版本号(`配置 v37 · 2 小时前 · 张三`)。

### 2.2 客户处理卡
在现有 `views.case` 基础上:
- **简报区**(渲染 `brief_json`):`来源 · 业务线 · 已知槽位(逐项) · 预算(区间/拒绝透露) · 信号(急/愤怒/大单/分销/信任疑虑) · 待回答的问题`。
- **最近 5 条对话**(`frontdesk_message`,每条截断 80 字,标注客户/AI/同事)。
- 按钮新增:`[出报价]`(向导)、`[发送官方入口卡]`(软件客户一键发 `card.official_buy`)、`[标记为冒充报告已处理]`(仅冒充报告)。

### 2.3 待跟进队列
- 现有排序保留;每行增加原因标签(待报价/付款/退款/大单/真人请求/敏感);筛选:全部/紧急/待报价/我接管的。
- 被接管超过 `takeover_stale_hours` 的显示 ⏰(现有逻辑)。

### 2.4 报价向导(R-ADMIN-4)
1. 选择商品:目录列表(按客户简报的业务线排前)或 `[自定义]`。
2. 自定义时依次输入:标题(≤32 字)→ 说明(≤200 字,可留空,默认取简报需求)→ 金额(USDT,数字)→ 交付承诺 `{next_step}`(可选模板)。
3. 预览:**以客户语言**渲染报价卡,显示唯一金额与收款网络/地址尾号。
4. 金额 ≥ `quote.confirm_above_u`(默认 1000)或自定义金额 → 要求输入金额再确认一次("请再输入一次金额")。
5. 发送:Bot 私聊;客户若是 Business 来源 → 同时在 Business 会话发 `biz.bind_bot`(若 24h 窗口内)。
6. 审计:`发送报价 · 客户 · 订单号 · 金额 · 商品/自定义`。

### 2.5 订单页
- 分组:**需要我处理**(payment_review、paid 待交付)/ 等待客户付款 / 进行中 / 最近完成。
- 订单卡:金额、唯一尾数、网络、TxID(可点链接到区块浏览器)、核验结论、客户、来源 case;按钮按角色显示(§4)。
- "确认收款"(人工)必须选择依据:`[已核对 TxID]`(需输入/选择 TxID)或 `[其他依据]`(需输入备注);写入 `PaymentEvent(kind=manual)` 与审计。

### 2.6 导出
- `/export orders 2026-10` → 机器人发送 CSV 文件(订单号、客户 ID、标题、金额、网络、TxID、状态、创建/付款/交付时间、操作人)。finance 与 owner 可用。

---

## 3. 配置体系

### 3.1 现状问题
- 配置散落:`texts.py`、`precheck.py` 常量、`lines.py` DEFAULT_LINES、`policy.proposed.json`(启动时读取的只读文件)、`AppSetting`(部分 JSON)、`global_settings`、`prompt_settings`、`.env`。
- 只有部分有校验(`set_lines`、`set_catalog`),没有版本与回滚,没有预览。

### 3.2 目标模型:注册表 + 版本化存储
- **配置注册表** `config/registry.py`:每个配置键声明一次:
```python
ConfigKey(
  key="copy.welcome",                # 命名空间.名称
  group="文案 · 入口",                # 后台分组
  title="欢迎卡",                     # 中文名
  help="客户第一次来、或发送 /start 时看到的卡片。",
  kind="copy",                        # copy | json | number | bool | list | secret
  schema={...},                       # JSON Schema(copy 类型为 {"zh": str, "en": str})
  default=DEFAULTS["copy.welcome"],   # 来自 03 的定稿文案
  lint=["placeholders:", "max_len:700", "html", "forbid_precheck_words"],
  edit_role="manager",                # 最低编辑角色
  publish_role="manager",             # 最低发布角色
  risk="normal",                      # normal | high | locked
  impact="所有新客户的第一条消息",
)
```
- **存储**:新表 `config_revision`(05 §3.1):每次发布写入一个**完整快照**(所有被覆盖的键的 JSON),`active` 指针指向当前版本;草稿存于 `config_draft`(每个管理员一份)。
- **读取**:`cfg.get("copy.welcome", lang="zh")` → 当前版本覆盖值 ∨ 注册表默认值;进程内缓存,发布时失效(单进程,无需分布式)。
- **迁移**:现有 `AppSetting` 中的 `frontdesk.lines`、`commerce.catalog`、`commerce.ton`、提示词等,在首次启动时导入为 revision 1(05 §4)。

### 3.3 命名空间与开放范围

| 分组 | 键(示例) | 类型 | 编辑/发布角色 | 风险 |
|---|---|---|---|---|
| 文案 · 入口/问答/售后/订单/转交/降级 | `copy.*`(03 §3–4 每个卡片一键) | copy | manager / manager | normal |
| 入口识别 | `entry.intents`(词表),`entry.source_order` | json | manager | normal(动作 1–5 locked) |
| 业务线 | `lines`(id、名称、关键词、敏感标记、答前卡、notes) | json | manager / owner | normal;`sensitive` 改为 false 需 owner |
| 问答流程 | `intake.flows`、`intake.max_questions`、`intake.llm_extract.*` | json | manager | normal |
| 预算 | `intake.budget_bands`(各线区间)、`escalation.large_deal_u` | json | manager | normal |
| 升级矩阵 | `escalation.rules` | json | owner | normal(P0 三条 locked) |
| SLA 与值班 | `sla.business_hours`、`sla.minutes`、`sla.owner_alert_after` | json | owner | normal |
| AI 剧本 | `playbooks.<line>`、`cta.rules` | json | manager | normal |
| AI 额度与成本 | `quotas.*`、`budget.*`、`llm.prices` | number | owner | normal |
| 风控 | `abuse.*`、`rate_limit.*` | number | owner | normal |
| 知识库 | `kb.products`、`kb.policies`、`kb.faq`、`kb.official` | json | manager / owner(价格字段变更需 owner) | normal |
| 体验 | `experience.effects.*`、`experience.pins.*`、`experience.reactions.*`、`experience.drafts.enabled` | bool/str | manager | normal |
| 通知 | `notify.routes`(原因 → 管理群话题/私信谁)、`notify.digest_hour` | json | owner | normal |
| 信任 | `trust.official_accounts` | list | owner | **high** |
| 收款 | `payments.usdt.addresses`、`payments.tolerance`、`payments.quote_valid_hours`、`payments.stars.enabled` | json | owner | **high** |
| 商品目录 | `catalog` | json | manager / owner | normal(价格变更需 owner 发布) |
| 角色 | `access.roles` | json | owner | **high** |
| 图标 | `ui.icons` 覆盖、`ui.custom_emoji.enabled` | json | manager | normal |
| **锁定(不出现在后台可编辑项)** | 护栏规则、秘密索要检测、AI 披露条款、persona 前言 1–9 条、`paid` 只认已验证事实、截图不是付款证明、敏感线不进 AI、合规底线条款 | — | 仅代码 | locked |
| **密钥(只写不读)** | 模型 API Key、链上查询 API Key | secret | owner | high;后台只显示"已设置 · 尾号 xxxx",可替换不可查看 |

### 3.4 校验(lint)规则
- `placeholders:{a},{b}`:中英版本必须包含且只包含声明的占位符(防止 `.format` 崩溃 —— 这是"机器人沉默"的头号原因)。
- `max_len:n`:Telegram 单条 4096 字符;卡片默认上限 700。
- `html`:只允许 `b i u s code pre a tg-emoji blockquote`,标签成对。
- `forbid_precheck_words`:`intake` 与 `copy.q.*` 命名空间禁止"人工""安全验证""安全预检""Security Verified""verify"(区分大小写不敏感)。
- `forbid_claims`(警告级):"保证、包过、稳赚、无限、100%、guarantee、unlimited" → 需二次确认。
- JSON Schema 校验(业务线、流程、剧本、升级规则、商品目录)。
- **流程可达性**:每个 intake flow 用一组样例回答模拟走完,必须到达出口;choice 题至少 2 个选项;band 题必须含"先不说"。
- **跨键一致性**:剧本引用的槽位必须在流程中存在;CTA 引用的卡片键必须存在;升级规则引用的 reason 必须在 `notify.routes` 或默认路由中。

### 3.5 草稿 → 预览 → 发布 → 回滚
1. **编辑**:在设置中心点某项 → 显示当前值(copy 类显示中英渲染效果)→ `[编辑]` → 机器人提示发送新内容(copy:先中文后英文,或发送 `zh:… / en:…`;json:发送 JSON 文本或 `.json` 文件)→ 进入草稿。
2. **自检**:草稿保存即跑 lint;失败显示具体原因(行号/字段),不可发布。
3. **预览**:`[预览]` → 机器人在管理员私聊中按**客户视角**发送渲染后的卡片(中英各一条,含按钮,按钮点击在预览中无副作用,callback 前缀 `pv:`)。流程类 → `[试走一遍]` 进入沙盒(§6)。
4. **发布**:`[发布]` → 显示 diff 摘要(键 + 前值前 60 字 → 新值前 60 字)→ 确认 → 写新 revision、切换 active、审计、管理群通知"配置 v38 已发布:欢迎卡(张三)"。
5. **回滚**:设置中心首页 `[历史版本]` → 列出最近 20 个 revision(时间、操作人、改动键)→ `[回滚到此版本]` → 生成一个**新** revision(内容=旧快照),不删除历史。
6. **发布后守护**(轻量):发布后 15 分钟内,若渲染/格式化异常(由 `ui.kit` 与 `intake` 捕获并计数)≥ 3 次 → 自动回滚到上一版并通知 owner。只针对 `copy.*`、`intake.*`、`lines`、`playbooks.*`。

### 3.6 导入/导出(R-ADMIN-6,无 Mini App 的批量编辑方案)
- `/config export [命名空间]` → 机器人发送 `whitecat-config-v38.json`(不含 secret;含 high 项但地址显示完整,仅 owner 可导出 high 项)。
- 管理员在电脑上编辑后,把 `.json` 文件发给机器人 → 机器人解析 → 对每个键 lint → 显示 diff 汇总 → 草稿 → 走正常预览/发布。
- 这让业主可以用任何文本编辑器批量改所有文案,而不需要 Mini App。

---

## 4. 角色与权限

| 能力 | owner | manager | agent | finance | viewer |
|---|---|---|---|---|---|
| 看首页/队列/客户卡/订单 | ✔ | ✔ | ✔ | ✔ | ✔ |
| 接管/结案/恢复 AI/加额度 | ✔ | ✔ | ✔ | ✘ | ✘ |
| 出报价(目录商品) | ✔ | ✔ | ✔ | ✘ | ✘ |
| 出报价(自定义金额) | ✔ | ✔ | ✔(≤ `quote.agent_custom_max_u`,默认 1000) | ✘ | ✘ |
| 确认收款 / 取消订单 / 登记退款 | ✔ | ✘ | ✘ | ✔ | ✘ |
| 开始交付 / 标记已交付 | ✔ | ✔ | ✔ | ✔ | ✘ |
| 编辑 normal 配置草稿 | ✔ | ✔ | ✘ | ✘ | ✘ |
| 发布 normal 配置 | ✔ | ✔ | ✘ | ✘ | ✘ |
| 编辑/发布 high 配置(收款、白名单、角色) | ✔ | ✘ | ✘ | ✘ | ✘ |
| 回滚配置 | ✔ | ✔(仅 normal) | ✘ | ✘ | ✘ |
| 导出订单 | ✔ | ✘ | ✘ | ✔ | ✘ |
| 广播 | ✔ | ✔ | ✘ | ✘ | ✘ |
| 查看操作记录 | ✔ | ✔ | 仅自己 | 仅订单相关 | ✔ |

- 存储:`access.roles` = `{"<user_id>": "owner|manager|agent|finance|viewer"}`;`.env` 的 `ADMIN_USER_IDS` 在迁移时全部导入为 `owner`(保证升级后不丢权限),之后业主在后台降级他人。
- 权限检查集中在 `admin/permissions.py::require(admin_id, capability)`,所有 `adm:` 回调与命令调用它;拒绝时 `query.answer("你的角色(客服)没有这个权限", show_alert=True)`。
- 管理群内的现有按钮(接管、恢复 AI、订单按钮)同样走权限检查。

---

## 5. 高危操作协议(R-ADMIN-5)

适用:`payments.usdt.addresses`、`payments.ton.address`、`trust.official_accounts`、`access.roles`。

1. 只有 owner 能编辑;草稿保存后,要求 owner **输入确认短语**(例如收款地址的后 6 位)。
2. 发布后**延迟生效** `high_risk_delay_minutes`(默认 15 分钟):期间新报价仍使用旧地址;管理群与所有 owner 私聊收到通知:"⚠️ 收款地址将在 15 分钟后从 …a1b2c3 变更为 …d4e5f6(操作人:XX)。如非本人操作,点击[立即撤销]"。
3. 任一 owner 可在生效前撤销。
4. 生效后:所有**未付款**的旧订单卡仍显示旧地址(旧地址继续被监听 7 天,避免客户付款落空);新订单使用新地址。
5. 审计记录完整前值与后值。

> 理由:对话式后台的安全边界是"谁拿到了管理员的 Telegram 账号"。延迟 + 广播 + 撤销,让单个账号被盗无法静默转走资金。

---

## 6. 预览与沙盒("客户视角模式",R-ADMIN-3)

- `/asclient on`:当前管理员在**机器人私聊**中被视为普通客户(不跳过前台、不进入管理命令),所有事件带 `test=1`,不计入漏斗;handoff 通知在管理群中标注 `[测试]`;不创建真实订单(报价向导对测试客户生成 `TEST-` 前缀订单,链上核验走假查询器或真实小额,可配)。
- `/asclient off`:退出;可选 `/asclient reset` 清空自己的 case、对话记忆与测试订单,便于从头再测。
- `/asclient draft`:沙盒内使用**当前管理员的草稿配置**而非线上配置 —— 这样改完欢迎卡/流程可以先自己走一遍再发布。
- Business 场景测试:业主用另一个号给自己的 Business 账号发消息(真机步骤见 02 §6);沙盒无法模拟 Business,方案如实说明。

---

## 7. 可观测性(管理员看得见的部分)

### 7.1 数据页
- **今日/7 天/30 天** 切换。
- 漏斗(F1–F8,01 §3.1),每一步数值与转化率,与上一周期对比。
- **卡点**:每个问答题的出示数 → 作答数 → 流失率;流失率 > 25% 标红。
- **来源**:按 `source` 分组的进线与付款。
- **转交原因分布**与平均首次回复时间(按优先级)。
- AI:回合数、有效对话率、护栏拦截数、成本(真实 token,WP11 后)。

### 7.2 每日摘要(现有 09:00 任务扩展)
```
☀️ 昨日白猫前台
进线 42 → 问答完成 18 → 报价/入口 11 → 付款 3(312 U)
最大卡点:定制 · 预算题 流失 31%
平均首次回复:P0 8 分钟 · P1 26 分钟
待处理:P0 0 · 待报价 2 · 冒充报告 1
配置变更:v37→v38(欢迎卡,张三)
```

### 7.3 告警(即时,管理群)
- P0 进入队列(现有);SLA 超时(现有 + owner 升级);熔断打开;预算 80%/100%;链上查询连续失败 10 分钟;配置自动回滚;冒充报告。

---

## 8. 通知路由(`notify.routes`)

```json
{
  "default": {"chat": "admin_group", "topic": "customer"},
  "P0": {"chat": "admin_group", "topic": "customer", "also_dm_roles": ["owner"], "after_minutes": 10},
  "impersonation_report": {"chat": "admin_group", "topic": "general"},
  "payment_review": {"chat": "admin_group", "topic": "customer", "also_dm_roles": ["finance"]},
  "config_published": {"chat": "admin_group", "topic": "general"}
}
```
- `topic=customer` = 该客户的论坛话题(现有);`general` = 管理群主话题。
- `also_dm_roles`:同时私信该角色的所有管理员(需对方启动过机器人)。

---

## 9. Mini App(可选,WP12)

**何时值得做**:当配置项数量与编辑频率使对话式编辑明显吃力(例如每周大量改知识库条目),或需要可视化漏斗图表时。

**业主需要提供**:① 一个 HTTPS 域名(或子域名);② 托管:静态前端可放任意静态托管;后端 API 需要能被公网 HTTPS 访问 —— 机器人现运行在 Windows 本机,最低成本方案是 Cloudflare Tunnel 把本机的 API 端口暴露为 HTTPS 子域名;③ 在 @BotFather 设置 Mini App URL。

**技术要点**【依据:core.telegram.org/bots/webapps】:前端把 `Telegram.WebApp.initData` 原样发给后端;后端以 `HMAC_SHA256(bot_token, "WebAppData")` 为密钥校验 `hash`,并检查 `auth_date` 不超过 24 小时;从 `user.id` 取角色后复用 §4 权限。注意 Bot API 10.2 起 Mini App 方法只能在原始域名下调用(2026-07-20 自动启用)。`web_app` 按钮"Not supported for messages sent on behalf of a business account",只用于 Bot 私聊/管理员。

**不依赖它的最小方案**:§3 的对话式设置中心 + §3.6 的 JSON 导入导出,已覆盖全部配置能力;漏斗用文字条形图(现有 `_bar`)。

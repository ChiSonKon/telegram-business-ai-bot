# 05 · 架构、数据模型与技术核实

> 原则:**在现有内核上长,不推倒重来。** `whitecat_core/`(护盾、语言、版本号、熔断、护栏、订单状态机)和 `ai_turn.py` 的回合网关设计是正确的,保留;重写的是"前台问答"和"配置";新增的是"入口/简报/升级/体验/埋点/USDT"。

---

## 1. 现有代码盘点与去留

| 路径 | 结论 | 说明 |
|---|---|---|
| `whitecat_core/*` | **保留** | 纯逻辑、已测;`guardrail` 保持锁定 |
| `interactive-bot/ai_turn.py` | **改** | 首轮改为简报注入(§7);持久化记忆;草稿占位;剧本出口动作;CTA |
| `frontdesk/gate.py` | **重写** → `frontdesk/entry.py` + `frontdesk/router.py` | 入口分类器 + 状态路由 |
| `frontdesk/precheck.py` | **重写** → `frontdesk/intake/` | 数据驱动问答引擎;保留"旧卡片乱序点击无副作用"语义 |
| `frontdesk/texts.py` | **迁移** → 配置 `copy.*` 默认值(`config/defaults_copy.py`) | 保留 `t()` 接口签名,内部改读配置 |
| `frontdesk/cards.py` | **改** | 欢迎卡新结构;卡片构建函数读配置 |
| `frontdesk/lines.py` | **改** | 业务线数据迁入配置;新增 `sensitive`、`answer_first_card`、`name_en` 等字段;`brief()` 保留 |
| `frontdesk/service.py` | **改** | `POLICY` 从 `policy.proposed.json` 改为读配置;新增 `brief` 读写;`handoff()` 接收 priority 与 brief;删除 `set_intent` 中按关键词猜角色的旧逻辑(由 entry 负责) |
| `frontdesk/persona.py` | **改** | 前言新增条款 8、9(锁定);新增 `assemble_system()` |
| `frontdesk/abuse.py`、`budget.py` | **保留** | 预算读配置 |
| `admin/*` | **扩展** | 权限、设置中心、报价向导、数据页 |
| `commerce/service.py` | **改** | 订单新增 USDT 字段;报价有效期读配置;`create_quote` 修正"作废所有 SKU"缺陷(见 §9) |
| `commerce/ton_monitor.py` | **保留** | 继续作为可选通道 |
| `commerce/` 新增 `providers/usdt_tron.py`、`order_card.py` | **新增** | |
| `handlers/user_handlers.py`、`business_handlers.py` | **改** | 统一调用 `frontdesk.router.handle_customer_text()`;Business 首条披露;语音回应 |
| `handlers/callback_handlers.py` | **改** | 前台回调改用 `fd:` 前缀(保留旧 `frontdesk_*` 前缀的兼容处理:旧卡片点击 → 提示已更新) |
| `handlers/consultation_handlers.py`、`consultation/*`、`/consult` | **删除**(WP11) | 与新前台并行的旧问卷;删除前确认无 `ConsultationSession` 活跃数据(迁移脚本打印数量) |
| `humanlike_sender.py` | **改** | Business 禁用 AI 语音(D7);语音前加"(AI 语音)"说明 |
| `timeout_tracker.py` | **改** | 内存表 → 不再需要(关怀卡策略关闭);保留函数签名为空实现或删除调用 |
| `memory/short_term.py` | **替换** → `memory/conversation_store.py`(DB) | 重启不失忆 |
| `docs/frontdesk/policy.proposed.json` | **退役** | 内容迁入配置默认值;文件保留为历史 |
| `knowledge_base.json` | **改** | `scraped_*` 移到 `quarantine` 键;`plans` 增加 `name_en`;通过配置 `kb.*` 可覆盖 |

---

## 2. 目标模块结构

```
interactive-bot/
  config/
    __init__.py          # cfg = ConfigService 单例: get(key, lang=None), get_raw(key)
    registry.py          # ConfigKey 定义与全部键的注册
    defaults_copy.py     # 03 中所有文案默认值(中英)
    defaults_flows.py    # intake 流程、剧本、升级矩阵、CTA 默认值
    store.py             # revision/draft 读写、发布、回滚、导入导出
    lint.py              # 校验规则(04 §3.4)
  frontdesk/
    entry.py             # 入口分类器 classify_entry(text, ctx) -> EntryIntent
    router.py            # handle_customer_text / handle_start / handle_callback 的统一编排
    answers.py           # 先答卡:价目、总览、官方入口、FAQ、身份直答、定制/IP/AI 说明卡
    trust.py             # 真伪核验、/order 查询、冒充报告
    escalation.py        # 升级矩阵评估 evaluate(brief, intent, ctx) -> Escalation|None;eta 计算
    intake/
      engine.py          # 槽位机:start_flow / next_question / accept_text / accept_button / complete
      extract_rules.py   # 规则抽取器(纯函数)
      extract_llm.py     # 可选模型抽取
      brief.py           # HandoffBrief 数据类、渲染(给 AI / 给管理员)
      resume.py          # 断点续接与回访判定
    (保留) abuse.py budget.py lines.py persona.py service.py cards.py texts.py(薄封装)
  ai/
    playbook.py          # 剧本阶段判定与渲染;出口动作
    cta.py               # CTA 规则
  experience/
    capabilities.py      # 按 (通道, 能力) 缓存可用性,失败自动降级 24h
    tgx.py               # Bot API > 7.4 的原始调用封装(do_api_request)
    effects.py pins.py cleanup.py reactions.py drafts.py
    ui_registry.py       # ui_message 表读写:记录关键卡片 message_id 与种类
  analytics/
    events.py            # emit(event, user_id, case_id=None, **props)
    funnel.py            # 漏斗/卡点/来源查询
  commerce/
    providers/base.py usdt_tron.py usdt_ton.py(可选) stars.py(现有逻辑迁入)
    order_card.py        # 订单卡渲染与原位更新、置顶
  memory/
    conversation_store.py
  admin/
    permissions.py settings_center.py quote_wizard.py data_views.py sandbox.py
```

**依赖方向**:`handlers → frontdesk.router → (entry, intake, escalation, answers, trust) → (config, analytics, experience, service)`;`ai_turn → (ai.playbook, intake.brief, memory, experience.drafts)`。`config` 与 `analytics` 不依赖任何上层模块。

---

## 3. 数据模型

### 3.1 新表(`db/model.py` 追加;`create_all` 自动建表)

```python
class FunnelEvent(Base):
    __tablename__ = "funnel_event"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id = Column(Integer, index=True, nullable=False)
    case_id = Column(Integer, index=True)
    channel = Column(String(16))            # bot | business
    source = Column(String(64))
    event = Column(String(48), index=True, nullable=False)
    line = Column(String(48))
    props_json = Column(Text, default="{}") # 仅枚举与数字,不放客户原文
    test = Column(Boolean, default=False, index=True)

class ConfigRevision(Base):
    __tablename__ = "config_revision"
    id = Column(Integer, primary_key=True)          # 版本号
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    author_id = Column(Integer, nullable=False)
    note = Column(String(256), default="")
    snapshot_json = Column(Text, nullable=False)    # 所有覆盖键的完整快照 {key: value}
    changed_keys = Column(Text, default="[]")
    effective_at = Column(Float)                    # 高危延迟生效(unix 秒);None=立即
    reverted_from = Column(Integer)                 # 回滚来源版本

class ConfigDraft(Base):
    __tablename__ = "config_draft"
    id = Column(Integer, primary_key=True)
    author_id = Column(Integer, index=True, nullable=False)
    key = Column(String(128), nullable=False)
    value_json = Column(Text, nullable=False)
    lint_json = Column(Text, default="[]")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # UNIQUE(author_id, key)

class UiMessage(Base):
    __tablename__ = "ui_message"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    chat_id = Column(Integer, nullable=False)
    message_id = Column(Integer, nullable=False)
    biz_conn_id = Column(String(256))
    kind = Column(String(32), nullable=False)       # intake | welcome | order | quote | known_issue | checking | captcha | pin_service
    ref = Column(String(64))                         # case_id / order_id
    pinned = Column(Boolean, default=False)
    state = Column(String(16), default="live")       # live | collapsed | void | deleted
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    unpin_after = Column(Float)

class ConversationTurn(Base):
    __tablename__ = "conversation_turn"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    case_id = Column(Integer, index=True)
    role = Column(String(16), nullable=False)        # user | assistant | staff
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class CustomerProfile(Base):
    __tablename__ = "customer_profile"
    user_id = Column(Integer, primary_key=True)
    lang = Column(String(8))
    first_source = Column(String(64))
    last_source = Column(String(64))
    lines_json = Column(Text, default="[]")          # 咨询过的业务线
    paid_orders = Column(Integer, default=0)
    biz_disclosed = Column(Boolean, default=False)   # Business 披露是否已发
    bot_started = Column(Boolean, default=False)     # 是否启动过 Bot 私聊(Stars/订单通知前提)
    last_effect_at = Column(Float)                   # 特效冷却
    notes = Column(Text, default="")                 # 同事备注
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class ChainLookup(Base):
    __tablename__ = "chain_lookup"                   # 客户提交的 TxID 与核验结果,防重复查询与审计
    id = Column(Integer, primary_key=True)
    order_id = Column(String(32), index=True)
    network = Column(String(16))
    txid = Column(String(128), index=True)
    result = Column(String(32))                       # exact | tolerance | underpaid | overpaid | not_found | wrong_asset | error
    raw_json = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

### 3.2 现有表加列(`db/migrate.py::ADD_COLUMNS` 追加)
```python
("frontdesk_case", "brief_json", "TEXT"),
("frontdesk_case", "source", "VARCHAR(64)"),
("frontdesk_case", "priority", "VARCHAR(4)"),
("frontdesk_case", "intake_flow", "VARCHAR(48)"),
("frontdesk_case", "last_customer_at", "DATETIME"),
("frontdesk_case", "last_status_card_at", "DATETIME"),
("frontdesk_case", "test", "BOOLEAN"),
("commerce_order", "currency", "VARCHAR(16)"),       # USDT | XTR | TON
("commerce_order", "network", "VARCHAR(16)"),        # TRC20 | TON
("commerce_order", "amount_str", "VARCHAR(32)"),     # 唯一金额字符串 "287.93"(精确,不用浮点)
("commerce_order", "base_amount_str", "VARCHAR(32)"),
("commerce_order", "pay_address", "VARCHAR(128)"),
("commerce_order", "detail", "TEXT"),
("commerce_order", "next_step", "TEXT"),
("commerce_order", "paid_at", "DATETIME"),
("commerce_order", "delivered_at", "DATETIME"),
("commerce_order", "expires_on", "DATETIME"),       # 本机器人售出商品的服务到期日(续费提醒)
("commerce_order", "source_case_id", "INTEGER"),
("commerce_order", "created_by", "INTEGER"),
("commerce_order", "test", "BOOLEAN"),
("admin_audit", "before_json", "TEXT"),
("admin_audit", "after_json", "TEXT"),
```
- `QUOTE_PENDING` **不**新增状态值:使用 `state=HUMAN_PENDING` + `handoff_reason=quote_request`(03 §2.1)。`ANSWERED` 是新状态值(字符串列,无需迁移)。
- 金额统一以**字符串十进制**存储与比较(`decimal.Decimal`),避免浮点误差。

### 3.3 事件清单(`analytics.events.emit` 的合法 event 名,写成常量集合,未知事件名在测试中报错)
| event | props | 写入位置 |
|---|---|---|
| entry | source, is_returning, intent | router(新 case 首条/`/start`) |
| first_value | card(price_software/price_overview/official_buy/verify/is_bot/ai/staff/known_issue) | answers/ai_turn/admin 回复 |
| answer_card_shown | card | answers |
| intake_started | flow | intake.engine |
| intake_step_shown | slot | intake.engine |
| intake_answered | slot, method(button/text/rule/llm) | intake.engine |
| intake_skipped | slot | intake.engine |
| intake_side_question | slot | intake.engine |
| intake_completed | flow, asked(int), secs(int) | intake.engine |
| intake_abandoned | flow, slot | 定时任务(intake 中 > 30 分钟无回应,每 case 一次) |
| intake_corrected | slot | 确认卡"改一下" |
| ai_turn | ok(bool), reason, latency_ms, turn_no | ai_turn |
| ai_effective | turn_no | router(AI 回复后 30 分钟内客户再发言) |
| ai_stopped | — | 草稿停止 |
| cta_shown / cta_clicked | kind | ai.cta / 回调 |
| handoff | reason, prio | service.handoff |
| human_first_reply | wait_min, prio | 管理群回复转发给客户时(首次) |
| quote_sent | order_id, amount | quote_wizard |
| pay_clicked | method | 回调 |
| txid_submitted | network | commerce |
| payment_verified | verdict, method(auto/txid/manual) | commerce |
| payment_review | verdict | commerce |
| delivered | — | commerce |
| self_resolved | issue | 已知问题卡 |
| verify_check | kind(account/address/order), official(bool) | trust |
| impersonation_report | — | trust |
| returned | gap_hours, action(continue/new) | resume |
| effect_sent / pin_set / pin_cleared | kind | experience |
| config_published / config_rolled_back | revision | config.store |

### 3.4 隐私
- `funnel_event.props_json` 禁止包含客户文本(在 `emit` 中对 props 做类型白名单:str 长度 ≤32 且必须来自枚举;int/float/bool)。
- `conversation_turn` 保留 90 天(配置),定时清理;`frontdesk_message` 现有。
- 导出 CSV 不含对话内容。

---

## 4. 迁移方案(首次启动执行,幂等)

1. `create_all` 建新表;`ensure_columns` 加列。
2. `config.store.bootstrap()`:若 `config_revision` 为空:
   - 读取现有 `AppSetting`:`frontdesk.lines` → `lines`;`commerce.catalog` → `catalog`;`commerce.ton` → `payments.ton`(地址)与 secret(API key);运营提示词(`prompt_settings`)→ `ai.operator_prompt`;`global_settings` 中的 `disable_captcha / rate_limit_per_minute / message_interval / llm_price_* / global_daily_budget_usd / global_daily_reply_cap` → 对应键;
   - 读取 `docs/frontdesk/policy.proposed.json` 的 quotas/handoff/knowledge → `quotas.*` 等;
   - `.env ADMIN_USER_IDS` → `access.roles`(全部 owner);
   - 写入 revision 1(author_id=0,note="迁移自旧配置")。
3. 旧读取点改为读配置;`AppSetting` 旧键保留不删(回退版本时仍可读)。
4. 业务线迁移时:`kyc`、`account` 两条(若存在于 AppSetting)标记 `sensitive=true`,notes 中推销话术移到 `notes_internal`(只给同事看,不进 AI)。
5. 知识库:`knowledge_base.json` 的 `scraped_products`/`scraped_services` 移入 `quarantine` 字段(脚本一次性改写文件,原文件备份为 `knowledge_base.backup-<date>.json`);`scraper.py` 改为写入 `quarantine`。
6. 进行中的 PRECHECK case:旧 `answers_json` 键(line/need/extra/product/issue/desc/ptype/offer/how)映射到新槽位(line→line, need→need, extra→key_feature, product→product, issue→issue, desc→desc, ptype→ptype, offer→scale, how→scale 追加);映射函数单测。
7. 回滚:新代码不删除旧表/旧键;回退到旧版本代码时,旧逻辑仍能读取 `AppSetting` 与 `answers_json`(新增列被旧代码忽略)。

---

## 5. 支付:USDT 通道设计

### 5.1 Provider 接口(`commerce/providers/base.py`)
```python
class PaymentProvider(Protocol):
    name: str                                   # "usdt_trc20"
    def enabled(self) -> bool
    def create_intent(self, order) -> dict      # 返回订单卡需要的 {network, address, amount_str}
    async def lookup_txid(self, txid: str, order) -> ChainResult   # 客户提交 TxID
    async def poll(self, since_ts: float) -> list[ChainEvent]      # 按地址轮询入账
```
`ChainResult = {found: bool, to: str, asset: str, amount_str: str, confirmed: bool, ts: float, raw: dict}`。

### 5.2 TRC20(【假设:按 TronGrid 公开 API 设计,必须用真实小额交易验证,验证前不对客户开放】)
- USDT-TRC20 合约地址:`TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t`(公开常量;写入配置默认值,owner 可改但属 high)。
- 轮询:`GET {api_base}/v1/accounts/{address}/transactions/trc20?only_to=true&only_confirmed=true&contract_address={usdt}&min_timestamp={ms}&limit=50`,`api_base` 默认 `https://api.trongrid.io`,`TRON-PRO-API-KEY` 头来自 secret 配置;间隔 20 秒(复用 APScheduler)。
- TxID 查询:优先用同一列表接口按时间窗筛选 `transaction_id == txid`;未命中再调用 `POST {api_base}/wallet/gettransactioninfobyid {"value": txid}`,解析 TRC20 Transfer 日志(topic0 = Transfer 事件签名),确认 `to == address`、合约为 USDT。解析函数 `parse_trc20_list(payload)` / `parse_tx_info(payload)` 为纯函数,用录制的真实响应做测试夹具(Sonnet 先用文档示例构造,真实响应由业主小额测试后补录)。
- 金额:TRC20 USDT 6 位小数;`amount_str = Decimal(value) / 10**6`。
- 匹配:`(address, amount_str == order.amount_str, ts ∈ [order.created, order.expires+宽限])`;唯一认领沿用 `claim_event(f"trc20:{txid}")`。
- 容差:`payments.tolerance = {"abs_u": "1", "pct": "1"}`:`order.amount - received ≤ min(abs, amount*pct%)` → `exact`(标注 tolerance)。
- 唯一金额分配(03 §9.3):在 `create_quote` 中,基础价减 0.01–0.99 的尾数,遍历直到与同地址所有 `awaiting_payment/payment_review` 订单不冲突(最多 99 次;冲突则用 0.001 级)。

### 5.3 订单卡原位更新
- 报价时发送订单卡并记录 `ui_message(kind=order, ref=order_id)`;每次状态变化调用 `order_card.refresh(order_id)`:编辑该消息(文本 = 当前状态模板)并按 03 §2.3 处理置顶/取消置顶;编辑失败(超过 48 小时等)→ 发新卡、旧卡去按钮。
- 通知类消息(`order.paid` 等)作为**新消息**发送(带特效),订单卡本身只更新状态 —— "一张置顶卡看状态,一条新消息给仪式感"。

### 5.4 Business 客户
- 报价时若 `profile.bot_started=false`:订单卡仍尝试发 Bot 私聊(会失败)→ 在 Business 会话(24h 窗口内)发 `biz.bind_bot`;客户点链接 `/start ord_<id>` → `bot_started=true` → 发订单卡并置顶。
- 若 Business 24h 窗口已过且未绑定:管理群提示"客户未绑定机器人,请人工联系"。

---

## 6. Telegram 能力核实结果

> 来源:`https://core.telegram.org/bots/api`(本轮下载于 2026-10-13,页首变更日志最新为 **Bot API 10.3,2026-08-24**)、`/bots/api-changelog`、`/bots/webapps`。项目使用 **python-telegram-bot 21.3,对应 Bot API 7.4**(本地 `telegram.__bot_api_version__` 已验证)。凡 7.4 之后的字段/方法,必须通过 `api_kwargs` 或 `bot.do_api_request()`(PTB 21.3 已有,本地验证)调用。

| # | 能力 | 官方文档原文要点 | 结论与用法 | 待真机验证 |
|---|---|---|---|---|
| 1 | `message_effect_id` | sendMessage:"Unique identifier of the message effect to be added to the message; for private chats only"(7.4 引入) | PTB 21.3 `send_message(message_effect_id=...)` 原生支持(本地验证)。Business 会话属私聊,文档未排除也未确认 | 4 个 ID 是否可用(🎉5046509860389126442 🔥5104841245755180586 👍5107584321108051014 ❤️5044134455711629726——**官方文档未公布 ID 列表**,来自客户端);Business 中是否生效 |
| 2 | `pinChatMessage` | "In private chats and channel direct messages chats, all non-service messages can be pinned";`disable_notification`:"Notifications are always disabled in channels and private chats";参数含 `business_connection_id` | 私聊可置顶无通知;PTB 21.3 `pin_chat_message` **没有** `business_connection_id`(本地验证)→ Business 需 `api_kwargs`。置顶产生服务消息(`Message.pinned_message`) | Business 置顶所需权限(文档 BusinessBotRights 无对应位);私聊置顶服务消息能否由机器人删除 |
| 3 | `deleteMessage` | "can only be deleted if it was sent less than 48 hours ago";"Bots can delete outgoing messages in private chats…";"Bots can delete incoming messages in private chats" | 私聊可删双方消息(48h 内);`deleteMessages` 批量 1–100 | — |
| 3b | `deleteBusinessMessages` | "Requires the can_delete_sent_messages business bot right to delete messages sent by the bot itself, or the can_delete_all_messages…" | Business 删除需权限位;PTB 21.3 无此方法 → `do_api_request("deleteBusinessMessages", …)` | 业主连接时是否勾选了删除权限 |
| 4 | `setMessageReaction` | "as non-premium users, bots can set up to one reaction per message… Paid reactions can't be used by bots";参数**无** `business_connection_id` | 仅 Bot 私聊/群可用;Business 不可用 | 👌 ✍ 在私聊中可用(私聊默认允许全部标准反应——推断) |
| 5 | `sendMessageDraft` | "stream a partial message to a user while the message is being generated… temporary 30-second preview… you must call sendMessage with the complete message";`chat_id`: "target private chat";"Pass an empty text to show a 'Thinking…' placeholder";`can_stop`、`keep_on_stop`(10.3);9.5:"Allowed all bots to use the method sendMessageDraft";参数**无** `business_connection_id` | 仅 Bot 私聊;`do_api_request("sendMessageDraft", {...})`;每 ≤30 秒刷新 | 客户端展示效果;停止按钮 |
| 6 | `MessageGenerationStopped` | Update 字段 `stopped_message_generation`:"A user asked the bot to stop the generation of a message";字段 chat、message_thread_id、draft_id | PTB 21.3 解析为 `update.api_kwargs["stopped_message_generation"]`(本地验证);需在 `allowed_updates` 显式列出 | — |
| 7 | 自定义表情 | 9.4:"Allowed bots to use custom emoji in messages directly sent by the bot to private, group and supergroup chats if the owner of the bot has a Telegram Premium subscription";按钮 `icon_custom_emoji_id` 同条件 | Bot 私聊/管理群:需业主 Premium;**Business 代发消息不属于"directly sent by the bot"——推断不可用**,现有降级逻辑保留 | Business 中是否被拒(现有日志可观察) |
| 8 | 按钮 `style` | "Must be one of 'danger' (red), 'success' (green) or 'primary' (blue)"(9.4) | 现有 `ui/kit.py` 通过 `api_kwargs` 使用,保留 | — |
| 8b | `DisabledButton` / `disabled`(10.3) | InlineKeyboardButton 新增 `disabled` | 可用于"已选"按钮置灰(收起卡片的备选方案);非必需 | — |
| 9 | Business 权限位 | BusinessBotRights:`can_reply`("send and edit messages in the private chats that had incoming messages in the last 24 hours")、`can_read_messages`、`can_delete_sent_messages`、`can_delete_all_messages`、`can_edit_name/bio/profile_photo/username`、`can_change_gift_settings`、`can_view_gifts_and_stars` 等 | **24 小时窗口**是硬约束(R-BIZ-2);启动时调用 `getBusinessConnection` 记录权限位(`do_api_request`),首页显示 | 业主连接实际勾选的权限 |
| 10 | Stars 发票 | `sendInvoice` 参数无 `business_connection_id` | Business 中无法发 Stars 发票(与 owner-checklist 一致);D10 默认关闭 Stars | — |
| 11 | Mini App 鉴权 | `secret_key = HMAC_SHA256(<bot_token>, "WebAppData")`;比较 `hex(HMAC_SHA256(data_check_string, secret_key)) == hash`;10.2:Mini App 方法禁止跨源(2026-07-20 自动启用);`web_app` 按钮 "Not supported for messages sent on behalf of a business account" | WP12 按此实现 | — |
| 12 | 临时消息 `ephemeral_message_parameters`(10.x) | "allowing bots to send group messages… visible only to a specific user and the bot" | 可用于**管理群**中只给操作者看的确认(例如"已接管"),减少群噪音;非必需,列入 WP10 可选 | 超级群话题中是否可用 |
| 13 | `allowed_updates` | — | PTB 21.3 的 `Update.ALL_TYPES` 不含 10.x 新类型 → `run_polling(allowed_updates=Update.ALL_TYPES + ["stopped_message_generation"])` | — |

**真机验证脚本**(WP7 提供 `scripts/probe_capabilities.py`,管理员私聊 `/probe` 触发):依次对管理员自己的私聊发送带 4 个 effect 的消息、置顶并尝试删除服务消息、设置 👌 反应、发送空草稿与带 `can_stop` 的草稿;若配置了 Business 测试会话 ID,再测 Business 中的 effect 与置顶。结果写入 `capabilities` 缓存与管理群报告。**所有体验特性默认值依赖此探测结果:探测失败的能力自动关闭。**

---

## 7. 与 `ai_turn.py` 的衔接(关键改动点)

```python
async def run_ai_turn(..., texts, ...):
    # 现有前置检查不变(ticket/人工锁/预检中/角色/熔断/预算/额度预占)
    brief = intake.brief.load(case_id)                       # 新
    stage = ai.playbook.current_stage(brief)                 # 新
    turn_rules = ai.playbook.turn_instructions(brief, texts, channel)   # 新:多问题/拒绝预算/Business 首条
    history = conversation_store.recent(user_id, 15)         # 替换内存 short_memory
    system = persona.assemble_system(role, lang, line_brief, playbook_text, brief_text, turn_rules, operator_prompt)
    draft = await experience.drafts.start(bot, chat_id, case_id, turn_no) if channel == "bot" else None   # 新
    ... 模型调用(不变)...
    if draft: draft.stop()
    ... 版本复核 / 护栏(不变)...
    if channel == "business" and not profile.biz_disclosed: reply = copy("biz.disclosure") + "\n" + reply   # 新
    ... 发送(不变)...
    conversation_store.add(user_id, case_id, "user", query); ...("assistant", reply)
    analytics.emit("ai_turn", ...)
    await ai.playbook.after_turn(brief, stage)   # 出口动作:handoff / CTA
```
- 删除 `_maybe_offer_leave`(两轮后的"留言给同事"按钮):由 D1(打字要求真人)与升级矩阵取代。若业主否决 D1,保留该函数并改为读 `escalation.explicit_human.min_ai_turns`。
- `run_first_turn`:`texts` 改为客户真实首句或按钮标记(03 §7.1)。
- 估算 token 改为读取 LLM 响应 `usage`(WP11;`llm/client.py` 需返回 usage)。

---

## 8. 测试基础设施:对话模拟器

`tests_core/sim.py`(新):
```python
class SimBot:            # 记录 send_message/edit/pin/unpin/delete/reaction/do_api_request 调用
class SimLLM:            # 可编程回复;记录 system/messages
class Sim:
    def __init__(self, env, channel="bot", lang="zh", uid=7): ...
    def start(self, param=None) -> list[Out]          # /start
    def say(self, text) -> list[Out]                   # 客户文本
    def click(self, label_or_data) -> list[Out]        # 按最近一条带按钮消息的按钮文字或 callback_data 点击
    def advance(self, hours=0, minutes=0)              # 冻结/推进时间(monkeypatch now 函数)
    @property events / case / brief / llm_calls
```
- 黄金对话 G-01…G-24 写在 `tests_core/golden/test_golden_*.py`,每个用例 ≤30 行。
- 断言辅助:`assert_no_words(outs, ["人工","安全验证"])`、`assert_buttons(outs[-1], [...])`、`assert_event(sim, "first_value", card="price_software")`。
- 时间:所有"现在"统一走 `whitecat_core.runtime.now()`(新增,便于 monkeypatch)。

---

## 9. 顺带修正的现有缺陷(在相应工作包中修)

| 缺陷 | 位置 | 修法 | WP |
|---|---|---|---|
| 点击前台按钮即 `abuse.mark_verified` | 未提交 `callback_handlers.py` | 删除 | WP0 |
| /start 与来意按钮把 HUMAN_PENDING 改成 RESOLVED | 未提交 `command_handlers.py`/`callback_handlers.py` | 删除;改为 pending_status 卡 | WP0/WP4 |
| `create_quote` 作废该客户**所有** SKU 的未付订单(注释说同 SKU) | `commerce/service.py` | 加 `Order.sku == sku` 条件 | WP8 |
| `send_quote` 永远发 Bot 私聊,Business 客户收不到且无提示 | `commerce_handlers.py` | 5.4 节 | WP8 |
| intake 中任何文字被当作当前题答案 | `precheck.accept_text` | 03 §5.3 | WP3 |
| 首轮 AI 收到伪装成客户原话的摘要 | `precheck.P[*]["query"]` | 03 §7.1 | WP5 |
| 短期记忆在进程内,重启失忆 | `memory/short_term.py` | DB 存储 | WP5 |
| `RETURNING_AFTER_HOURS=24` 强制重走预检 | `gate.py` | resume | WP3 |
| token 以字符数÷2 估算 | `ai_turn.py` | 读 usage | WP11 |
| `edited_business_message` 未处理 | handlers | 编辑后的文本若在 AI 回复前到达 → 替换防抖队列中的原文;若已回复 → 作为新消息处理 | WP11 |
| `Update.ALL_TYPES` 不含新更新类型 | `__main__.py` | 显式追加 | WP7 |

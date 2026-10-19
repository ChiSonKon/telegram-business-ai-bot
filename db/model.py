from sqlalchemy import Boolean, Column, Integer, String, DateTime, Text, ForeignKey, Float
from sqlalchemy.sql import func
from .database import Base


class MediaGroupMesssage(Base):
    __tablename__ = "media_group_message"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer)
    message_id = Column(Integer)
    media_group_id = Column(Integer)
    is_header = Column(Boolean)
    caption_html = Column(String(1024 * 64))


class FormnStatus(Base):
    __tablename__ = "formn_status"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer)
    message_thread_id = Column(Integer)
    status = Column(String(64))


class MessageMap(Base):
    __tablename__ = "message_map"
    id = Column(Integer, primary_key=True, index=True)
    user_chat_message_id = Column(Integer)
    group_chat_message_id = Column(Integer)
    user_id = Column(Integer)


class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, index=True)
    first_name = Column(String(64))
    last_name = Column(String(64))
    username = Column(String(64))
    is_premium = Column(Boolean)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    message_thread_id = Column(Integer, default=0)
    # ═══ 新增字段：AI/人工模式状态 ═══
    chat_mode = Column(String(16), default="AI_MODE")  # AI_MODE | HUMAN_MODE


class AppSetting(Base):
    """运行时配置项，用于管理后台保存欢迎语、按钮等动态设置。"""

    __tablename__ = "app_setting"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(128), unique=True, index=True, nullable=False)
    value = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class BusinessContact(Base):
    """Telegram Business 接管私聊联系人，用于通过 business_connection_id 群发。"""

    __tablename__ = "business_contact"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, index=True)
    chat_id = Column(Integer, index=True)
    business_connection_id = Column(String(256), index=True)
    first_name = Column(String(64))
    last_name = Column(String(64))
    username = Column(String(64))
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), default=func.now())


class ConsultationProduct(Base):
    """可配置的咨询产品。流程内容存储为独立实体，避免把业务规则写死在 handler。"""
    __tablename__ = "consultation_product"
    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(128), unique=True, index=True, nullable=False)
    name = Column(String(256), nullable=False)
    description = Column(Text, default="")
    answer_mode = Column(String(16), default="auto")  # rule | ai | auto
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ConsultationQuestion(Base):
    """产品清单节点，支持单选、多选、文本、数字、文件和确认等类型。"""
    __tablename__ = "consultation_question"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("consultation_product.id"), index=True, nullable=False)
    key = Column(String(128), nullable=False)
    title = Column(String(512), nullable=False)
    question_type = Column(String(32), default="text")
    options_json = Column(Text, default="[]")
    required = Column(Boolean, default=True)
    position = Column(Integer, default=0)
    condition_json = Column(Text, default="{}")
    enabled = Column(Boolean, default=True)


class ConsultationRule(Base):
    """关键词/正则命中后的固定话术，可按产品和优先级配置。"""
    __tablename__ = "consultation_rule"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("consultation_product.id"), index=True, nullable=False)
    pattern = Column(String(512), nullable=False)
    reply = Column(Text, nullable=False)
    is_regex = Column(Boolean, default=False)
    priority = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)


class ConsultationSession(Base):
    """客户咨询进度和答案；同时适用于 Business 入口和访客机器人入口。"""
    __tablename__ = "consultation_session"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    product_id = Column(Integer, ForeignKey("consultation_product.id"), index=True)
    channel = Column(String(32), default="business")
    status = Column(String(32), default="active")
    current_question_key = Column(String(128))
    answers_json = Column(Text, default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())
from sqlalchemy import Boolean, Column, Integer, String, DateTime, Text, ForeignKey, Float
from sqlalchemy.sql import func
from .database import Base

class FrontdeskCase(Base):
    __tablename__ = "frontdesk_case"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    business_owner_id = Column(Integer, index=True, nullable=False, default=0)
    channel = Column(String(32), nullable=False, default="bot")
    state = Column(String(32), nullable=False, default="FRONTDESK")
    role = Column(String(32))
    initial_message = Column(Text, default="")
    answers_json = Column(Text, default="{}")
    summary = Column(Text, default="")
    off_topic_count = Column(Integer, default=0)
    uncertain_count = Column(Integer, default=0)
    extensions = Column(Integer, default=0)
    handoff_reason = Column(String(128))
    handoff_at = Column(DateTime(timezone=True))
    order_verification_status = Column(String(32), default="unverified")
    brief_json = Column(Text)                    # 交接简报(WP3)
    source = Column(String(64))                  # 来源(/start src_xxx)
    priority = Column(String(4))                 # P0..P3
    intake_flow = Column(String(48))
    last_customer_at = Column(DateTime(timezone=True))
    last_status_card_at = Column(DateTime(timezone=True))
    test = Column(Boolean, default=False)        # 客户视角模式产生的测试数据
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class AiUsageLedger(Base):
    __tablename__ = "ai_usage_ledger"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    business_owner_id = Column(Integer, index=True, nullable=False, default=0)
    case_id = Column(Integer, ForeignKey("frontdesk_case.id"), index=True)
    role = Column(String(32))
    event = Column(String(32), nullable=False)  # reserve, success, release, attempt, cost
    reply_units = Column(Integer, default=0)
    model_attempts = Column(Integer, default=0)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    estimated = Column(Boolean, default=False)
    reservation_key = Column(String(128), unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class FrontdeskMessage(Base):
    __tablename__ = "frontdesk_message"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    case_id = Column(Integer, ForeignKey("frontdesk_case.id"), index=True)
    channel = Column(String(32), default="bot")
    direction = Column(String(16), nullable=False)
    text = Column(Text, default="")
    message_id = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class KnowledgeSource(Base):
    __tablename__ = "knowledge_source"
    id = Column(Integer, primary_key=True)
    source_url = Column(String(512), unique=True, nullable=False)
    fetched_at = Column(DateTime(timezone=True))
    content_hash = Column(String(128))
    product_id = Column(String(128))
    fact_type = Column(String(64))
    approval_status = Column(String(32), default="pending")
    valid_until = Column(DateTime(timezone=True))
    supersedes = Column(String(512))

class OrderVerification(Base):
    __tablename__ = "order_verification"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    case_id = Column(Integer, ForeignKey("frontdesk_case.id"), index=True)
    order_ref = Column(String(256))
    status = Column(String(32), default="payment_review")
    evidence_type = Column(String(64))
    verified_by = Column(String(128))
    verified_at = Column(DateTime(timezone=True))
    unique_event_id = Column(String(256), unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    __tablename__ = "commerce_order"
    id = Column(String(32), primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    sku = Column(String(64), nullable=False)
    title = Column(String(256), default="")
    state = Column(String(32), nullable=False, default="draft", index=True)
    lang = Column(String(8), default="zh")
    stars = Column(Integer)             # Telegram Stars 价格(整数星)
    ton_nano = Column(Integer)          # TON 价格(nanoTON),为空表示不支持链上支付
    ton_memo = Column(String(64), unique=True)
    expires_at = Column(Float)          # unix 秒;报价有效期
    currency = Column(String(16))       # USDT | XTR | TON
    network = Column(String(16))        # TRC20 | TON
    amount_str = Column(String(32))     # 唯一金额字符串(精确十进制,不用浮点)
    base_amount_str = Column(String(32))
    pay_address = Column(String(128))
    gm_trade_id = Column(String(64), index=True)   # GMPay 交易号
    pay_token = Column(String(16))                 # 客户所选币种(usdt/trx/...)
    pay_url = Column(String(512))                  # 网关收银台链接
    paid_txid = Column(String(128))
    detail = Column(Text)
    next_step = Column(Text)
    paid_at = Column(DateTime(timezone=True))
    delivered_at = Column(DateTime(timezone=True))
    expires_on = Column(DateTime(timezone=True))   # 服务到期日(续费提醒)
    source_case_id = Column(Integer)
    created_by = Column(Integer)
    test = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PaymentEvent(Base):
    """已被认领的支付事实;event_key UNIQUE 保证同一笔交易/回调只能被认领一次。"""
    __tablename__ = "commerce_payment_event"
    id = Column(Integer, primary_key=True)
    event_key = Column(String(256), unique=True, nullable=False)
    order_id = Column(String(32), index=True, nullable=False)
    kind = Column(String(16), nullable=False)       # stars | ton
    verdict = Column(String(32), nullable=False)    # exact / underpaid / ...
    amount_raw = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Outbox(Base):
    """可靠投递队列:管理群暂不可达时持久化重试;dedupe_key 保证同一事件只投递一次。"""
    __tablename__ = "outbox"
    id = Column(Integer, primary_key=True)
    dedupe_key = Column(String(200), unique=True, nullable=False)
    chat_id = Column(Integer, nullable=False)
    thread_id = Column(Integer)
    text = Column(Text, nullable=False)
    attempts = Column(Integer, default=0)
    next_try = Column(Float, default=0.0)
    sent = Column(Boolean, default=False, index=True)
    case_uid = Column(Integer)          # 有值时,管理群通知附带"处理"按钮
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AdminAudit(Base):
    """后台操作审计:谁、在什么时候、对什么做了什么。"""
    __tablename__ = "admin_audit"
    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer, index=True, nullable=False)
    action = Column(String(64), nullable=False)
    target = Column(String(128))
    detail = Column(Text)
    before_json = Column(Text)
    after_json = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)



class FunnelEvent(Base):
    """漏斗埋点:props 只放枚举与数字,绝不放客户原文。"""
    __tablename__ = "funnel_event"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id = Column(Integer, index=True, nullable=False)
    case_id = Column(Integer, index=True)
    channel = Column(String(16))            # bot | business
    source = Column(String(64))
    event = Column(String(48), index=True, nullable=False)
    line = Column(String(48))
    props_json = Column(Text, default="{}")
    test = Column(Boolean, default=False, index=True)


class CustomerProfile(Base):
    __tablename__ = "customer_profile"
    user_id = Column(Integer, primary_key=True)
    lang = Column(String(8))
    first_source = Column(String(64))
    last_source = Column(String(64))
    lines_json = Column(Text, default="[]")
    paid_orders = Column(Integer, default=0)
    biz_disclosed = Column(Boolean, default=False)
    bot_started = Column(Boolean, default=False)
    last_effect_at = Column(Float)
    notes = Column(Text, default="")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ConfigRevision(Base):
    __tablename__ = "config_revision"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    author_id = Column(Integer, nullable=False)
    note = Column(String(256), default="")
    snapshot_json = Column(Text, nullable=False)
    changed_keys = Column(Text, default="[]")
    effective_at = Column(Float)
    reverted_from = Column(Integer)


class ConfigDraft(Base):
    __tablename__ = "config_draft"
    id = Column(Integer, primary_key=True)
    author_id = Column(Integer, index=True, nullable=False)
    key = Column(String(128), nullable=False)
    value_json = Column(Text, nullable=False)
    lint_json = Column(Text, default="[]")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UiMessage(Base):
    __tablename__ = "ui_message"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    chat_id = Column(Integer, nullable=False)
    message_id = Column(Integer, nullable=False)
    biz_conn_id = Column(String(256))
    kind = Column(String(32), nullable=False)
    ref = Column(String(64))
    pinned = Column(Boolean, default=False)
    state = Column(String(16), default="live")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    unpin_after = Column(Float)


class ConversationTurn(Base):
    __tablename__ = "conversation_turn"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    case_id = Column(Integer, index=True)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ChainLookup(Base):
    __tablename__ = "chain_lookup"
    id = Column(Integer, primary_key=True)
    order_id = Column(String(32), index=True)
    network = Column(String(16))
    txid = Column(String(128), index=True)
    result = Column(String(32))
    raw_json = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

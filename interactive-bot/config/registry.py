"""配置注册表(04 §3.2):每个可在后台编辑的键声明一次。

锁定项(护栏规则、persona 前言、AI 披露、paid 只认已验证事实、敏感线不进 AI……)不在注册表中,
也不允许被写入(LOCKED_KEYS)。
"""
from __future__ import annotations

import os
import string
from dataclasses import dataclass, field

from . import defaults_copy, defaults_flows, defaults_intake

LOCKED_KEYS = frozenset({
    "guardrail.rules", "guardrail.secret_patterns", "persona.preamble", "ai.disclosure", "orders.paid_requires_verified_fact",
    "orders.screenshot_is_not_payment", "sensitive.never_ai", "compliance.floor",
})
HIGH_RISK_PREFIXES = ("payments.usdt.", "payments.ton.", "trust.", "access.")
GUARDED_NS = ("copy.", "intake.", "lines", "playbooks")        # 发布后守护(渲染异常 ≥3 次自动回滚)的命名空间


@dataclass
class ConfigKey:
    key: str
    group: str
    title: str
    help: str = ""
    kind: str = "json"                 # copy | json | number | bool | list | text | secret
    default: object = None
    lint: list = field(default_factory=list)
    edit_role: str = "manager"
    publish_role: str = "manager"
    risk: str = "normal"               # normal | high
    impact: str = ""
    sync: str | None = None            # 发布时同步写入旧存储:appsetting:<key> | global:<name> | catalog | prompt

    @property
    def guarded(self) -> bool:
        return self.key.startswith(GUARDED_NS)

    def placeholders(self, lang: str = "zh") -> set[str]:
        if self.kind != "copy" or not isinstance(self.default, dict):
            return set()
        return placeholders_of(self.default.get(lang) or self.default.get("zh") or "")


def placeholders_of(text: str) -> set[str]:
    out = set()
    try:
        for _, name, _, _ in string.Formatter().parse(text):
            if name:
                out.add(name.split(".")[0].split("[")[0])
    except ValueError:
        pass
    return out


_COPY_TITLES = {
    "welcome": "欢迎卡", "welcome_back_recent": "回访卡(24 小时–7 天)", "welcome_back_old_prefix": "回访前缀(>7 天)", "pending_status": "处理中卡",
    "pending_short": "处理中(简短)", "card.price_software": "软件价目卡", "card.price_overview": "业务总览价目卡", "card.official_buy": "官方购买入口卡",
    "card.is_bot": "身份直答", "card.custom_intro": "定制说明卡", "card.network_intro": "静态 IP 说明卡", "card.ai_intro": "AI 账号说明卡",
    "card.custom_low_budget": "低预算说明卡", "human.ask": "要真人:追问一句", "human.done": "已交给同事", "biz.disclosure": "Business 披露行",
    "biz.greeting_new": "Business 新客问候", "biz.greeting_known": "Business 老客问候", "offline": "AI 故障降级", "quota_ended": "额度用完",
    "needs_human": "涉及价格/付款(请同事确认)", "voice.reply": "收到语音", "intake.progress_confirm": "确认卡", "intake.side_noted": "旁问已记下",
    "verify.account_ok": "核验:官方账号", "verify.account_bad": "核验:非官方账号", "verify.addr_ok": "核验:官方地址", "verify.addr_bad": "核验:非官方地址",
    "order.quote": "报价单", "order.paid": "已收款", "order.delivered": "已交付", "support.license.ask": "索要卡密前 4 位",
}


def _copy_group(k: str) -> str:
    if k.startswith(("order.", "btn.pay", "btn.topped", "btn.all_good", "btn.need_help", "btn.renew", "btn.not_now")):
        return "文案 · 订单与支付"
    if k.startswith(("support.", "care.")):
        return "文案 · 售后与安抚"
    if k.startswith(("verify.", "card.sensitive")):
        return "文案 · 信任与合规"
    if k.startswith(("intake.", "q.", "opt.")):
        return "文案 · 问答"
    if k.startswith(("handoff.", "human.", "eta.", "offline", "quota_ended", "needs_human", "voice.", "ai_voice", "escalation.", "resume.", "pending")):
        return "文案 · 转交与降级"
    if k.startswith("biz."):
        return "文案 · Business"
    return "文案 · 入口与先答"


def _build() -> dict[str, ConfigKey]:
    reg: dict[str, ConfigKey] = {}

    def add(**kw):
        reg[kw["key"]] = ConfigKey(**kw)

    for k, v in defaults_copy.COPY.items():
        lint = ["placeholders", "max_len:700", "html", "forbid_claims"]
        if k.startswith(("intake.", "q.", "opt.", "welcome", "card.custom_intro", "card.network_intro", "card.ai_intro", "card.sensitive")) and not k.startswith("welcome_back"):
            lint.append("forbid_precheck_words")
        add(key="copy." + k, group=_copy_group(k), title=_COPY_TITLES.get(k, k), kind="copy", default=v, lint=lint,
            help="客户看到的文案(中英各一份)。占位符 {x} 必须保持一致。", impact="见具体卡片出现的位置")

    D = defaults_flows.DEFAULTS
    add(key="lines", group="业务线", title="业务线", kind="json", default=None, lint=["schema:lines"], publish_role="manager",
        help="业务线 id、名称、关键词、是否敏感、说明。敏感线(sensitive=true)只登记不进 AI。", impact="客户选择业务、AI 说明、预检", sync="appsetting:frontdesk.lines")
    add(key="entry.intents", group="入口识别", title="入口词表", kind="json", default=D["entry.intents"], lint=["schema:dict"],
        help="入口分类器关键词。前 5 类动作锁定,只能增删关键词。", impact="所有新消息的第一步分类")
    add(key="intake.flows", group="问答流程", title="问答流程", kind="json", default=D["intake.flows"], lint=["schema:flows"],
        help="每条业务线的题目、选项、出口。", impact="问答卡片与交接简报")
    add(key="intake.max_questions", group="问答流程", title="最多出示题数", kind="number", default=D["intake.max_questions"], lint=["int:1:6"])
    add(key="intake.budget_bands", group="预算与升级", title="预算区间", kind="json", default=D["intake.budget_bands"], lint=["schema:bands"],
        help="必须包含 code=declined(先不说)。")
    add(key="intake.line_flow", group="问答流程", title="业务线→流程映射", kind="json", default=D["intake.line_flow"], lint=["schema:dict"])
    add(key="intake.side_answers", group="问答流程", title="旁问规则答复", kind="json", default=D["intake.side_answers"], lint=["schema:list"])
    add(key="intake.llm_extract.enabled", group="问答流程", title="模型辅助抽取", kind="bool", default=False, help="首句较长且必填缺失时让模型补填一次(默认关闭)。")
    add(key="intake.llm_extract.daily_cap", group="问答流程", title="模型抽取日上限", kind="number", default=300, lint=["int:0:100000"])
    add(key="escalation.enabled", group="升级矩阵", title="升级矩阵总开关", kind="bool", default=True, edit_role="owner", publish_role="owner")
    add(key="escalation.rules", group="升级矩阵", title="升级规则覆盖", kind="json", default=[], lint=["schema:escalation"], edit_role="owner", publish_role="owner",
        help="按 id 覆盖 enabled/prio/ask/card。payment_issue / refund / paid_but_broken 为锁定项,无法禁用。")
    add(key="escalation.large_deal_u", group="预算与升级", title="大单阈值(U)", kind="number", default=D["escalation.large_deal_u"], lint=["int:1:10000000"])
    add(key="escalation.explicit_human.min_ai_turns", group="升级矩阵", title="聊满几轮才可要真人(0=随时)", kind="number", default=0, lint=["int:0:20"], edit_role="owner", publish_role="owner")
    add(key="sla.business_hours", group="SLA 与值班", title="工作时间", kind="json", default=D["sla.business_hours"], lint=["schema:dict"], edit_role="owner", publish_role="owner")
    add(key="sla.minutes", group="SLA 与值班", title="各优先级响应分钟", kind="json", default=D["sla.minutes"], lint=["schema:dict"], edit_role="owner", publish_role="owner")
    add(key="sla.owner_alert_after", group="SLA 与值班", title="P0 超时私信业主(分钟)", kind="number", default=D["sla.owner_alert_after"], lint=["int:1:1440"], edit_role="owner", publish_role="owner")
    add(key="notify.routes", group="通知路由", title="通知路由", kind="json", default=D["notify.routes"], lint=["schema:dict"], edit_role="owner", publish_role="owner")
    add(key="playbooks", group="AI 剧本", title="AI 引导剧本", kind="json", default=D["playbooks"], lint=["schema:playbooks"], help="各业务线的阶段、必问、禁说、异议、出口动作。")
    add(key="cta.rules", group="AI 剧本", title="CTA 规则", kind="json", default=D["cta.rules"], lint=["schema:list"])
    add(key="memory.retention_days", group="AI 额度与成本", title="对话记忆保留天数", kind="number", default=90, lint=["int:1:3650"])
    add(key="intake.cny_per_u", group="预算与升级", title="人民币兑 U 汇率(估算用)", kind="number", default=7.0, lint=["num:1:100"])
    for gk, title in (("disable_captcha", "关闭人机验证"), ("rate_limit_per_minute", "每分钟限流"), ("message_interval", "发言间隔(秒)")):
        add(key="abuse." + gk, group="风控", title=title, kind="bool" if gk == "disable_captcha" else "number", default=False if gk == "disable_captcha" else 0,
            edit_role="owner", publish_role="owner", sync=f"global:{gk}")
    for gk, title in (("llm_price_in_per_m", "输入价($/百万token)"), ("llm_price_out_per_m", "输出价($/百万token)"),
                      ("global_daily_budget_usd", "每日预算($)"), ("global_daily_reply_cap", "每日回复条数兜底")):
        add(key="budget." + gk, group="AI 额度与成本", title=title, kind="number", default=0, lint=["num:0:1000000"],
            edit_role="owner", publish_role="owner", sync=f"global:{gk}")
    add(key="ai.operator_prompt", group="AI 额度与成本", title="运营提示词", kind="text", default="", lint=["max_len:8000", "forbid_claims"],
        help="附在系统安全前言之后的业务说明;身份披露、不报价不给地址等规则始终优先。", sync="prompt")
    add(key="catalog", group="商品目录", title="商品目录", kind="json", default={}, lint=["schema:catalog"], publish_role="owner",
        help="{sku: {title_zh,title_en,usdt,category,next_step,stars,ton}};usdt 为 USDT 价格(走 GMPay),category 为业务线编号;价格变更需业主发布。", sync="catalog")
    add(key="config.high_risk_delay_minutes", group="收款与信任", title="高危配置延迟生效(分钟)", kind="number", default=15, lint=["int:0:1440"],
        edit_role="owner", publish_role="owner", risk="high")
    add(key="trust.official_accounts", group="收款与信任", title="官方账号白名单", kind="json", default=D["trust.official_accounts"], lint=["schema:accounts"],
        edit_role="owner", publish_role="owner", risk="high", help="[{username, role_zh, role_en}]。客户发 @用户名 时据此核验真伪。", impact="冒充核验")
    add(key="trust.official_addresses", group="收款与信任", title="官方收款地址(核验用)", kind="json", default=D["trust.official_addresses"], lint=["schema:addresses"],
        edit_role="owner", publish_role="owner", risk="high", help="[{network,address,purpose}]。")
    add(key="payments.ton.address", group="收款与信任", title="TON 收款地址", kind="text",
        default=os.environ.get("TON_ADDRESS", ""), lint=["max_len:128"],
        edit_role="owner", publish_role="owner", risk="high", impact="TON 订单的收款地址", sync="ton_address")
    add(key="quote.confirm_above_u", group="预算与升级", title="自定义报价二次确认阈值(U)", kind="number",
        default=D.get("quote.confirm_above_u", 500), lint=["num:0:10000000"],
        help="管理员使用 /qcustom 发送自定义报价时,超过此金额需二次确认。", impact="管理员报价二次确认")
    add(key="quote.agent_custom_max_u", group="权限", title="客服自定义报价上限(U)", kind="number",
        default=D.get("quote.agent_custom_max_u", 1000), lint=["num:0:10000000"],
        help="客服(agent)角色的自定义报价单笔上限,超过此金额需经理或业主确认。", impact="客服自定义报价权限")
    add(key="payments.tolerance", group="收款与信任", title="付款容差", kind="json",
        default=D.get("payments.tolerance", {"abs_u": "1", "pct": "1"}), lint=["schema:tolerance"],
        help="少付容差放行设置,abs_u 为允许的绝对金额(U),pct 为允许的百分比,少付在此范围内自动放行。", impact="GMPay 到账核销与放行")
    add(key="payments.quote_valid_hours", group="收款与信任", title="报价单有效期(小时)", kind="number",
        default=D.get("payments.quote_valid_hours", 24), lint=["num:1:720"],
        help="报价单有效小时数,超时未付进入 expired_late。", impact="生成新报价单的到期时间")
    add(key="kb.known_issues", group="知识库", title="售后已知问题规则", kind="json",
        default=D.get("kb.known_issues", []), lint=["schema:known_issues"],
        help="售后已知问题规则列表,命中后优先出示已知问题卡(Windows/macOS 拦截等)。", impact="售后已知问题自助解决")
    add(key="access.roles", group="权限", title="管理员与角色", kind="json", default={}, lint=["schema:roles"],
        edit_role="owner", publish_role="owner", risk="high", help='{"<user_id>": "owner|manager|agent|finance|viewer"}')
    return reg


_REGISTRY: dict[str, ConfigKey] | None = None


def registry() -> dict[str, ConfigKey]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build()
    return _REGISTRY


def get_key(key: str) -> ConfigKey | None:
    return registry().get(key)


def groups() -> list[str]:
    seen = []
    for ck in registry().values():
        if ck.group not in seen:
            seen.append(ck.group)
    return seen


def keys_in_group(group: str) -> list[ConfigKey]:
    return [ck for ck in registry().values() if ck.group == group]


def default_value(key: str):
    """注册表键的默认值(copy/json 默认值来自 defaults_*;lines 默认值来自 frontdesk.lines)。"""
    ck = get_key(key)
    if ck is None:
        raise KeyError(key)
    if key == "lines":
        from ..frontdesk import lines as biz_lines
        return biz_lines.DEFAULT_LINES
    return ck.default

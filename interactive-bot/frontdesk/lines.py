"""业务线注册表:数据而非代码。每条业务线有关键词、预检字段与运营说明;后台可随时增删,无需改核心。"""
from __future__ import annotations

import json

from db.database import SessionMaker
from db.model import AppSetting, FrontdeskCase

LINES_KEY = "frontdesk.lines"

DEFAULT_LINES = [
    {"id": "software", "name_zh": "WhiteCat 5.0 软件", "name_en": "WhiteCat 5.0 Software",
     "keywords": ["软件", "whitecat", "获客", "授权", "卡密", "试用", "安装", "养号", "采集", "私信", "套餐", "续费", "激活", "mcp",
                  "software", "license", "trial", "install", "renew", "activate"],
     "fields": [{"zh": "目前处于哪个阶段（了解 / 试用 / 选套餐 / 续费）", "en": "which stage you're at (exploring / trial / choosing a plan / renewing)"},
                {"zh": "使用的系统与设备数量", "en": "your OS and number of devices"}],
     "notes_zh": "试用与购买一律引导到官网与软件内购买页或官方购买机器人，以实时订单为准；不承诺私信回复率、过验证成功率等任何效果；提醒须合法合规使用。",
     "notes_en": "Direct trial and purchase to the official site, in-app checkout or the official purchase bot; the live order is authoritative. Never promise reply rates or verification success; remind customers to use it lawfully."},
    {"id": "bot_custom", "name_zh": "Telegram 机器人定制", "name_en": "Custom Bots & Automation", "extra": True,
     "keywords": ["定制", "开发", "机器人", "自动化", "脚本", "发卡", "商城", "交易bot", "客服机器人", "custom", "develop", "automation", "bot"],
     "fields": [{"zh": "要解决的具体问题", "en": "the specific problem to solve"},
                {"zh": "最重要的功能", "en": "the most important feature"},
                {"zh": "期望交付时间（预算是可选项）", "en": "desired delivery time (budget is optional)"}],
     "notes_zh": "288U 起只是公开起价；最终报价与交期必须由同事出具报价单，不要给出确定价格或交期。不介绍也不承诺骚扰、刷量、规避平台风控类功能。",
     "notes_en": "288U is only the public starting price. A teammate must issue the final quote and delivery date. Don't offer or promise spam, fake-traffic or platform-evasion features."},
    {"id": "network", "name_zh": "独享纯净静态网络", "name_en": "Dedicated Static IP", "extra": True,
     "keywords": ["ip", "静态", "住宅", "机房", "网络", "代理", "独享", "proxy", "network"],
     "fields": [{"zh": "IP 类型（住宅 / 机房）", "en": "IP type (residential / datacenter)"},
                {"zh": "数量与使用周期", "en": "quantity and duration"}],
     "notes_zh": "机房静态IP阶梯：1~10条 2U起、10~20条 1.5U、20~200条 1U、200~400条 0.5U、500条以上 0.43U；住宅静态IP：1~20条 3U、20条以上 2U。库存与实时价格以同事确认为准。",
     "notes_en": "Datacenter IPs: 1-10 from 2U, 10-20 1.5U, 20-200 1U, 200-400 0.5U, 500+ 0.43U. Residential IPs: 1-20 3U, 20+ 2U. Live stock and pricing confirmed by teammate."},
    {"id": "kyc", "name_zh": "平台认证咨询", "name_en": "Platform verification enquiry", "extra": True, "sensitive": True,
     "keywords": ["认证", "kyc", "stripe", "sumsub", "didit", "okx", "币安", "bybit", "实名", "出海认证", "verification"],
     "fields": [{"zh": "目标软件与平台（交易所 / Sumsub / Stripe 等）", "en": "target platform (Exchange / Sumsub / Stripe etc.)"},
                {"zh": "目标国家/地区（东南亚 / 欧美 等）", "en": "target country/region (Southeast Asia / Europe & US etc.)"},
                {"zh": "个人认证还是企业认证", "en": "individual or corporate verification"}],
     "notes_zh": "支持承接各类KYC认证，按软件与国家评估：交易所20U起（东南亚20U、欧美80U），Sumsub 80U，其他软件转同事评估报价；绝不索要账号密码或私钥。",
     "notes_en": "Support KYC services by app & country: Exchanges from 20U (SE Asia 20U, Europe/US 80U), Sumsub 80U, other apps quoted by teammate. Never ask for passwords or keys.",
     "notes_internal": "海外协同认证，不碰密码不碰号，交易所/东南亚20U，Sumsub/欧美80U，其他软件转人工评估。"},
    {"id": "account", "name_zh": "账号类咨询", "name_en": "Account enquiry", "extra": True, "sensitive": True,
     "keywords": ["号池", "账号", "推特", "蓝v", "tdata", "协议号", "老号", "gmail", "dc老号", "accounts"],
     "fields": [{"zh": "所需平台与类型（推特蓝V / TG协议号 / DC / 邮箱）", "en": "platform and type (Twitter Blue, TG session, DC, Gmail)"},
                {"zh": "需求数量与用途", "en": "quantity and intended use"}],
     "notes_zh": "TG协议号(tdata/session)请前往 @baimaotgbot 自助选购；推特蓝V年费90U、DC号0.5U、企业Gmail 0.5U、个人Gmail老号3U(10个起2U)；售后以订单约定为准。",
     "notes_en": "TG protocol sessions via @baimaotgbot; Twitter Blue annual 90U, DC 0.5U, Business Gmail 0.5U, Personal aged Gmail 3U (10+ @ 2U). After-sales per order terms.",
     "notes_internal": "TG协议号引导至 @baimaotgbot；推特蓝V 90U/年、DC 0.5U、Gmail企业 0.5U、个人老号3U(10+ 2U)。"},
    {"id": "ai_account", "name_zh": "AI 大模型代充与成品", "name_en": "AI Models & Subscriptions", "extra": True,
     "keywords": ["ai账号", "chatgpt", "gpt", "gemini", "claude", "代充", "plus", "订阅", "ai account", "subscription"],
     "fields": [{"zh": "目标产品（ChatGPT Plus / Gemini / Claude 等）", "en": "target product (ChatGPT Plus, Gemini, Claude…)"},
                {"zh": "使用方式与周期", "en": "how you'll use it and for how long"}],
     "notes_zh": "绝不索要账号密码、验证码或私钥；ChatGPT Plus 23U、Gemini Pro 8U、ChatGPT Pro(5x 120U / 20x 210U / 25x 510U)、Claude暂无报价；售后范围以订单约定为准，不要扩大承诺。",
     "notes_en": "Never ask for passwords, codes or keys. ChatGPT Plus 23U, Gemini Pro 8U, ChatGPT Pro (5x 120U / 20x 210U / 25x 510U), Claude currently unquoted. After-sales scope follows the order terms; never widen it."},
    {"id": "airdrop", "name_zh": "Web3 空投军火库与会员", "name_en": "Web3 Airdrop & KOL Circle", "extra": True,
     "keywords": ["空投", "airdrop", "撸空投", "军火库", "kol", "转发圈", "量化", "套利", "教学", "脚本库"],
     "fields": [{"zh": "想了解的板块（空投会员教学 / KOL转发圈 / 脚本库）", "en": "interest (Airdrop Coaching / KOL Circle / Scripts)"},
                {"zh": "目前的经验阶段（小白入门 / 团队工作室）", "en": "experience level (Beginner / Studio team)"}],
     "notes_zh": "空投会员方案：标准版588U、豪华版888U、一人工作室1288U(支持线下)、极客版1888U永久、顶配2888U永久；KOL转发圈：月付40U、季付88U、年付268U、终身398U；官网 https://airdrop.web3baimao.com/ ，真实成交反馈见频道 https://t.me/biqrxnxiYW 。",
     "notes_en": "Airdrop memberships: Standard 588U, Deluxe 888U, Studio 1288U (offline support), Geek 1888U, Ultimate 2888U. KOL circle: 40U/mo, 88U/qtr, 268U/yr, 398U/life. Site: https://airdrop.web3baimao.com/ , feedback: https://t.me/biqrxnxiYW ."},
    {"id": "other_biz", "name_zh": "其他业务需求", "name_en": "Other Enquiries",
     "keywords": ["其他业务", "其他需求", "other business", "something else"],
     "fields": [{"zh": "具体想办理什么", "en": "what exactly you need"}],
     "notes_zh": "非标业务只做需求登记，由人工同事确认是否承接。",
     "notes_en": "Only record the request for a teammate to review."},
]


def get_lines() -> list[dict]:
    from ..config import cfg
    ov = cfg.get("lines")
    if ov:
        return ov
    with SessionMaker() as db:
        row = db.query(AppSetting).filter(AppSetting.key == LINES_KEY).first()
    try:
        data = json.loads(row.value) if row and row.value else None
    except ValueError:
        data = None
    return data or DEFAULT_LINES


def set_lines(lines: list[dict]) -> None:
    seen = set()
    for ln in lines:
        if not isinstance(ln, dict) or not ln.get("id") or not (ln.get("name_zh") or ln.get("name_en")):
            raise ValueError("每条业务线需要 id 与名称")
        if ln["id"] in seen:
            raise ValueError(f"重复的 id: {ln['id']}")
        seen.add(ln["id"])
        if not isinstance(ln.get("keywords", []), list) or not isinstance(ln.get("fields", []), list):
            raise ValueError(f"{ln['id']}: keywords/fields 必须是数组")
    with SessionMaker() as db:
        row = db.query(AppSetting).filter(AppSetting.key == LINES_KEY).first()
        text = json.dumps(lines, ensure_ascii=False)
        if row:
            row.value = text
        else:
            db.add(AppSetting(key=LINES_KEY, value=text))
        db.commit()


def match_line(text: str, lines: list[dict] | None = None) -> dict | None:
    """关键词命中数最多者胜;并列取先出现的。无命中返回 None。"""
    low = (text or "").lower()
    best, best_n = None, 0
    for ln in lines or get_lines():
        n = sum(1 for k in ln.get("keywords", []) if k and k.lower() in low)
        if n > best_n:
            best, best_n = ln, n
    return best


def resolve(user_id: int, text: str, owner_id: int = 0) -> dict | None:
    """本次消息命中则更新并记住;否则沿用该客户已记录的业务线。"""
    lines = get_lines()
    hit = match_line(text, lines)
    with SessionMaker() as db:
        case = db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if not case:
            return hit
        try:
            answers = json.loads(case.answers_json or "{}")
        except ValueError:
            answers = {}
        if hit and hit["id"] == "other_biz" and answers.get("line"):
            hit = None                                      # 兜底业务线不抢占已确定的业务线
        if hit and answers.get("line") != hit["id"]:
            answers["line"] = hit["id"]
            case.answers_json = json.dumps(answers, ensure_ascii=False)
            db.commit()
        current = hit or next((ln for ln in lines if ln["id"] == answers.get("line")), None)
    return current


def brief(line: dict | None, lang: str) -> str:
    """注入提示词的业务线说明。字段是'待了解信息',每轮最多问其中一项,已知的不重复问。"""
    if not line:
        return ""
    en = lang == "en"
    name = line.get("name_en" if en else "name_zh") or line.get("name_zh") or line["id"]
    fields = [f.get("en" if en else "zh") or f.get("zh", "") for f in line.get("fields", [])]
    notes = line.get("notes_en" if en else "notes_zh") or ""
    if en:
        out = f"Current business line: {name}.\n"
        if fields:
            out += "Information still worth learning (ask at most ONE per turn, skip what the customer already told you): " + "; ".join(fields) + ".\n"
        return out + (f"Line notes: {notes}" if notes else "")
    out = f"当前业务线:{name}。\n"
    if fields:
        out += "仍值得了解的信息(每轮最多问其中一项,客户已说过的不要重复问):" + ";".join(fields) + "。\n"
    return out + (f"业务线说明:{notes}" if notes else "")


def is_sensitive(line_id: str | None) -> bool:
    ln = next((x for x in get_lines() if x["id"] == line_id), None)
    return bool(ln and ln.get("sensitive"))

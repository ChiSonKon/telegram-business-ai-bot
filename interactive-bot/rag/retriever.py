"""
RAG 检索器 — 从 knowledge_base.json 中检索产品/服务/FAQ
每次 LLM 调用都会注入匹配的上下文，确保 AI 基于真实数据回答
"""
import json
import os
import logging

logger = logging.getLogger(__name__)

_knowledge_cache = None
_cache_mtime = 0

_KB_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "knowledge_base.json",
)


def _load_knowledge(filepath: str = None) -> dict:
    """带缓存的知识库加载"""
    global _knowledge_cache, _cache_mtime
    path = filepath or _KB_FILE

    try:
        mtime = os.path.getmtime(path)
        if _knowledge_cache and mtime == _cache_mtime:
            return _knowledge_cache

        with open(path, "r", encoding="utf-8") as f:
            _knowledge_cache = json.load(f)
        _cache_mtime = mtime
        logger.info(f"Knowledge base loaded: {path}")
        return _knowledge_cache
    except FileNotFoundError:
        logger.error(f"Knowledge base not found: {path}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Knowledge base JSON error: {e}")
        return {}


def _has_any(query: str, keywords: list[str]) -> bool:
    """查询中是否包含任何关键词"""
    q = query.lower()
    return any(k.lower() in q for k in keywords)


def search_knowledge(query: str, top_k: int = 5) -> str:
    """
    根据用户问题检索知识库。
    策略：始终返回产品概览 + 按触发词追加详细信息。
    确保 AI 永远有真实数据可引用。
    """
    kb = _load_knowledge()
    if not kb:
        return ""

    sections = []

    # ═══ 始终注入：品牌 + 产品概览 ═══
    # 这样 AI 每次回复都有真实套餐数据，不会编造
    brand = kb.get("brand", {})
    products = kb.get("products", [])

    overview_lines = [
        f"品牌: {brand.get('name', '')} - {brand.get('slogan', '')}",
        f"定位: {brand.get('identity', '')}",
        "",
        "全部套餐方案："
    ]
    for p in products:
        featured = " [推荐]" if p.get("featured") else ""
        target = f" ({p.get('target', '')})" if p.get("target") else ""
        overview_lines.append(f"  - {p['name']}{target}{featured}")
        # 列出前4个特色
        for feat in p.get("features", [])[:4]:
            overview_lines.append(f"      {feat}")
        if p.get("contact_required"):
            overview_lines.append(f"      购买方式: 联系客服 @biqrxnxi_baimao")
        else:
            overview_lines.append(f"      购买链接: {p.get('purchase_url', '')}")
        overview_lines.append("")

    sections.append("\n".join(overview_lines))

    # ═══ 发卡网实时商品/价格（由定时爬虫刷新）═══
    scraped_products = kb.get("scraped_products", [])
    if scraped_products:
        scraped_lines = [
            "发卡网实时商品/价格（以最近一次抓取为准，成交前仍建议让客户到发卡网确认）："
        ]
        for item in scraped_products[:30]:
            name = item.get("name", "")
            price = item.get("price", "")
            stock = item.get("stock", "")
            if name:
                extra = f"，库存/状态: {stock}" if stock else ""
                scraped_lines.append(f"  - {name}: {price or '价格见发卡网'}{extra}")
        scraped_lines.append(f"  最近更新: {kb.get('last_updated', '未知')}")
        sections.append("\n".join(scraped_lines))

    # ═══ 套餐详情（当问具体套餐时） ═══
    product_detail_triggers = [
        "套餐", "方案", "价格", "多少钱", "报价", "费用", "买", "购买",
        "标准", "豪华", "副业", "工作室", "经销商", "合伙人", "企业",
        "永久", "会员", "详细", "对比", "区别", "哪个好",
    ]
    if _has_any(query, product_detail_triggers):
        detail_lines = ["套餐详细功能清单：\n"]
        for p in products:
            detail_lines.append(f"[{p['name']}] 适用: {p.get('target', '')}")
            for feat in p.get("features", []):
                detail_lines.append(f"  - {feat}")
            if p.get("contact_required"):
                detail_lines.append(f"  购买: 联系客服 ({p.get('contact_note', '')})")
            else:
                detail_lines.append(f"  购买: {p.get('purchase_url', '')}")
            detail_lines.append("")
        sections.append("\n".join(detail_lines))

    # ═══ 核心业务（当问服务内容时） ═══
    biz_triggers = [
        "空投", "撸空投", "airdrop", "套利", "量化", "交易",
        "做什么", "干什么", "业务", "服务", "赚钱", "怎么赚",
        "脚本", "自动化", "ip", "IP", "三件套", "指纹",
        "MEV", "RPC", "DEX", "CEX",
    ]
    if _has_any(query, biz_triggers):
        biz_lines = ["白猫工作室四大核心业务：\n"]
        for biz in kb.get("core_business", []):
            biz_lines.append(f"{biz['icon']} {biz['name']} ({biz['en']})")
            biz_lines.append(f"  痛点: {biz['pain']}")
            biz_lines.append(f"  方案: {biz['solution']}")
            biz_lines.append("")
        sections.append("\n".join(biz_lines))

    # ═══ KOL 转发圈 ═══
    kol_triggers = [
        "kol", "KOL", "转发", "博主", "信息圈", "alpha",
        "情报", "策略", "消息源",
    ]
    if _has_any(query, kol_triggers):
        kol = kb.get("kol_circle", {})
        if kol:
            kol_lines = [
                f"KOL信息转发圈 - {kol.get('description', '')}",
                "定价方案："
            ]
            for p in kol.get("pricing", []):
                kol_lines.append(f"  - {p['plan']}: {p.get('note', '')}")
            kol_lines.append(f"  {kol.get('purchase_note', '')}")
            sections.append("\n".join(kol_lines))

    # ═══ FAQ ═══
    faq_triggers = [
        "小白", "新手", "不懂", "来得及", "太晚", "太卷",
        "能赚", "收益", "多少", "安全", "靠谱", "为什么",
        "基础", "英文", "学", "教",
    ]
    if _has_any(query, faq_triggers):
        faq_lines = ["常见问题：\n"]
        for faq in kb.get("faq", []):
            faq_lines.append(f"Q: {faq['question']}")
            faq_lines.append(f"A: {faq['answer'][:200]}")
            faq_lines.append("")
        sections.append("\n".join(faq_lines))

    # ═══ 安全 ═══
    safe_triggers = ["安全", "女巫", "sybil", "风险", "骗", "靠谱", "信任"]
    if _has_any(query, safe_triggers):
        sec = kb.get("security", {})
        if sec:
            sections.append(
                f"安全保障：\n"
                f"  反女巫: {sec.get('anti_sybil', '')}\n"
                f"  信任: {sec.get('trust', '')}\n"
                f"  线下: {sec.get('offline', '')}"
            )

    # ═══ AI 账号 ═══
    ai_triggers = ["AI账号", "ai账号", "chatgpt", "ChatGPT", "GPT", "gpt", "claude", "Claude", "AI"]
    if _has_any(query, ai_triggers):
        sections.append(
            "AI账号服务：\n"
            "  白猫工作室有售卖各类主流AI账号。\n"
            "  具体库存和价格请到发卡网查看: https://web3baimao.com/"
        )

    # 付款方式、银行卡、付款后工作室/微信等敏感信息由 handlers.payment_rules 规则模块处理，
    # 不再注入给 LLM，避免模型在条件不足时泄露。

    # ═══ 始终注入联系方式 ═══
    contact = kb.get("contact", {})
    if contact:
        sections.append(
            f"联系方式：\n"
            f"  人工客服: {contact.get('customer_service_tg', '')}\n"
            f"  发卡网: {contact.get('shop_url', '')}\n"
            f"  成交反馈: {contact.get('feedback_channel', '')}\n"
            f"  详细介绍: {contact.get('landing_page', '')}"
        )

    result = "\n\n".join(sections)
    if result:
        logger.info(f"RAG: {len(sections)} sections for: {query[:30]}")
    return result


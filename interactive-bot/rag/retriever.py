"""
知识检索:从 knowledge_base.json(kb.v2)中按业务线与问题关键词取回事实,注入每次 AI 回合。

原则:体量小(只注入相关产品)、事实可溯源(价格只来自此处)、未公开的信息明确标注"需同事确认"。
金额授权:出站护栏只放行本文本中出现过的金额,因此价格必须写在知识库里,AI 才能引用。
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

_knowledge_cache = None
_cache_mtime = 0

_KB_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "knowledge_base.json",
)

POLICY_TRIGGERS = ["价格", "多少钱", "费用", "报价", "购买", "怎么买", "退款", "退", "保证", "质保", "售后", "补差", "优惠", "便宜",
                   "试用", "交付", "多久", "付款", "支付", "到账", "price", "refund", "pay", "warranty", "how much", "trial", "cost"]


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
    q = (query or "").lower()
    return any(k.lower() in q for k in keywords)


def _plan_line(p: dict) -> str:
    parts = [f"{p['name']}：{p['price']}{p.get('unit', '')}"]
    for k in ("duration", "seats", "note"):
        if p.get(k):
            parts.append(p[k])
    return "  - " + " · ".join(parts)


def _product_detail(p: dict) -> str:
    out = [f"【{p['name']}】" + (f" {p['version']}" if p.get("version") else ""), p.get("summary", "")]
    if p.get("platforms"):
        out.append(f"支持平台：{p['platforms']}")
    if p.get("plans"):
        out.append("价格（参考价）：")
        out += [_plan_line(x) for x in p["plans"]]
    if p.get("features"):
        out.append("要点：")
        out += [f"  • {x}" for x in p["features"]]
    if p.get("facts"):
        out.append("须知：")
        out += [f"  • {x}" for x in p["facts"]]
    return "\n".join(x for x in out if x)


def search_knowledge(query: str, line_id: str | None = None, top_k: int = 5, extra_line_ids: list[str] | None = None) -> str:
    """line_id:预检已确认的业务线;其产品详情始终注入。问题里命中其他产品的关键词时一并注入(最多 top_k 个)。"""
    kb = _load_knowledge()
    if not kb:
        return ""
    brand, official = kb.get("brand", {}), kb.get("official", {})
    products = kb.get("products", [])

    sections = [
        f"品牌：{brand.get('name', '')}（{brand.get('slogan', '')}）。{brand.get('identity', '')}\n{brand.get('price_note', '')}",
        "业务概览：\n" + "\n".join(f"  - {p['name']}：{p.get('summary', '')}" for p in products if not p.get("extra", False)),
    ]

    chosen = [p for p in products if line_id and p.get("line") == line_id]
    for p in products:                                    # 客户并行关注的业务线,其产品资料同样注入(各业务口径互不混用)
        if p not in chosen and p.get("line") in (extra_line_ids or []):
            chosen.append(p)
    for p in products:
        if p not in chosen and _has_any(query, p.get("keywords", [])):
            chosen.append(p)
    for p in chosen[:top_k]:
        sections.append(_product_detail(p))

    if _has_any(query, POLICY_TRIGGERS) or not chosen:
        sections.append("政策与口径：\n" + "\n".join(f"  - {x}" for x in kb.get("policies", [])))
    if _has_any(query, ["怎么", "能不能", "支持", "试用", "安装", "系统", "吗", "?", "？", "how", "can"]):
        faq = [f"Q: {i['q']}\nA: {i['a']}" for i in kb.get("faq", [])]
        if faq:
            sections.append("常见问题：\n" + "\n\n".join(faq))

    site_label = {"software": ("软件官网(WhiteCat 5.0 下载/价格/FAQ)", "website"), "airdrop": ("空投业务官网(会员方案/KOL 转发圈/教程)", "airdrop_site")}
    focus = (line_id or (chosen[0].get("line") if chosen else None)) or ""
    label, key = site_label.get(focus, ("综合业务主站(业务介绍/成交案例/客户好评)", "main_site"))
    links = [f"{label} {official.get(key) or official.get('main_site', '')}"]
    if key != "main_site" and official.get("main_site"):          # 不跨业务线带出别的站点(软件对话里不出现空投站,反之亦然)
        links.append(f"综合业务主站(业务介绍/成交案例/客户好评) {official['main_site']}")
    if official.get("purchase_bot"):
        links.append(f"官方购买机器人 {official['purchase_bot']}（{official.get('purchase_bot_url', '')}）")
    if official.get("docs"):
        links.append(f"文档 {official['docs']}")
    if official.get("releases"):
        links.append(f"下载与校验和 {official['releases']}")
    sections.append("官方入口（只能提供这些，不提供任何个人账号）：\n" + "\n".join(f"  - {x}" for x in links))

    result = "\n\n".join(sections)
    logger.info("RAG: %d sections, products=%s", len(sections), [p["id"] for p in chosen])
    return result

"""引导式输入的解析与校验(纯函数):给出明确的错误提示,而不是让管理员猜 JSON。"""
from __future__ import annotations

import re

PROMPTS = {
    "cost": "请发送：<code>输入价 输出价 每日预算</code>（美元，输入/输出价为每百万 token）\n例：<code>0.27 1.1 5</code>\n单价请查看你的模型服务商账单页。",
    "sku": "请发送一行，用 | 分隔：\n<code>编号|中文名|English名|USDT价格|类目编号|交付说明(可选)</code>\n例：<code>bot_basic|基础客服机器人|Basic support bot|288|bot_custom|同事今天 18:00 前确认开工时间</code>\n价格只写数字(USDT);修改已有商品:用同一个编号重发即可。",
    "ton": "请发送 TON 收款地址，可在后面附 toncenter 的 API key（可选）：\n<code>UQxxxx… [apikey]</code>",
    "line": "请发送一行，用 | 分隔：\n<code>编号|中文名|English名|关键词1,关键词2|需要追问的问题(可选)</code>\n例：<code>nft|NFT 铸造|NFT minting|nft,铸造|目标链和数量</code>",
    "find": "请发送客户的数字 ID 或 @用户名。",
}


class WizardError(ValueError):
    pass


def parse_cost(text: str) -> dict:
    parts = text.replace(",", " ").replace("，", " ").split()
    try:
        nums = [float(x.lstrip("$")) for x in parts]
    except ValueError:
        raise WizardError("只能填数字，例如：0.27 1.1 5")
    if len(nums) not in (2, 3) or any(n < 0 for n in nums):
        raise WizardError("需要 2 或 3 个非负数字：输入价 输出价 [每日预算]")
    out = {"llm_price_in_per_m": nums[0], "llm_price_out_per_m": nums[1]}
    if len(nums) == 3:
        if nums[2] <= 0:
            raise WizardError("每日预算必须大于 0")
        out["global_daily_budget_usd"] = nums[2]
    return out


def sku_prompt(categories: list[tuple[str, str]]) -> str:
    """商品向导提示:附上当前可选类目(来自业务线注册表)。"""
    cats = "\n".join(f"• <code>{cid}</code> {name}" for cid, name in categories) or "(暂无业务线,先在「设置 → 业务线」添加)"
    return PROMPTS["sku"] + "\n\n<b>可选类目编号</b>\n" + cats


def parse_sku(text: str, categories: list[str] | None = None) -> tuple[str, dict]:
    if "|" in text:
        return _parse_sku_pipe(text, categories)
    parts = text.split()
    if len(parts) < 5:
        raise WizardError("需要 5 项：编号 中文名 English名 Stars价 TON价（没有的填 -）")
    sku, zh, en, stars, ton = parts[0], parts[1], " ".join(parts[2:-2]), parts[-2], parts[-1]
    if not re.fullmatch(r"[a-z0-9_\-]{1,32}", sku):
        raise WizardError("编号只能用小写字母、数字、下划线、连字符，最长 32 位")
    item: dict = {"title_zh": zh, "title_en": en}
    try:
        if stars != "-":
            if not stars.isdigit() or int(stars) <= 0:
                raise ValueError
            item["stars"] = int(stars)
        if ton != "-":
            item["ton"] = float(ton)
            if item["ton"] <= 0:
                raise ValueError
    except ValueError:
        raise WizardError("Stars 价必须是正整数，TON 价必须是正数，或填 -")
    if "stars" not in item and "ton" not in item:
        raise WizardError("至少要有一个价格(建议用新格式：编号|中文名|English名|USDT价格|类目编号)")
    return sku, item


def _parse_sku_pipe(text: str, categories: list[str] | None) -> tuple[str, dict]:
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 5:
        raise WizardError("至少需要 5 项，用 | 分隔：编号|中文名|English名|USDT价格|类目编号")
    sku, zh, en, price, category = parts[:5]
    if not re.fullmatch(r"[a-z0-9_\-]{1,32}", sku):
        raise WizardError("编号只能用小写字母、数字、下划线、连字符，最长 32 位")
    if not zh:
        raise WizardError("中文名不能为空")
    try:
        from ..commerce.service import parse_amount
        amount = parse_amount(price)
    except ValueError:
        raise WizardError("价格必须是大于 0.01 的数字，最多两位小数，例如 288 或 88.5") from None
    if categories is not None and category not in categories:
        raise WizardError(f"类目 {category!r} 不存在。可选：{'、'.join(categories) or '(无)'}")
    item: dict = {"title_zh": zh, "title_en": en or zh, "usdt": format(amount, "f"), "category": category}
    if len(parts) > 5 and parts[5]:
        item["next_step"] = parts[5][:500]
    return sku, item


def parse_ton(text: str) -> dict:
    parts = text.split()
    if not parts:
        raise WizardError("请发送收款地址")
    addr = parts[0]
    if not re.fullmatch(r"(EQ|UQ|kQ|0Q)[A-Za-z0-9_\-]{46}", addr):
        raise WizardError("地址格式不对：应为 48 位、以 EQ / UQ 开头的 TON 地址")
    cfg = {"address": addr}
    if len(parts) > 1:
        cfg["api_key"] = parts[1]
    return cfg


def parse_line(text: str) -> dict:
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 4:
        raise WizardError("至少需要 4 项，用 | 分隔：编号|中文名|English名|关键词")
    lid, zh, en, kws = parts[:4]
    if not re.fullmatch(r"[a-z0-9_\-]{1,32}", lid):
        raise WizardError("编号只能用小写字母、数字、下划线、连字符")
    keywords = [k.strip() for k in re.split(r"[,，]", kws) if k.strip()]
    if not keywords:
        raise WizardError("至少要有一个关键词")
    line = {"id": lid, "name_zh": zh, "name_en": en or zh, "keywords": keywords, "fields": []}
    if len(parts) > 4 and parts[4]:
        line["fields"] = [{"zh": parts[4], "en": parts[4]}]
        line["extra"] = True
    return line


def describe_cost(before: tuple | None, new: dict) -> str:
    old = f"${before[0]} / ${before[1]}" if before else "未配置"
    s = f"单价：{old} → ${new['llm_price_in_per_m']} / ${new['llm_price_out_per_m']}（输入/输出，每百万 token）"
    if "global_daily_budget_usd" in new:
        s += f"\n每日预算 → ${new['global_daily_budget_usd']}"
    return s

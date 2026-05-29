"""付款与敏感信息门禁规则。

这些规则用于在进入 LLM 之前拦截高风险付款场景，避免模型因为 RAG 上下文
过早泄露银行卡、工作室地址、微信等敏感信息。
"""
from __future__ import annotations

import json
import os
from typing import MutableMapping


_KB_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "knowledge_base.json",
)


PAYMENT_QUERY_KEYWORDS = [
    "付款", "付钱", "支付", "转账", "汇款", "打钱", "收款",
    "usdt", "币安", "binance", "okx", "钱包地址", "链上", "bep20", "bsc",
    "银行卡", "银行", "支付宝", "微信", "微信支付", "银行卡转账",
]

FIAT_PAYMENT_KEYWORDS = [
    "银行卡", "银行", "银行卡转账", "支付宝", "微信", "微信支付",
    "人民币", "rmb", "国内转账", "转卡", "卡号",
]

STRONG_PURCHASE_KEYWORDS = [
    "我要买", "我要付款", "我现在付", "现在付款", "马上付款", "立刻付款",
    "直接付款", "直接转", "直接打", "怎么转给你", "收款码", "给我账号",
    "给我卡号", "发我卡号", "发银行卡", "下单", "成交", "安排", "我确定买",
]

PAID_KEYWORDS = [
    "已付款", "已经付款", "我付款了", "付了", "付过了", "已经付", "已支付",
    "转了", "已转", "已经转", "打了", "已打款", "付款截图", "支付截图",
]

AIRDROP_PACKAGE_KEYWORDS = [
    "智能机器人定制", "双向机器人", "机器人标准版", "机器人定制版", "合约定制", "合约开发",
    "合约审计", "安全审计", "群发协议", "防刷验证", "群控", "定制开发",
]

NON_AIRDROP_PACKAGE_KEYWORDS = ["ai账号", "AI账号", "chatgpt", "claude", "gpt账号", "账号服务"]


def _load_kb() -> dict:
    try:
        with open(_KB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _has_any(text: str, keywords: list[str]) -> bool:
    t = (text or "").lower()
    return any(k.lower() in t for k in keywords)


def is_payment_query(text: str) -> bool:
    return _has_any(text, PAYMENT_QUERY_KEYWORDS)


def wants_fiat_payment(text: str) -> bool:
    return _has_any(text, FIAT_PAYMENT_KEYWORDS)


def has_strong_purchase_intent(text: str) -> bool:
    return _has_any(text, STRONG_PURCHASE_KEYWORDS)


def has_paid_intent(text: str) -> bool:
    return _has_any(text, PAID_KEYWORDS)


def is_airdrop_package_context(text: str) -> bool:
    return _has_any(text, AIRDROP_PACKAGE_KEYWORDS)


def is_non_airdrop_context(text: str) -> bool:
    return _has_any(text, NON_AIRDROP_PACKAGE_KEYWORDS)


def _payment_methods() -> dict:
    return _load_kb().get("payment_methods", {})


def _post_payment_info() -> dict:
    return _load_kb().get("post_payment_info", {})


def build_crypto_payment_message() -> str:
    crypto = _payment_methods().get("crypto", {})
    return (
        "可以，付款优先走 U，方便对账 ✅\n\n"
        f"币安 UID：{crypto.get('binance_uid', '')}\n"
        f"OKX UID：{crypto.get('okx_uid', '')}\n"
        f"链上 BEP-20（BSC）：{crypto.get('on_chain_bep20', '')}\n\n"
        "付完把付款截图发我，我这边帮你核对。"
    )


def build_fiat_payment_message() -> str:
    fiat = _payment_methods().get("fiat", {})
    crypto_msg = build_crypto_payment_message()
    return (
        f"{crypto_msg}\n\n"
        "如果你要走银行卡转账，也可以用这个：\n"
        f"户名：{fiat.get('name', '')}\n"
        f"银行：{fiat.get('bank', '')}\n"
        f"卡号：{fiat.get('account', '')}\n\n"
        "转完务必发付款截图，方便我给你核账。"
    )


def build_payment_message(text: str) -> str | None:
    """根据触发条件返回付款信息；银行卡只在明确条件下放出。"""
    if not is_payment_query(text):
        return None

    if wants_fiat_payment(text) or has_strong_purchase_intent(text):
        return build_fiat_payment_message()

    return (
        f"{build_crypto_payment_message()}\n\n"
        "如果你只能走支付宝/微信/银行卡转账，直接告诉我，我再给你国内转账方式。"
    )


def update_payment_context(text: str, state: MutableMapping) -> None:
    """记录用户是否在谈空投教学套餐，供截图确认后使用。"""
    if is_airdrop_package_context(text):
        state["payment_airdrop_package_ok"] = True
    if is_non_airdrop_context(text):
        state["payment_airdrop_package_ok"] = False


def build_paid_text_message(text: str, state: MutableMapping) -> str | None:
    """处理“已付款”类文本，先要求两次截图确认，不直接释放敏感信息。"""
    update_payment_context(text, state)

    screenshot_count = int(state.get("payment_screenshot_count", 0) or 0)
    package_ok = bool(state.get("payment_airdrop_package_ok", False))

    # 已经收到两次截图后，客户再明确确认是空投教学套餐，才释放信息。
    if screenshot_count >= 2 and package_ok and is_airdrop_package_context(text):
        return build_post_payment_release_message()

    if not has_paid_intent(text):
        return None

    state["awaiting_payment_screenshot"] = True
    if is_non_airdrop_context(text):
        return (
            "收到付款信息了 ✅\n\n"
            "不过技术经理微信只给进行「智能机器人或合约定制开发」合作的客户。\n"
            "如果你有其他咨询，我先帮你转人工对接。"
        )

    if package_ok:
        return (
            "收到，你这边先发一张付款截图给我。\n"
            "为了防止对错账，再补发第二张付款截图或订单截图。\n\n"
            "我确认是「智能机器人或合约定制开发」付款后，再发技术经理联系方式。"
        )

    return (
        "收到，你先发付款截图给我核对一下 ✅\n\n"
        "需要两次确认：\n"
        "1. 发付款截图\n"
        "2. 再补一张订单截图或付款详情截图\n\n"
        "并告诉我你定制的是不是「智能机器人或合约开发」，确认后我再发后续技术对接信息。"
    )


def build_payment_media_message(state: MutableMapping) -> str | None:
    """收到截图/图片后的流程：两次截图 + 空投教学套餐确认后才释放。"""
    if not state.get("awaiting_payment_screenshot"):
        return None

    count = int(state.get("payment_screenshot_count", 0) or 0) + 1
    state["payment_screenshot_count"] = count

    if count < 2:
        return (
            "收到第一张截图 ✅\n"
            "麻烦再补一张订单截图或付款详情截图，我做第二次确认。"
        )

    if not state.get("payment_airdrop_package_ok"):
        return (
            "两张截图我收到了 ✅\n\n"
            "再确认一下：你付款的是「智能机器人或合约开发」吗？\n"
            "确认是此开发服务后，我再发技术经理微信进行对接。"
        )

    return build_post_payment_release_message()


def build_post_payment_release_message() -> str:
    info = _post_payment_info()
    return (
        "确认收到两次截图 ✅\n\n"
        "添加时备注好：白猫工作室付款并对接\n\n"
        f"技术经理微信：{info.get('assistant_wechat', '').split(' ')[0]}"
    )

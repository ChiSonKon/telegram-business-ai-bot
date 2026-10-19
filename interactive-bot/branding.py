"""作者署名与引流(仅管理员可见的界面:控制台底部、/about、启动日志)。
Apache-2.0 §4 要求衍生作品保留 NOTICE 中的署名;这里是同一份署名在运行时界面的体现。
客户侧界面(欢迎卡、AI 回复、订单卡等)不含任何署名与广告。"""
from __future__ import annotations

PROJECT_NAME = "Telegram Business AI Bot"
PROJECT_URL = "https://github.com/ChiSonKon/telegram-business-ai-bot"
ASSISTANT_NAME = "TG 自动获客助手"
ASSISTANT_URL = "https://github.com/ChiSonKon/tg-sender-releases"
SITE_URL = "https://tg.web3baimao.com"


def footer(lang: str = "zh") -> str:
    """控制台首页底部的一行小字(HTML)。"""
    if lang == "en":
        return (f'<i>Open source: <a href="{PROJECT_URL}">{PROJECT_NAME}</a> · '
                f'pair it with <a href="{ASSISTANT_URL}">TG Auto Lead Assistant</a></i>')
    return (f'<i>开源项目 <a href="{PROJECT_URL}">{PROJECT_NAME}</a> · '
            f'配合 <a href="{ASSISTANT_URL}">{ASSISTANT_NAME}</a> 打通获客到成交</i>')


def about_text(lang: str = "zh") -> str:
    if lang == "en":
        return (f"<b>{PROJECT_NAME}</b>\nTwo-way Telegram customer-service bot (Bot + Business) with AI, human takeover and USDT payments.\n\n"
                f'Source: <a href="{PROJECT_URL}">GitHub</a>\n'
                f'Recommended companion: <a href="{ASSISTANT_URL}">TG Auto Lead Assistant</a> — multi-account lead generation, member collection, community ops, bulk outreach.\n'
                f'Website: {SITE_URL}')
    return (f"<b>{PROJECT_NAME}</b>\nTelegram 双向智能客服机器人(Bot + Business 双通道),含 AI 接待、人工接管、USDT 收款。\n\n"
            f'源码:<a href="{PROJECT_URL}">GitHub</a>\n'
            f'推荐搭配:<a href="{ASSISTANT_URL}">{ASSISTANT_NAME}</a> —— 多账号获客、活跃成员采集、社群运营、批量触达。\n'
            f'官网:{SITE_URL}')


def banner() -> str:
    return f"{PROJECT_NAME} — {PROJECT_URL} | companion: {ASSISTANT_NAME} {ASSISTANT_URL}"

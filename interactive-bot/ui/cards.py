"""客户侧 / 管理侧通用卡片与按钮组。"""
from __future__ import annotations

import html

from telegram import InlineKeyboardMarkup

from .kit import btn, kb

SITE_URL = "https://web3baimao.com/"          # 综合业务主站;软件/空投各有专属官网,按业务线在卡片里单独给


# ═══ 客户侧 ═══

CARE_TEXT = (
    "[[bell]] <b>温馨提示</b>\n\n"
    "刚刚接待您的是白猫 AI 分身 [[robot]]\n"
    "需要的话，可以一键转接真人客服 [[point_up]]"
)

HUMAN_MODE_TEXT = (
    "[[user]] <b>已为您转接人工客服</b>\n\n"
    "[[chat]] 现在就可以把问题发过来，客服上线后会第一时间回复您。\n"
    "[[robot]] 想继续让 AI 分身服务，点下方按钮即可。"
)

AI_READY_TEXT = (
    "[[robot]] <b>白猫 AI 分身已就绪</b>\n\n"
    "[[chat]] 关于空投、脚本、套餐或方案的问题，直接发我就行。\n"
    "[[clock]] 7×24 小时在线解答。"
)

AI_BACK_TEXT = (
    "[[robot]] <b>AI 助手已重新上线</b>\n\n"
    "[[chat]] 有问题继续发我，需要真人服务随时点按钮。"
)

AI_OFFLINE_TEXT = (
    "[[alert]] <b>AI 暂时离线</b>\n\n"
    "[[hourglass]] 请稍后重试，或直接呼叫人工客服。"
)


def care_markup(user_id: int) -> InlineKeyboardMarkup:
    return kb([
        [btn("转接人工客服", icon="user", style="danger", callback_data=f"call_human_{user_id}")],
        [btn("继续与 AI 分身沟通", icon="robot", style="primary", callback_data=f"continue_ai_{user_id}")],
    ])


def human_mode_markup(user_id: int) -> InlineKeyboardMarkup:
    return kb([[btn("转接 AI 为您服务", icon="robot", style="primary", callback_data=f"restore_ai_{user_id}")]])


def ai_ready_markup(user_id: int) -> InlineKeyboardMarkup:
    return kb([
        [btn("转接人工客服", icon="user", style="danger", callback_data=f"call_human_{user_id}")],
        [btn("访问官网", icon="globe", style="primary", url=SITE_URL)],
    ])


# ═══ 管理侧 ═══

def admin_control_markup(user_id: int, won: bool = False) -> InlineKeyboardMarkup:
    rows = [[
        btn("接管对话", icon="bell_off", style="danger", callback_data=f"admin_takeover_{user_id}"),
        btn("恢复 AI", icon="robot", style="primary", callback_data=f"admin_restore_{user_id}"),
    ]]
    if not won:
        rows.append([btn("标记成交", icon="money", style="success", callback_data=f"lead_won_{user_id}")])
    return kb(rows)


def human_request_text(user_id: int, full_name: str) -> str:
    return (
        "[[alert]][[alert]] <b>客户请求人工客服介入</b> [[alert]][[alert]]\n\n"
        f"[[user]] <a href=\"tg://user?id={user_id}\">{html.escape(full_name or str(user_id))}</a>\n"
        f"[[tag]] ID: <code>{user_id}</code>\n\n"
        "[[bolt]] 请客服尽快上线跟进！"
    )

"""
回调处理器 — 处理内联按钮点击事件

1. 客户侧: [呼叫老板/转人工] / [恢复AI对话]
2. 管理侧: [接管对话] / [恢复 AI]
3. 验证码回调
"""
import html
import os
import random
import time
from string import ascii_letters as letters

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telegram.helpers import mention_html

from db.database import SessionMaker
from db.model import User

from ..admin import permissions
from .. import admin_group_id, admin_user_ids, logger, disable_captcha
from ..utils import delete_message_later
from ..start_settings import build_call_human_markup
from ..middleware.state_machine import ChatMode, set_chat_mode, get_chat_mode
from ..timeout_tracker import dismiss_care
from ..ui import edit_card, replace_card, send_card
from ..ui.cards import (
    AI_BACK_TEXT,
    AI_READY_TEXT,
    HUMAN_MODE_TEXT,
    admin_control_markup,
    ai_ready_markup,
    human_mode_markup,
    human_request_text,
)

db = SessionMaker()




async def _safe_answer(query, *args, **kwargs) -> None:
    """应答回调失败(网络超时等)不能中断后续处理:先更新卡片比让按钮停止转圈更重要。"""
    try:
        await query.answer(*args, **kwargs)
    except Exception as exc:
        logger.warning("query.answer failed: %s", type(exc).__name__)


_ack_tasks: set = set()


def _ack(query, *args, **kwargs) -> None:
    """非阻塞后台应答 query.answer，绝不卡死主界面渲染;任务由模块级集合持有引用,避免被回收。"""
    import asyncio
    try:
        task = asyncio.create_task(_safe_answer(query, *args, **kwargs))
    except Exception:
        return
    _ack_tasks.add(task)
    task.add_done_callback(_ack_tasks.discard)


async def callback_frontdesk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """前台按钮:来意选择 / 选项作答 / 跳过 / 留言。校验归属,旧卡片乱序点击无副作用。"""
    from whitecat_core.runtime import lang_for
    from ..ai_turn import run_first_turn
    from ..frontdesk import precheck
    from ..frontdesk.router import advance
    from ..frontdesk.service import get_role, handoff, in_precheck, is_human_locked
    from ..frontdesk.texts import t
    query = update.callback_query
    logger.info("frontdesk callback: %s from %s", query.data, query.from_user.id)
    from .user_handlers import ensure_topic_for_customer
    await ensure_topic_for_customer(query.from_user, context)
    parts = (query.data or "").split(":")
    try:
        kind = parts[0]
        uid = int(parts[2] if kind == "frontdesk_intent" else parts[1])
    except (ValueError, IndexError):
        _ack(query, "这张卡片已失效，请发送 /start 重新开始", show_alert=True)
        return
    if query.from_user.id != uid:
        _ack(query, "这不是您的操作按钮喔", show_alert=True)
        return

    lang, biz = lang_for(uid), _biz_conn_id(query)
    if kind == "frontdesk_leave":
        # D1:客户点旧卡片上的"留言给同事" → 直接问一句话,下一条消息转交
        _ack(query)
        if not is_human_locked(uid):
            from ..frontdesk import router
            router._esc_set(uid, {"rule": "explicit_human", "reason": "explicit_human", "prio": "P2", "card": "human.done", "orig": ""})
            from ..config import copy
            await replace_card(query, copy("human.ask", lang), None, biz)
        return
    if is_human_locked(uid):
        _ack(query, t("pending_short", lang), show_alert=True)
        return
    if kind == "frontdesk_intent":
        role = {"visitor": "prospect"}.get(parts[1], parts[1])           # "先看看"=从业务选择开始
        if role not in ("prospect", "support", "partner"):
            _ack(query, t("pick_first", lang), show_alert=True)
            return
        precheck.begin(uid, role)
    elif kind == "frontdesk_ans" and len(parts) == 4:
        if not precheck.answer(uid, parts[2], parts[3]):
            _ack(query)
            return
    elif kind == "frontdesk_skip" and len(parts) == 3:
        if not precheck.answer(uid, parts[2], precheck.SKIP):
            _ack(query)
            return
    else:
        _ack(query)
        return

    # 非阻塞后台应答按钮，零延迟直接渲染下一张卡片
    _ack(query)

    async def edit(text, markup):
        await replace_card(query, text, markup, biz)

    gate = await advance(context.bot, uid, query.message.chat_id, lang, biz, edit=edit)
    if gate.ai and gate.immediate:
        await run_first_turn(context.bot, uid, query.message.chat_id, gate.texts, biz)


async def callback_fd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """前台"先答"按钮(fd:go / fd:menu / fd:role / fd:pick / fd:cta)。按钮不携带 uid:任何人点击只作用于自己的会话。"""
    from whitecat_core.runtime import lang_for
    from ..ai_turn import run_first_turn
    from ..frontdesk import router
    query = update.callback_query
    uid = query.from_user.id
    from .user_handlers import ensure_topic_for_customer
    await ensure_topic_for_customer(query.from_user, context)
    lang, biz = lang_for(uid), _biz_conn_id(query)
    chat_id = query.message.chat_id if query.message else uid

    async def edit(text, markup):
        await replace_card(query, text, markup, biz)

    async def send_new(text, markup):
        await router.send(context.bot, uid, chat_id, text, markup, biz, lang)

    try:
        gate, alert = await router.handle_fd_callback(context.bot, query, query.data or "", edit=edit, send_new=send_new, lang=lang, biz_conn_id=biz)
    except Exception:
        logger.exception("fd callback failed: %s", query.data)
        _ack(query, "这张卡片已失效，请发送 /start 重新开始", show_alert=True)
        return
    _ack(query, alert, show_alert=True) if alert else _ack(query)
    if gate.ai and gate.immediate:
        await run_first_turn(context.bot, uid, chat_id, gate.texts, biz)


# ==========================================
# 客户侧：转人工
# ==========================================

def _biz_conn_id(query) -> str | None:
    return getattr(query.message, "business_connection_id", None) if query.message else None


async def _notify_topic(context: ContextTypes.DEFAULT_TYPE, user_id: int, template: str, markup=None):
    """向客户所在的管理群话题发送卡片。"""
    from ..global_settings import get_admin_group_id
    with SessionMaker() as sdb:
        u = sdb.query(User).filter(User.user_id == user_id).first()
        thread_id = u.message_thread_id if u else None
    if not thread_id:
        return
    await send_card(context.bot, get_admin_group_id(), template, markup, message_thread_id=thread_id)


async def callback_call_human(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """客户点击 [呼叫客服/转人工] 按钮"""
    query = update.callback_query
    user = query.from_user

    # 提取 user_id（确保是本人点击）
    target_user_id = int(query.data.split("_")[-1])
    if user.id != target_user_id:
        await query.answer("这不是您的操作按钮喔", show_alert=True)
        return

    await query.answer("已为您转接人工客服")
    set_chat_mode(user.id, ChatMode.HUMAN_MODE)
    dismiss_care(user.id)

    await edit_card(query, HUMAN_MODE_TEXT, human_mode_markup(user.id), _biz_conn_id(query))
    await _notify_topic(context, user.id, human_request_text(user.id, user.full_name), admin_control_markup(user.id))


# ==========================================
# 客户侧：自行恢复 AI
# ==========================================

async def callback_restore_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """客户点击 [转接AI为您服务] 按钮"""
    query = update.callback_query
    user = query.from_user

    target_user_id = int(query.data.split("_")[-1])
    if user.id != target_user_id:
        await query.answer("这不是您的操作按钮喔", show_alert=True)
        return

    if not set_chat_mode(user.id, ChatMode.AI_MODE):
        await query.answer("人工接管中，请等待人工同事处理", show_alert=True)
        return
    await query.answer("AI 数字分身已就绪！")

    # 刷新超时关怀状态
    context.user_data["last_interaction_time"] = time.time()
    context.user_data["care_prompt_sent"] = False

    await edit_card(query, AI_READY_TEXT, ai_ready_markup(user.id), _biz_conn_id(query))
    await _notify_topic(
        context, user.id,
        f"[[robot]] 客户 {mention_html(user.id, user.full_name)} 已自行切换回 AI 模式",
    )


async def callback_continue_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """客户在超时关怀卡片中点击 [继续与 AI 分身沟通]"""
    query = update.callback_query
    user = query.from_user
    target_user_id = int(query.data.split("_")[-1])
    if user.id != target_user_id:
        await query.answer("这不是您的操作按钮喔", show_alert=True)
        return

    await query.answer("好的，继续为您解答")
    dismiss_care(user.id)
    context.user_data["last_interaction_time"] = time.time()
    context.user_data["care_prompt_sent"] = False
    await edit_card(query, AI_READY_TEXT, ai_ready_markup(user.id), _biz_conn_id(query))


# ==========================================
# 管理侧：接管对话
# ==========================================

async def callback_admin_takeover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员点击 [接管对话] 按钮"""
    query = update.callback_query
    admin = query.from_user

    if not permissions.can(admin.id, "case_ops"):
        await query.answer("No permission", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[-1])
    from ..admin import actions as admin_actions
    set_chat_mode(target_user_id, ChatMode.HUMAN_MODE)
    admin_actions.take_over(admin.id, target_user_id)      # 同步前台状态 + 审计 + 让在途 AI 回复作废
    dismiss_care(target_user_id)

    await query.answer("Conversation taken over")
    await send_card(
        context.bot, query.message.chat_id,
        f"[[bell_off]] 管理员 <b>{html.escape(admin.first_name)}</b> 已接管对话\n"
        f"[[chat]] AI 已关闭，后续消息将直接转发给客户。",
        message_thread_id=query.message.message_thread_id,
    )


# ==========================================
# 管理侧：恢复 AI
# ==========================================

async def callback_admin_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员点击 [恢复 AI] 按钮"""
    query = update.callback_query
    admin = query.from_user

    if not permissions.can(admin.id, "case_ops"):
        await query.answer("No permission", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[-1])
    from ..admin import actions as admin_actions
    if not admin_actions.release_to_ai(admin.id, target_user_id, "话题按钮"):
        admin_actions.resolve(admin.id, target_user_id)     # 非咨询/售后来意不恢复 AI,改为结案,客户下次来消息重新预检
    set_chat_mode(target_user_id, ChatMode.AI_MODE, admin_release=True)

    await query.answer("AI restored")
    await send_card(
        context.bot, query.message.chat_id,
        f"[[robot]] 管理员 <b>{html.escape(admin.first_name)}</b> 已恢复 AI 模式\n"
        f"[[chat]] 后续消息将由 AI 自动回复。",
        message_thread_id=query.message.message_thread_id,
    )

    # 通知客户（Business 客户无法由 Bot 直接私信，失败忽略）
    await send_card(context.bot, target_user_id, AI_BACK_TEXT, ai_ready_markup(target_user_id))


# ==========================================
# 管理侧：标记成交
# ==========================================

async def callback_lead_won(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员在线索卡上点击 [标记成交]"""
    query = update.callback_query
    admin = query.from_user
    if not permissions.can(admin.id, "case_ops"):
        await query.answer("No permission", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[-1])
    await query.answer("已标记成交")
    try:
        await query.edit_message_reply_markup(admin_control_markup(target_user_id, won=True))
    except Exception:
        pass
    await send_card(
        context.bot, query.message.chat_id,
        f"[[party]] <b>成交！</b> 客户 <code>{target_user_id}</code> "
        f"由 <b>{html.escape(admin.first_name)}</b> 标记为已成交 [[money]]",
        message_thread_id=query.message.message_thread_id,
    )


# ==========================================
# 管理面板回调
# ==========================================

async def callback_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理面板按钮回调"""
    query = update.callback_query
    admin = query.from_user

    if not permissions.can(admin.id, "view"):
        await query.answer("No permission", show_alert=True)
        return

    action = query.data

    if action == "panel_stats":
        total = db.query(User).count()
        from ..middleware.state_machine import ChatMode as CM
        ai_count = db.query(User).filter(User.chat_mode == "AI_MODE").count()
        human_count = db.query(User).filter(User.chat_mode == "HUMAN_MODE").count()

        from .. import rate_limit_per_minute, bot_username
        from ..ai_settings import get_primary_ai_config

        primary_ai = get_primary_ai_config()

        await query.message.edit_text(
            f"📊 系统状态面板\n"
            f"{'='*30}\n\n"
            f"👥 总用户数: {total}\n"
            f"🤖 AI 模式用户: {ai_count}\n"
            f"👩‍💻 人工模式用户: {human_count}\n\n"
            f"🔧 当前配置:\n"
            f"  Bot: @{bot_username}\n"
            f"  首位AI: {primary_ai['name']}\n"
            f"  模型: {primary_ai['model']}\n"
            f"  API: {primary_ai['base_url']}\n"
            f"  限流: {rate_limit_per_minute} 次/分钟\n\n"
            f"👑 管理员: {', '.join(str(x) for x in admin_user_ids)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")]
            ]),
        )

    elif action == "panel_group":
        try:
            chat = await context.bot.get_chat(admin_group_id)
            group_info = (
                f"📋 管理群组信息\n"
                f"{'='*30}\n\n"
                f"名称: {chat.title}\n"
                f"ID: {admin_group_id}\n"
                f"类型: {chat.type}\n"
                f"成员数: {await chat.get_member_count()}"
            )
        except Exception as e:
            group_info = f"获取群组信息失败: {e}"

        await query.message.edit_text(
            group_info,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")]
            ]),
        )

    elif action == "panel_broadcast":
        from .broadcast_handlers import get_broadcast_keyboard, get_broadcast_panel_text

        await query.message.edit_text(
            get_broadcast_panel_text(),
            reply_markup=get_broadcast_keyboard(),
        )

    elif action == "panel_users":
        users = db.query(User).order_by(User.id.desc()).limit(15).all()
        lines = [f"👥 最近用户列表 (最新15位)\n{'='*30}\n"]
        for u in users:
            mode_icon = "🤖" if u.chat_mode == "AI_MODE" else "👩‍💻"
            premium = "⭐" if u.is_premium else ""
            lines.append(
                f"{mode_icon}{premium} {u.first_name or '?'} | "
                f"@{u.username or '-'} | {u.user_id}"
            )
        await query.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 刷新", callback_data="panel_users")],
                [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")]
            ]),
        )

    elif action == "panel_ai_settings":
        from .ai_config_handlers import get_ai_config_keyboard
        from ..ai_settings import get_ai_config_text

        await query.message.edit_text(
            get_ai_config_text(),
            reply_markup=get_ai_config_keyboard(),
        )

    elif action == "panel_admin_manage":
        admin_list = "\n".join(f"  👑 {uid}" for uid in admin_user_ids)
        await query.message.edit_text(
            f"👥 管理员管理\n"
            f"{'='*30}\n\n"
            f"当前管理员:\n{admin_list}\n\n"
            f"添加管理员:\n"
            f"  /addadmin [user_id]\n\n"
            f"移除管理员:\n"
            f"  /removeadmin [user_id]\n\n"
            f"注: 在 .env 中配置的是超级管理员，不可通过命令移除。",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")]
            ]),
        )

    elif action == "panel_start_settings":
        from .start_config_handlers import get_start_config_keyboard
        from ..start_settings import get_start_config_text

        await query.message.edit_text(
            get_start_config_text(),
            reply_markup=get_start_config_keyboard(),
            parse_mode="HTML"
        )

    elif action == "panel_reset_all_ai":
        # 已移除:批量恢复 AI 会绕过人工接管与额度,请在控制台逐个客户处理
        await query.answer("该功能已移除，请在控制台逐个客户处理", show_alert=True)
        return

    elif action == "panel_main":
        from ..admin import views
        await edit_card(query, *views.home(), None)

    await query.answer()


def _get_admin_panel_text():
    return (
        "⚡ 白猫智能客服 - 管理后台\n"
        f"{'='*30}\n\n"
        "选择功能模块："
    )


# ==========================================
# 验证码回调
# ==========================================

async def send_challenge(bot, chat_id: int, user, context, lang: str = "zh", biz_conn_id: str | None = None) -> None:
    """发送一次图片验证码(仅在频繁超额调用 AI 时由 abuse 判定触发)。"""
    file_name = random.choice(os.listdir("./assets/imgs"))
    code = file_name.replace("image_", "").replace(".png", "")
    file = f"./assets/imgs/{file_name}"
    codes = ["".join(random.sample(letters, 5)) for _ in range(0, 7)]
    codes.append(code)
    random.shuffle(codes)
    photo = context.bot_data.get(f"image|{code}") or file
    buttons = [InlineKeyboardButton(x, callback_data=f"vcode_{x}_{user.id}") for x in codes]
    matrix = [buttons[i: i + 4] for i in range(0, len(buttons), 4)]
    caption = (
        f"{mention_html(user.id, user.first_name)} 为保障服务稳定，请先完成一次简单验证：选择图片中的文字。"
        if lang != "en" else
        f"{mention_html(user.id, user.first_name)} To keep the service stable, please complete a quick check: pick the text shown in the image."
    )
    kwargs = {"business_connection_id": biz_conn_id} if biz_conn_id else {}
    sent = await bot.send_photo(chat_id, photo, caption=caption, reply_markup=InlineKeyboardMarkup(matrix), parse_mode="HTML", **kwargs)
    biggest = sorted(sent.photo, key=lambda x: x.file_size, reverse=True)[0]
    context.bot_data[f"image|{code}"] = biggest.file_id
    context.user_data["vcode"] = code
    if not biz_conn_id:
        await delete_message_later(60, sent.chat.id, sent.message_id, context)


async def challenge_if_abusive(bot, chat_id: int, user, context, lang: str = "zh", biz_conn_id: str | None = None) -> bool:
    """True = 已拦下并发出验证(或处于冷却),调用方应停止处理本条消息。"""
    from ..frontdesk.abuse import needs_challenge
    if not needs_challenge(user.id):
        return False
    if context.user_data.get("is_human_error_time", 0) > time.time() - 120:
        return True                      # 刚答错:冷却 2 分钟,不重复打扰
    await send_challenge(bot, chat_id, user, context, lang, biz_conn_id)
    return True


async def callback_query_vcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """验证码按钮回调"""
    query = update.callback_query
    user = query.from_user
    code = query.data.split("_")[1]
    user_id = query.data.split("_")[2]
    if user_id == str(user.id):
        if code == context.user_data.get("vcode"):
            await query.answer("Correct!")
            sent = await context.bot.send_message(
                update.effective_chat.id,
                f"{mention_html(user.id, user.first_name)} , welcome.",
                parse_mode="HTML",
            )
            context.user_data["is_human"] = True
            from ..frontdesk.abuse import mark_verified
            mark_verified(user.id)
        else:
            await query.answer("Wrong, muted 2 minutes")
            context.user_data["is_human_error_time"] = time.time()
    await query.message.delete()


async def callback_set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """语言选择按钮回调 (支持 set_lang:zh / lang:zh / set_lang:en / lang:en)"""
    query = update.callback_query
    if not query:
        return
    _ack(query)
    data = query.data or ""
    chosen_lang = data.split(":", 1)[1] if ":" in data else "zh"
    user = query.from_user
    chat_id = update.effective_chat.id if update.effective_chat else user.id

    from whitecat_core import runtime
    runtime.force_lang(user.id, chosen_lang)

    from ..middleware.state_machine import ChatMode, set_chat_mode
    set_chat_mode(user.id, ChatMode.AI_MODE)

    from .user_handlers import ensure_topic_for_customer
    await ensure_topic_for_customer(user, context)

    from ..frontdesk.cards import welcome_text, welcome_markup
    from ..frontdesk import router
    from ..ui import render

    text = render(welcome_text(chosen_lang))
    markup = welcome_markup(user.id, chosen_lang, router.case_source(user.id))

    try:
        await query.edit_message_text(text=text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        from ..ui import send_card
        await send_card(context.bot, chat_id, welcome_text(chosen_lang), markup)


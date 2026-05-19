"""管理后台：按钮式广播、确认广播和定时广播。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from db.database import SessionMaker, engine
from db.model import BusinessContact, User

from .. import admin_group_id, admin_user_ids, logger


BROADCAST_STATE = "admin_broadcast_state"
BROADCAST_DRAFT = "admin_broadcast_draft"
BROADCAST_INTERVAL = 3
TZ = ZoneInfo("Asia/Shanghai")


def ensure_broadcast_tables() -> None:
    BusinessContact.__table__.create(bind=engine, checkfirst=True)


def get_broadcast_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 立即广播", callback_data="broadcast_now")],
        [InlineKeyboardButton("⏰ 定时广播", callback_data="broadcast_schedule")],
        [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")],
    ])


def get_broadcast_panel_text() -> str:
    db = SessionMaker()
    try:
        bot_users = db.query(User).count()
        biz_users = db.query(BusinessContact).count()
    finally:
        db.close()
    return (
        "📤 广播中心\n"
        "==============================\n\n"
        f"🤖 Bot 私聊用户：{bot_users}\n"
        f"💼 Business 私聊用户：{biz_users}\n\n"
        "请选择广播方式：\n"
        "• 立即广播：输入/转发内容后确认发送\n"
        "• 定时广播：先输入时间，再输入/转发内容并确认"
    )


def upsert_business_contact(message) -> None:
    """记录 Business 私聊联系人，供后续 business_connection_id 群发。"""
    user = message.from_user
    if not user or not message.business_connection_id:
        return
    ensure_broadcast_tables()
    db = SessionMaker()
    try:
        contact = db.query(BusinessContact).filter(BusinessContact.user_id == user.id).first()
        if not contact:
            contact = BusinessContact(user_id=user.id)
            db.add(contact)
        contact.chat_id = message.chat.id
        contact.business_connection_id = message.business_connection_id
        contact.first_name = user.first_name
        contact.last_name = user.last_name
        contact.username = user.username
        db.commit()
    finally:
        db.close()


async def callback_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin = query.from_user
    if admin.id not in admin_user_ids:
        await query.answer("No permission", show_alert=True)
        return

    action = query.data
    if action == "broadcast_panel":
        await query.message.edit_text(get_broadcast_panel_text(), reply_markup=get_broadcast_keyboard())
    elif action == "broadcast_now":
        context.user_data[BROADCAST_STATE] = "await_content_now"
        context.user_data.pop(BROADCAST_DRAFT, None)
        await query.message.reply_text(
            "📣 请发送要立即广播的内容。\n\n"
            "可以直接输入文本，也可以转发/发送图片、文件、视频等消息。\n"
            "收到后我会先显示确认按钮，不会立刻群发。"
        )
    elif action == "broadcast_schedule":
        context.user_data[BROADCAST_STATE] = "await_schedule_time"
        context.user_data.pop(BROADCAST_DRAFT, None)
        await query.message.reply_text(
            "⏰ 请发送定时广播时间。\n\n"
            "支持格式：\n"
            "• 2026-05-14 21:30\n"
            "• 21:30（今天该时间，若已过则明天）\n"
            "• 30m / 2h（多少分钟/小时后）"
        )
    elif action == "broadcast_cancel":
        context.user_data.pop(BROADCAST_STATE, None)
        context.user_data.pop(BROADCAST_DRAFT, None)
        await query.message.edit_text("已取消广播。", reply_markup=get_broadcast_keyboard())
    elif action == "broadcast_confirm":
        draft = context.user_data.get(BROADCAST_DRAFT)
        if not draft:
            await query.answer("广播草稿已失效", show_alert=True)
            return
        if draft.get("run_at"):
            run_at = datetime.fromisoformat(draft["run_at"])
            context.job_queue.run_once(_broadcast_job, run_at, data=draft)
            await query.message.edit_text(f"✅ 定时广播已创建：{run_at.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            await query.message.edit_text("📤 已确认，开始广播...")
            context.job_queue.run_once(_broadcast_job, 0, data=draft)
        context.user_data.pop(BROADCAST_STATE, None)
        context.user_data.pop(BROADCAST_DRAFT, None)
    await query.answer()


async def handle_admin_broadcast_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in admin_user_ids:
        return

    message = update.effective_message
    if not message:
        return

    state = context.user_data.get(BROADCAST_STATE)
    if not state:
        return

    if state == "await_schedule_time":
        text = (message.text or "").strip()
        try:
            run_at = _parse_schedule_time(text)
        except ValueError as e:
            await message.reply_text(f"❌ 时间格式错误：{e}\n请重新发送时间，或点取消。")
            raise ApplicationHandlerStop
        context.user_data[BROADCAST_DRAFT] = {"run_at": run_at.isoformat()}
        context.user_data[BROADCAST_STATE] = "await_content_schedule"
        await message.reply_text(
            f"✅ 定时时间：{run_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "现在请发送要广播的内容。"
        )
        raise ApplicationHandlerStop

    if state in {"await_content_now", "await_content_schedule"}:
        draft = context.user_data.get(BROADCAST_DRAFT, {})
        draft.update({
            "from_chat_id": update.effective_chat.id,
            "message_id": message.message_id,
            "admin_chat_id": update.effective_chat.id,
            "admin_id": user.id,
            "payload": _snapshot_message(message),
        })
        context.user_data[BROADCAST_DRAFT] = draft

        target_count = _count_targets()
        when = "立即发送" if not draft.get("run_at") else datetime.fromisoformat(draft["run_at"]).strftime("%Y-%m-%d %H:%M:%S")
        await message.reply_text(
            "📋 广播确认\n"
            "==============================\n\n"
            f"发送时间：{when}\n"
            f"目标人数：{target_count}\n"
            f"预计耗时：约 {target_count * BROADCAST_INTERVAL} 秒\n\n"
            "确认后才会开始广播。",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ 确认广播", callback_data="broadcast_confirm")],
                [InlineKeyboardButton("❌ 取消", callback_data="broadcast_cancel")],
            ]),
        )
        context.user_data[BROADCAST_STATE] = "await_confirm"
        raise ApplicationHandlerStop


def _parse_schedule_time(text: str) -> datetime:
    now = datetime.now(TZ)
    lower = text.lower().strip()
    if lower.endswith("m") and lower[:-1].isdigit():
        return now + timedelta(minutes=int(lower[:-1]))
    if lower.endswith("h") and lower[:-1].isdigit():
        return now + timedelta(hours=int(lower[:-1]))
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=TZ)
            if dt <= now:
                raise ValueError("时间必须晚于当前时间")
            return dt
        except ValueError:
            pass
    try:
        t = datetime.strptime(text, "%H:%M").time()
        dt = datetime.combine(now.date(), t, tzinfo=TZ)
        if dt <= now:
            dt += timedelta(days=1)
        return dt
    except ValueError as exc:
        raise ValueError("请使用 2026-05-14 21:30、21:30、30m 或 2h") from exc


def _count_targets() -> int:
    db = SessionMaker()
    try:
        ids = {u.user_id for u in db.query(User.user_id).all() if u.user_id}
        ids.update(c.user_id for c in db.query(BusinessContact.user_id).all() if c.user_id)
        return len(ids)
    finally:
        db.close()


def _load_targets():
    db = SessionMaker()
    try:
        users = {u.user_id: u for u in db.query(User).all() if u.user_id}
        contacts = {c.user_id: c for c in db.query(BusinessContact).all() if c.user_id}
        ids = sorted(set(users) | set(contacts))
        return [(uid, users.get(uid), contacts.get(uid)) for uid in ids]
    finally:
        db.close()


def _snapshot_message(message) -> dict:
    """保存广播消息内容。

    Telegram Business 不允许 copy/forward 消息（尤其是带按钮消息），
    因此 Business 广播需要根据 file_id/text + reply_markup 重新 send。
    """
    payload = {
        "kind": "text",
        "text": message.text_html or message.text or "",
        "caption": message.caption_html or message.caption or None,
        "reply_markup": message.reply_markup,
    }
    if message.photo:
        payload.update({"kind": "photo", "file_id": message.photo[-1].file_id})
    elif message.document:
        payload.update({"kind": "document", "file_id": message.document.file_id})
    elif message.video:
        payload.update({"kind": "video", "file_id": message.video.file_id})
    elif message.animation:
        payload.update({"kind": "animation", "file_id": message.animation.file_id})
    elif message.audio:
        payload.update({"kind": "audio", "file_id": message.audio.file_id})
    elif message.voice:
        payload.update({"kind": "voice", "file_id": message.voice.file_id})
    elif message.sticker:
        payload.update({"kind": "sticker", "file_id": message.sticker.file_id})
    return payload


async def _send_payload(bot, chat_id: int, payload: dict, business_connection_id: str | None = None) -> None:
    """按消息快照重新发送广播内容，保留 inline 按钮。

    不再依赖 copy/forward：
    - Business 消息不能 copy/forward；
    - 普通 copy_message 在部分场景不会带上原消息按钮。
    """
    common = {
        "chat_id": chat_id,
        "reply_markup": payload.get("reply_markup"),
    }
    if business_connection_id:
        common["business_connection_id"] = business_connection_id
    kind = payload.get("kind")
    text = payload.get("text") or payload.get("caption") or ""
    caption = payload.get("caption")

    if kind == "text":
        await bot.send_message(text=text or " ", parse_mode="HTML", **common)
    elif kind == "photo":
        await bot.send_photo(photo=payload["file_id"], caption=caption, parse_mode="HTML", **common)
    elif kind == "document":
        await bot.send_document(document=payload["file_id"], caption=caption, parse_mode="HTML", **common)
    elif kind == "video":
        await bot.send_video(video=payload["file_id"], caption=caption, parse_mode="HTML", **common)
    elif kind == "animation":
        await bot.send_animation(animation=payload["file_id"], caption=caption, parse_mode="HTML", **common)
    elif kind == "audio":
        await bot.send_audio(audio=payload["file_id"], caption=caption, parse_mode="HTML", **common)
    elif kind == "voice":
        await bot.send_voice(voice=payload["file_id"], caption=caption, parse_mode="HTML", **common)
    elif kind == "sticker":
        # sticker 不支持文字 caption，但可以附带 inline 按钮的支持情况取决于 Bot API。
        await bot.send_sticker(sticker=payload["file_id"], **common)
    else:
        await bot.send_message(text=text or "暂不支持的广播消息类型", parse_mode="HTML", **common)


async def _broadcast_job(context: ContextTypes.DEFAULT_TYPE):
    draft = context.job.data
    targets = _load_targets()
    success = failed = blocked = 0
    logger.info(
        "Broadcast job started: targets=%s, payload_kind=%s, has_markup=%s",
        len(targets),
        (draft.get("payload") or {}).get("kind"),
        bool((draft.get("payload") or {}).get("reply_markup")),
    )

    for uid, user, contact in targets:
        sent = False
        try:
            if contact and contact.business_connection_id:
                if draft.get("payload"):
                    logger.info("Broadcast via payload business: uid=%s", uid)
                    await _send_payload(
                        context.bot,
                        contact.chat_id,
                        draft["payload"],
                        business_connection_id=contact.business_connection_id,
                    )
                else:
                    raise RuntimeError("Business 广播缺少可重发的消息内容，请使用后台广播中心重新创建广播")
                sent = True
            if not sent:
                if draft.get("payload"):
                    logger.info("Broadcast via payload normal: uid=%s", uid)
                    await _send_payload(context.bot, uid, draft["payload"])
                else:
                    await context.bot.copy_message(
                        chat_id=uid,
                        from_chat_id=draft["from_chat_id"],
                        message_id=draft["message_id"],
                    )
                sent = True
            success += 1
        except Exception as e:
            msg = str(e).lower()
            if "blocked" in msg or "deactivated" in msg or "can't initiate" in msg:
                blocked += 1
            else:
                failed += 1
            logger.warning(f"Broadcast to {uid} failed: {e}")
        await asyncio.sleep(BROADCAST_INTERVAL)

    try:
        await context.bot.send_message(
            draft.get("admin_chat_id") or admin_group_id,
            "📊 广播完成报告\n\n"
            f"成功: {success}\n"
            f"失败: {failed}\n"
            f"无法私聊/已注销: {blocked}\n"
            f"总计: {len(targets)}",
        )
    except Exception:
        pass
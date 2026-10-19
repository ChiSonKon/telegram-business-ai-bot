"""/probe(owner,私聊):对管理员自己的私聊逐项探测体验能力,结果写入能力缓存并回报。

探测失败的能力自动关闭(体验特性默认值依赖探测结果,03 §8.1 / 05 §6)。
"""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from . import capabilities, tgx

EFFECTS = {"🎉": "5046509860389126442", "🔥": "5104841245755180586", "👍": "5107584321108051014", "❤️": "5044134455711629726"}


async def run_probe(bot, chat_id: int, biz: str | None = None) -> list[str]:
    """逐项探测并写入能力缓存。返回报告行。"""
    lines = []
    channel = "business" if biz else "bot"
    # 1) 特效
    okc = 0
    for emoji, eid in EFFECTS.items():
        try:
            kw = {"chat_id": chat_id, "text": f"{emoji} 特效探测", "message_effect_id": eid}
            if biz:
                kw["business_connection_id"] = biz
            await bot.send_message(**kw)
            okc += 1
            lines.append(f"特效 {emoji}:可用")
        except Exception as exc:
            lines.append(f"特效 {emoji}:不可用({type(exc).__name__})")
    capabilities.set_probe_result(channel, "effect", okc > 0)
    # 2) 置顶 + 删除服务消息
    try:
        msg = await bot.send_message(chat_id=chat_id, text="📌 置顶探测", **({"business_connection_id": biz} if biz else {}))
        pinned = await tgx.pin(bot, chat_id, msg.message_id, biz)
        capabilities.set_probe_result(channel, "pin", pinned)
        lines.append(f"置顶:{'可用' if pinned else '不可用'}")
        if pinned:
            deleted = await tgx.delete_messages(bot, chat_id, [msg.message_id + 1], biz)
            capabilities.set_probe_result(channel, "pin_service_delete", deleted)
            lines.append(f"删除置顶服务消息:{'可行' if deleted else '不可行(会留下一条系统提示)'}")
            await tgx.unpin(bot, chat_id, msg.message_id, biz)
        mid = msg.message_id
    except Exception as exc:
        capabilities.set_probe_result(channel, "pin", False)
        lines.append(f"置顶:不可用({type(exc).__name__})")
        mid = None
    # 3) 反应(Business 无此参数)
    if not biz and mid:
        r = await tgx.react(bot, chat_id, mid, "👌")
        capabilities.set_probe_result("bot", "react", r)
        lines.append(f"反应 👌:{'可用' if r else '不可用'}")
    # 4) 草稿("思考中"占位)
    if not biz:
        d1 = await tgx.send_draft(bot, chat_id, 999001, "", True)
        capabilities.set_probe_result("bot", "draft", d1)
        lines.append(f"思考中占位(sendMessageDraft):{'可用' if d1 else '不可用'}")
    return lines


async def cmd_probe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..admin import permissions
    uid = update.effective_user.id
    if not permissions.can(uid, "config_high"):
        await update.message.reply_text(permissions.deny_text(uid))
        return
    await update.message.reply_text("开始探测体验能力,会在本对话里发几条测试消息……")
    lines = await run_probe(context.bot, update.effective_chat.id)
    await update.message.reply_text("探测结果(已写入能力缓存,不可用的能力自动关闭):\n" + "\n".join(lines))

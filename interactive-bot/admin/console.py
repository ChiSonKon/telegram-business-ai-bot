"""控制台入口:/admin、`adm:*` 回调路由、引导式输入、定时提醒。所有操作限管理员。"""
from __future__ import annotations

import html
import logging
import csv
import io

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from .. import admin_user_ids
from ..commerce import service as commerce
from ..frontdesk import budget, lines as biz_lines
from ..global_settings import get_global_setting, update_global_config
from ..ui import btn, edit_card, kb, send_card
from . import actions, permissions, queries as q, views, wizards

logger = logging.getLogger(__name__)
WAIT_KEY = "adm_wait"
PENDING_KEY = "adm_pending"


def _is_admin(uid: int) -> bool:
    return permissions.is_admin(uid)


# adm:* 动作 → 所需能力(未列出的只读动作默认 view)
CAP_BY_ACT = {
    "take": "case_ops", "done": "case_ops", "rel": "case_ops", "relok": "case_ops", "grant": "case_ops",
    "qt": "quote_catalog", "qts": "quote_catalog",
    "set": "config_edit", "wiz": "config_edit", "skudel": "config_edit", "linedel": "config_edit", "promptreset": "config_edit",
    "guardtoggle": "config_edit", "ok": "config_edit",
    "roleadd": "config_high", "role": "config_high", "rolesel": "config_high", "roledel": "config_high",
}


async def open_console(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/admin:发送控制台首页。"""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("No permission.")
        return
    context.user_data.pop(WAIT_KEY, None)
    context.user_data.pop(PENDING_KEY, None)
    text, markup = views.home()
    await send_card(context.bot, update.effective_chat.id, text, markup)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/about:项目署名与推荐搭配(仅管理员)。"""
    if not _is_admin(update.effective_user.id):
        return
    from .. import branding
    await send_card(context.bot, update.effective_chat.id, branding.about_text(), None)


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/export orders YYYY-MM: finance/owner only, private chat."""
    uid = update.effective_user.id
    if not permissions.can(uid, "export_orders"):
        await update.message.reply_text(permissions.deny_text(uid))
        return
    if len(context.args) != 2 or context.args[0] != "orders":
        await update.message.reply_text("用法：/export orders YYYY-MM")
        return
    month = context.args[1]
    try:
        rows = q.export_orders(month)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    fields = ("order_id", "customer_id", "title", "amount", "network", "txid", "status", "created_at", "paid_at", "delivered_at", "operator")
    out = io.StringIO(newline="")
    out.write("\ufeff")
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) or "" for k in fields})
    buf = io.BytesIO(out.getvalue().encode("utf-8"))
    buf.name = f"orders-{month}.csv"
    await context.bot.send_document(update.effective_chat.id, buf, caption=f"{month} 订单，共 {len(rows)} 笔")
    actions.audit(uid, "导出订单", month, f"{len(rows)} 笔")


def _is_group_query(query) -> bool:
    chat = getattr(query.message, "chat", None) if query else None
    return bool(chat and getattr(chat, "type", "") in ("group", "supergroup"))


async def _show(query, rendered):
    if _is_group_query(query):
        admin_id = query.from_user.id
        try:
            sent = await send_card(query.get_bot(), admin_id, rendered[0], rendered[1])
            if sent:
                await query.answer("已在机器人私聊窗口打开，请前往私聊查看", show_alert=False)
            else:
                await query.answer("⚠️ 请先在私聊中向机器人发送 /start，以便接收处理卡", show_alert=True)
        except Exception:
            await query.answer("⚠️ 请先在私聊中向机器人发送 /start，以便接收处理卡", show_alert=True)
    else:
        await edit_card(query, rendered[0], rendered[1], None)


def _confirm_markup():
    return kb([[btn("确认保存", icon="check", style="success", callback_data="adm:ok"), btn("取消", icon="back", callback_data="adm:cancel")]])


def _roles_view(admin_id: int):
    m = permissions._effective_map()
    from db.database import SessionMaker
    from db.model import User
    with SessionMaker() as db:
        users = {u.user_id: {"username": u.username, "name": u.first_name} for u in db.query(User).filter(User.user_id.in_(list(m) or [0])).all()}
    return views.roles_page(m, permissions.role_of(admin_id) == "owner", users, permissions._pending())


def _role_detail_view(uid: int):
    from db.database import SessionMaker
    from db.model import User
    with SessionMaker() as db:
        u = db.query(User).filter(User.user_id == uid).first()
    name = (f"@{u.username}" if u and u.username else (u.first_name if u else None)) or str(uid)
    return views.role_detail(uid, name, permissions._effective_map().get(uid, "viewer"), uid in permissions._env_ids())


async def _ask_confirm(query_or_msg, context, kind: str, payload, preview: str, *, is_query: bool):
    context.user_data[PENDING_KEY] = {"kind": kind, "payload": payload}
    text = f"[[note]] <b>请确认</b>\n\n{preview}"
    if is_query:
        await edit_card(query_or_msg, text, _confirm_markup(), None)
    else:
        await send_card(query_or_msg.get_bot(), query_or_msg.chat_id, text, _confirm_markup())


async def callback_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin = query.from_user
    if not _is_admin(admin.id):
        await query.answer("No permission", show_alert=True)
        return
    p = query.data.split(":")
    act = p[1] if len(p) > 1 else "home"
    arg = p[2:]
    note = None
    if not permissions.can(admin.id, CAP_BY_ACT.get(act, "view")):
        await query.answer(permissions.deny_text(admin.id), show_alert=True)
        return
    try:
        if act == "home":
            context.user_data.pop(WAIT_KEY, None)
            await _show(query, views.home())
        elif act == "q":
            flt, page = arg[0], int(arg[1])
            await _show(query, views.queue(q.handoff_queue(urgent_only=(flt == "urgent"), flt=flt, admin_id=admin.id), flt, page))
        elif act == "case":
            case_uid = int(arg[0])
            rendered = views.case(q.case_view(case_uid))
            if _is_group_query(query):
                try:
                    sent = await send_card(context.bot, admin.id, rendered[0], rendered[1])
                    if sent:
                        await query.answer("已在机器人私聊窗口打开处理卡，请前往私聊查看与处理", show_alert=True)
                    else:
                        await query.answer("⚠️ 未能发送私聊：请先在私聊中向机器人发送 /start", show_alert=True)
                except Exception as ex:
                    logger.warning("私聊发送处理卡失败: %s", ex)
                    await query.answer("⚠️ 未能发送私聊：请先在私聊中向机器人发送 /start", show_alert=True)
            else:
                await _show(query, rendered)
        elif act == "take":
            note = "已接管，AI 不会再回复" if actions.take_over(admin.id, int(arg[0])) else "状态已变化"
            await _show(query, views.case(q.case_view(int(arg[0]))))
        elif act == "done":
            note = "已标记解决" if actions.resolve(admin.id, int(arg[0])) else "状态已变化"
            await _show(query, views.case(q.case_view(int(arg[0]))))
        elif act == "rel":
            await _show(query, views.release_choices(int(arg[0])))
        elif act == "relok":
            st = actions.release_to_ai(admin.id, int(arg[0]), arg[1])
            note = "已恢复 AI" if st else "当前状态不可恢复"
            await _show(query, views.case(q.case_view(int(arg[0]))))
        elif act == "grant":
            uid, also = int(arg[0]), arg[1] == "1"
            n = actions.grant_replies(admin.id, uid, 6)
            if n and also:
                actions.release_to_ai(admin.id, uid, "quota")
            note = "已增加 6 轮" + ("并恢复 AI" if also else "") if n else "该客户没有 AI 额度"
            await _show(query, views.case(q.case_view(uid)))
        elif act == "qt":
            await _show(query, views.quote_choices(int(arg[0]), commerce.get_catalog()))
        elif act == "qts":
            from ..handlers.commerce_handlers import send_quote
            uid = int(arg[0])
            try:
                order, ok = await send_quote(context.bot, uid, arg[1])
                actions.audit(admin.id, "发送报价", uid, f"{arg[1]} → {order.id}")
                note = "报价已发送" if ok else "已创建，但客户可能未启动机器人"
            except KeyError:
                note = "商品不存在"
            await _show(query, views.case(q.case_view(uid)))
        elif act == "ord":
            await _show(query, views.orders(q.orders_overview()))
        elif act == "ordc":
            o = q.order_card(arg[0])
            if not o:
                note = "订单不存在"
                await _show(query, views.orders(q.orders_overview()))
            else:
                from ..commerce.notify import admin_markup
                rows = list(admin_markup(o["id"], o["state"]).inline_keyboard)
                rows.append([btn("查看客户", icon="user", callback_data=f"adm:case:{o['uid']}")])
                rows.append([btn("返回订单", icon="back", callback_data="adm:ord")])
                price = " / ".join(x for x in (f"{o['amount']} {o['currency'] or 'USDT'}" if o["amount"] else "", f"{o['stars']}⭐" if o["stars"] else "", f"{o['ton_nano'] / 1e9:g} TON" if o["ton_nano"] else "") if x)
                txid = html.escape(o["txid"] or "")
                detail = f"\n网络 {html.escape(o['network'] or '-')} · TxID <code>{txid or '-'}</code>" + (f"\n来源客户单 <code>{o['source_case_id']}</code>" if o["source_case_id"] else "")
                await edit_card(query, f"[[payment]] <b>订单</b> <code>{o['id']}</code>\n{html.escape(o['title'])}\n客户 {html.escape(o['name'])} <code>{o['uid']}</code>\n"
                                       f"状态 <b>{views.STATE_ORDER_ZH.get(o['state'], o['state'])}</b> · {price}{detail}\n创建于 {q.fmt_wait(o['age_min'])}前", kb(rows), None)
        elif act == "today":
            await _show(query, views.today(q.week_series()))
        elif act == "data":
            await _show(query, views.data_page(int(arg[0]) if arg else 7))
        elif act == "log":
            await _show(query, views.log(q.audit_log()))
        elif act == "more":
            await _show(query, views.more_tools())
        elif act == "roles":
            await _show(query, _roles_view(admin.id))
        elif act == "roleadd":
            context.user_data[WAIT_KEY] = "admin"
            await edit_card(query, "请发送要添加的管理员，每行一位：\n<code>@用户名 [角色]</code>\n角色可选：业主 / 主管 / 客服 / 财务 / 只读（不写默认客服）\n"
                                   "例：\n<code>@alice 主管</code>\n<code>@bob</code>\n也可以直接发数字 ID。\n\n"
                                   "对方需要先给机器人发过消息才能识别；还没发过也没关系，我会记下，对方第一次 /start 后自动生效。",
                            kb([[btn("取消", icon="back", callback_data="adm:roles")]]), None)
        elif act == "role":
            uid = int(arg[0])
            await _show(query, _role_detail_view(uid))
        elif act == "rolesel":
            uid, role = int(arg[0]), arg[1]
            await _ask_confirm(query, context, "role_set", (uid, role),
                               f"把 <code>{uid}</code> 设为 <b>{permissions.ROLE_ZH.get(role, role)}</b>？立即生效。", is_query=True)
        elif act == "roledel":
            uid = int(arg[0])
            await _ask_confirm(query, context, "role_del", uid, f"移除管理员 <code>{uid}</code>？对方将立即失去后台权限。", is_query=True)
        elif act == "find":
            context.user_data[WAIT_KEY] = "find"
            await edit_card(query, wizards.PROMPTS["find"], kb([[btn("取消", icon="back", callback_data="adm:home")]]), None)
        elif act == "set":
            await _show(query, _settings_page(arg[0] if arg else ""))
        elif act == "wiz":
            context.user_data[WAIT_KEY] = arg[0]
            prompt = wizards.PROMPTS[arg[0]]
            if arg[0] == "sku":
                from ..frontdesk import lines as _lines
                prompt = wizards.sku_prompt([(ln["id"], ln.get("name_zh") or ln["id"]) for ln in _lines.get_lines()])
            await edit_card(query, prompt, kb([[btn("取消", icon="back", callback_data="adm:set")]]), None)
        elif act == "guardtoggle":
            cur = bool(get_global_setting("disable_captcha"))
            update_global_config({"disable_captcha": not cur})
            actions.audit(admin.id, "人机验证", "", "开启" if cur else "关闭")
            note = "已" + ("开启" if cur else "关闭")
            await _show(query, views.set_guard())
        elif act == "skudel":
            await _ask_confirm(query, context, "sku_del", arg[0], f"删除商品 <code>{html.escape(arg[0])}</code>？已发出的报价不受影响。", is_query=True)
        elif act == "linedel":
            await _ask_confirm(query, context, "line_del", arg[0], f"删除业务线 <code>{html.escape(arg[0])}</code>？", is_query=True)
        elif act == "promptreset":
            await _ask_confirm(query, context, "prompt_reset", None, "用仓库中的 <code>sales_system_prompt.txt</code> 覆盖当前运营提示词？当前内容会丢失。", is_query=True)
        elif act == "ok":
            note = await _apply_pending(query, context, admin.id)
        elif act == "cancel":
            context.user_data.pop(PENDING_KEY, None)
            await _show(query, views.settings_hub())
        else:
            note = "未知操作"
    except Exception as exc:                       # 控制台任何一步失败都要给出明确反馈,不能静默
        logger.exception("admin console error: %s", act)
        note = f"出错了：{type(exc).__name__}"
    await query.answer(note or "", show_alert=bool(note and note.startswith("出错")))


def _settings_page(which: str):
    if which == "cost":
        return views.set_cost()
    if which == "shop":
        return views.set_shop(commerce.get_catalog(), commerce.get_ton_config())
    if which == "lines":
        return views.set_lines(biz_lines.get_lines())
    if which == "guard":
        return views.set_guard()
    if which == "prompt":
        from .. import prompt_settings
        return views.set_prompt(prompt_settings.get_system_prompt())
    return views.settings_hub()


def _mirror(admin_id: int, key: str, value, note: str) -> None:
    """旧向导已把值写进旧存储;这里同步登记一个配置版本(留下历史、可回滚)。失败不影响主操作。"""
    try:
        from ..config import store
        store.quick_publish(admin_id, key, value, note, audit=False)
    except Exception as exc:
        logger.warning("mirror to config store failed (%s): %s", key, type(exc).__name__)


async def _apply_pending(query, context, admin_id: int) -> str:
    pend = context.user_data.pop(PENDING_KEY, None)
    if not pend:
        await _show(query, views.settings_hub())
        return "没有待确认的修改（可能已处理）"
    kind, payload = pend["kind"], pend["payload"]
    if kind in ("sku", "sku_del"):                    # 商品目录价格变更需业主发布:无权限时明确拒绝,避免只写旧存储导致"保存了却不生效"
        from ..config import registry
        ck = registry.get_key("catalog")
        if ck and not permissions.can_publish_key(admin_id, ck.publish_role, ck.risk):
            await _show(query, views.set_shop(commerce.get_catalog(), commerce.get_ton_config()))
            return "商品与价格的变更需要业主发布，你的角色没有这个权限，未保存。"
    if kind == "cost":
        for k, v in payload.items():
            _mirror(admin_id, "budget." + k, v, "成本向导")
        actions.audit(admin_id, "设置AI成本", "", str(payload))
        page, msg = views.set_cost(), "已保存"
    elif kind == "sku":
        cat = commerce.get_catalog()
        cat[payload[0]] = payload[1]
        commerce.set_catalog(cat)
        _mirror(admin_id, "catalog", cat, "保存商品")
        actions.audit(admin_id, "保存商品", payload[0], str(payload[1]))
        page, msg = _settings_page("shop"), "商品已保存"
    elif kind in ("role_set", "role_del", "role_add"):
        try:
            if kind == "role_set":
                permissions.set_role(admin_id, payload[0], payload[1])
                msg, page = "角色已更新", _role_detail_view(payload[0])
            elif kind == "role_del":
                permissions.remove_role(admin_id, payload)
                msg, page = "已移除", _roles_view(admin_id)
            else:
                done, waiting = [], []
                for who, role in payload:
                    status, uid = permissions.add_admin(admin_id, who, role)
                    (done if status == "added" else waiting).append(who)
                msg = f"已添加 {len(done)} 位" + (f"，{len(waiting)} 位待对方首次互动后生效" if waiting else "")
                page = _roles_view(admin_id)
            actions.audit(admin_id, "管理员设置", str(payload)[:60], msg)
        except (ValueError, permissions.PermissionDenied) as exc:
            msg, page = f"未保存：{exc}", _roles_view(admin_id)
    elif kind == "sku_del":
        cat = commerce.get_catalog()
        cat.pop(payload, None)
        commerce.set_catalog(cat)
        _mirror(admin_id, "catalog", cat, "删除商品")
        actions.audit(admin_id, "删除商品", payload)
        page, msg = _settings_page("shop"), "已删除"
    elif kind == "ton":
        cfg = commerce.get_ton_config()
        cfg.update(payload)
        commerce.set_ton_config(cfg)
        actions.audit(admin_id, "设置TON收款地址", "", payload["address"][:10] + "…")
        page, msg = _settings_page("shop"), "已保存，请用小额订单实测一笔"
    elif kind == "line":
        new_lines = [x for x in biz_lines.get_lines() if x["id"] != payload["id"]] + [payload]
        biz_lines.set_lines(new_lines)
        _mirror(admin_id, "lines", new_lines, "保存业务线")
        actions.audit(admin_id, "保存业务线", payload["id"])
        page, msg = _settings_page("lines"), "业务线已保存，立即生效"
    elif kind == "line_del":
        remaining = [x for x in biz_lines.get_lines() if x["id"] != payload]
        if not remaining:
            page, msg = _settings_page("lines"), "至少保留一条业务线"
        else:
            biz_lines.set_lines(remaining)
            _mirror(admin_id, "lines", remaining, "删除业务线")
            actions.audit(admin_id, "删除业务线", payload)
            page, msg = _settings_page("lines"), "已删除"
    elif kind == "prompt_reset":
        from .. import prompt_settings
        with open(prompt_settings._PROMPT_FILE, "r", encoding="utf-8") as f:
            prompt_settings.set_system_prompt(f.read().strip())
        actions.audit(admin_id, "重置提示词", "", "仓库默认")
        page, msg = _settings_page("prompt"), "已恢复为仓库默认"
    else:
        page, msg = views.settings_hub(), "未知修改"
    await _show(query, page)
    return msg


async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员在引导式输入中发来的文本。没有等待中的输入时直接放行,不影响其他处理器。"""
    user = update.effective_user
    kind = context.user_data.get(WAIT_KEY) if user else None
    if not kind or not _is_admin(user.id) or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/"):
        context.user_data.pop(WAIT_KEY, None)         # 发了别的命令 = 放弃当前输入
        return
    try:
        if kind == "find":
            uid = q.find_user(text)
            context.user_data.pop(WAIT_KEY, None)
            rendered = views.case(q.case_view(uid)) if uid else ("没有找到这位客户。", kb([views.BACK]))
            await send_card(context.bot, update.effective_chat.id, rendered[0], rendered[1])
        elif kind == "cost":
            new = wizards.parse_cost(text)
            await _ask_confirm(update.message, context, "cost", new, wizards.describe_cost(budget.prices(), new), is_query=False)
        elif kind == "sku":
            from ..frontdesk import lines as _lines
            sku, item = wizards.parse_sku(text, [ln["id"] for ln in _lines.get_lines()])
            existing = commerce.get_catalog().get(sku)
            prev = "（覆盖现有商品）" if existing else "（新商品）"
            prices = " · ".join(x for x in (f"{item['usdt']} USDT" if item.get("usdt") else "", f"{item['stars']}⭐" if item.get("stars") else "",
                                            f"{item['ton']} TON" if item.get("ton") else "") if x)
            await _ask_confirm(update.message, context, "sku", (sku, item),
                               f"商品 <code>{html.escape(sku)}</code> {prev}\n{html.escape(item['title_zh'])} / {html.escape(item['title_en'])}\n价格：{prices}"
                               + (f"\n类目：<code>{html.escape(item['category'])}</code>" if item.get("category") else ""), is_query=False)
        elif kind == "ton":
            cfg = wizards.parse_ton(text)
            cur = commerce.get_ton_config().get("address")
            await _ask_confirm(update.message, context, "ton", cfg,
                               f"收款地址：{('<code>' + html.escape(cur) + '</code>') if cur else '未配置'} →\n<code>{html.escape(cfg['address'])}</code>\n⚠️ 请再核对一遍，地址错误会导致收款丢失。", is_query=False)
        elif kind == "admin":
            entries = []
            for ln in [x for x in text.splitlines() if x.strip()]:
                try:
                    entries.append(permissions.parse_admin_line(ln))
                except ValueError as exc:
                    raise wizards.WizardError(str(exc))
            preview = "\n".join(f"• {('@' + w) if isinstance(w, str) else w} → {permissions.ROLE_ZH[r]}" for w, r in entries)
            await _ask_confirm(update.message, context, "role_add", entries, f"添加管理员：\n{html.escape(preview)}\n立即生效；你的角色以外的权限不变。", is_query=False)
        elif kind == "line":
            line = wizards.parse_line(text)
            await _ask_confirm(update.message, context, "line", line,
                               f"业务线 <code>{html.escape(line['id'])}</code> {html.escape(line['name_zh'])}\n关键词：{html.escape('、'.join(line['keywords']))}"
                               + (f"\n追问：{html.escape(line['fields'][0]['zh'])}" if line["fields"] else ""), is_query=False)
        context.user_data.pop(WAIT_KEY, None)
    except wizards.WizardError as exc:                # 格式错误:留在当前步骤,给出明确原因,可直接重发
        await update.message.reply_text(f"❌ {exc}\n请重新发送，或发送 /admin 取消。")
    raise ApplicationHandlerStop


# ---------- 定时提醒 ----------
async def sla_check(bot) -> int:
    """超时未处理的待跟进客户、接管后长期未释放的客户,各提醒一次(按事件去重)。返回新入队数。"""
    from ..outbox import enqueue_admin
    n = 0
    from ..config import cfg
    from ..frontdesk import escalation
    from .. import admin_user_ids
    owner_after = int(cfg.get("sla.owner_alert_after", default=10))
    for it in q.handoff_queue():
        if it["prio"] == "P0" and it["wait_min"] >= owner_after:         # P0 久未接手:再提醒并私信业主
            for oid in admin_user_ids:
                if enqueue_admin(f"sla-owner:{it['key']}:{oid}", f"[[alert]] <b>🔴 P0 超过 {owner_after} 分钟无人接手</b>\n{html.escape(it['name'])}（{it['reason'] or '待跟进'}）已等待 {q.fmt_wait(it['wait_min'])}", None, chat_id=oid):
                    n += 1
        if it["wait_min"] >= escalation.sla_minutes(it["prio"]) and enqueue_admin(
                f"sla:{it['key']}", f"[[clock]] <b>{views.DOT[it['prio']]} 超时未处理</b>\n{html.escape(it['name'])} 已等待 {q.fmt_wait(it['wait_min'])}（{it['reason'] or '待跟进'}）", it["uid"]):
            n += 1
    for it in q.active_takeovers():
        if it["since_min"] >= q.TAKEOVER_STALE_HOURS * 60 and enqueue_admin(
                f"stale:{it['key']}", f"[[clock]] <b>接管未释放</b>\n{html.escape(it['name'])} 已接管 {q.fmt_wait(it['since_min'])}。处理完请「标记已解决」，否则客户的新消息不会得到 AI 回复。", it["uid"]):
            n += 1
    return n


def digest_text() -> str:
    """04 §7.2 每日摘要:昨日漏斗、最大卡点、首次回复时长、待处理、配置变更。"""
    summ = q.funnel_summary(1, end_offset_days=1)
    f = summ["funnel"]
    lines = ["☀️ <b>昨日白猫前台</b>", f"进线 {f['F1']} → 问答完成 {f['F3']} → 报价/入口 {f['F5']} → 付款 {f['F7']}"]
    drops = [d for d in summ["dropoffs"] if d[2] >= 3]
    if drops:
        d = drops[0]
        lines.append(f"最大卡点：{html.escape(d[0])} · {html.escape(d[1])} 流失 {d[4] * 100:.0f}%")
    waits = summ["handoffs"]
    if waits["median_wait_min"] is not None:
        lines.append(f"同事首次回复中位等待：{waits['median_wait_min']:.0f} 分钟")
    p = q.pending_counts()
    lines.append(f"待处理：P0 {p['p0']} · 待报价 {p['quote']} · 冒充报告 {p['impersonation']}")
    return "\n".join(lines)


async def daily_digest(bot) -> bool:
    from datetime import datetime, timedelta, timezone
    from ..outbox import enqueue_admin
    day = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    return enqueue_admin(f"digest:{day}", digest_text())

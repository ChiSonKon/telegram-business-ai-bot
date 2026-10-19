"""设置中心(04 §3.5):对话式配置——分组 → 配置项 → 编辑草稿 → lint → 预览 → 发布 → 历史/回滚。

命令:/cfg(打开)、/config export [命名空间](导出 JSON)、发送 .json 文件(导入为草稿)。
回调:cfg:*(设置中心)、cfgx:revoke:<rev>(撤销尚未生效的高危修订)、pv:*(预览按钮,无副作用)。
"""
from __future__ import annotations

import html
import io
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from ..config import registry as R, store
from ..config import lint as L
from ..ui import btn, edit_card, kb, send_card
from . import permissions

logger = logging.getLogger(__name__)
WAIT = "cfg_wait"
PEND = "cfg_pend"
BACK_HOME = [btn("设置中心", icon="gear", callback_data="cfg:home")]


def _keys() -> list[str]:
    return list(R.registry())


def _ki(key: str) -> int:
    return _keys().index(key)


def _short(v, n: int = 700) -> str:
    s = json.dumps(v, ensure_ascii=False, indent=1) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "…"


def _e(x) -> str:
    return html.escape(str(x))


def _fmt_time(ts) -> str:
    if not ts:
        return "-"
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts, timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")


def _can_open(uid: int) -> bool:
    return permissions.can(uid, "config_edit")


# ---------- 页面 ----------
def home(admin_id: int) -> tuple[str, object]:
    active = store.get_active()
    drafts = store.list_drafts(admin_id)
    rows = []
    for g in R.groups():
        ks = R.keys_in_group(g)
        mod = sum(1 for ck in ks if ck.key in active)
        d = sum(1 for ck in ks if ck.key in drafts)
        tag = (f" · 已改 {mod}" if mod else "") + (f" · 草稿 {d}" if d else "")
        rows.append([btn(f"{g}{tag}", callback_data=f"cfg:g:{R.groups().index(g)}")])
    if drafts:
        rows.insert(0, [btn(f"发布我的全部草稿 ({len(drafts)})", icon="check", style="success", callback_data="cfg:puball")])
    rows.append([btn("历史版本 / 回滚", icon="refresh", callback_data="cfg:hist"), btn("返回控制台", icon="back", callback_data="adm:home")])
    rev = store.latest_revision_id()
    return (f"[[gear]] <b>设置中心</b>  配置 v{rev}\n"
            "每一项都会显示当前值与影响范围;修改先存为草稿,自检通过、预览确认后才发布,发布后可一键回滚。\n"
            "客户看到的文案、问答流程、AI 剧本、升级规则、阈值都在这里。"), kb(rows)


def group_page(gi: int, admin_id: int) -> tuple[str, object]:
    g = R.groups()[gi]
    active, drafts = store.get_active(), store.list_drafts(admin_id)
    rows = []
    for ck in R.keys_in_group(g):
        mark = ("✏️" if ck.key in drafts else ("●" if ck.key in active else "○"))
        high = " 🔒" if ck.risk == "high" else ""
        rows.append([btn(f"{mark} {ck.title}{high}", callback_data=f"cfg:k:{_ki(ck.key)}")])
    rows.append([btn("返回", icon="back", callback_data="cfg:home")])
    return f"[[folder]] <b>{_e(g)}</b>\n●=已修改  ○=默认  ✏️=我有草稿  🔒=高危(仅业主)", kb(rows)


def key_page(ki: int, admin_id: int) -> tuple[str, object]:
    key = _keys()[ki]
    ck = R.get_key(key)
    cur = store.effective_value(key)
    drafts = store.list_drafts(admin_id)
    modified = key in store.get_active()
    lines = [f"[[note]] <b>{_e(ck.title)}</b>  <code>{_e(key)}</code>",
             f"状态:{'已修改' if modified else '默认'}" + (" · 🔒高危(业主操作,发布后延迟生效)" if ck.risk == "high" else "")]
    if ck.help:
        lines.append(_e(ck.help))
    if ck.impact:
        lines.append(f"影响范围:{_e(ck.impact)}")
    if ck.kind == "copy":
        lines.append(f"\n<b>当前(中)</b>\n{_e(_short(cur.get('zh', ''), 400))}\n<b>当前(英)</b>\n{_e(_short(cur.get('en', ''), 400))}")
        ph = sorted(ck.placeholders("zh") | ck.placeholders("en"))
        if ph:
            lines.append("占位符(必须保留):" + " ".join(f"<code>{{{p}}}</code>" for p in ph))
    else:
        lines.append(f"\n<b>当前值</b>\n<pre>{_e(_short(cur))}</pre>")
    rows = [[btn("编辑", icon="edit", style="primary", callback_data=f"cfg:e:{ki}")]]
    if key in drafts:
        dv = drafts[key]
        issues = L.lint(key, dv)
        lines.append("\n<b>✏️ 我的草稿</b>\n" + (f"<pre>{_e(_short(dv, 500))}</pre>" if ck.kind != "copy" else f"{_e(_short(dv.get('zh', ''), 300))}\n{_e(_short(dv.get('en', ''), 300))}"))
        lines += [str(i) for i in issues] or ["✅ 自检通过"]
        rows.append([btn("预览", icon="search", callback_data=f"cfg:pv:{ki}"), btn("发布", icon="check", style="success", callback_data=f"cfg:pub:{ki}")])
        rows.append([btn("丢弃草稿", callback_data=f"cfg:dd:{ki}")])
    if modified:
        rows.append([btn("恢复默认(存为草稿)", callback_data=f"cfg:rd:{ki}")])
    rows.append([btn("返回", icon="back", callback_data=f"cfg:g:{R.groups().index(ck.group)}")])
    return "\n".join(lines), kb(rows)


def history_page() -> tuple[str, object]:
    hs = store.history(20)
    body = "\n".join(f"v{h['id']} · {_fmt_time(h['created_at'])} · <code>{h['author']}</code> · {_e(', '.join(h['changed'])[:60])}"
                     + (" ⏳" if h["effective_at"] and h["effective_at"] > datetime.now(timezone.utc).timestamp() else "") + (f" ↩v{h['reverted_from']}" if h["reverted_from"] else "")
                     for h in hs) or "还没有发布记录"
    rows = [[btn(f"v{h['id']} · {_fmt_time(h['created_at'])}", callback_data=f"cfg:hv:{h['id']}")] for h in hs[:10]]
    rows.append(BACK_HOME)
    return f"[[refresh]] <b>历史版本</b>(最近 {len(hs)} 个)\n⏳=延迟生效中  ↩=回滚产生\n\n{body}", kb(rows)


def revision_page(rid: int) -> tuple[str, object]:
    h = next((x for x in store.history(200) if x["id"] == rid), None)
    if not h:
        return "没有找到这个版本。", kb([BACK_HOME])
    rows = [[btn("回滚到此版本", icon="undo", style="danger", callback_data=f"cfg:rb:{rid}")]]
    if h["effective_at"] and h["effective_at"] > datetime.now(timezone.utc).timestamp():
        rows.insert(0, [btn("立即撤销(尚未生效)", icon="ban", style="danger", callback_data=f"cfgx:revoke:{rid}")])
    rows.append([btn("返回", icon="back", callback_data="cfg:hist")])
    return (f"<b>版本 v{rid}</b>\n时间:{_fmt_time(h['created_at'])}  操作人:<code>{h['author']}</code>\n备注:{_e(h['note'] or '-')}\n"
            f"改动:{_e(', '.join(h['changed']) or '无')}" + (f"\n生效时间:{_fmt_time(h['effective_at'])}" if h["effective_at"] else "")), kb(rows)


def _diff_text(admin_id: int, keys: list[str] | None) -> tuple[str, list[str]]:
    drafts = {k: v for k, v in store.list_drafts(admin_id).items() if keys is None or k in keys}
    lines, warns = [], []
    for k, v in drafts.items():
        ck = R.get_key(k)
        lines.append(f"• <b>{_e(ck.title)}</b> <code>{_e(k)}</code>{' 🔒' if ck.risk == 'high' else ''}\n  前:{_e(_short(store.effective_value(k), 60))}\n  后:{_e(_short(v, 60))}")
        warns += [f"{ck.title}:{i.msg}" for i in L.lint(k, v) if i.level == "warn"]
    return "\n".join(lines) or "没有草稿", warns


# ---------- 入口 ----------
async def cmd_cfg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _can_open(uid):
        await update.message.reply_text(permissions.deny_text(uid))
        return
    context.user_data.pop(WAIT, None)
    text, markup = home(uid)
    await send_card(context.bot, update.effective_chat.id, text, markup)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.pop(WAIT, None):
        await update.message.reply_text("已取消。")


async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/config export [命名空间] —— 导出当前配置 JSON(不含密钥;高危项仅业主可导出)。"""
    uid = update.effective_user.id
    if not _can_open(uid):
        await update.message.reply_text(permissions.deny_text(uid))
        return
    args = context.args or []
    if not args or args[0] != "export":
        await update.message.reply_text("用法:/config export [命名空间]  例如 /config export copy、/config export intake;把编辑好的 .json 文件直接发给我即可导入为草稿。")
        return
    ns = args[1] if len(args) > 1 else None
    if ns == "copy":
        ns = "copy"
    data = store.export(ns, include_high=permissions.can(uid, "config_high"))
    buf = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    buf.name = f"whitecat-config-v{data['version']}.json"
    await context.bot.send_document(update.effective_chat.id, buf, caption=f"配置 v{data['version']}  共 {len(data['values'])} 项")


async def handle_config_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员私聊发来 .json:解析 → 逐键 lint → 存为草稿 → 汇总。"""
    uid = update.effective_user.id
    msg = update.message
    if not msg or not msg.document or not _can_open(uid):
        return
    if not (msg.document.file_name or "").lower().endswith(".json"):
        return
    try:
        f = await context.bot.get_file(msg.document.file_id)
        raw = bytes(await f.download_as_bytearray())
        data = json.loads(raw.decode("utf-8"))
        results = store.import_json(uid, data)
    except Exception as exc:
        await msg.reply_text(f"导入失败:{type(exc).__name__} {exc}")
        raise ApplicationHandlerStop
    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]
    lines = [f"[[check]] 已存为草稿 {len(ok)} 项" + (f",有 {len(bad)} 项未通过自检:" if bad else "")]
    for r in bad[:10]:
        lines.append(f"• <code>{_e(r.key)}</code> " + "; ".join(i.msg for i in r.issues)[:150])
    if not results:
        lines = ["没有发现与当前配置不同的项。"]
    await send_card(context.bot, update.effective_chat.id, "\n".join(lines), kb([[btn("打开设置中心", icon="gear", callback_data="cfg:home")]]))
    raise ApplicationHandlerStop


# ---------- 回调 ----------
async def callback_cfg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    p = (q.data or "").split(":")
    act, arg = (p[1] if len(p) > 1 else "home"), p[2:]
    if p[0] == "cfgx":
        return await _revoke(q, context, uid, arg)
    if not _can_open(uid):
        await q.answer(permissions.deny_text(uid), show_alert=True)
        return
    note = None
    try:
        if act == "home":
            context.user_data.pop(WAIT, None)
            await edit_card(q, *home(uid), None)
        elif act == "g":
            await edit_card(q, *group_page(int(arg[0]), uid), None)
        elif act == "k":
            context.user_data.pop(WAIT, None)
            await edit_card(q, *key_page(int(arg[0]), uid), None)
        elif act == "e":
            key = _keys()[int(arg[0])]
            ck = R.get_key(key)
            if not permissions.can_edit_key(uid, ck.edit_role, ck.risk):
                await q.answer(permissions.deny_text(uid), show_alert=True)
                return
            context.user_data[WAIT] = {"mode": "edit", "key": key, "step": "zh" if ck.kind == "copy" else "value"}
            await edit_card(q, _edit_prompt(ck), kb([[btn("取消", icon="back", callback_data=f"cfg:k:{arg[0]}")]]), None)
        elif act == "pv":
            await _preview(q, context, uid, _keys()[int(arg[0])])
            note = "预览已发到对话"
        elif act == "dd":
            store.discard_draft(uid, _keys()[int(arg[0])])
            await edit_card(q, *key_page(int(arg[0]), uid), None)
            note = "草稿已丢弃"
        elif act == "rd":
            key = _keys()[int(arg[0])]
            store.save_draft(uid, key, R.default_value(key))
            await edit_card(q, *key_page(int(arg[0]), uid), None)
            note = "已把默认值存为草稿,发布后生效"
        elif act in ("pub", "puball"):
            keys = [_keys()[int(arg[0])]] if act == "pub" else None
            await _ask_publish(q, context, uid, keys)
        elif act == "pubok":
            await _do_publish(q, context, uid, confirm_warnings=False)
        elif act == "warnok":
            await _do_publish(q, context, uid, confirm_warnings=True)
        elif act == "hist":
            await edit_card(q, *history_page(), None)
        elif act == "hv":
            await edit_card(q, *revision_page(int(arg[0])), None)
        elif act == "rb":
            rid = int(arg[0])
            await edit_card(q, f"[[alert]] <b>回滚到 v{rid}?</b>\n会生成一个新版本(内容=v{rid}),历史不会删除。",
                            kb([[btn("确认回滚", icon="check", style="danger", callback_data=f"cfg:rbok:{rid}")], [btn("取消", icon="back", callback_data=f"cfg:hv:{rid}")]]), None)
        elif act == "rbok":
            new = store.rollback(uid, int(arg[0]))
            await edit_card(q, f"[[check]] 已回滚,新版本 v{new}。", kb([BACK_HOME]), None)
            note = "已回滚"
        else:
            note = "未知操作"
    except permissions.PermissionDenied:
        note = permissions.deny_text(uid)
    except Exception as exc:
        logger.exception("settings center error: %s", act)
        note = f"出错了:{type(exc).__name__}"
    await q.answer(note or "", show_alert=bool(note and (note.startswith("出错") or note.startswith("你的角色"))))


def _edit_prompt(ck: R.ConfigKey) -> str:
    if ck.kind == "copy":
        ph = sorted(ck.placeholders("zh") | ck.placeholders("en"))
        return (f"[[edit]] <b>编辑:{_e(ck.title)}</b>\n请发送新文案:先发<b>中文</b>,我再问<b>英文</b>;也可以一条消息写成\n<code>zh: …</code>\n<code>en: …</code>\n"
                + ("必须保留占位符:" + " ".join(f"<code>{{{p}}}</code>" for p in ph) + "\n" if ph else "")
                + "支持 HTML:b i u s code pre a。发送 /cancel 放弃。")
    hint = {"number": "发送一个数字", "bool": "发送 true 或 false(或 开/关)", "text": "直接发送文本"}.get(ck.kind, "发送 JSON 文本,或直接发送 .json 文件")
    return f"[[edit]] <b>编辑:{_e(ck.title)}</b>\n{hint}。\n当前值:\n<pre>{_e(_short(store.effective_value(ck.key), 600))}</pre>"


def _parse_value(ck: R.ConfigKey, text: str):
    text = text.strip()
    if ck.kind == "number":
        return float(text) if "." in text else int(text)
    if ck.kind == "bool":
        return text.lower() in ("true", "1", "开", "on", "yes", "是")
    if ck.kind == "text":
        return text
    return json.loads(text)


async def _preview(q, context, uid: int, key: str):
    ck = R.get_key(key)
    dv = store.draft_value(uid, key)
    if dv is store._MISSING:
        dv = store.effective_value(key)
    chat = q.message.chat_id
    if ck.kind != "copy":
        await send_card(context.bot, chat, f"<b>预览 · {_e(ck.title)}</b>\n<pre>{_e(_short(dv, 1500))}</pre>", None)
        return
    class _Sample(dict):
        def __missing__(self, k):
            return f"‹{k}›"
    from ..ui import send_card as _send
    for lang in ("zh", "en"):
        try:
            text = dv[lang].format_map(_Sample())
        except Exception as exc:
            text = f"(渲染失败:{type(exc).__name__})"
        markup = None
        if key == "copy.welcome":
            from ..frontdesk.router import welcome_markup
            mk = welcome_markup(None, lang)
            markup = kb([[btn(b.text, callback_data="pv:x") for b in row] for row in mk.inline_keyboard])
        await _send(context.bot, chat, f"<i>预览 · {lang}</i>\n\n{text}", markup)


async def _ask_publish(q, context, uid: int, keys):
    diff, warns = _diff_text(uid, keys)
    if diff == "没有草稿":
        await edit_card(q, "没有可发布的草稿。", kb([BACK_HOME]), None)
        return
    context.user_data[PEND] = {"keys": keys}
    await edit_card(q, f"[[note]] <b>确认发布?</b>\n\n{diff}" + (f"\n\n⚠️ 注意:\n" + "\n".join(_e(w) for w in warns) if warns else ""),
                    kb([[btn("确认发布" if not warns else "我已知晓,继续发布", icon="check", style="success", callback_data="cfg:warnok" if warns else "cfg:pubok")],
                        [btn("取消", icon="back", callback_data="cfg:home")]]), None)


async def _do_publish(q, context, uid: int, *, confirm_warnings: bool, phrase: str | None = None):
    pend = context.user_data.get(PEND)
    if not pend:
        await edit_card(q, "没有待发布的内容(可能已处理)。", kb([BACK_HOME]), None)
        return
    try:
        rid = store.publish(uid, pend["keys"], "设置中心", confirm_warnings=confirm_warnings, confirm_phrase=phrase)
    except store.PublishBlocked as exc:
        await edit_card(q, "[[alert]] <b>自检未通过,不能发布</b>\n" + "\n".join(_e(str(i)) for i in exc.issues), kb([BACK_HOME]), None)
        return
    except store.NeedsConfirmation as exc:
        if exc.kind == "phrase":
            context.user_data[WAIT] = {"mode": "phrase", "keys": pend["keys"], "phrase": exc.detail}
            await edit_card(q, f"[[lock]] <b>高危配置:需要二次确认</b>\n请回复确认短语:<code>{_e(exc.detail)}</code>\n(收款地址取新地址的后 6 位;其余输入「确认」)\n发布后将延迟生效,期间所有业主会收到通知并可撤销。",
                            kb([[btn("取消", icon="back", callback_data="cfg:home")]]), None)
        else:
            await _ask_publish(q, context, uid, pend["keys"])
        return
    context.user_data.pop(PEND, None)
    pending = next((h for h in store.history(1) if h["id"] == rid and h["effective_at"]), None)
    msg = f"[[check]] <b>已发布 v{rid}</b>"
    if pending:
        msg += f"\n⏳ 高危配置将在 {_fmt_time(pending['effective_at'])} 生效;生效前可在「历史版本」立即撤销。"
        await _notify_owners_high(context.bot, uid, rid, pending["effective_at"])
    await edit_card(q, msg, kb([BACK_HOME]), None)


async def _notify_owners_high(bot, actor: int, rid: int, effective_at):
    for oid in permissions.owners():
        try:
            await send_card(bot, oid, f"[[alert]] <b>⚠️ 高危配置将在 {_fmt_time(effective_at)} 生效(v{rid})</b>\n操作人 <code>{actor}</code>。如非本人操作,请立即撤销。",
                            kb([[btn("立即撤销", icon="ban", style="danger", callback_data=f"cfgx:revoke:{rid}")]]))
        except Exception:
            pass


async def _revoke(q, context, uid: int, arg):
    try:
        ok = store.revoke(uid, int(arg[-1]))
    except permissions.PermissionDenied:
        await q.answer(permissions.deny_text(uid), show_alert=True)
        return
    await q.answer("已撤销,配置保持原值" if ok else "无法撤销(已生效或已被更新的版本覆盖)", show_alert=True)
    if ok:
        try:
            await edit_card(q, f"[[check]] 高危修订 v{arg[-1]} 已撤销,配置保持原值。", kb([BACK_HOME]), None)
        except Exception:
            pass


# ---------- 文本输入 ----------
async def handle_cfg_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """设置中心等待输入时接管管理员的私聊文本;没有等待中的输入直接放行。"""
    msg = update.message
    wait = context.user_data.get(WAIT)
    uid = update.effective_user.id if update.effective_user else 0
    if not wait or not msg or not msg.text or not _can_open(uid):
        return
    text = msg.text.strip()
    if text in ("/cancel", "取消"):
        context.user_data.pop(WAIT, None)
        await msg.reply_text("已取消。")
        raise ApplicationHandlerStop
    if text.startswith("/"):
        context.user_data.pop(WAIT, None)
        return
    try:
        if wait["mode"] == "phrase":
            pend = context.user_data.get(PEND) or {"keys": wait["keys"]}
            try:
                rid = store.publish(uid, pend["keys"], "设置中心(高危)", confirm_warnings=True, confirm_phrase=text)
            except store.NeedsConfirmation:
                await msg.reply_text("确认短语不对,请重新输入,或发送 /cancel 放弃。")
                raise ApplicationHandlerStop
            context.user_data.pop(WAIT, None)
            context.user_data.pop(PEND, None)
            h = next((x for x in store.history(1) if x["id"] == rid), None)
            await send_card(context.bot, update.effective_chat.id, f"[[check]] <b>已发布 v{rid}</b>" + (f"\n⏳ 将在 {_fmt_time(h['effective_at'])} 生效,生效前可撤销。" if h and h["effective_at"] else ""),
                            kb([BACK_HOME]))
            if h and h["effective_at"]:
                await _notify_owners_high(context.bot, uid, rid, h["effective_at"])
            raise ApplicationHandlerStop
        key = wait["key"]
        ck = R.get_key(key)
        if ck.kind == "copy":
            m = re.search(r"(?is)^\s*zh\s*[:：]\s*(.*?)\s*\n\s*en\s*[:：]\s*(.*)$", text)
            if m:
                value = {"zh": m.group(1).strip(), "en": m.group(2).strip()}
            elif wait["step"] == "zh":
                wait.update(step="en", zh=text)
                await msg.reply_text("好的,请再发送这张文案的英文版本。")
                raise ApplicationHandlerStop
            else:
                value = {"zh": wait["zh"], "en": text}
        else:
            value = _parse_value(ck, text)
        issues = store.save_draft(uid, key, value)
    except ApplicationHandlerStop:
        raise
    except permissions.PermissionDenied:
        await msg.reply_text(permissions.deny_text(uid))
        raise ApplicationHandlerStop
    except (ValueError, json.JSONDecodeError) as exc:
        await msg.reply_text(f"格式不对:{exc}\n请重新发送,或发送 /cancel 放弃。")
        raise ApplicationHandlerStop
    context.user_data.pop(WAIT, None)
    ki = _ki(key)
    text_out, markup = key_page(ki, uid)
    await send_card(context.bot, update.effective_chat.id, "[[check]] 草稿已保存。\n\n" + text_out, markup)
    raise ApplicationHandlerStop


async def callback_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """预览卡上的按钮:无任何副作用。"""
    await update.callback_query.answer("这是预览,按钮没有实际作用", show_alert=False)

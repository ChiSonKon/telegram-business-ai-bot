"""售后与复购(WP9):已知问题自助卡、卡密打码、交付后回访、续费提醒。

- 已知问题卡:规则匹配(零模型),"解决了"→ self_resolved + 卡片收起;"还是不行"→ 继续售后问答。
- 卡密:客户若发了完整卡密(连续 >12 位字母数字),不回显、提醒只需前 4 位,转发管理群/落库前打码(保留前 4 位)。
- 回访/续费:各任务幂等(PaymentEvent 认领 key),仅 Bot 私聊;配置 aftercare.*.enabled 可单独关闭。
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from db.database import SessionMaker
from db.model import Order

from ..commerce import service
from ..config import cfg, copy
from ..experience import ui_registry
from ..ui import btn, kb, send_card

logger = logging.getLogger(__name__)

from ..config.defaults_flows import DEFAULT_KNOWN_ISSUES


# 连续 13 位及以上字母数字视为疑似完整卡密;64 位十六进制(交易哈希)与订单号不在此列
_KEY_RE = re.compile(r"(?<![A-Za-z0-9/_\-.])[A-Za-z0-9][A-Za-z0-9\-]{11,}[A-Za-z0-9](?![A-Za-z0-9/_\-.])")
_HASH_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{64}$")


def known_issues() -> list[dict]:
    return cfg.get("kb.known_issues", default=DEFAULT_KNOWN_ISSUES) or []


def match_known(text: str) -> dict | None:
    low = (text or "").lower()
    for item in known_issues():
        if any(str(t).lower() in low for t in item.get("triggers", [])):
            return item
    return None


def _is_key_like(token: str) -> bool:
    if _HASH_RE.match(token):
        return False
    core = token.replace("-", "")
    return len(core) > 12 and any(c.isdigit() for c in core) and any(c.isalpha() for c in core)


def mask_keys(text: str) -> str:
    """把疑似完整卡密替换为「前 4 位…****」;其余文字不动。"""
    def sub(m):
        tok = m.group(0)
        return f"{tok[:4]}…****" if _is_key_like(tok) else tok
    return _KEY_RE.sub(sub, text or "")


def contains_full_key(text: str) -> bool:
    return mask_keys(text) != (text or "")


def _remember(user_id: int, text: str) -> None:
    service._set(f"known_issue_text:{user_id}", text[:300])


def recall(user_id: int) -> str:
    return service._get(f"known_issue_text:{user_id}", "")


async def send_known_issue(bot, user_id: int, chat_id: int, lang: str, item: dict, text: str):
    """发已知问题卡并登记(之后可收起)。返回消息或 None。"""
    from . import answers
    rows = []
    guide = item.get("guide_url") or answers._official().get("install_guide")
    if guide:
        rows.append([btn(copy("btn.install_guide", lang), icon="info", url=guide)])
    rows.append([btn(copy("btn.resolved", lang), style="success", callback_data=f"aft:ok:{item['id']}"),
                 btn(copy("btn.not_resolved", lang), callback_data=f"aft:no:{item['id']}")])
    msg = await send_card(bot, chat_id, copy(item["card_key"], lang), kb(rows))
    if msg:
        _remember(user_id, text)
        ui_registry.record(user_id, chat_id, msg.message_id, "known_issue", ref=item["id"])
    return msg


# ---------- 交付后回访 ----------
def _bot_started(uid: int) -> bool:
    from . import profile
    return profile.bot_started(uid)


def _enabled(key: str) -> bool:
    return bool(cfg.get(f"aftercare.{key}.enabled", default=True))


def _claim(key: str, order_id: str) -> bool:
    return service.claim_event(key, order_id, "mark", "sent", "")


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def checkin_job(bot, now: float | None = None) -> int:
    """delivered_at + 24h 且未发 → Bot 私聊回访一次(Business 客户不发)。返回发送数。"""
    if not _enabled("checkin"):
        return 0
    cutoff = datetime.fromtimestamp(now or time.time(), timezone.utc) - timedelta(hours=24)
    with SessionMaker() as db:
        rows = [r for r in db.query(Order).filter(Order.state == "delivered", Order.delivered_at.isnot(None)).all()
                if _aware(r.delivered_at) <= cutoff]
        db.expunge_all()
    n = 0
    for o in rows:
        if not _bot_started(o.user_id) or not _claim(f"checkin:{o.id}", o.id):
            continue
        lang = o.lang if o.lang in ("zh", "en") else "zh"
        msg = await send_card(bot, o.user_id, copy("order.checkin", lang, id=o.id), kb([[
            btn(copy("btn.all_good", lang), style="success", callback_data=f"aft:fine:{o.id}"),
            btn(copy("btn.need_help", lang), callback_data=f"aft:help:{o.id}")]]))
        if msg:
            ui_registry.record(o.user_id, o.user_id, msg.message_id, "checkin", ref=o.id)
            n += 1
    return n


# ---------- 续费提醒 ----------
async def renew_job(bot, now: float | None = None) -> int:
    """expires_on 前 3 天内且未提醒 → Bot 私聊一次 order.renew。"""
    if not _enabled("renew"):
        return 0
    t = datetime.fromtimestamp(now or time.time(), timezone.utc)
    with SessionMaker() as db:
        due = [r for r in db.query(Order).filter(Order.expires_on.isnot(None), Order.state.in_(("paid", "fulfilling", "delivered"))).all()
               if _aware(r.expires_on) - timedelta(days=3) <= t < _aware(r.expires_on)]
        db.expunge_all()
    n = 0
    for o in due:
        if not _bot_started(o.user_id) or not _claim(f"renew3:{o.id}", o.id):
            continue
        lang = o.lang if o.lang in ("zh", "en") else "zh"
        msg = await send_card(bot, o.user_id, copy("order.renew", lang, title=o.title or o.id, date=o.expires_on.strftime("%Y-%m-%d")), kb([[
            btn(copy("btn.renew", lang), style="primary", callback_data=f"aft:renew:{o.id}"),
            btn(copy("btn.not_now", lang), callback_data=f"aft:later:{o.id}")]]))
        if msg:
            ui_registry.record(o.user_id, o.user_id, msg.message_id, "renew", ref=o.id)
            n += 1
    return n


def renew_brief(order: Order) -> str:
    return f"续费意向|原订单 {order.id}|{order.title}|到期 {str(order.expires_on)[:10]}"

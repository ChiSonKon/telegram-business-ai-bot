"""AI 回合后的规则 CTA 卡(03 §7.3):官方入口等。AI 不生成链接,卡片由规则发送,按钮为官方 URL。"""
from __future__ import annotations

import json
import logging

from db.database import SessionMaker
from db.model import FrontdeskCase

from ..config import cfg

logger = logging.getLogger(__name__)


def rules() -> list[dict]:
    return cfg.get("cta.rules", default=[]) or []


def _card(card_key: str, lang: str):
    from ..frontdesk import answers
    fn = {"official_buy": answers.official_buy}.get(card_key)
    return fn(lang) if fn else None


def _take_once(user_id: int, card_key: str) -> bool:
    with SessionMaker() as db:
        c = db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=0).order_by(FrontdeskCase.id.desc()).first()
        if not c:
            return False
        try:
            a = json.loads(c.answers_json or "{}")
        except ValueError:
            a = {}
        sent = a.setdefault("cta_sent", [])
        if card_key in sent:
            return False
        sent.append(card_key)
        c.answers_json = json.dumps(a, ensure_ascii=False)
        db.commit()
        return True


async def maybe_send(bot, user_id: int, chat_id: int, line: str | None, turn_no: int, lang: str, biz_conn_id: str | None = None) -> str | None:
    """满足规则则发送一张 CTA 卡,返回卡片键;否则 None。"""
    from ..frontdesk import router
    for r in rules():
        if r.get("line") != line or turn_no < int(r.get("after_turn", 1)):
            continue
        card = _card(r["card"], lang)
        if not card:
            continue
        if r.get("once_per_case", True) and not _take_once(user_id, r["card"]):
            continue
        text, markup = card
        await router.send(bot, user_id, chat_id, text, markup, biz_conn_id, lang)
        router._emit("cta_shown", user_id, kind=r["card"][:32])
        return r["card"]
    return None

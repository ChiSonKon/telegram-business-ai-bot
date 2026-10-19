"""Bot 私聊:未选业务的新用户自由发言被拦截(不转发、不进 AI),只回菜单卡。"""
import importlib

PKG = "interactive-bot"


def test_needs_pick_until_a_business_is_chosen(env):
    uh = importlib.import_module(f"{PKG}.handlers.user_handlers")
    from db.database import SessionMaker
    from db.model import FrontdeskCase
    assert uh._needs_pick(910001) is True
    with SessionMaker() as s:
        s.add(FrontdeskCase(user_id=910001, business_owner_id=0, state="FRONTDESK"))
        s.commit()
    assert uh._needs_pick(910001) is True            # 只 /start 过,仍算未选
    with SessionMaker() as s:
        c = s.query(FrontdeskCase).filter_by(user_id=910001).first()
        c.state = "PRECHECK"
        s.commit()
    assert uh._needs_pick(910001) is False


def test_welcome_asks_to_pick_a_business_first(env):
    from importlib import import_module
    txt = import_module(f"{PKG}.config").copy("welcome", "zh")
    assert "先点下面的按钮" in txt

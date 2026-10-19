"""全局美化层:高级表情替换、按钮自动上色、被拒绝时三档降级、无关错误不会关闭高级表情。"""
import asyncio
import importlib

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

PKG = "interactive-bot"


@pytest.fixture()
def ui(env):
    icons = importlib.import_module(f"{PKG}.ui.icons")
    icons._suspended_until = 0.0
    icons.set_custom_emoji_enabled(True)
    en = importlib.import_module(f"{PKG}.ui.enrich")
    yield en
    icons._suspended_until = 0.0


def run(c):
    return asyncio.run(c)


def test_enrich_html_replaces_plain_emoji_but_not_inside_tags_or_code(ui):
    out = ui.enrich_html('✅ 完成 <b>好</b> <code>✅</code> <a href="https://x/✅">链接</a>')
    assert out.count("<tg-emoji") == 1 and out.startswith('<tg-emoji emoji-id="')
    assert "<code>✅</code>" in out and 'href="https://x/✅"' in out
    already = '<tg-emoji emoji-id="1">✅</tg-emoji> 已处理'
    assert ui.enrich_html(already) == already                               # 幂等:已渲染的不再套娃


def test_smart_style_rules(ui):
    s = ui.smart_style
    assert s("❌ 取消") == "danger" and s("删除 abc") == "danger"
    assert s("✅ 确认广播") == "success" and s("保存") == "success" and s("新增AI") == "success"
    assert s("◀ 返回主菜单") is None and s("🔄 刷新") is None
    assert s("设置中心") == "primary"


def test_decorate_markup_adds_style_and_icon_but_keeps_explicit(ui):
    mk = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确认广播", callback_data="a"),
                                InlineKeyboardButton("普通按钮", callback_data="b"),
                                InlineKeyboardButton("删除", callback_data="c", api_kwargs={"style": "primary"})],
                               [InlineKeyboardButton("◀ 返回主菜单", callback_data="d")]])
    out = ui.decorate_markup(mk)
    a, b, c = out.inline_keyboard[0]
    assert a.api_kwargs["style"] == "success" and a.api_kwargs.get("icon_custom_emoji_id") and "✅" not in a.text and a.text == "确认广播"
    assert b.api_kwargs["style"] == "primary"
    assert c.api_kwargs["style"] == "primary"                               # 显式设置的颜色不被覆盖
    back = out.inline_keyboard[1][0]
    assert "style" not in (back.api_kwargs or {})                           # 返回类不上色
    assert ui.decorate_markup(None) is None


class Recorder:
    def __init__(self, fail_with=()):
        self.calls, self.fail_with = [], list(fail_with)

    async def __call__(self, bot, **kw):
        self.calls.append(kw)
        if self.fail_with:
            raise self.fail_with.pop(0)
        return "sent"


def test_wrapper_enriches_html_plain_text_and_buttons(ui):
    rec = Recorder()
    w = ui.make_wrapper(rec, "text")
    mk = InlineKeyboardMarkup([[InlineKeyboardButton("保存", callback_data="x")]])
    assert run(w(None, chat_id=1, text="✅ 完成 <b>x</b>", parse_mode="HTML", reply_markup=mk)) == "sent"
    sent = rec.calls[0]
    assert "<tg-emoji" in sent["text"] and sent["reply_markup"].inline_keyboard[0][0].api_kwargs["style"] == "success"
    rec2 = Recorder()
    run(ui.make_wrapper(rec2, "text")(None, chat_id=1, text="a<b ✅"))       # 未指定 parse_mode:先转义再替换
    assert rec2.calls[0]["parse_mode"] == "HTML" and "a&lt;b" in rec2.calls[0]["text"] and "<tg-emoji" in rec2.calls[0]["text"]


def test_wrapper_skips_entities_but_decorates_business(ui):
    rec = Recorder()
    run(ui.make_wrapper(rec, "text")(None, chat_id=1, text="✅ ok", parse_mode="HTML", entities=["x"]))
    assert rec.calls[0]["text"] == "✅ ok"                                              # 已带 entities:不介入
    rec2 = Recorder()
    mk = InlineKeyboardMarkup([[InlineKeyboardButton("🖥 WhiteCat 5.0 软件", callback_data="a")]])
    run(ui.make_wrapper(rec2, "text")(None, chat_id=1, text="✅ ok", parse_mode="HTML", business_connection_id="b", reply_markup=mk))
    sent = rec2.calls[0]
    assert "<tg-emoji" in sent["text"]                                                   # Business 聊天里也用高级表情
    btn = sent["reply_markup"].inline_keyboard[0][0]
    assert btn.api_kwargs.get("icon_custom_emoji_id") and btn.api_kwargs["style"] == "primary" and btn.text == "WhiteCat 5.0 软件"


def test_business_rejection_disables_only_business_channel(ui):
    icons = importlib.import_module(f"{PKG}.ui.icons")
    caps = importlib.import_module(f"{PKG}.experience.capabilities")
    rec = Recorder(fail_with=[BadRequest("custom emoji not allowed in business messages")])
    run(ui.make_wrapper(rec, "text")(None, chat_id=1, text="✅ 好", parse_mode="HTML", business_connection_id="b"))
    assert len(rec.calls) == 2 and "<tg-emoji" not in rec.calls[1]["text"]               # 降级为普通 emoji 后送达
    assert caps.ok("business", "custom_emoji") is False and icons.custom_emoji_enabled() is True    # 只关 Business,Bot 私聊不受影响
    rec2 = Recorder()
    run(ui.make_wrapper(rec2, "text")(None, chat_id=1, text="✅ 好", parse_mode="HTML", business_connection_id="b"))
    assert rec2.calls[0]["text"] == "✅ 好"                                              # 之后 24 小时 Business 不再尝试


def test_full_tgios_pack_covers_line_icons(ui):
    icons = importlib.import_module(f"{PKG}.ui.icons")
    assert len(icons.PACK) >= 150
    eng = importlib.import_module(f"{PKG}.frontdesk.intake.engine")
    for line, emoji in eng.LINE_ICONS.items():
        eid, rest = ui._leading_icon(f"{emoji} 名称")
        assert eid and rest == "名称", (line, emoji)                                       # 业务线选项按钮都能换成高级表情图标


def test_rejected_emoji_degrades_but_keeps_button_colors(ui):
    rec = Recorder(fail_with=[BadRequest("Bad Request: custom emoji are not allowed")])
    mk = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 确认", callback_data="x")]])
    run(ui.make_wrapper(rec, "text")(None, chat_id=1, text="✅ 好", parse_mode="HTML", reply_markup=mk))
    assert len(rec.calls) == 2
    second = rec.calls[1]
    assert "<tg-emoji" not in second["text"] and "✅" in second["text"]            # 普通 emoji
    btn = second["reply_markup"].inline_keyboard[0][0]
    assert btn.api_kwargs == {"style": "success"} and btn.text.startswith("✅")      # 颜色保留,图标变回文字


def test_style_rejected_too_falls_back_to_original(ui):
    rec = Recorder(fail_with=[BadRequest("custom emoji nope"), BadRequest("button style invalid")])
    mk = InlineKeyboardMarkup([[InlineKeyboardButton("保存", callback_data="x")]])
    run(ui.make_wrapper(rec, "text")(None, chat_id=1, text="✅ 好", parse_mode="HTML", reply_markup=mk))
    assert len(rec.calls) == 3 and rec.calls[2]["text"] == "✅ 好" and rec.calls[2]["reply_markup"] is mk


def test_unrelated_bad_request_propagates_and_does_not_disable_emoji(ui):
    icons = importlib.import_module(f"{PKG}.ui.icons")
    rec = Recorder(fail_with=[BadRequest("Bad Request: can't parse entities: Unsupported start tag")])
    with pytest.raises(BadRequest):
        run(ui.make_wrapper(rec, "text")(None, chat_id=1, text="✅ <bad", parse_mode="HTML"))
    assert icons.custom_emoji_enabled() is True and len(rec.calls) == 1


def test_install_is_idempotent(ui):
    import telegram
    ui.install()
    first = telegram.Bot.send_message
    ui.install()
    assert telegram.Bot.send_message is first and first._wc_wrapped


def test_unrendered_icon_tokens_never_reach_users(ui):
    rec = Recorder()
    run(ui.make_wrapper(rec, "text")(None, chat_id=1, text="[[check]] 好了 [[nope]]", parse_mode="HTML"))
    assert "[[" not in rec.calls[0]["text"] and "<tg-emoji" in rec.calls[0]["text"]
    rec2 = Recorder()
    run(ui.make_wrapper(rec2, "text")(None, chat_id=1, text="[[check]] 好了"))                      # 纯文本:换成普通 emoji
    assert rec2.calls[0]["text"].startswith("✅") or "tg-emoji" in rec2.calls[0]["text"]
    rec3 = Recorder()
    run(ui.make_wrapper(rec3, "text")(None, chat_id=1, text="[[check]] 好了", parse_mode="HTML", business_connection_id="b"))
    assert "[[" not in rec3.calls[0]["text"]                                                         # Business 同样不会漏出占位符

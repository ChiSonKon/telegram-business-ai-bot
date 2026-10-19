"""前台预检(兼容层)。

WP3 起问答逻辑在 intake.engine(数据驱动、先答后问、最多 3 题、可跳过、旁问不填槽);
本模块保留原有函数名供 handlers/gate/router 调用，底层统一使用 intake.engine。
"""
from __future__ import annotations

from . import lines as biz_lines
from .intake import engine
from .intake.engine import AcceptResult, Completed  # noqa: F401
from .intake.engine import SKIP

# 兼容只读审计/文案测试的导出；流程实现不再依赖旧 precheck 模块。
P = engine._done_texts()


def entry_flow(role: str, text: str = "", line: str | None = None) -> tuple[str, str | None]:
    """(flow_id, line_id):按来意与首句决定从哪条流程开始。"""
    from ..config import cfg
    if role == "support":
        return "support", None
    if role == "partner":
        return "partner", None
    if role == "visitor":
        return "visitor", None
    if line is None and text:
        ln = biz_lines.match_line(text)
        line = ln["id"] if ln else None
    if line:
        ln = next((x for x in biz_lines.get_lines() if x["id"] == line), None)
        if ln and ln.get("sensitive"):
            return "sensitive", line
        return cfg.get("intake.line_flow", default={}).get(line, "generic"), line
    return "prospect", None


def begin(user_id: int, role: str, text: str = "", owner_id: int = 0, channel: str = "bot", line: str | None = None) -> None:
    """进入问答。已知信息自动填充(例如首句就说清了业务和需求)。"""
    flow, line = entry_flow(role, text, line)
    engine.start_flow(user_id, flow, text, owner_id=owner_id, channel=channel, line=line)
    if flow == "sensitive":
        engine._mutate(user_id, owner_id, lambda st, c: st.update(first=text) if text else None)
        if text and len("".join(text.split())) >= 10:       # 首句已经把平台和需求说清楚:直接登记
            engine.accept_text(user_id, text, "zh", owner_id)


def is_active(user_id: int, owner_id: int = 0) -> bool:
    return engine.is_active(user_id, owner_id)


def current_step(user_id: int, owner_id: int = 0) -> str | None:
    return engine.current_step(user_id, owner_id)


def prompt(user_id: int, lang: str, owner_id: int = 0):
    """当前待回答步骤的卡片;全部收齐返回 None。"""
    return engine.current_question(user_id, lang, owner_id)


def answer(user_id: int, step: str, value: str, owner_id: int = 0, method: str = "button") -> bool:
    """记录一步答案(按钮)。步骤不在当前流程或已作答则忽略,防止旧卡片乱序点击。"""
    if value == SKIP:
        return engine.skip(user_id, step, owner_id)
    return engine.accept_button(user_id, step, value, owner_id)


def accept_text(user_id: int, text: str, owner_id: int = 0, lang: str = "zh") -> AcceptResult | None:
    return engine.accept_text(user_id, text, lang, owner_id)


def complete(user_id: int, lang: str, owner_id: int = 0):
    return engine.complete(user_id, lang, owner_id)


def set_flag(user_id: int, key: str, value, owner_id: int = 0) -> None:
    engine._mutate(user_id, owner_id, lambda st, c: st.__setitem__(key, value))


def sensitive_waiting(user_id: int, owner_id: int = 0) -> bool:
    a = engine.active_state(user_id, owner_id)
    return bool(a and a[1]["flow"] == "sensitive")

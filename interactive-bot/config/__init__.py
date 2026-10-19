"""配置门面:store 覆盖值优先,注册表/默认值兜底;调用方只用 cfg.get / copy。

- 文案键在 store 里以 "copy.<key>" 保存;cfg.get("welcome") 自动映射。
- 草稿覆盖层:/asclient draft 期间,同一管理员的草稿值优先于线上值(contextvar,仅限当前处理链)。
- copy() 渲染失败(占位符缺失等)时回退到默认文案并上报给发布后守护,绝不让客户面对沉默。
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
from typing import Any

from . import defaults_copy, defaults_flows

logger = logging.getLogger(__name__)
_MISSING = object()
_draft_user: contextvars.ContextVar = contextvars.ContextVar("cfg_draft_user", default=None)


def _store_key(key: str) -> str:
    return "copy." + key if key in defaults_copy.COPY else key


def _override(key: str):
    """store(以及草稿覆盖层)里的覆盖值;没有则 _MISSING。任何异常都当作没有覆盖。"""
    try:
        from . import store
        sk = _store_key(key)
        uid = _draft_user.get()
        if uid:
            v = store.draft_value(uid, sk)
            if v is not _MISSING:
                return v
        return store.override(sk)
    except Exception:
        return _MISSING


@contextlib.contextmanager
def draft_scope(admin_id: int | None):
    """在此上下文内,读取配置时优先使用该管理员的草稿(客户视角沙盒:/asclient draft)。"""
    token = _draft_user.set(admin_id)
    try:
        yield
    finally:
        _draft_user.reset(token)


class _Cfg:
    def get(self, key: str, lang: str | None = None, default: Any = None) -> Any:
        override = _override(key)
        if override is not _MISSING:
            val = override
        elif key in defaults_copy.COPY:
            val = defaults_copy.COPY[key]
        elif key in defaults_flows.DEFAULTS:
            val = defaults_flows.DEFAULTS[key]
        else:
            return default
        if lang and isinstance(val, dict) and ("zh" in val or "en" in val):
            return val.get(lang) or val.get("zh")
        return val


cfg = _Cfg()


def copy(key: str, lang: str, **fmt) -> str:
    """取文案并 format;缺 key 抛 KeyError(测试据此发现拼写错误)。线上覆盖文案格式化失败 → 回退默认文案并上报。"""
    if key not in defaults_copy.COPY and _override(key) is _MISSING:
        raise KeyError(key)
    text = cfg.get(key, lang)
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, IndexError, ValueError):
        logger.error("copy %s render failed; falling back to default", key)
        try:
            from . import store
            store.report_render_error(key)
        except Exception:
            pass
        base = defaults_copy.COPY.get(key)
        if base is None:
            raise
        return (base.get(lang) or base["zh"]).format(**fmt)

"""网络代理解析:显式开关,不再静默自动探测。"""
from __future__ import annotations

import os
import socket

_PROBES = [(10808, "socks5"), (7890, "http"), (10809, "http")]


def _probe() -> str | None:
    for port, scheme in _PROBES:
        try:
            s = socket.socket()
            s.settimeout(0.4)
            s.connect(("127.0.0.1", port))
            s.close()
            return f"{scheme}://127.0.0.1:{port}"
        except Exception:
            pass
    return None


def resolve_telegram_proxy() -> tuple[str | None, str]:
    """返回 (代理URL或None, 来源)。TELEGRAM_PROXY=<url> 直接使用;=auto 才探测;未设置 = 不用代理。"""
    raw = (os.getenv("TELEGRAM_PROXY") or "").strip()
    if not raw:
        return None, "none"
    if raw.lower() == "auto":
        return _probe(), "auto"
    return raw, "env"


def detect_telegram_proxy() -> str | None:
    return resolve_telegram_proxy()[0]

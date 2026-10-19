"""中英双语判定:首句判定 + 粘性切换。"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HAN = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")
_URL = re.compile(r"https?://\S+|\S+@\S+|t\.me/\S+|@\w+|\b[A-Za-z0-9]{20,}\b")


def detect(text: str) -> str | None:
    """返回 'zh' / 'en' / None(信息不足,如纯表情、数字、链接)。"""
    t = _URL.sub("", text or "")
    han, lat = len(_HAN.findall(t)), len(_LATIN.findall(t))
    if han == 0 and lat < 2:
        return None
    # 一个汉字信息量约等于一个英文词(~3 字母);中文句中夹品牌名/英文术语仍判中文
    if han >= 2 and han * 3 >= lat:
        return "zh"
    return "en" if lat >= 2 else "zh"


def _is_real_english(text: str) -> bool:
    """至少 3 个英文单词(去掉链接后),才足以说明客户改用英文。"""
    t = _URL.sub("", text or "")
    return len(re.findall(r"[A-Za-z]{2,}", t)) >= 3


@dataclass
class LangState:
    current: str = "zh"
    locked: bool = False
    initialized: bool = False
    _streak: int = 0
    _streak_lang: str | None = None
    _last_text: str | None = None

    def update(self, text: str, *, switch_after: int = 2) -> str:
        if self.locked:
            return self.current
        if text == self._last_text:      # 同一条消息会被入口与前台各读一次,只计一次
            return self.current
        self._last_text = text
        seen = detect(text)
        if seen is None:
            return self.current
        if not self.initialized:
            self.current, self.initialized = seen, True
            return self.current
        if seen == self.current:
            self._streak, self._streak_lang = 0, None
            return self.current
        if seen == "en" and not _is_real_english(text):
            return self.current          # "IP"/"USDT"/"VPN" 这类术语不算语言切换
        if seen == "zh" and self.current == "en" and len(_HAN.findall(text)) < 2:
            return self.current
        self._streak = self._streak + 1 if self._streak_lang == seen else 1
        self._streak_lang = seen
        if self._streak >= switch_after:
            self.current, self._streak, self._streak_lang = seen, 0, None
        return self.current

    def force(self, lang: str) -> None:
        self.current, self.locked, self.initialized = lang, True, True

"""运行时 AI 引擎配置。

管理员可以在机器人后台维护多个 OpenAI 兼容接口配置；调用时按优先级
依次尝试，首位不可用时自动切换到下一个可用配置。
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from db.database import SessionMaker, engine
from db.model import AppSetting

from . import llm_api_base, llm_api_key, llm_model


SETTING_AI_CONFIGS = "ai.configs"


def ensure_ai_settings_table() -> None:
    """确保运行时配置表存在。"""
    AppSetting.__table__.create(bind=engine, checkfirst=True)


def _session():
    return SessionMaker()


def _get_setting(key: str, default: str) -> str:
    ensure_ai_settings_table()
    db = _session()
    try:
        setting = db.query(AppSetting).filter(AppSetting.key == key).first()
        return setting.value if setting else default
    finally:
        db.close()


def _set_setting(key: str, value: str) -> None:
    ensure_ai_settings_table()
    db = _session()
    try:
        setting = db.query(AppSetting).filter(AppSetting.key == key).first()
        if setting:
            setting.value = value
        else:
            setting = AppSetting(key=key, value=value)
            db.add(setting)
        db.commit()
    finally:
        db.close()


def _default_ai_configs() -> list[dict[str, Any]]:
    return [
        {
            "id": "default",
            "name": "默认AI",
            "base_url": llm_api_base,
            "api_key": llm_api_key,
            "model": llm_model,
            "enabled": True,
            "priority": 1,
        }
    ]


def _truthy(value: Any) -> bool:
    text = str(value).strip().lower()
    return text not in {"0", "false", "no", "off", "disable", "disabled", "否", "停用", "关闭"}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip()).strip("-").lower()
    return slug[:32] or uuid.uuid4().hex[:8]


def _normalize_config(item: dict[str, Any], index: int = 0) -> dict[str, Any]:
    name = str(item.get("name") or f"AI-{index + 1}").strip()
    base_url = str(item.get("base_url") or item.get("api_base") or "").strip().rstrip("/")
    api_key = str(item.get("api_key") or "").strip()
    model = str(item.get("model") or "").strip()
    config_id = str(item.get("id") or _slug(name) or uuid.uuid4().hex[:8]).strip()
    try:
        priority = int(item.get("priority", index + 1) or index + 1)
    except (TypeError, ValueError):
        priority = index + 1

    return {
        "id": config_id[:48],
        "name": name or f"AI-{index + 1}",
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "enabled": _truthy(item.get("enabled", True)),
        "priority": max(1, priority),
    }


def _dedupe_ids(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    for cfg in configs:
        base = _slug(cfg.get("id") or cfg.get("name") or "ai")
        candidate = base
        counter = 2
        while candidate in seen:
            candidate = f"{base}-{counter}"
            counter += 1
        cfg["id"] = candidate[:48]
        seen.add(cfg["id"])
    return configs


def get_ai_configs() -> list[dict[str, Any]]:
    """读取全部 AI 配置；无数据库配置时回退到 .env 默认值。"""
    raw = _get_setting(SETTING_AI_CONFIGS, "")
    configs: list[dict[str, Any]] = []
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                configs = [
                    _normalize_config(item, idx)
                    for idx, item in enumerate(data)
                    if isinstance(item, dict)
                ]
        except Exception:
            configs = []

    if not configs:
        configs = _default_ai_configs()

    configs = [cfg for cfg in configs if cfg["base_url"] and cfg["api_key"] and cfg["model"]]
    if not configs:
        configs = _default_ai_configs()
    return sorted(_dedupe_ids(configs), key=lambda c: (c["priority"], c["name"]))


def set_ai_configs(configs: list[dict[str, Any]]) -> None:
    normalized = [
        _normalize_config(cfg, idx)
        for idx, cfg in enumerate(configs)
        if cfg.get("base_url") and cfg.get("api_key") and cfg.get("model")
    ]
    if not normalized:
        raise ValueError("至少需要保留 1 个完整 AI 配置")
    if not any(cfg["enabled"] for cfg in normalized):
        normalized[0]["enabled"] = True
    for idx, cfg in enumerate(sorted(_dedupe_ids(normalized), key=lambda c: c["priority"]), start=1):
        cfg["priority"] = idx
    _set_setting(SETTING_AI_CONFIGS, json.dumps(normalized, ensure_ascii=False))


def get_enabled_ai_configs() -> list[dict[str, Any]]:
    enabled = [cfg for cfg in get_ai_configs() if cfg.get("enabled")]
    return enabled or get_ai_configs()[:1]


def get_primary_ai_config() -> dict[str, Any]:
    return get_enabled_ai_configs()[0]


def add_ai_config(name: str, base_url: str, api_key: str, model: str, enabled: bool = True) -> None:
    configs = get_ai_configs()
    configs.append(
        {
            "id": _slug(name) or uuid.uuid4().hex[:8],
            "name": name,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "enabled": enabled,
            "priority": len(configs) + 1,
        }
    )
    set_ai_configs(configs)


def promote_ai_config(config_id: str) -> bool:
    """将指定配置设为首位。"""
    configs = get_ai_configs()
    target = None
    others = []
    for cfg in configs:
        if cfg["id"] == config_id:
            target = cfg
        else:
            others.append(cfg)
    if not target:
        return False
    target["enabled"] = True
    ordered = [target] + others
    for idx, cfg in enumerate(ordered, start=1):
        cfg["priority"] = idx
    set_ai_configs(ordered)
    return True


def toggle_ai_config(config_id: str) -> bool:
    configs = get_ai_configs()
    changed = False
    for cfg in configs:
        if cfg["id"] == config_id:
            if cfg.get("enabled") and sum(1 for item in configs if item.get("enabled")) <= 1:
                raise ValueError("至少需要保留 1 个启用中的 AI")
            cfg["enabled"] = not cfg.get("enabled", True)
            changed = True
            break
    if changed:
        set_ai_configs(configs)
    return changed


def delete_ai_config(config_id: str) -> bool:
    configs = get_ai_configs()
    if len(configs) <= 1:
        raise ValueError("至少需要保留 1 个 AI 配置")
    kept = [cfg for cfg in configs if cfg["id"] != config_id]
    if len(kept) == len(configs):
        return False
    set_ai_configs(kept)
    return True


def mask_api_key(api_key: str) -> str:
    if not api_key:
        return "未设置"
    return api_key[:6] + "..." + api_key[-4:] if len(api_key) > 12 else "***"


def format_ai_configs() -> str:
    lines = []
    for idx, cfg in enumerate(get_ai_configs(), start=1):
        status = "✅启用" if cfg.get("enabled") else "⏸停用"
        primary = " 🥇首位" if idx == 1 and cfg.get("enabled") else ""
        lines.append(
            f"{idx}. {status}{primary} {cfg['name']}\n"
            f"   ID: {cfg['id']}\n"
            f"   API: {cfg['base_url']}\n"
            f"   模型: {cfg['model']}\n"
            f"   Key: {mask_api_key(cfg['api_key'])}"
        )
    return "\n\n".join(lines) if lines else "未设置"


def get_ai_config_text() -> str:
    primary = get_primary_ai_config()
    return (
        "🤖 AI 引擎设置\n"
        "==============================\n\n"
        "当前会按列表顺序调用：首位 AI 失败会自动尝试下一个启用的 AI；"
        "备用 AI 成功后会自动升为首位。\n\n"
        f"当前首位：{primary['name']} / {primary['model']}\n\n"
        f"配置列表：\n{format_ai_configs()}"
    )


def parse_ai_config_line(line: str) -> dict[str, Any]:
    parts = [part.strip() for part in line.split("|")]
    if len(parts) < 4:
        raise ValueError("格式应为：名称 | API Base | API Key | 模型 | 启用(可选)")
    name, base_url, api_key, model = parts[:4]
    if not name or not base_url or not api_key or not model:
        raise ValueError("名称、API Base、API Key、模型都不能为空")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("API Base 必须以 http:// 或 https:// 开头")
    return {
        "name": name,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "enabled": _truthy(parts[4]) if len(parts) > 4 and parts[4] else True,
    }


def parse_ai_configs_bulk(text: str) -> list[dict[str, Any]]:
    configs = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            cfg = parse_ai_config_line(line)
        except ValueError as exc:
            raise ValueError(f"第 {line_no} 行：{exc}") from exc
        cfg["priority"] = len(configs) + 1
        configs.append(cfg)
    if not configs:
        raise ValueError("没有解析到任何 AI 配置")
    return configs


def get_ai_config_help(mode: str = "add") -> str:
    title = "新增一个 AI 配置" if mode == "add" else "批量覆盖 AI 配置"
    return (
        f"请发送{title}，格式：\n"
        "名称 | API Base | API Key | 模型 | 启用(可选 yes/no)\n\n"
        "示例：\n"
        "DeepSeek | https://api.deepseek.com/v1 | sk-xxxx | deepseek-chat | yes\n"
        "OpenAI | https://api.openai.com/v1 | sk-xxxx | gpt-4o-mini | yes\n"
        "Ollama | http://localhost:11434/v1 | ollama | llama3.1 | no\n\n"
        "说明：\n"
        "• 模型直接写在第 4 列，可随时换成供应商支持的模型名。\n"
        "• 批量覆盖时每行一个配置，行顺序就是自动切换优先级。\n"
        "• API Key 会保存到本机数据库，请只在可信环境使用。"
    )
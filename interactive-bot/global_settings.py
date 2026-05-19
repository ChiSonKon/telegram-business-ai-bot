"""运行时全局参数配置。"""
import json
import logging
from typing import Any, Dict

from db.database import SessionMaker, engine
from db.model import AppSetting

from . import (
    is_delete_topic_as_ban_forever as env_ban_forever,
    is_delete_user_messages as env_delete_msgs,
    disable_captcha as env_disable_captcha,
    message_interval as env_msg_interval,
    rate_limit_per_minute as env_rate_limit,
)

logger = logging.getLogger(__name__)

SETTING_GLOBAL_CONFIG = "bot.global_config"

def _session():
    return SessionMaker()

def _default_config() -> Dict[str, Any]:
    return {
        "is_delete_topic_as_ban_forever": env_ban_forever,
        "is_delete_user_messages": env_delete_msgs,
        "disable_captcha": env_disable_captcha,
        "message_interval": env_msg_interval,
        "rate_limit_per_minute": env_rate_limit,
    }

def get_global_config() -> Dict[str, Any]:
    """读取全局配置。"""
    db = _session()
    try:
        setting = db.query(AppSetting).filter(AppSetting.key == SETTING_GLOBAL_CONFIG).first()
        if setting and setting.value:
            try:
                data = json.loads(setting.value)
                # 与默认配置合并，保证新加的字段有默认值
                config = _default_config()
                config.update(data)
                return config
            except Exception as e:
                logger.warning(f"解析全局配置失败: {e}")
        
        # 不存在则初始化
        config = _default_config()
        val = json.dumps(config, ensure_ascii=False)
        if setting:
            setting.value = val
        else:
            db.add(AppSetting(key=SETTING_GLOBAL_CONFIG, value=val))
        db.commit()
        return config
    except Exception as e:
        logger.error(f"读取全局配置失败: {e}")
        return _default_config()
    finally:
        db.close()

def update_global_config(updates: Dict[str, Any]) -> None:
    """更新全局配置。"""
    db = _session()
    try:
        current = get_global_config()
        current.update(updates)
        val = json.dumps(current, ensure_ascii=False)
        
        setting = db.query(AppSetting).filter(AppSetting.key == SETTING_GLOBAL_CONFIG).first()
        if setting:
            setting.value = val
        else:
            db.add(AppSetting(key=SETTING_GLOBAL_CONFIG, value=val))
        db.commit()
    finally:
        db.close()

def get_global_setting(key: str) -> Any:
    """获取单个全局配置项。"""
    return get_global_config().get(key)

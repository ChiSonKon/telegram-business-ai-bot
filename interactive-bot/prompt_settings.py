"""运行时系统提示词配置 — 支持热更新（Hot Reload）。

关键设计：
  get_system_prompt() 在每次 LLM 调用前执行。
  必须绕过 SQLAlchemy ORM Session 缓存和连接池隐式事务，
  直接通过 engine.connect() + 原生 SQL 读取，确保拿到最新提交值。
"""
import os
import logging
from sqlalchemy import text
from db.database import SessionMaker, engine
from db.model import AppSetting

logger = logging.getLogger(__name__)

SETTING_SYSTEM_PROMPT = "system_prompt"
_PROMPT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "sales_system_prompt.txt",
)


def _session():
    return SessionMaker()


def get_system_prompt() -> str:
    """从数据库实时获取系统提示词 — 每次调用都保证读到最新值。

    使用 engine.connect() + 原生 SQL，绕过 ORM Session 的
    identity-map 缓存和连接池复用导致的隐式事务快照问题。
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM app_setting WHERE key = :k"),
                {"k": SETTING_SYSTEM_PROMPT},
            ).fetchone()
            if row and row[0] and str(row[0]).strip():
                logger.debug("System prompt hot-loaded from DB (%d chars)", len(row[0]))
                return row[0]
    except Exception as e:
        logger.error(f"从数据库读取系统提示词失败: {e}")

    # ── 数据库中没有：从文件迁移一次 ──
    fallback_prompt = "你是白猫工作室的 AI 客服管家/主理人。"
    try:
        with open(_PROMPT_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                fallback_prompt = content
    except Exception as e:
        logger.warning(f"无法读取系统提示词文件: {e}")

    # 写入数据库
    set_system_prompt(fallback_prompt)
    logger.info("System prompt migrated from file to DB (%d chars)", len(fallback_prompt))
    return fallback_prompt


def set_system_prompt(content: str) -> None:
    """将新的提示词保存到数据库（立即生效，下次 get 即可读到）。"""
    db = _session()
    try:
        setting = db.query(AppSetting).filter(
            AppSetting.key == SETTING_SYSTEM_PROMPT
        ).first()
        if setting:
            setting.value = content
        else:
            setting = AppSetting(key=SETTING_SYSTEM_PROMPT, value=content)
            db.add(setting)
        db.commit()
        logger.info("System prompt saved to DB (%d chars)", len(content))
    finally:
        db.close()

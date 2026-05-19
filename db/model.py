from sqlalchemy import Boolean, Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from .database import Base


class MediaGroupMesssage(Base):
    __tablename__ = "media_group_message"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer)
    message_id = Column(Integer)
    media_group_id = Column(Integer)
    is_header = Column(Boolean)
    caption_html = Column(String(1024 * 64))


class FormnStatus(Base):
    __tablename__ = "formn_status"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer)
    message_thread_id = Column(Integer)
    status = Column(String(64))


class MessageMap(Base):
    __tablename__ = "message_map"
    id = Column(Integer, primary_key=True, index=True)
    user_chat_message_id = Column(Integer)
    group_chat_message_id = Column(Integer)
    user_id = Column(Integer)


class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, index=True)
    first_name = Column(String(64))
    last_name = Column(String(64))
    username = Column(String(64))
    is_premium = Column(Boolean)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    message_thread_id = Column(Integer, default=0)
    # ═══ 新增字段：AI/人工模式状态 ═══
    chat_mode = Column(String(16), default="AI_MODE")  # AI_MODE | HUMAN_MODE


class AppSetting(Base):
    """运行时配置项，用于管理后台保存欢迎语、按钮等动态设置。"""

    __tablename__ = "app_setting"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(128), unique=True, index=True, nullable=False)
    value = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class BusinessContact(Base):
    """Telegram Business 接管私聊联系人，用于通过 business_connection_id 群发。"""

    __tablename__ = "business_contact"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, index=True)
    chat_id = Column(Integer, index=True)
    business_connection_id = Column(String(256), index=True)
    first_name = Column(String(64))
    last_name = Column(String(64))
    username = Column(String(64))
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), default=func.now())

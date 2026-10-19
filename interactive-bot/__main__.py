# -*- coding: utf-8 -*-
"""
White Cat Studio - Web3 Agentic Customer Service Engine
Based on python-telegram-bot v21.x
"""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    PicklePersistence,
    TypeHandler,
    filters,
)
from telegram.ext.filters import MessageFilter
from telegram.request import HTTPXRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db.database import engine
from db.model import Base

from . import app_name, bot_token, logger
from .global_settings import get_admin_group_id

# Create tables
Base.metadata.create_all(bind=engine)
from db.migrate import ensure_columns
ensure_columns(engine)
try:                                           # 首次启动:把旧配置(业务线/商品/提示词/全局参数/管理员)导入为配置版本 1,幂等
    from .config import store as _config_store
    _config_store.bootstrap()
except Exception:                              # 配置存储故障不能阻止机器人启动:读取侧会整体回退到默认值
    logger.exception("config bootstrap failed; running with defaults")

# Import all handlers
from .handlers import aftercare_handlers, commerce_handlers, gateway_handlers
from .frontdesk import aftercare as aftercare_jobs
from .commerce.gmpay_monitor import poll_gmpay
from .commerce.ton_monitor import poll_ton
from .outbox import drain as drain_outbox
from .admin import console as admin_console, sandbox, settings_center
from .experience import drafts as drafts_mod, probe as probe_mod
from .handlers.user_handlers import forwarding_message_u2a
from .handlers.admin_handlers import forwarding_message_a2u
from .handlers.callback_handlers import (
    callback_call_human,
    callback_restore_ai,
    callback_continue_ai,
    callback_admin_takeover,
    callback_admin_restore,
    callback_admin_panel,
    callback_query_vcode,
    callback_lead_won,
    callback_frontdesk,
    callback_fd,
    callback_set_lang,
)
from .handlers.broadcast_handlers import (
    callback_broadcast,
    ensure_broadcast_tables,
    handle_admin_broadcast_input,
)
from .handlers.start_config_handlers import (
    callback_start_config,
    handle_admin_start_config_input,
)
from .handlers.ai_config_handlers import (
    callback_ai_config,
    handle_admin_ai_config_input,
)
from .handlers.prompt_config_handlers import (
    callback_prompt_config,
    handle_admin_prompt_config_input,
)
from .handlers.global_config_handlers import (
    callback_global_config,
    handle_admin_global_config_input,
)
from .handlers.group_handlers import handle_group_mention
from .handlers.command_handlers import (
    start, admin_panel, clear, broadcast, broadcast_all,
    status, addadmin, removeadmin,
    set_admin_group, get_group_id, cmd_order,
)
from .handlers.business_handlers import handle_business_message, handle_edited_business_message
from .start_settings import ensure_settings_table
from .ai_settings import ensure_ai_settings_table
from .rag.scraper import run_scraper
from .timeout_tracker import check_timeout_care

ensure_settings_table()
ensure_ai_settings_table()
ensure_broadcast_tables()


class AdminGroupFilter(MessageFilter):
    """动态管理群消息过滤器，保证群组变更后无需重启即刻生效"""
    def filter(self, message):
        return message.chat_id == get_admin_group_id()


admin_group_filter = AdminGroupFilter()


async def error_handler(update, context):
    cb = getattr(getattr(update, "callback_query", None), "data", None) if update else None
    logger.error(f"Exception while handling an update: {type(context.error).__name__}: {context.error} | callback={cb}")
    logger.debug("Exception detail:", exc_info=context.error)


async def refresh_shop_data():
    """定时刷新发卡网/落地页数据，供 RAG 实时引用。"""
    try:
        result = await run_scraper()
        logger.info(
            "Shop data refreshed: %s products, %s services",
            len(result.get("quarantine", {}).get("scraped_products", [])),
            len(result.get("quarantine", {}).get("scraped_services", [])),
        )
    except Exception as e:
        logger.error(f"Shop data refresh failed: {e}", exc_info=True)


if __name__ == "__main__":
    from . import branding
    logger.info(branding.banner())
    logger.info("Starting bot...")

    from .net import resolve_telegram_proxy
    proxy_url, proxy_source = resolve_telegram_proxy()
    logger.info("Telegram proxy: %s (source: %s)", proxy_url or "none", proxy_source)
    req_kwargs = {"proxy": proxy_url} if proxy_url else {}

    from .ui import enrich
    enrich.install()
    try:                                      # 角色表里的管理员同步进 admin_user_ids(旧代码里直接判断它)
        from .admin import permissions as _permissions
        _permissions.sync_admin_ids()
    except Exception as _exc:
        logger.warning("sync_admin_ids failed: %s", type(_exc).__name__)                      # 全局高级表情 + 按钮颜色(含旧后台面板),被拒绝时自动降级

    pickle_persistence = PicklePersistence(filepath=f"./assets/{app_name}.pickle")
    application = (
        ApplicationBuilder()
        .token(bot_token)
        .request(HTTPXRequest(connection_pool_size=32, pool_timeout=10.0, connect_timeout=10.0, read_timeout=30.0, write_timeout=30.0, **req_kwargs))
        .get_updates_request(HTTPXRequest(connection_pool_size=8, pool_timeout=10.0, connect_timeout=10.0, read_timeout=25.0, write_timeout=25.0, **req_kwargs))
        .persistence(persistence=pickle_persistence)
        .build()
    )

    # === Command handlers ===
    application.add_handler(
        CommandHandler("start", start, filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler("admin", admin_panel, filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE)
    )
    application.add_handler(CommandHandler("about", admin_console.cmd_about, filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("export", admin_console.cmd_export, filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE))
    application.add_handler(
        CommandHandler("asclient", sandbox.cmd_asclient, filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE)
    )
    application.add_handler(TypeHandler(Update, drafts_mod.on_stop_generation), group=-9)
    application.add_handler(CommandHandler("probe", probe_mod.cmd_probe, filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("cfg", settings_center.cmd_cfg, filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("cancel", settings_center.cmd_cancel, filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("config", settings_center.cmd_config, filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE))
    application.add_handler(CallbackQueryHandler(settings_center.callback_cfg, pattern=r"^cfgx?:"))
    application.add_handler(CallbackQueryHandler(settings_center.callback_preview, pattern=r"^pv:"))
    application.add_handler(MessageHandler(filters.Document.FileExtension("json") & filters.ChatType.PRIVATE, settings_center.handle_config_document), group=-8)
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, settings_center.handle_cfg_text), group=-8)
    application.add_handler(
        CommandHandler("order", cmd_order, filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler("clear", clear, admin_group_filter)
    )
    application.add_handler(
        CommandHandler("broadcast", broadcast, admin_group_filter)
    )
    application.add_handler(
        CommandHandler("broadcast_all", broadcast_all)
    )
    application.add_handler(
        CommandHandler("status", status)
    )
    application.add_handler(
        CommandHandler("addadmin", addadmin)
    )
    application.add_handler(
        CommandHandler("removeadmin", removeadmin)
    )
    # 管理群一键绑定与群ID查询指令
    application.add_handler(
        CommandHandler("set_admin_group", set_admin_group)
    )
    application.add_handler(
        CommandHandler(["group_id", "id"], get_group_id)
    )
    # === 管理控制台 ===
    application.add_handler(CallbackQueryHandler(admin_console.callback_admin, pattern=r"^adm:"))
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, admin_console.admin_text_input), group=-7)

    # === 成交与支付(必须先于通用消息处理器)===
    application.add_handler(CommandHandler("setcatalog", commerce_handlers.cmd_setcatalog))
    application.add_handler(CommandHandler("setton", commerce_handlers.cmd_setton))
    application.add_handler(CommandHandler("quote", commerce_handlers.cmd_quote))
    application.add_handler(CommandHandler("setprice", commerce_handlers.cmd_setprice))
    application.add_handler(CommandHandler("resetprompt", commerce_handlers.cmd_resetprompt))
    application.add_handler(CommandHandler("setlines", commerce_handlers.cmd_setlines))
    application.add_handler(PreCheckoutQueryHandler(commerce_handlers.pre_checkout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, commerce_handlers.successful_payment))
    application.add_handler(CallbackQueryHandler(aftercare_handlers.callback_aft, pattern=r"^aft:"))
    application.add_handler(CommandHandler("qcustom", gateway_handlers.cmd_qcustom))
    application.add_handler(CallbackQueryHandler(gateway_handlers.callback_quote_confirm, pattern=r"^qc:"))
    application.add_handler(CallbackQueryHandler(gateway_handlers.callback_gateway, pattern=r"^pay:(gm|txid|help|switch):"))
    application.add_handler(CallbackQueryHandler(commerce_handlers.callback_pay, pattern=r"^pay:"))
    application.add_handler(CallbackQueryHandler(commerce_handlers.callback_order_admin, pattern=r"^ord:"))

    # === Callback handlers ===
    application.add_handler(CallbackQueryHandler(callback_set_lang, pattern=r"^(set_lang|lang):"))
    application.add_handler(
        CallbackQueryHandler(callback_frontdesk, pattern=r"^frontdesk_(intent|ans|skip|leave):")
    )
    application.add_handler(CallbackQueryHandler(callback_fd, pattern=r"^fd:"))
    application.add_handler(
        CallbackQueryHandler(callback_call_human, pattern="^call_human_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_restore_ai, pattern="^restore_ai_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_continue_ai, pattern="^continue_ai_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_admin_takeover, pattern="^admin_takeover_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_admin_restore, pattern="^admin_restore_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_admin_panel, pattern="^panel_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_start_config, pattern="^startcfg_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_ai_config, pattern="^aicfg_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_broadcast, pattern="^broadcast_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_query_vcode, pattern="^vcode_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_prompt_config, pattern="^promptcfg_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_global_config, pattern="^globalcfg_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_lead_won, pattern="^lead_won_")
    )

    # === Admin runtime config input ===
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_admin_broadcast_input,
        ),
        group=-1,
    )

    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_admin_ai_config_input,
        ),
        group=-5,
    )

    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_admin_start_config_input,
        ),
        group=-4,
    )

    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_admin_prompt_config_input,
        ),
        group=-3,
    )

    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_admin_global_config_input,
        ),
        group=-2,
    )

    # === Business message handler ===
    try:
        application.add_handler(TypeHandler(Update, handle_edited_business_message), group=-10)
        application.add_handler(
            MessageHandler(
                filters.UpdateType.BUSINESS_MESSAGE & ~filters.COMMAND,
                handle_business_message,
            )
        )
        logger.info("TG Business handler enabled")
    except Exception as e:
        logger.warning(f"TG Business handler not available (need PTB 21.2+): {e}")

    # === Group @mention handler ===
    application.add_handler(
        MessageHandler(
            ~filters.COMMAND
            & ~filters.ChatType.PRIVATE
            & ~admin_group_filter,
            handle_group_mention,
        )
    )

    # === Admin group handler (Admin -> User) ===
    application.add_handler(
        MessageHandler(
            ~filters.COMMAND & admin_group_filter,
            forwarding_message_a2u,
        )
    )

    # === Private chat handler (User -> AI/Admin) ===
    application.add_handler(
        MessageHandler(
            ~filters.COMMAND & filters.ChatType.PRIVATE,
            forwarding_message_u2a,
        )
    )

    # === Error handler ===
    application.add_error_handler(error_handler)

    # === Scheduler ===
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # 10分钟未回复超时关怀检测巡检（每30秒检查一次）
    async def run_timeout_care_job():
        try:
            await check_timeout_care(application.bot)
        except Exception as e:
            logger.error(f"Timeout care check job error: {e}")

    scheduler.add_job(run_timeout_care_job, "interval", seconds=30, id="timeout_care_job")

    async def run_ton_poll_job():
        try:
            await poll_ton(application.bot)
        except Exception as e:
            logger.error(f"TON poll job error: {type(e).__name__}")

    async def run_checkin_job():
        try:
            await aftercare_jobs.checkin_job(application.bot)
        except Exception as e:
            logger.error(f"Checkin job error: {type(e).__name__}")

    async def run_renew_job():
        try:
            await aftercare_jobs.renew_job(application.bot)
        except Exception as e:
            logger.error(f"Renew job error: {type(e).__name__}")

    scheduler.add_job(run_checkin_job, "interval", minutes=30, id="checkin_job", max_instances=1, coalesce=True)
    scheduler.add_job(run_renew_job, "cron", hour=10, minute=0, id="renew_job", coalesce=True)

    async def run_gmpay_poll_job():
        try:
            await poll_gmpay(application.bot)
        except Exception as e:
            logger.error(f"GMPay poll job error: {type(e).__name__}")

    scheduler.add_job(run_gmpay_poll_job, "interval", seconds=20, id="gmpay_poll_job", max_instances=1, coalesce=True)
    scheduler.add_job(run_ton_poll_job, "interval", seconds=20, id="ton_poll_job", max_instances=1, coalesce=True)

    async def run_outbox_job():
        try:
            await drain_outbox(application.bot)
        except Exception as e:
            logger.error(f"Outbox drain error: {type(e).__name__}")

    scheduler.add_job(run_outbox_job, "interval", seconds=30, id="outbox_job", max_instances=1, coalesce=True)

    async def run_sla_job():
        try:
            await admin_console.sla_check(application.bot)
        except Exception as e:
            logger.error(f"SLA job error: {type(e).__name__}")

    async def run_digest_job():
        try:
            await admin_console.daily_digest(application.bot)
        except Exception as e:
            logger.error(f"Digest job error: {type(e).__name__}")

    async def run_intake_abandon_job():
        try:
            from .analytics.jobs import intake_abandon_job
            intake_abandon_job()
        except Exception as e:
            logger.error(f"Intake abandon job error: {type(e).__name__}")

    scheduler.add_job(run_sla_job, "interval", minutes=2, id="sla_job", max_instances=1, coalesce=True)
    async def run_memory_purge_job():
        try:
            from .config import cfg
            from .memory.conversation_store import ConversationStore
            ConversationStore().purge_older_than(int(cfg.get("memory.retention_days", default=90)))
        except Exception as e:
            logger.error(f"Memory purge job error: {type(e).__name__}")

    async def run_unpin_job():
        try:
            from .experience import pins
            await pins.unpin_job(application.bot)
        except Exception as e:
            logger.error(f"Unpin job error: {type(e).__name__}")

    scheduler.add_job(run_unpin_job, "interval", minutes=5, id="unpin_job", max_instances=1, coalesce=True)
    scheduler.add_job(run_memory_purge_job, "cron", hour=3, minute=30, id="memory_purge_job", coalesce=True)
    scheduler.add_job(run_intake_abandon_job, "interval", minutes=10, id="intake_abandon_job", max_instances=1, coalesce=True)
    scheduler.add_job(run_digest_job, "cron", hour=9, minute=0, id="digest_job", coalesce=True)

    logger.info("Bot started, polling...")
    scheduler.start()
    application.run_polling(allowed_updates=list(Update.ALL_TYPES) + ["stopped_message_generation"], timeout=15)


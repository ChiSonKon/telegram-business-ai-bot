"""
White Cat Studio - Web3 Agentic Customer Service Engine
Based on python-telegram-bot v21.x
"""
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PicklePersistence,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db.database import engine
from db.model import Base

from . import app_name, bot_token, admin_group_id, logger

# Create tables
Base.metadata.create_all(bind=engine)

# Import all handlers
from .handlers.user_handlers import forwarding_message_u2a
from .handlers.admin_handlers import forwarding_message_a2u
from .handlers.callback_handlers import (
    callback_call_human,
    callback_restore_ai,
    callback_admin_takeover,
    callback_admin_restore,
    callback_admin_panel,
    callback_query_vcode,
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
)
from .handlers.business_handlers import handle_business_message
from .start_settings import ensure_settings_table
from .ai_settings import ensure_ai_settings_table
from .rag.scraper import run_scraper

ensure_settings_table()
ensure_ai_settings_table()
ensure_broadcast_tables()


async def error_handler(update, context):
    logger.error(f"Exception while handling an update: {context.error}")
    logger.debug("Exception detail:", exc_info=context.error)


async def refresh_shop_data():
    """定时刷新发卡网/落地页数据，供 RAG 实时引用。"""
    try:
        result = await run_scraper()
        logger.info(
            "Shop data refreshed: %s products, %s services",
            len(result.get("scraped_products", [])),
            len(result.get("scraped_services", [])),
        )
    except Exception as e:
        logger.error(f"Shop data refresh failed: {e}", exc_info=True)


if __name__ == "__main__":
    logger.info("Starting bot...")

    pickle_persistence = PicklePersistence(filepath=f"./assets/{app_name}.pickle")
    application = (
        ApplicationBuilder()
        .token(bot_token)
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
    application.add_handler(
        CommandHandler("clear", clear, filters.Chat([admin_group_id]))
    )
    application.add_handler(
        CommandHandler("broadcast", broadcast, filters.Chat([admin_group_id]))
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

    # === Callback handlers ===
    application.add_handler(
        CallbackQueryHandler(callback_call_human, pattern="^call_human_")
    )
    application.add_handler(
        CallbackQueryHandler(callback_restore_ai, pattern="^restore_ai_")
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
            & filters.ChatType.GROUPS
            & ~filters.Chat([admin_group_id]),
            handle_group_mention,
        )
    )

    # === Admin group handler (Admin -> User) ===
    application.add_handler(
        MessageHandler(
            ~filters.COMMAND & filters.Chat([admin_group_id]),
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

    logger.info("Bot started, polling...")
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(refresh_shop_data, "interval", minutes=10, id="refresh_shop_data")
    scheduler.start()
    application.run_polling()

"""轻量级增量迁移:只做'补列',幂等,可重复执行。新表由 create_all 负责。"""
from sqlalchemy import inspect, text

# (表, 列, DDL 类型)
ADD_COLUMNS = [
    ("outbox", "case_uid", "INTEGER"),
    ("frontdesk_case", "handoff_at", "DATETIME"),
    ("frontdesk_case", "brief_json", "TEXT"),
    ("frontdesk_case", "source", "VARCHAR(64)"),
    ("frontdesk_case", "priority", "VARCHAR(4)"),
    ("frontdesk_case", "intake_flow", "VARCHAR(48)"),
    ("frontdesk_case", "last_customer_at", "DATETIME"),
    ("frontdesk_case", "last_status_card_at", "DATETIME"),
    ("frontdesk_case", "test", "BOOLEAN"),
    ("ai_usage_ledger", "estimated", "BOOLEAN DEFAULT 0"),
    ("commerce_order", "currency", "VARCHAR(16)"),
    ("commerce_order", "network", "VARCHAR(16)"),
    ("commerce_order", "amount_str", "VARCHAR(32)"),
    ("commerce_order", "base_amount_str", "VARCHAR(32)"),
    ("commerce_order", "pay_address", "VARCHAR(128)"),
    ("commerce_order", "gm_trade_id", "VARCHAR(64)"),
    ("commerce_order", "pay_token", "VARCHAR(16)"),
    ("commerce_order", "pay_url", "VARCHAR(512)"),
    ("commerce_order", "paid_txid", "VARCHAR(128)"),
    ("commerce_order", "detail", "TEXT"),
    ("commerce_order", "next_step", "TEXT"),
    ("commerce_order", "paid_at", "DATETIME"),
    ("commerce_order", "delivered_at", "DATETIME"),
    ("commerce_order", "expires_on", "DATETIME"),
    ("commerce_order", "source_case_id", "INTEGER"),
    ("commerce_order", "created_by", "INTEGER"),
    ("commerce_order", "test", "BOOLEAN"),
    ("admin_audit", "before_json", "TEXT"),
    ("admin_audit", "after_json", "TEXT"),
]


def ensure_columns(engine) -> list[str]:
    added = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, col, ddl in ADD_COLUMNS:
            if table in insp.get_table_names() and col not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {ddl}'))
                added.append(f"{table}.{col}")
    return added

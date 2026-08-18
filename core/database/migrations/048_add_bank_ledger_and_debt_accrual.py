"""
迁移048：银行流水账、预约过期回收与欠税滞纳字段
"""

import sqlite3


def _column_exists(cursor: sqlite3.Cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def up(cursor: sqlite3.Cursor):
    """建立银行资金流水表，并为预约过期与欠税滞纳补字段。"""
    # 1) 银行资金流水：所有导致银行/钱包余额变动的动作都要留痕，
    #    包括凭空产生的定期利息与被销毁的手续费、违约金。
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bank_transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            tx_type TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            wallet_delta INTEGER NOT NULL DEFAULT 0,
            bank_delta INTEGER NOT NULL DEFAULT 0,
            wallet_after INTEGER NOT NULL DEFAULT 0,
            bank_after INTEGER NOT NULL DEFAULT 0,
            locked_after INTEGER NOT NULL DEFAULT 0,
            ref_id INTEGER,
            remark TEXT,
            created_at DATETIME NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bank_transactions_user_time
        ON bank_transactions(user_id, created_at)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bank_transactions_type_time
        ON bank_transactions(tx_type, created_at)
    """)

    # 2) 预约取款需要过期时间，否则一笔永不确认的预约会把资金永久锁死，
    #    既取不出也扣不到税。历史数据按 ready_at 兜底填充。
    if not _column_exists(cursor, "bank_withdraw_reservations", "expires_at"):
        cursor.execute("""
            ALTER TABLE bank_withdraw_reservations
            ADD COLUMN expires_at DATETIME
        """)
        cursor.execute("""
            UPDATE bank_withdraw_reservations
            SET expires_at = datetime(ready_at, '+72 hours')
            WHERE expires_at IS NULL
        """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_bank_reservations_expires_at
        ON bank_withdraw_reservations(status, expires_at)
    """)

    # 3) 欠税滞纳金按天累计，需要记录最后一次累计日期避免同日重复计息。
    if not _column_exists(cursor, "tax_debts", "last_accrued_date"):
        cursor.execute("""
            ALTER TABLE tax_debts
            ADD COLUMN last_accrued_date TEXT
        """)


def down(cursor: sqlite3.Cursor):
    """SQLite 不安全移除列，仅回滚新表和索引。"""
    cursor.execute("DROP INDEX IF EXISTS idx_bank_reservations_expires_at")
    cursor.execute("DROP INDEX IF EXISTS idx_bank_transactions_type_time")
    cursor.execute("DROP INDEX IF EXISTS idx_bank_transactions_user_time")
    cursor.execute("DROP TABLE IF EXISTS bank_transactions")

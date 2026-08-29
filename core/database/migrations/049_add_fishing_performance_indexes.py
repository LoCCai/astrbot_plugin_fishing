"""
迁移049：钓鱼记录过期清理与自动钓鱼轮询的热点索引
"""

import sqlite3


def up(cursor: sqlite3.Cursor):
    """为钓鱼记录过期清理与自动钓鱼轮询补齐索引。

    钓鱼记录的 30 天过期清理（cleanup_old_fishing_records）按纯 timestamp
    范围过滤，已有的 (user_id, timestamp) 复合索引最左前缀是 user_id，
    无法服务该查询；自动钓鱼线程每 40 秒按 auto_fishing_enabled = 1
    过滤用户，此前同样没有可用索引。
    """
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_fishing_records_timestamp ON fishing_records(timestamp)"
    )
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_auto_fishing
        ON users(auto_fishing_enabled) WHERE auto_fishing_enabled = 1
    """)


def down(cursor: sqlite3.Cursor):
    """SQLite 不安全移除列，仅回滚新增索引。"""
    cursor.execute("DROP INDEX IF EXISTS idx_users_auto_fishing")
    cursor.execute("DROP INDEX IF EXISTS idx_fishing_records_timestamp")

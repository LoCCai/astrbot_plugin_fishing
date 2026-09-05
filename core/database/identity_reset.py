"""自增序列重置的双方言封装。

SQLite 的自增计数器记录在内部表 sqlite_sequence；PostgreSQL 的
identity 列通过 ALTER COLUMN ... RESTART WITH 1 重置。调用方统一
使用 delete_resetting_identity，由各后端游标负责吸收方言差异
（PG 适配游标把该语句改写为 RESTART，未注册的表则整体跳过）。
"""

import sqlite3


def delete_resetting_identity(cursor: sqlite3.Cursor, table: str) -> None:
    """清空指定表的数据并重置其自增计数（仅 SQLite 语义，PG 由游标翻译）。"""
    cursor.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))

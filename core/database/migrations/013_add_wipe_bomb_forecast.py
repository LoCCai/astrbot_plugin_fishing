import sqlite3
from astrbot.api import logger

def up(cursor: sqlite3.Cursor):
    """
    Adds the wipe_bomb_forecast column to the users table.
    """
    logger.debug("Running migration 013: adding wipe_bomb_forecast to users table")
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN wipe_bomb_forecast TEXT")
        logger.info("Migration 013 applied successfully.")
    except sqlite3.OperationalError as e:
        # Avoid crashing if the column already exists
        if "duplicate column name" in str(e):
            logger.warning("Column 'wipe_bomb_forecast' already exists in 'users'. Skipping.")
        else:
            raise

def down(cursor: sqlite3.Cursor):
    """
    Removes the wipe_bomb_forecast column from the users table.
    This is a simplified downgrade and may not be suitable for production with large tables.
    """
    logger.debug("Running migration 013 downgrade: removing wipe_bomb_forecast from users table")
    try:
        # Check if the column exists before attempting a complex downgrade
        cursor.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'wipe_bomb_forecast' not in columns:
            logger.warning("Column 'wipe_bomb_forecast' does not exist in 'users'. Skipping downgrade.")
            return

        # Proceed with the downgrade
        # SQLite ≥ 3.35 支持直接 DROP COLUMN（原重建表方式会丢失表约束，
        # 且 down 流程不被迁移框架调用，仅作维护用途）
        cursor.execute("PRAGMA foreign_keys=off;")
        cursor.execute("BEGIN TRANSACTION;")

        cursor.execute("ALTER TABLE users DROP COLUMN wipe_bomb_forecast")

        cursor.execute("COMMIT;")
        cursor.execute("PRAGMA foreign_keys=on;")
        
        logger.info("Migration 013 downgrade completed successfully.")
    except Exception as e:
        cursor.execute("ROLLBACK;")
        logger.error(f"Failed to downgrade migration 013: {e}")
        raise

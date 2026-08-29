import sqlite3
from astrbot.api import logger

def up(cursor: sqlite3.Cursor):
    """
    应用此迁移：为 users 表添加命运之轮功能所需的所有字段。
    """
    logger.debug("正在执行 031_add_wheel_of_fate_fields: 为 users 表添加命运之轮字段...")

    try:
        # 检查现有列，避免重复添加
        cursor.execute("PRAGMA table_info(users)")
        columns = [info[1] for info in cursor.fetchall()]

        # 逐一检查并添加每一个缺失的字段（列名均为脚本内固定值）
        if "in_wheel_of_fate" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN in_wheel_of_fate BOOLEAN")
            logger.info("成功为 users 表添加 'in_wheel_of_fate' 字段。")
        if "wof_current_level" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN wof_current_level INTEGER")
            logger.info("成功为 users 表添加 'wof_current_level' 字段。")
        if "wof_current_prize" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN wof_current_prize INTEGER")
            logger.info("成功为 users 表添加 'wof_current_prize' 字段。")
        if "wof_entry_fee" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN wof_entry_fee INTEGER")
            logger.info("成功为 users 表添加 'wof_entry_fee' 字段。")
        if "last_wof_play_time" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN last_wof_play_time DATETIME")
            logger.info("成功为 users 表添加 'last_wof_play_time' 字段。")
        if "wof_last_action_time" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN wof_last_action_time DATETIME")
            logger.info("成功为 users 表添加 'wof_last_action_time' 字段。")

    except sqlite3.Error as e:
        logger.error(f"在迁移 031_add_wheel_of_fate_fields 期间发生错误: {e}")
        raise
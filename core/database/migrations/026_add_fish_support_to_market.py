import sqlite3


def get_default_value(column_name):
    """获取字段的默认值"""
    defaults = {
        'refine_level': '1',
        'seller_nickname': "''",
        'item_name': "''",
        'item_description': "''"
    }
    return defaults.get(column_name, 'NULL')


def up(cursor: sqlite3.Cursor):
    """
    为market表添加对fish类型的支持
    """
    from astrbot.api import logger
    logger.info("正在执行 026_add_fish_support_to_market: 更新market表约束以支持鱼类类型...")
    
    # SQLite不支持直接修改CHECK约束，需要重建表
    # 1. 创建新的market表结构
    cursor.execute("""
        CREATE TABLE market_new (
            market_id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id TEXT NOT NULL,
            item_type TEXT NOT NULL CHECK (item_type IN ('rod', 'accessory', 'item', 'fish')),
            item_id INTEGER NOT NULL, 
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            price INTEGER NOT NULL CHECK (price > 0),
            listed_at DATETIME DEFAULT CURRENT_TIMESTAMP, 
            expires_at DATETIME,
            refine_level INTEGER DEFAULT 1,
            seller_nickname TEXT,
            item_name TEXT,
            item_description TEXT,
            item_instance_id INTEGER,
            is_anonymous INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    
    # 2. 复制数据。执行到此处时 market 已含 025 及之前引入的全部列
    #    （item_instance_id、is_anonymous 已存在），直接按列名复制。
    cursor.execute("""
        INSERT INTO market_new (
            market_id, user_id, item_type, item_id, quantity, price,
            listed_at, expires_at, refine_level, seller_nickname,
            item_name, item_description, item_instance_id, is_anonymous
        )
        SELECT market_id, user_id, item_type, item_id, quantity, price,
               listed_at, expires_at, COALESCE(refine_level, 1),
               COALESCE(seller_nickname, ''), COALESCE(item_name, ''),
               COALESCE(item_description, ''), item_instance_id,
               COALESCE(is_anonymous, 0)
        FROM market
    """)
    
    # 3. 删除旧表
    cursor.execute("DROP TABLE market")
    
    # 4. 重命名新表
    cursor.execute("ALTER TABLE market_new RENAME TO market")
    
    # 5. 重新创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_user_id ON market(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_item_type ON market(item_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_listed_at ON market(listed_at)")
    
    cursor.connection.commit()
    logger.info("market表约束更新完成，现在支持rod、accessory、item和fish类型")


def down(cursor: sqlite3.Cursor):
    """
    回滚：移除对fish类型的支持
    """
    logger.info("正在回滚 026_add_fish_support_to_market: 移除fish类型支持...")
    
    # 1. 创建回滚的market表结构（只支持rod、accessory和item）
    cursor.execute("""
        CREATE TABLE market_rollback (
            market_id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id TEXT NOT NULL,
            item_type TEXT NOT NULL CHECK (item_type IN ('rod', 'accessory', 'item')),
            item_id INTEGER NOT NULL, 
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            price INTEGER NOT NULL CHECK (price > 0),
            listed_at DATETIME DEFAULT CURRENT_TIMESTAMP, 
            expires_at DATETIME,
            refine_level INTEGER DEFAULT 1,
            seller_nickname TEXT,
            item_name TEXT,
            item_description TEXT,
            item_instance_id INTEGER,
            is_anonymous INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    
    # 2. 复制 rod、accessory 和 item 类型的数据（此时 market 已含全部列）
    cursor.execute("""
        INSERT INTO market_rollback (
            market_id, user_id, item_type, item_id, quantity, price,
            listed_at, expires_at, refine_level, seller_nickname,
            item_name, item_description, item_instance_id, is_anonymous
        )
        SELECT market_id, user_id, item_type, item_id, quantity, price,
               listed_at, expires_at, COALESCE(refine_level, 1),
               COALESCE(seller_nickname, ''), COALESCE(item_name, ''),
               COALESCE(item_description, ''), item_instance_id,
               COALESCE(is_anonymous, 0)
        FROM market
        WHERE item_type IN ('rod', 'accessory', 'item')
    """)
    
    # 3. 删除当前表
    cursor.execute("DROP TABLE market")
    
    # 4. 重命名回滚表
    cursor.execute("ALTER TABLE market_rollback RENAME TO market")
    
    # 5. 重新创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_user_id ON market(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_item_type ON market(item_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_listed_at ON market(listed_at)")
    
    cursor.connection.commit()
    logger.info("market表约束回滚完成，现在只支持rod、accessory和item类型")

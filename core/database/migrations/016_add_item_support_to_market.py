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
    为market表添加对item类型的支持
    """
    from astrbot.api import logger
    logger.info("正在执行 016_add_item_support_to_market: 更新market表约束以支持道具类型...")
    
    # SQLite不支持直接修改CHECK约束，需要重建表
    # 1. 创建新的market表结构
    cursor.execute("""
        CREATE TABLE market_new (
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
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    
    # 2. 复制数据。迁移按版本严格顺序执行，执行到此处时 market 表必然是
    #    001 建立的 8 列结构，refine_level 等列在本迁移中才引入，
    #    直接用字面量默认值补齐，不按运行时表结构动态拼 SQL。
    cursor.execute("""
        INSERT INTO market_new (
            market_id, user_id, item_type, item_id, quantity, price,
            listed_at, expires_at, refine_level, seller_nickname,
            item_name, item_description
        )
        SELECT market_id, user_id, item_type, item_id, quantity, price,
               listed_at, expires_at, 1, '', '', ''
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
    logger.info("market表约束更新完成，现在支持rod、accessory和item类型")


def down(cursor: sqlite3.Cursor):
    """
    回滚：移除对item类型的支持
    """
    logger.info("正在回滚 016_add_item_support_to_market: 移除item类型支持...")
    
    # 1. 创建回滚的market表结构（只支持rod和accessory）
    cursor.execute("""
        CREATE TABLE market_rollback (
            market_id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id TEXT NOT NULL,
            item_type TEXT NOT NULL CHECK (item_type IN ('rod', 'accessory')),
            item_id INTEGER NOT NULL, 
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            price INTEGER NOT NULL CHECK (price > 0),
            listed_at DATETIME DEFAULT CURRENT_TIMESTAMP, 
            expires_at DATETIME,
            refine_level INTEGER DEFAULT 1,
            seller_nickname TEXT,
            item_name TEXT,
            item_description TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    
    # 2. 复制 rod 和 accessory 类型的数据（此时 market 已含本迁移引入的全部列）
    cursor.execute("""
        INSERT INTO market_rollback (
            market_id, user_id, item_type, item_id, quantity, price,
            listed_at, expires_at, refine_level, seller_nickname,
            item_name, item_description
        )
        SELECT market_id, user_id, item_type, item_id, quantity, price,
               listed_at, expires_at, COALESCE(refine_level, 1),
               COALESCE(seller_nickname, ''), COALESCE(item_name, ''),
               COALESCE(item_description, '')
        FROM market
        WHERE item_type IN ('rod', 'accessory')
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
    logger.info("market表约束回滚完成，现在只支持rod和accessory类型")

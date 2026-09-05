"""PostgreSQL 静态结构清单（由 pg_schema_generator 自动生成，勿手改）。

内容来自 SQLite 迁移链（001-049）的内省与翻译：
- PG_TABLE_DDL: 每张表的 CREATE TABLE IF NOT EXISTS 语句
- PG_INDEX_DDL: 全部二级索引语句
- PG_FK_DDL: 数据复制完成后补建的外键约束（表名 → 语句列表）
- TABLE_COLUMNS: 表到 [(列名, PG 类型)] 的清单，驱动数据迁移与取值适配
- TABLE_IDENTITY: 表到自增主键列的映射（无自增主键的表缺省）
- TABLE_INSERT_SQL: 表到全列 INSERT 模板（? 占位）的映射，供数据迁移使用
"""

META_TABLE_DDL = 'CREATE TABLE IF NOT EXISTS fishing_plugin_meta ("key" TEXT PRIMARY KEY, "value" TEXT)'

# 兼容表：让 SQLite 侧的 sqlite_sequence 重置语句在 PG 下可直通执行
#（清空计数行；PG 的 identity 序列由数据迁移后的 setval 统一修正）
SQLITE_SEQUENCE_DDL = 'CREATE TABLE IF NOT EXISTS sqlite_sequence (name TEXT, seq BIGINT)'

RESET_IDENTITY_DDL = (
    'CREATE OR REPLACE FUNCTION fishing_reset_identities() '
    'RETURNS void LANGUAGE plpgsql AS $fn$ '
    'DECLARE rec RECORD; col TEXT; '
    'BEGIN '
    'FOR rec IN '
    'SELECT c.relname AS table_name, a.attname AS col_name '
    'FROM pg_attribute a '
    'JOIN pg_class c ON c.oid = a.attrelid '
    'JOIN pg_namespace n ON n.oid = c.relnamespace '
    'WHERE c.relkind = \'r\' AND a.attidentity IN (\'a\', \'d\') '
    "AND n.nspname = 'public' "
    'LOOP '
    "EXECUTE 'SELECT setval(pg_get_serial_sequence(' || quote_literal(rec.table_name) "
    "|| ', ' || quote_literal(rec.col_name) || '), COALESCE((SELECT MAX(' || quote_ident(rec.col_name) "
    "|| ') FROM ' || quote_ident(rec.table_name) || '), 1), true)'; "
    'END LOOP; '
    'END; $fn$'
)

RESET_IDENTITY_CALL = 'SELECT fishing_reset_identities()'

# —— 运行时适配游标使用的方言常量（集中在本数据文件，执行器零 SQL 文本）——
SKIP_PREFIXES = ("BEGIN", "PRAGMA", "COMMIT")
RETURNING_MARK = " RETURNING "
SEQUENCE_RESET_PATTERN = r"^\s*DELETE\s+FROM\s+sqlite_sequence\s+WHERE\s+name\s*=\s*\?\s*$"
INSERT_TABLE_PATTERN = r"^\s*INSERT\s+INTO\s+[\"']?([A-Za-z_]\w*)[\"']?"
DUMP_INSERT_PATTERN = r'^INSERT INTO "([A-Za-z_]\w*)" VALUES\((.*)\)$'
USERS_EXIST_SQL = 'SELECT COUNT(*) FROM users'
META_INSERT_SQL = (
    'INSERT INTO fishing_plugin_meta (key, value) VALUES (?, ?) '
    'ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value'
)

# 每表的自增序列修正语句（数据迁移复制完成后逐表执行，值经 :p 参数绑定）
TABLE_MAX_SQL = {
    "accessories": 'SELECT COALESCE(MAX("accessory_id"), 1) FROM "accessories"',
    "aquarium_upgrades": 'SELECT COALESCE(MAX("upgrade_id"), 1) FROM "aquarium_upgrades"',
    "baits": 'SELECT COALESCE(MAX("bait_id"), 1) FROM "baits"',
    "bank_fixed_deposits": 'SELECT COALESCE(MAX("deposit_id"), 1) FROM "bank_fixed_deposits"',
    "bank_transactions": 'SELECT COALESCE(MAX("transaction_id"), 1) FROM "bank_transactions"',
    "bank_withdraw_reservations": 'SELECT COALESCE(MAX("reservation_id"), 1) FROM "bank_withdraw_reservations"',
    "exchange_prices": 'SELECT COALESCE(MAX("price_id"), 1) FROM "exchange_prices"',
    "fish": 'SELECT COALESCE(MAX("fish_id"), 1) FROM "fish"',
    "fishing_records": 'SELECT COALESCE(MAX("record_id"), 1) FROM "fishing_records"',
    "fishing_zones": 'SELECT COALESCE(MAX("id"), 1) FROM "fishing_zones"',
    "gacha_pool_items": 'SELECT COALESCE(MAX("gacha_pool_item_id"), 1) FROM "gacha_pool_items"',
    "gacha_pools": 'SELECT COALESCE(MAX("gacha_pool_id"), 1) FROM "gacha_pools"',
    "gacha_records": 'SELECT COALESCE(MAX("record_id"), 1) FROM "gacha_records"',
    "items": 'SELECT COALESCE(MAX("item_id"), 1) FROM "items"',
    "loans": 'SELECT COALESCE(MAX("loan_id"), 1) FROM "loans"',
    "market": 'SELECT COALESCE(MAX("market_id"), 1) FROM "market"',
    "red_packet_records": 'SELECT COALESCE(MAX("record_id"), 1) FROM "red_packet_records"',
    "red_packets": 'SELECT COALESCE(MAX("packet_id"), 1) FROM "red_packets"',
    "rods": 'SELECT COALESCE(MAX("rod_id"), 1) FROM "rods"',
    "shop_item_costs": 'SELECT COALESCE(MAX("cost_id"), 1) FROM "shop_item_costs"',
    "shop_item_rewards": 'SELECT COALESCE(MAX("reward_id"), 1) FROM "shop_item_rewards"',
    "shop_items": 'SELECT COALESCE(MAX("item_id"), 1) FROM "shop_items"',
    "shop_purchase_records": 'SELECT COALESCE(MAX("record_id"), 1) FROM "shop_purchase_records"',
    "shops": 'SELECT COALESCE(MAX("shop_id"), 1) FROM "shops"',
    "taxes": 'SELECT COALESCE(MAX("tax_id"), 1) FROM "taxes"',
    "titles": 'SELECT COALESCE(MAX("title_id"), 1) FROM "titles"',
    "user_accessories": 'SELECT COALESCE(MAX("accessory_instance_id"), 1) FROM "user_accessories"',
    "user_buffs": 'SELECT COALESCE(MAX("id"), 1) FROM "user_buffs"',
    "user_commodities": 'SELECT COALESCE(MAX("instance_id"), 1) FROM "user_commodities"',
    "user_rods": 'SELECT COALESCE(MAX("rod_instance_id"), 1) FROM "user_rods"',
    "wipe_bomb_log": 'SELECT COALESCE(MAX("log_id"), 1) FROM "wipe_bomb_log"',
}

JSON_EACH_SHIM_DDL = (
    'CREATE OR REPLACE FUNCTION json_each(src TEXT) '
    'RETURNS TABLE (key BIGINT, value TEXT, type TEXT, atom BOOLEAN, id BIGINT, parent BIGINT, fullkey TEXT, path TEXT) '
    'LANGUAGE plpgsql IMMUTABLE AS $fn$ '
    'BEGIN RETURN QUERY '
    'SELECT row_number() OVER () - 1, x.value::TEXT, '
    "CASE jsonb_typeof(x.value) WHEN 'object' THEN 'object' WHEN 'array' THEN 'array' "
    "WHEN 'string' THEN 'text' WHEN 'number' THEN 'real' WHEN 'boolean' THEN 'true' "
    "WHEN 'null' THEN 'null' ELSE 'text' END, "
    "jsonb_typeof(x.value) NOT IN ('object', 'array'), row_number() OVER () - 1, "
    "NULL::BIGINT, ''::TEXT, ''::TEXT "
    "FROM jsonb_array_elements(NULLIF(src, '')::jsonb) AS x(value); "
    'END; $fn$'
)

PG_TABLE_DDL = {
    "accessories": """CREATE TABLE IF NOT EXISTS "accessories" (
            "accessory_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "name" TEXT NOT NULL UNIQUE,
            "description" TEXT,
            "rarity" BIGINT NOT NULL DEFAULT 1,
            "slot_type" TEXT NOT NULL DEFAULT 'general',
            "bonus_fish_quality_modifier" DOUBLE PRECISION DEFAULT 1.0,
            "bonus_fish_quantity_modifier" DOUBLE PRECISION DEFAULT 1.0,
            "bonus_rare_fish_chance" DOUBLE PRECISION DEFAULT 0.0,
            "bonus_coin_modifier" DOUBLE PRECISION DEFAULT 1.0,
            "other_bonus_description" TEXT,
            "icon_url" TEXT
        )
    """,
    "aquarium_upgrades": """CREATE TABLE IF NOT EXISTS "aquarium_upgrades" (
            "upgrade_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "level" BIGINT NOT NULL UNIQUE,
            "capacity" BIGINT NOT NULL,
            "cost_coins" BIGINT NOT NULL,
            "cost_premium" BIGINT DEFAULT 0,
            "description" TEXT,
            "created_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "baits": """CREATE TABLE IF NOT EXISTS "baits" (
            "bait_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "name" TEXT NOT NULL UNIQUE,
            "description" TEXT,
            "rarity" BIGINT NOT NULL DEFAULT 1,
            "effect_description" TEXT,
            "duration_minutes" BIGINT DEFAULT 0,
            "cost" BIGINT DEFAULT 0,
            "required_rod_rarity" BIGINT DEFAULT 0,
            "success_rate_modifier" DOUBLE PRECISION DEFAULT 0.0,
            "rare_chance_modifier" DOUBLE PRECISION DEFAULT 0.0,
            "garbage_reduction_modifier" DOUBLE PRECISION DEFAULT 0.0,
            "value_modifier" DOUBLE PRECISION DEFAULT 1.0,
            "quantity_modifier" DOUBLE PRECISION DEFAULT 1.0,
            "is_consumable" BIGINT DEFAULT 1,
            "weight_modifier" DOUBLE PRECISION DEFAULT 1.0
        )
    """,
    "bank_accounts": """CREATE TABLE IF NOT EXISTS "bank_accounts" (
            "user_id" TEXT PRIMARY KEY,
            "balance" BIGINT NOT NULL DEFAULT 0,
            "today_withdrawn" BIGINT NOT NULL DEFAULT 0,
            "last_withdraw_reset_date" TEXT,
            "created_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "locked_balance" BIGINT NOT NULL DEFAULT 0
        )
    """,
    "bank_fixed_deposits": """CREATE TABLE IF NOT EXISTS "bank_fixed_deposits" (
            "deposit_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "principal" BIGINT NOT NULL,
            "term_days" BIGINT NOT NULL,
            "interest_rate" DOUBLE PRECISION NOT NULL,
            "expected_interest" BIGINT NOT NULL,
            "status" TEXT NOT NULL DEFAULT 'active',
            "started_at" TIMESTAMPTZ NOT NULL,
            "matures_at" TIMESTAMPTZ NOT NULL,
            "completed_at" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "bank_transactions": """CREATE TABLE IF NOT EXISTS "bank_transactions" (
            "transaction_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "tx_type" TEXT NOT NULL,
            "amount" BIGINT NOT NULL DEFAULT 0,
            "wallet_delta" BIGINT NOT NULL DEFAULT 0,
            "bank_delta" BIGINT NOT NULL DEFAULT 0,
            "wallet_after" BIGINT NOT NULL DEFAULT 0,
            "bank_after" BIGINT NOT NULL DEFAULT 0,
            "locked_after" BIGINT NOT NULL DEFAULT 0,
            "ref_id" BIGINT,
            "remark" TEXT,
            "created_at" TIMESTAMPTZ NOT NULL
        )
    """,
    "bank_withdraw_reservations": """CREATE TABLE IF NOT EXISTS "bank_withdraw_reservations" (
            "reservation_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "amount" BIGINT NOT NULL,
            "fee_amount" BIGINT NOT NULL DEFAULT 0,
            "status" TEXT NOT NULL DEFAULT 'pending',
            "ready_at" TIMESTAMPTZ NOT NULL,
            "created_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "expires_at" TIMESTAMPTZ
        )
    """,
    "check_ins": """CREATE TABLE IF NOT EXISTS "check_ins" (
            "user_id" TEXT NOT NULL,
            "check_in_date" DATE NOT NULL
        )
    """,
    "commodities": """CREATE TABLE IF NOT EXISTS "commodities" (
            "commodity_id" TEXT PRIMARY KEY,
            "name" TEXT NOT NULL,
            "description" TEXT
        )
    """,
    "exchange_prices": """CREATE TABLE IF NOT EXISTS "exchange_prices" (
            "price_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "date" TEXT NOT NULL,
            "time" TEXT NOT NULL,
            "commodity_id" TEXT NOT NULL,
            "price" BIGINT NOT NULL,
            "update_type" TEXT DEFAULT 'auto',
            "created_at" TEXT NOT NULL
        )
    """,
    "fish": """CREATE TABLE IF NOT EXISTS "fish" (
            "fish_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "name" TEXT NOT NULL UNIQUE,
            "description" TEXT,
            "rarity" BIGINT NOT NULL,
            "base_value" BIGINT NOT NULL,
            "min_weight" BIGINT NOT NULL,
            "max_weight" BIGINT NOT NULL,
            "icon_url" TEXT
        )
    """,
    "fishing_records": """CREATE TABLE IF NOT EXISTS "fishing_records" (
            "record_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "fish_id" BIGINT NOT NULL,
            "weight" BIGINT NOT NULL,
            "value" BIGINT NOT NULL,
            "rod_instance_id" BIGINT,
            "accessory_instance_id" BIGINT,
            "bait_id" BIGINT,
            "timestamp" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "location_id" BIGINT,
            "is_king_size" BIGINT DEFAULT 0
        )
    """,
    "fishing_zones": """CREATE TABLE IF NOT EXISTS "fishing_zones" (
            "id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "name" TEXT NOT NULL,
            "description" TEXT,
            "daily_rare_fish_quota" BIGINT NOT NULL,
            "rare_fish_caught_today" BIGINT NOT NULL DEFAULT 0,
            "configs" TEXT,
            "is_active" BIGINT DEFAULT 1,
            "available_from" TEXT,
            "available_until" TEXT,
            "required_item_id" BIGINT,
            "requires_pass" SMALLINT DEFAULT 0,
            "fishing_cost" BIGINT DEFAULT 10
        )
    """,
    "gacha_pool_items": """CREATE TABLE IF NOT EXISTS "gacha_pool_items" (
            "gacha_pool_item_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "gacha_pool_id" BIGINT NOT NULL,
            "item_type" TEXT NOT NULL,
            "item_id" BIGINT NOT NULL,
            "quantity" BIGINT DEFAULT 1,
            "weight" BIGINT NOT NULL
        )
    """,
    "gacha_pools": """CREATE TABLE IF NOT EXISTS "gacha_pools" (
            "gacha_pool_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "name" TEXT NOT NULL UNIQUE,
            "description" TEXT,
            "cost_coins" BIGINT DEFAULT 0,
            "cost_premium_currency" BIGINT DEFAULT 0,
            "is_limited_time" BIGINT DEFAULT 0,
            "open_until" TEXT
        )
    """,
    "gacha_records": """CREATE TABLE IF NOT EXISTS "gacha_records" (
            "record_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "gacha_pool_id" BIGINT NOT NULL,
            "item_type" TEXT NOT NULL,
            "item_id" BIGINT NOT NULL,
            "item_name" TEXT NOT NULL,
            "quantity" BIGINT DEFAULT 1,
            "rarity" BIGINT DEFAULT 1,
            "timestamp" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "items": """CREATE TABLE IF NOT EXISTS "items" (
            "item_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "name" TEXT NOT NULL UNIQUE,
            "description" TEXT,
            "rarity" BIGINT NOT NULL DEFAULT 1,
            "effect_description" TEXT,
            "cost" BIGINT DEFAULT 0,
            "is_consumable" BIGINT DEFAULT 1,
            "icon_url" TEXT,
            "effect_type" TEXT,
            "effect_payload" TEXT
        )
    """,
    "loans": """CREATE TABLE IF NOT EXISTS "loans" (
            "loan_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "lender_id" TEXT NOT NULL,
            "borrower_id" TEXT NOT NULL,
            "principal" BIGINT NOT NULL,
            "interest_rate" DOUBLE PRECISION NOT NULL DEFAULT 0.05,
            "borrowed_at" TIMESTAMPTZ NOT NULL,
            "due_amount" BIGINT NOT NULL,
            "repaid_amount" BIGINT NOT NULL DEFAULT 0,
            "status" TEXT NOT NULL DEFAULT 'active',
            "due_date" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ NOT NULL,
            "updated_at" TIMESTAMPTZ NOT NULL
        )
    """,
    "market": """CREATE TABLE IF NOT EXISTS "market" (
            "market_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "item_type" TEXT NOT NULL,
            "item_id" BIGINT NOT NULL,
            "quantity" BIGINT NOT NULL,
            "price" BIGINT NOT NULL,
            "listed_at" TEXT NOT NULL,
            "expires_at" TEXT,
            "refine_level" BIGINT DEFAULT 1,
            "seller_nickname" TEXT,
            "item_name" TEXT,
            "item_description" TEXT,
            "item_instance_id" BIGINT,
            "is_anonymous" BIGINT DEFAULT 0,
            "quality_level" BIGINT DEFAULT 0,
            UNIQUE(user_id, item_type, item_id, quality_level, item_instance_id)
        )
    """,
    "red_packet_records": """CREATE TABLE IF NOT EXISTS "red_packet_records" (
            "record_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "packet_id" BIGINT NOT NULL,
            "user_id" TEXT NOT NULL,
            "amount" BIGINT NOT NULL,
            "claimed_at" TIMESTAMPTZ NOT NULL
        )
    """,
    "red_packets": """CREATE TABLE IF NOT EXISTS "red_packets" (
            "packet_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "sender_id" TEXT NOT NULL,
            "group_id" TEXT NOT NULL,
            "packet_type" TEXT NOT NULL,
            "total_amount" BIGINT NOT NULL,
            "total_count" BIGINT NOT NULL,
            "remaining_amount" BIGINT NOT NULL,
            "remaining_count" BIGINT NOT NULL,
            "password" TEXT,
            "created_at" TIMESTAMPTZ NOT NULL,
            "expires_at" TIMESTAMPTZ NOT NULL,
            "is_expired" BIGINT DEFAULT 0
        )
    """,
    "rods": """CREATE TABLE IF NOT EXISTS "rods" (
            "rod_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "name" TEXT NOT NULL UNIQUE,
            "description" TEXT,
            "rarity" BIGINT NOT NULL DEFAULT 1,
            "source" TEXT NOT NULL,
            "purchase_cost" BIGINT,
            "bonus_fish_quality_modifier" DOUBLE PRECISION DEFAULT 1.0,
            "bonus_fish_quantity_modifier" DOUBLE PRECISION DEFAULT 1.0,
            "bonus_rare_fish_chance" DOUBLE PRECISION DEFAULT 0.0,
            "durability" BIGINT,
            "icon_url" TEXT
        )
    """,
    "shop_item_costs": """CREATE TABLE IF NOT EXISTS "shop_item_costs" (
            "cost_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "item_id" BIGINT NOT NULL,
            "cost_type" TEXT NOT NULL,
            "cost_amount" BIGINT NOT NULL,
            "cost_item_id" BIGINT,
            "cost_relation" TEXT DEFAULT 'and',
            "group_id" BIGINT,
            "quality_level" BIGINT DEFAULT 0
        )
    """,
    "shop_item_rewards": """CREATE TABLE IF NOT EXISTS "shop_item_rewards" (
            "reward_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "item_id" BIGINT NOT NULL,
            "reward_type" TEXT NOT NULL,
            "reward_item_id" BIGINT,
            "reward_quantity" BIGINT NOT NULL DEFAULT 1,
            "reward_refine_level" BIGINT,
            "quality_level" BIGINT DEFAULT 0
        )
    """,
    "shop_items": """CREATE TABLE IF NOT EXISTS "shop_items" (
            "item_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "shop_id" BIGINT NOT NULL,
            "name" TEXT NOT NULL,
            "description" TEXT,
            "category" TEXT NOT NULL DEFAULT 'general',
            "stock_total" BIGINT,
            "stock_sold" BIGINT NOT NULL DEFAULT 0,
            "per_user_limit" BIGINT,
            "per_user_daily_limit" BIGINT,
            "is_active" SMALLINT NOT NULL DEFAULT 1,
            "start_time" TIMESTAMPTZ,
            "end_time" TIMESTAMPTZ,
            "sort_order" BIGINT NOT NULL DEFAULT 100,
            "created_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ
        )
    """,
    "shop_purchase_records": """CREATE TABLE IF NOT EXISTS "shop_purchase_records" (
            "record_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "item_id" BIGINT NOT NULL,
            "quantity" BIGINT NOT NULL DEFAULT 1,
            "timestamp" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "shops": """CREATE TABLE IF NOT EXISTS "shops" (
            "shop_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "name" TEXT NOT NULL,
            "description" TEXT,
            "shop_type" TEXT NOT NULL DEFAULT 'normal',
            "is_active" SMALLINT NOT NULL DEFAULT 1,
            "start_time" TIMESTAMPTZ,
            "end_time" TIMESTAMPTZ,
            "daily_start_time" TIME,
            "daily_end_time" TIME,
            "sort_order" BIGINT NOT NULL DEFAULT 100,
            "created_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ
        )
    """,
    "tax_debts": """CREATE TABLE IF NOT EXISTS "tax_debts" (
            "user_id" TEXT PRIMARY KEY,
            "debt_amount" BIGINT NOT NULL DEFAULT 0,
            "created_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "last_accrued_date" TEXT
        )
    """,
    "taxes": """CREATE TABLE IF NOT EXISTS "taxes" (
            "tax_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "tax_amount" BIGINT NOT NULL,
            "tax_rate" DOUBLE PRECISION NOT NULL,
            "original_amount" BIGINT NOT NULL,
            "timestamp" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "tax_type" TEXT NOT NULL,
            "balance_after" BIGINT NOT NULL
        )
    """,
    "titles": """CREATE TABLE IF NOT EXISTS "titles" (
            "title_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "name" TEXT NOT NULL UNIQUE,
            "description" TEXT NOT NULL,
            "display_format" TEXT DEFAULT '{name}'
        )
    """,
    "user_accessories": """CREATE TABLE IF NOT EXISTS "user_accessories" (
            "accessory_instance_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "accessory_id" BIGINT NOT NULL,
            "obtained_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "is_equipped" BIGINT DEFAULT 0,
            "refine_level" BIGINT DEFAULT 1,
            "is_locked" BIGINT DEFAULT 0,
            "display_code" TEXT
        )
    """,
    "user_achievement_progress": """CREATE TABLE IF NOT EXISTS "user_achievement_progress" (
            "user_id" TEXT NOT NULL,
            "achievement_id" BIGINT NOT NULL,
            "current_progress" BIGINT DEFAULT 0,
            "completed_at" TIMESTAMPTZ,
            "claimed_at" TIMESTAMPTZ
        )
    """,
    "user_aquarium": """CREATE TABLE IF NOT EXISTS "user_aquarium" (
            "user_id" TEXT NOT NULL,
            "fish_id" BIGINT NOT NULL,
            "quality_level" BIGINT DEFAULT 0,
            "quantity" BIGINT DEFAULT 0,
            "added_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "user_bait_inventory": """CREATE TABLE IF NOT EXISTS "user_bait_inventory" (
            "user_id" TEXT NOT NULL,
            "bait_id" BIGINT NOT NULL,
            "quantity" BIGINT DEFAULT 0
        )
    """,
    "user_buffs": """CREATE TABLE IF NOT EXISTS "user_buffs" (
            "id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "buff_type" TEXT NOT NULL,
            "payload" TEXT,
            "started_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "expires_at" TIMESTAMPTZ
        )
    """,
    "user_commodities": """CREATE TABLE IF NOT EXISTS "user_commodities" (
            "instance_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "commodity_id" TEXT NOT NULL,
            "quantity" BIGINT NOT NULL,
            "purchase_price" BIGINT NOT NULL,
            "purchased_at" TEXT NOT NULL,
            "expires_at" TEXT NOT NULL
        )
    """,
    "user_fish_inventory": """CREATE TABLE IF NOT EXISTS "user_fish_inventory" (
            "user_id" TEXT NOT NULL,
            "fish_id" BIGINT NOT NULL,
            "quality_level" BIGINT DEFAULT 0,
            "quantity" BIGINT DEFAULT 0,
            "no_sell_until" TIMESTAMPTZ
        )
    """,
    "user_fish_stats": """CREATE TABLE IF NOT EXISTS "user_fish_stats" (
            "user_id" TEXT NOT NULL,
            "fish_id" BIGINT NOT NULL,
            "first_caught_at" TIMESTAMPTZ,
            "last_caught_at" TIMESTAMPTZ,
            "max_weight" BIGINT NOT NULL,
            "min_weight" BIGINT NOT NULL,
            "total_caught" BIGINT NOT NULL DEFAULT 0,
            "total_weight" BIGINT NOT NULL DEFAULT 0
        )
    """,
    "user_items": """CREATE TABLE IF NOT EXISTS "user_items" (
            "user_id" TEXT NOT NULL,
            "item_id" BIGINT NOT NULL,
            "quantity" BIGINT DEFAULT 0
        )
    """,
    "user_rods": """CREATE TABLE IF NOT EXISTS "user_rods" (
            "rod_instance_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "rod_id" BIGINT NOT NULL,
            "obtained_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "current_durability" BIGINT,
            "is_equipped" BIGINT DEFAULT 0,
            "refine_level" BIGINT DEFAULT 1,
            "is_locked" BIGINT DEFAULT 0,
            "display_code" TEXT
        )
    """,
    "user_titles": """CREATE TABLE IF NOT EXISTS "user_titles" (
            "user_id" TEXT NOT NULL,
            "title_id" BIGINT NOT NULL,
            "unlocked_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "users": """CREATE TABLE IF NOT EXISTS "users" (
            "user_id" TEXT PRIMARY KEY,
            "nickname" TEXT,
            "coins" BIGINT DEFAULT 200,
            "premium_currency" BIGINT DEFAULT 0,
            "total_fishing_count" BIGINT DEFAULT 0,
            "total_weight_caught" BIGINT DEFAULT 0,
            "total_coins_earned" BIGINT DEFAULT 0,
            "equipped_rod_instance_id" BIGINT,
            "equipped_accessory_instance_id" BIGINT,
            "current_bait_id" BIGINT,
            "bait_start_time" TIMESTAMPTZ,
            "current_title_id" BIGINT,
            "auto_fishing_enabled" BIGINT DEFAULT 0,
            "last_fishing_time" TIMESTAMPTZ,
            "last_wipe_bomb_time" TIMESTAMPTZ,
            "last_steal_time" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            "last_login_time" TIMESTAMPTZ,
            "consecutive_login_days" BIGINT DEFAULT 0,
            "fish_pond_capacity" BIGINT DEFAULT 480,
            "last_stolen_at" TIMESTAMPTZ,
            "fishing_zone_id" BIGINT DEFAULT 1,
            "wipe_bomb_forecast" TEXT,
            "aquarium_capacity" BIGINT DEFAULT 50,
            "exchange_account_status" BIGINT DEFAULT 0,
            "last_electric_fish_time" TIMESTAMPTZ,
            "in_wheel_of_fate" SMALLINT,
            "wof_current_level" BIGINT,
            "wof_current_prize" BIGINT,
            "wof_entry_fee" BIGINT,
            "last_wof_play_time" TIMESTAMPTZ,
            "wof_last_action_time" TIMESTAMPTZ,
            "wof_plays_today" BIGINT DEFAULT 0,
            "last_wof_date" TEXT,
            "last_sicbo_time" TIMESTAMPTZ,
            "max_wipe_bomb_multiplier" DOUBLE PRECISION DEFAULT 0.0,
            "min_wipe_bomb_multiplier" DOUBLE PRECISION DEFAULT NULL,
            "wipe_bomb_attempts_today" BIGINT NOT NULL DEFAULT 0,
            "last_wipe_bomb_date" TEXT DEFAULT NULL,
            "max_coins" BIGINT DEFAULT 0,
            "exchange_capacity_level" BIGINT DEFAULT 0
        )
    """,
    "wipe_bomb_log": """CREATE TABLE IF NOT EXISTS "wipe_bomb_log" (
            "log_id" BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            "user_id" TEXT NOT NULL,
            "contribution_amount" BIGINT NOT NULL,
            "reward_multiplier" DOUBLE PRECISION NOT NULL,
            "reward_amount" BIGINT NOT NULL,
            "timestamp" TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "zone_fish_mapping": """CREATE TABLE IF NOT EXISTS "zone_fish_mapping" (
            "zone_id" BIGINT,
            "fish_id" BIGINT
        )
    """,
}

PG_INDEX_DDL = [
    """CREATE INDEX idx_users_coins ON users(coins)""",
    """CREATE INDEX idx_users_last_login ON users(last_login_time)""",
    """CREATE INDEX idx_user_bait_inventory_user ON user_bait_inventory(user_id)""",
    """CREATE INDEX idx_user_rods_user ON user_rods(user_id)""",
    """CREATE INDEX idx_user_accessories_user ON user_accessories(user_id)""",
    """CREATE INDEX idx_user_titles_user ON user_titles(user_id)""",
    """CREATE INDEX idx_wipe_bomb_log_user_time ON wipe_bomb_log(user_id, timestamp)""",
    """CREATE INDEX idx_fishing_records_user_time ON fishing_records(user_id, timestamp)""",
    """CREATE INDEX idx_fishing_records_fish_id ON fishing_records(fish_id)""",
    """CREATE INDEX idx_gacha_records_user_time ON gacha_records(user_id, timestamp)""",
    """CREATE INDEX idx_user_buffs_user_id ON user_buffs(user_id)""",
    """CREATE INDEX idx_user_buffs_expires_at ON user_buffs(expires_at)""",
    """CREATE INDEX idx_gacha_pool_items_pool_type ON gacha_pool_items(gacha_pool_id, item_type)""",
    """CREATE INDEX idx_user_rods_locked ON user_rods(user_id, is_locked)""",
    """CREATE INDEX idx_user_accessories_locked ON user_accessories(user_id, is_locked)""",
    """CREATE INDEX idx_user_fish_stats_user ON user_fish_stats(user_id)""",
    """CREATE INDEX idx_user_fish_stats_fish ON user_fish_stats(fish_id)""",
    """CREATE INDEX idx_shops_active_time ON shops(is_active, start_time, end_time)""",
    """CREATE INDEX idx_shops_type ON shops(shop_type)""",
    """CREATE INDEX idx_shops_daily_time ON shops(daily_start_time, daily_end_time)""",
    """CREATE INDEX idx_shop_items_shop ON shop_items(shop_id)""",
    """CREATE INDEX idx_shop_items_active_time ON shop_items(is_active, start_time, end_time)""",
    """CREATE INDEX idx_shop_items_category ON shop_items(category)""",
    """CREATE INDEX idx_shop_item_rewards_item ON shop_item_rewards(item_id)""",
    """CREATE INDEX idx_shop_item_rewards_type ON shop_item_rewards(reward_type)""",
    """CREATE INDEX idx_shop_purchase_user_item ON shop_purchase_records(user_id, item_id)""",
    """CREATE INDEX idx_shop_purchase_time ON shop_purchase_records(timestamp)""",
    """CREATE INDEX idx_shop_item_costs_item ON shop_item_costs(item_id)""",
    """CREATE INDEX idx_shop_item_costs_type ON shop_item_costs(cost_type)""",
    """CREATE INDEX idx_shop_item_costs_relation ON shop_item_costs(cost_relation)""",
    """CREATE INDEX idx_shop_item_costs_group ON shop_item_costs(group_id)""",
    """CREATE UNIQUE INDEX idx_user_rods_display_code ON user_rods(display_code)""",
    """CREATE UNIQUE INDEX idx_user_accessories_display_code ON user_accessories(display_code)""",
    """CREATE INDEX idx_aquarium_upgrades_level ON aquarium_upgrades(level)""",
    """CREATE INDEX idx_exchange_prices_date_commodity ON exchange_prices(date, commodity_id)""",
    """CREATE INDEX idx_exchange_prices_created_at ON exchange_prices(created_at)""",
    """CREATE INDEX idx_user_commodities_user ON user_commodities(user_id)""",
    """CREATE INDEX idx_user_commodities_commodity ON user_commodities(commodity_id)""",
    """CREATE INDEX idx_user_commodities_expires ON user_commodities(expires_at)""",
    """CREATE INDEX idx_market_user_item_quality ON market(user_id, item_type, item_id, quality_level)""",
    """CREATE INDEX idx_market_item_quality ON market(item_type, item_id, quality_level)""",
    """CREATE INDEX idx_shop_item_costs_fish_quality ON shop_item_costs(cost_type, cost_item_id, quality_level) WHERE cost_type = 'fish'""",
    """CREATE INDEX idx_red_packets_group ON red_packets(group_id, is_expired)""",
    """CREATE INDEX idx_red_packet_records_packet ON red_packet_records(packet_id)""",
    """CREATE INDEX idx_red_packet_records_user ON red_packet_records(user_id, packet_id)""",
    """CREATE INDEX idx_loans_lender ON loans(lender_id, status)""",
    """CREATE INDEX idx_loans_borrower ON loans(borrower_id, status)""",
    """CREATE INDEX idx_loans_status ON loans(status)""",
    """CREATE INDEX idx_loans_overdue ON loans(lender_id, status, due_date)""",
    """CREATE INDEX idx_bank_reservations_user_status ON bank_withdraw_reservations(user_id, status)""",
    """CREATE INDEX idx_bank_reservations_ready_at ON bank_withdraw_reservations(status, ready_at)""",
    """CREATE INDEX idx_bank_fixed_deposits_user_status ON bank_fixed_deposits(user_id, status)""",
    """CREATE INDEX idx_bank_fixed_deposits_matures_at ON bank_fixed_deposits(status, matures_at)""",
    """CREATE INDEX idx_tax_debts_amount ON tax_debts(debt_amount)""",
    """CREATE INDEX idx_taxes_timestamp ON taxes(timestamp)""",
    """CREATE INDEX idx_taxes_user_timestamp ON taxes(user_id, timestamp)""",
    """CREATE INDEX idx_bank_transactions_user_time ON bank_transactions(user_id, created_at)""",
    """CREATE INDEX idx_bank_transactions_type_time ON bank_transactions(tx_type, created_at)""",
    """CREATE INDEX idx_bank_reservations_expires_at ON bank_withdraw_reservations(status, expires_at)""",
]

PG_FK_DDL = {
    "bank_accounts": ["""ALTER TABLE "bank_accounts" ADD CONSTRAINT fk_bank_accounts_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"""],
    "bank_fixed_deposits": ["""ALTER TABLE "bank_fixed_deposits" ADD CONSTRAINT fk_bank_fixed_deposits_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"""],
    "bank_transactions": ["""ALTER TABLE "bank_transactions" ADD CONSTRAINT fk_bank_transactions_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"""],
    "bank_withdraw_reservations": ["""ALTER TABLE "bank_withdraw_reservations" ADD CONSTRAINT fk_bank_withdraw_reservations_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"""],
    "check_ins": ["""ALTER TABLE "check_ins" ADD CONSTRAINT fk_check_ins_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"""],
    "exchange_prices": ["""ALTER TABLE "exchange_prices" ADD CONSTRAINT fk_exchange_prices_0 FOREIGN KEY (commodity_id) REFERENCES commodities(commodity_id)"""],
    "fishing_records": ["""ALTER TABLE "fishing_records" ADD CONSTRAINT fk_fishing_records_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE""", """ALTER TABLE "fishing_records" ADD CONSTRAINT fk_fishing_records_1 FOREIGN KEY (fish_id) REFERENCES fish(fish_id) ON DELETE RESTRICT"""],
    "gacha_pool_items": ["""ALTER TABLE "gacha_pool_items" ADD CONSTRAINT fk_gacha_pool_items_0 FOREIGN KEY (gacha_pool_id) REFERENCES gacha_pools(gacha_pool_id) ON DELETE CASCADE"""],
    "gacha_records": ["""ALTER TABLE "gacha_records" ADD CONSTRAINT fk_gacha_records_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE""", """ALTER TABLE "gacha_records" ADD CONSTRAINT fk_gacha_records_1 FOREIGN KEY (gacha_pool_id) REFERENCES gacha_pools(gacha_pool_id) ON DELETE CASCADE"""],
    "loans": ["""ALTER TABLE "loans" ADD CONSTRAINT fk_loans_0 FOREIGN KEY (lender_id) REFERENCES users(user_id)""", """ALTER TABLE "loans" ADD CONSTRAINT fk_loans_1 FOREIGN KEY (borrower_id) REFERENCES users(user_id)"""],
    "red_packet_records": ["""ALTER TABLE "red_packet_records" ADD CONSTRAINT fk_red_packet_records_0 FOREIGN KEY (packet_id) REFERENCES red_packets(packet_id)"""],
    "shop_item_costs": ["""ALTER TABLE "shop_item_costs" ADD CONSTRAINT fk_shop_item_costs_0 FOREIGN KEY (item_id) REFERENCES shop_items(item_id) ON DELETE CASCADE"""],
    "shop_item_rewards": ["""ALTER TABLE "shop_item_rewards" ADD CONSTRAINT fk_shop_item_rewards_0 FOREIGN KEY (item_id) REFERENCES shop_items(item_id) ON DELETE CASCADE"""],
    "shop_items": ["""ALTER TABLE "shop_items" ADD CONSTRAINT fk_shop_items_0 FOREIGN KEY (shop_id) REFERENCES shops(shop_id) ON DELETE CASCADE"""],
    "shop_purchase_records": ["""ALTER TABLE "shop_purchase_records" ADD CONSTRAINT fk_shop_purchase_records_0 FOREIGN KEY (item_id) REFERENCES shop_items(item_id) ON DELETE CASCADE""", """ALTER TABLE "shop_purchase_records" ADD CONSTRAINT fk_shop_purchase_records_1 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"""],
    "tax_debts": ["""ALTER TABLE "tax_debts" ADD CONSTRAINT fk_tax_debts_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"""],
    "taxes": ["""ALTER TABLE "taxes" ADD CONSTRAINT fk_taxes_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"""],
    "user_accessories": ["""ALTER TABLE "user_accessories" ADD CONSTRAINT fk_user_accessories_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE""", """ALTER TABLE "user_accessories" ADD CONSTRAINT fk_user_accessories_1 FOREIGN KEY (accessory_id) REFERENCES accessories(accessory_id) ON DELETE CASCADE"""],
    "user_achievement_progress": ["""ALTER TABLE "user_achievement_progress" ADD CONSTRAINT fk_user_achievement_progress_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"""],
    "user_aquarium": ["""ALTER TABLE "user_aquarium" ADD CONSTRAINT fk_user_aquarium_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE""", """ALTER TABLE "user_aquarium" ADD CONSTRAINT fk_user_aquarium_1 FOREIGN KEY (fish_id) REFERENCES fish(fish_id) ON DELETE CASCADE"""],
    "user_bait_inventory": ["""ALTER TABLE "user_bait_inventory" ADD CONSTRAINT fk_user_bait_inventory_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE""", """ALTER TABLE "user_bait_inventory" ADD CONSTRAINT fk_user_bait_inventory_1 FOREIGN KEY (bait_id) REFERENCES baits(bait_id) ON DELETE CASCADE"""],
    "user_buffs": ["""ALTER TABLE "user_buffs" ADD CONSTRAINT fk_user_buffs_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"""],
    "user_commodities": ["""ALTER TABLE "user_commodities" ADD CONSTRAINT fk_user_commodities_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE""", """ALTER TABLE "user_commodities" ADD CONSTRAINT fk_user_commodities_1 FOREIGN KEY (commodity_id) REFERENCES commodities(commodity_id) ON DELETE CASCADE"""],
    "user_fish_inventory": ["""ALTER TABLE "user_fish_inventory" ADD CONSTRAINT fk_user_fish_inventory_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE""", """ALTER TABLE "user_fish_inventory" ADD CONSTRAINT fk_user_fish_inventory_1 FOREIGN KEY (fish_id) REFERENCES fish(fish_id) ON DELETE CASCADE"""],
    "user_fish_stats": ["""ALTER TABLE "user_fish_stats" ADD CONSTRAINT fk_user_fish_stats_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE""", """ALTER TABLE "user_fish_stats" ADD CONSTRAINT fk_user_fish_stats_1 FOREIGN KEY (fish_id) REFERENCES fish(fish_id) ON DELETE RESTRICT"""],
    "user_items": ["""ALTER TABLE "user_items" ADD CONSTRAINT fk_user_items_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE""", """ALTER TABLE "user_items" ADD CONSTRAINT fk_user_items_1 FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE"""],
    "user_rods": ["""ALTER TABLE "user_rods" ADD CONSTRAINT fk_user_rods_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE""", """ALTER TABLE "user_rods" ADD CONSTRAINT fk_user_rods_1 FOREIGN KEY (rod_id) REFERENCES rods(rod_id) ON DELETE CASCADE"""],
    "user_titles": ["""ALTER TABLE "user_titles" ADD CONSTRAINT fk_user_titles_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE""", """ALTER TABLE "user_titles" ADD CONSTRAINT fk_user_titles_1 FOREIGN KEY (title_id) REFERENCES titles(title_id) ON DELETE CASCADE"""],
    "wipe_bomb_log": ["""ALTER TABLE "wipe_bomb_log" ADD CONSTRAINT fk_wipe_bomb_log_0 FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE"""],
    "zone_fish_mapping": ["""ALTER TABLE "zone_fish_mapping" ADD CONSTRAINT fk_zone_fish_mapping_0 FOREIGN KEY (zone_id) REFERENCES fishing_zones(id) ON DELETE CASCADE""", """ALTER TABLE "zone_fish_mapping" ADD CONSTRAINT fk_zone_fish_mapping_1 FOREIGN KEY (fish_id) REFERENCES fish(fish_id) ON DELETE CASCADE"""],
}

TABLE_COLUMNS = {
    "accessories": [("accessory_id", 'BIGINT'), ("name", 'TEXT'), ("description", 'TEXT'), ("rarity", 'BIGINT'), ("slot_type", 'TEXT'), ("bonus_fish_quality_modifier", 'DOUBLE PRECISION'), ("bonus_fish_quantity_modifier", 'DOUBLE PRECISION'), ("bonus_rare_fish_chance", 'DOUBLE PRECISION'), ("bonus_coin_modifier", 'DOUBLE PRECISION'), ("other_bonus_description", 'TEXT'), ("icon_url", 'TEXT')],
    "aquarium_upgrades": [("upgrade_id", 'BIGINT'), ("level", 'BIGINT'), ("capacity", 'BIGINT'), ("cost_coins", 'BIGINT'), ("cost_premium", 'BIGINT'), ("description", 'TEXT'), ("created_at", 'TIMESTAMPTZ')],
    "baits": [("bait_id", 'BIGINT'), ("name", 'TEXT'), ("description", 'TEXT'), ("rarity", 'BIGINT'), ("effect_description", 'TEXT'), ("duration_minutes", 'BIGINT'), ("cost", 'BIGINT'), ("required_rod_rarity", 'BIGINT'), ("success_rate_modifier", 'DOUBLE PRECISION'), ("rare_chance_modifier", 'DOUBLE PRECISION'), ("garbage_reduction_modifier", 'DOUBLE PRECISION'), ("value_modifier", 'DOUBLE PRECISION'), ("quantity_modifier", 'DOUBLE PRECISION'), ("is_consumable", 'BIGINT'), ("weight_modifier", 'DOUBLE PRECISION')],
    "bank_accounts": [("user_id", 'TEXT'), ("balance", 'BIGINT'), ("today_withdrawn", 'BIGINT'), ("last_withdraw_reset_date", 'TEXT'), ("created_at", 'TIMESTAMPTZ'), ("updated_at", 'TIMESTAMPTZ'), ("locked_balance", 'BIGINT')],
    "bank_fixed_deposits": [("deposit_id", 'BIGINT'), ("user_id", 'TEXT'), ("principal", 'BIGINT'), ("term_days", 'BIGINT'), ("interest_rate", 'DOUBLE PRECISION'), ("expected_interest", 'BIGINT'), ("status", 'TEXT'), ("started_at", 'TIMESTAMPTZ'), ("matures_at", 'TIMESTAMPTZ'), ("completed_at", 'TIMESTAMPTZ'), ("created_at", 'TIMESTAMPTZ'), ("updated_at", 'TIMESTAMPTZ')],
    "bank_transactions": [("transaction_id", 'BIGINT'), ("user_id", 'TEXT'), ("tx_type", 'TEXT'), ("amount", 'BIGINT'), ("wallet_delta", 'BIGINT'), ("bank_delta", 'BIGINT'), ("wallet_after", 'BIGINT'), ("bank_after", 'BIGINT'), ("locked_after", 'BIGINT'), ("ref_id", 'BIGINT'), ("remark", 'TEXT'), ("created_at", 'TIMESTAMPTZ')],
    "bank_withdraw_reservations": [("reservation_id", 'BIGINT'), ("user_id", 'TEXT'), ("amount", 'BIGINT'), ("fee_amount", 'BIGINT'), ("status", 'TEXT'), ("ready_at", 'TIMESTAMPTZ'), ("created_at", 'TIMESTAMPTZ'), ("updated_at", 'TIMESTAMPTZ'), ("expires_at", 'TIMESTAMPTZ')],
    "check_ins": [("user_id", 'TEXT'), ("check_in_date", 'DATE')],
    "commodities": [("commodity_id", 'TEXT'), ("name", 'TEXT'), ("description", 'TEXT')],
    "exchange_prices": [("price_id", 'BIGINT'), ("date", 'TEXT'), ("time", 'TEXT'), ("commodity_id", 'TEXT'), ("price", 'BIGINT'), ("update_type", 'TEXT'), ("created_at", 'TEXT')],
    "fish": [("fish_id", 'BIGINT'), ("name", 'TEXT'), ("description", 'TEXT'), ("rarity", 'BIGINT'), ("base_value", 'BIGINT'), ("min_weight", 'BIGINT'), ("max_weight", 'BIGINT'), ("icon_url", 'TEXT')],
    "fishing_records": [("record_id", 'BIGINT'), ("user_id", 'TEXT'), ("fish_id", 'BIGINT'), ("weight", 'BIGINT'), ("value", 'BIGINT'), ("rod_instance_id", 'BIGINT'), ("accessory_instance_id", 'BIGINT'), ("bait_id", 'BIGINT'), ("timestamp", 'TIMESTAMPTZ'), ("location_id", 'BIGINT'), ("is_king_size", 'BIGINT')],
    "fishing_zones": [("id", 'BIGINT'), ("name", 'TEXT'), ("description", 'TEXT'), ("daily_rare_fish_quota", 'BIGINT'), ("rare_fish_caught_today", 'BIGINT'), ("configs", 'TEXT'), ("is_active", 'BIGINT'), ("available_from", 'TEXT'), ("available_until", 'TEXT'), ("required_item_id", 'BIGINT'), ("requires_pass", 'SMALLINT'), ("fishing_cost", 'BIGINT')],
    "gacha_pool_items": [("gacha_pool_item_id", 'BIGINT'), ("gacha_pool_id", 'BIGINT'), ("item_type", 'TEXT'), ("item_id", 'BIGINT'), ("quantity", 'BIGINT'), ("weight", 'BIGINT')],
    "gacha_pools": [("gacha_pool_id", 'BIGINT'), ("name", 'TEXT'), ("description", 'TEXT'), ("cost_coins", 'BIGINT'), ("cost_premium_currency", 'BIGINT'), ("is_limited_time", 'BIGINT'), ("open_until", 'TEXT')],
    "gacha_records": [("record_id", 'BIGINT'), ("user_id", 'TEXT'), ("gacha_pool_id", 'BIGINT'), ("item_type", 'TEXT'), ("item_id", 'BIGINT'), ("item_name", 'TEXT'), ("quantity", 'BIGINT'), ("rarity", 'BIGINT'), ("timestamp", 'TIMESTAMPTZ')],
    "items": [("item_id", 'BIGINT'), ("name", 'TEXT'), ("description", 'TEXT'), ("rarity", 'BIGINT'), ("effect_description", 'TEXT'), ("cost", 'BIGINT'), ("is_consumable", 'BIGINT'), ("icon_url", 'TEXT'), ("effect_type", 'TEXT'), ("effect_payload", 'TEXT')],
    "loans": [("loan_id", 'BIGINT'), ("lender_id", 'TEXT'), ("borrower_id", 'TEXT'), ("principal", 'BIGINT'), ("interest_rate", 'DOUBLE PRECISION'), ("borrowed_at", 'TIMESTAMPTZ'), ("due_amount", 'BIGINT'), ("repaid_amount", 'BIGINT'), ("status", 'TEXT'), ("due_date", 'TIMESTAMPTZ'), ("created_at", 'TIMESTAMPTZ'), ("updated_at", 'TIMESTAMPTZ')],
    "market": [("market_id", 'BIGINT'), ("user_id", 'TEXT'), ("item_type", 'TEXT'), ("item_id", 'BIGINT'), ("quantity", 'BIGINT'), ("price", 'BIGINT'), ("listed_at", 'TEXT'), ("expires_at", 'TEXT'), ("refine_level", 'BIGINT'), ("seller_nickname", 'TEXT'), ("item_name", 'TEXT'), ("item_description", 'TEXT'), ("item_instance_id", 'BIGINT'), ("is_anonymous", 'BIGINT'), ("quality_level", 'BIGINT')],
    "red_packet_records": [("record_id", 'BIGINT'), ("packet_id", 'BIGINT'), ("user_id", 'TEXT'), ("amount", 'BIGINT'), ("claimed_at", 'TIMESTAMPTZ')],
    "red_packets": [("packet_id", 'BIGINT'), ("sender_id", 'TEXT'), ("group_id", 'TEXT'), ("packet_type", 'TEXT'), ("total_amount", 'BIGINT'), ("total_count", 'BIGINT'), ("remaining_amount", 'BIGINT'), ("remaining_count", 'BIGINT'), ("password", 'TEXT'), ("created_at", 'TIMESTAMPTZ'), ("expires_at", 'TIMESTAMPTZ'), ("is_expired", 'BIGINT')],
    "rods": [("rod_id", 'BIGINT'), ("name", 'TEXT'), ("description", 'TEXT'), ("rarity", 'BIGINT'), ("source", 'TEXT'), ("purchase_cost", 'BIGINT'), ("bonus_fish_quality_modifier", 'DOUBLE PRECISION'), ("bonus_fish_quantity_modifier", 'DOUBLE PRECISION'), ("bonus_rare_fish_chance", 'DOUBLE PRECISION'), ("durability", 'BIGINT'), ("icon_url", 'TEXT')],
    "shop_item_costs": [("cost_id", 'BIGINT'), ("item_id", 'BIGINT'), ("cost_type", 'TEXT'), ("cost_amount", 'BIGINT'), ("cost_item_id", 'BIGINT'), ("cost_relation", 'TEXT'), ("group_id", 'BIGINT'), ("quality_level", 'BIGINT')],
    "shop_item_rewards": [("reward_id", 'BIGINT'), ("item_id", 'BIGINT'), ("reward_type", 'TEXT'), ("reward_item_id", 'BIGINT'), ("reward_quantity", 'BIGINT'), ("reward_refine_level", 'BIGINT'), ("quality_level", 'BIGINT')],
    "shop_items": [("item_id", 'BIGINT'), ("shop_id", 'BIGINT'), ("name", 'TEXT'), ("description", 'TEXT'), ("category", 'TEXT'), ("stock_total", 'BIGINT'), ("stock_sold", 'BIGINT'), ("per_user_limit", 'BIGINT'), ("per_user_daily_limit", 'BIGINT'), ("is_active", 'SMALLINT'), ("start_time", 'TIMESTAMPTZ'), ("end_time", 'TIMESTAMPTZ'), ("sort_order", 'BIGINT'), ("created_at", 'TIMESTAMPTZ'), ("updated_at", 'TIMESTAMPTZ')],
    "shop_purchase_records": [("record_id", 'BIGINT'), ("user_id", 'TEXT'), ("item_id", 'BIGINT'), ("quantity", 'BIGINT'), ("timestamp", 'TIMESTAMPTZ')],
    "shops": [("shop_id", 'BIGINT'), ("name", 'TEXT'), ("description", 'TEXT'), ("shop_type", 'TEXT'), ("is_active", 'SMALLINT'), ("start_time", 'TIMESTAMPTZ'), ("end_time", 'TIMESTAMPTZ'), ("daily_start_time", 'TIME'), ("daily_end_time", 'TIME'), ("sort_order", 'BIGINT'), ("created_at", 'TIMESTAMPTZ'), ("updated_at", 'TIMESTAMPTZ')],
    "tax_debts": [("user_id", 'TEXT'), ("debt_amount", 'BIGINT'), ("created_at", 'TIMESTAMPTZ'), ("updated_at", 'TIMESTAMPTZ'), ("last_accrued_date", 'TEXT')],
    "taxes": [("tax_id", 'BIGINT'), ("user_id", 'TEXT'), ("tax_amount", 'BIGINT'), ("tax_rate", 'DOUBLE PRECISION'), ("original_amount", 'BIGINT'), ("timestamp", 'TIMESTAMPTZ'), ("tax_type", 'TEXT'), ("balance_after", 'BIGINT')],
    "titles": [("title_id", 'BIGINT'), ("name", 'TEXT'), ("description", 'TEXT'), ("display_format", 'TEXT')],
    "user_accessories": [("accessory_instance_id", 'BIGINT'), ("user_id", 'TEXT'), ("accessory_id", 'BIGINT'), ("obtained_at", 'TIMESTAMPTZ'), ("is_equipped", 'BIGINT'), ("refine_level", 'BIGINT'), ("is_locked", 'BIGINT'), ("display_code", 'TEXT')],
    "user_achievement_progress": [("user_id", 'TEXT'), ("achievement_id", 'BIGINT'), ("current_progress", 'BIGINT'), ("completed_at", 'TIMESTAMPTZ'), ("claimed_at", 'TIMESTAMPTZ')],
    "user_aquarium": [("user_id", 'TEXT'), ("fish_id", 'BIGINT'), ("quality_level", 'BIGINT'), ("quantity", 'BIGINT'), ("added_at", 'TIMESTAMPTZ')],
    "user_bait_inventory": [("user_id", 'TEXT'), ("bait_id", 'BIGINT'), ("quantity", 'BIGINT')],
    "user_buffs": [("id", 'BIGINT'), ("user_id", 'TEXT'), ("buff_type", 'TEXT'), ("payload", 'TEXT'), ("started_at", 'TIMESTAMPTZ'), ("expires_at", 'TIMESTAMPTZ')],
    "user_commodities": [("instance_id", 'BIGINT'), ("user_id", 'TEXT'), ("commodity_id", 'TEXT'), ("quantity", 'BIGINT'), ("purchase_price", 'BIGINT'), ("purchased_at", 'TEXT'), ("expires_at", 'TEXT')],
    "user_fish_inventory": [("user_id", 'TEXT'), ("fish_id", 'BIGINT'), ("quality_level", 'BIGINT'), ("quantity", 'BIGINT'), ("no_sell_until", 'TIMESTAMPTZ')],
    "user_fish_stats": [("user_id", 'TEXT'), ("fish_id", 'BIGINT'), ("first_caught_at", 'TIMESTAMPTZ'), ("last_caught_at", 'TIMESTAMPTZ'), ("max_weight", 'BIGINT'), ("min_weight", 'BIGINT'), ("total_caught", 'BIGINT'), ("total_weight", 'BIGINT')],
    "user_items": [("user_id", 'TEXT'), ("item_id", 'BIGINT'), ("quantity", 'BIGINT')],
    "user_rods": [("rod_instance_id", 'BIGINT'), ("user_id", 'TEXT'), ("rod_id", 'BIGINT'), ("obtained_at", 'TIMESTAMPTZ'), ("current_durability", 'BIGINT'), ("is_equipped", 'BIGINT'), ("refine_level", 'BIGINT'), ("is_locked", 'BIGINT'), ("display_code", 'TEXT')],
    "user_titles": [("user_id", 'TEXT'), ("title_id", 'BIGINT'), ("unlocked_at", 'TIMESTAMPTZ')],
    "users": [("user_id", 'TEXT'), ("nickname", 'TEXT'), ("coins", 'BIGINT'), ("premium_currency", 'BIGINT'), ("total_fishing_count", 'BIGINT'), ("total_weight_caught", 'BIGINT'), ("total_coins_earned", 'BIGINT'), ("equipped_rod_instance_id", 'BIGINT'), ("equipped_accessory_instance_id", 'BIGINT'), ("current_bait_id", 'BIGINT'), ("bait_start_time", 'TIMESTAMPTZ'), ("current_title_id", 'BIGINT'), ("auto_fishing_enabled", 'BIGINT'), ("last_fishing_time", 'TIMESTAMPTZ'), ("last_wipe_bomb_time", 'TIMESTAMPTZ'), ("last_steal_time", 'TIMESTAMPTZ'), ("created_at", 'TIMESTAMPTZ'), ("last_login_time", 'TIMESTAMPTZ'), ("consecutive_login_days", 'BIGINT'), ("fish_pond_capacity", 'BIGINT'), ("last_stolen_at", 'TIMESTAMPTZ'), ("fishing_zone_id", 'BIGINT'), ("wipe_bomb_forecast", 'TEXT'), ("aquarium_capacity", 'BIGINT'), ("exchange_account_status", 'BIGINT'), ("last_electric_fish_time", 'TIMESTAMPTZ'), ("in_wheel_of_fate", 'SMALLINT'), ("wof_current_level", 'BIGINT'), ("wof_current_prize", 'BIGINT'), ("wof_entry_fee", 'BIGINT'), ("last_wof_play_time", 'TIMESTAMPTZ'), ("wof_last_action_time", 'TIMESTAMPTZ'), ("wof_plays_today", 'BIGINT'), ("last_wof_date", 'TEXT'), ("last_sicbo_time", 'TIMESTAMPTZ'), ("max_wipe_bomb_multiplier", 'DOUBLE PRECISION'), ("min_wipe_bomb_multiplier", 'DOUBLE PRECISION'), ("wipe_bomb_attempts_today", 'BIGINT'), ("last_wipe_bomb_date", 'TEXT'), ("max_coins", 'BIGINT'), ("exchange_capacity_level", 'BIGINT')],
    "wipe_bomb_log": [("log_id", 'BIGINT'), ("user_id", 'TEXT'), ("contribution_amount", 'BIGINT'), ("reward_multiplier", 'DOUBLE PRECISION'), ("reward_amount", 'BIGINT'), ("timestamp", 'TIMESTAMPTZ')],
    "zone_fish_mapping": [("zone_id", 'BIGINT'), ("fish_id", 'BIGINT')],
}

TABLE_IDENTITY = {
    "accessories": "accessory_id",
    "aquarium_upgrades": "upgrade_id",
    "baits": "bait_id",
    "bank_fixed_deposits": "deposit_id",
    "bank_transactions": "transaction_id",
    "bank_withdraw_reservations": "reservation_id",
    "exchange_prices": "price_id",
    "fish": "fish_id",
    "fishing_records": "record_id",
    "fishing_zones": "id",
    "gacha_pool_items": "gacha_pool_item_id",
    "gacha_pools": "gacha_pool_id",
    "gacha_records": "record_id",
    "items": "item_id",
    "loans": "loan_id",
    "market": "market_id",
    "red_packet_records": "record_id",
    "red_packets": "packet_id",
    "rods": "rod_id",
    "shop_item_costs": "cost_id",
    "shop_item_rewards": "reward_id",
    "shop_items": "item_id",
    "shop_purchase_records": "record_id",
    "shops": "shop_id",
    "taxes": "tax_id",
    "titles": "title_id",
    "user_accessories": "accessory_instance_id",
    "user_buffs": "id",
    "user_commodities": "instance_id",
    "user_rods": "rod_instance_id",
    "wipe_bomb_log": "log_id",
}

TABLE_INSERT_SQL = {
    "accessories": """INSERT INTO "accessories" ("accessory_id", "name", "description", "rarity", "slot_type", "bonus_fish_quality_modifier", "bonus_fish_quantity_modifier", "bonus_rare_fish_chance", "bonus_coin_modifier", "other_bonus_description", "icon_url") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "aquarium_upgrades": """INSERT INTO "aquarium_upgrades" ("upgrade_id", "level", "capacity", "cost_coins", "cost_premium", "description", "created_at") VALUES (?, ?, ?, ?, ?, ?, ?)""",
    "baits": """INSERT INTO "baits" ("bait_id", "name", "description", "rarity", "effect_description", "duration_minutes", "cost", "required_rod_rarity", "success_rate_modifier", "rare_chance_modifier", "garbage_reduction_modifier", "value_modifier", "quantity_modifier", "is_consumable", "weight_modifier") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "bank_accounts": """INSERT INTO "bank_accounts" ("user_id", "balance", "today_withdrawn", "last_withdraw_reset_date", "created_at", "updated_at", "locked_balance") VALUES (?, ?, ?, ?, ?, ?, ?)""",
    "bank_fixed_deposits": """INSERT INTO "bank_fixed_deposits" ("deposit_id", "user_id", "principal", "term_days", "interest_rate", "expected_interest", "status", "started_at", "matures_at", "completed_at", "created_at", "updated_at") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "bank_transactions": """INSERT INTO "bank_transactions" ("transaction_id", "user_id", "tx_type", "amount", "wallet_delta", "bank_delta", "wallet_after", "bank_after", "locked_after", "ref_id", "remark", "created_at") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "bank_withdraw_reservations": """INSERT INTO "bank_withdraw_reservations" ("reservation_id", "user_id", "amount", "fee_amount", "status", "ready_at", "created_at", "updated_at", "expires_at") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "check_ins": """INSERT INTO "check_ins" ("user_id", "check_in_date") VALUES (?, ?)""",
    "commodities": """INSERT INTO "commodities" ("commodity_id", "name", "description") VALUES (?, ?, ?)""",
    "exchange_prices": """INSERT INTO "exchange_prices" ("price_id", "date", "time", "commodity_id", "price", "update_type", "created_at") VALUES (?, ?, ?, ?, ?, ?, ?)""",
    "fish": """INSERT INTO "fish" ("fish_id", "name", "description", "rarity", "base_value", "min_weight", "max_weight", "icon_url") VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
    "fishing_records": """INSERT INTO "fishing_records" ("record_id", "user_id", "fish_id", "weight", "value", "rod_instance_id", "accessory_instance_id", "bait_id", "timestamp", "location_id", "is_king_size") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "fishing_zones": """INSERT INTO "fishing_zones" ("id", "name", "description", "daily_rare_fish_quota", "rare_fish_caught_today", "configs", "is_active", "available_from", "available_until", "required_item_id", "requires_pass", "fishing_cost") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "gacha_pool_items": """INSERT INTO "gacha_pool_items" ("gacha_pool_item_id", "gacha_pool_id", "item_type", "item_id", "quantity", "weight") VALUES (?, ?, ?, ?, ?, ?)""",
    "gacha_pools": """INSERT INTO "gacha_pools" ("gacha_pool_id", "name", "description", "cost_coins", "cost_premium_currency", "is_limited_time", "open_until") VALUES (?, ?, ?, ?, ?, ?, ?)""",
    "gacha_records": """INSERT INTO "gacha_records" ("record_id", "user_id", "gacha_pool_id", "item_type", "item_id", "item_name", "quantity", "rarity", "timestamp") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "items": """INSERT INTO "items" ("item_id", "name", "description", "rarity", "effect_description", "cost", "is_consumable", "icon_url", "effect_type", "effect_payload") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "loans": """INSERT INTO "loans" ("loan_id", "lender_id", "borrower_id", "principal", "interest_rate", "borrowed_at", "due_amount", "repaid_amount", "status", "due_date", "created_at", "updated_at") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "market": """INSERT INTO "market" ("market_id", "user_id", "item_type", "item_id", "quantity", "price", "listed_at", "expires_at", "refine_level", "seller_nickname", "item_name", "item_description", "item_instance_id", "is_anonymous", "quality_level") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "red_packet_records": """INSERT INTO "red_packet_records" ("record_id", "packet_id", "user_id", "amount", "claimed_at") VALUES (?, ?, ?, ?, ?)""",
    "red_packets": """INSERT INTO "red_packets" ("packet_id", "sender_id", "group_id", "packet_type", "total_amount", "total_count", "remaining_amount", "remaining_count", "password", "created_at", "expires_at", "is_expired") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "rods": """INSERT INTO "rods" ("rod_id", "name", "description", "rarity", "source", "purchase_cost", "bonus_fish_quality_modifier", "bonus_fish_quantity_modifier", "bonus_rare_fish_chance", "durability", "icon_url") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "shop_item_costs": """INSERT INTO "shop_item_costs" ("cost_id", "item_id", "cost_type", "cost_amount", "cost_item_id", "cost_relation", "group_id", "quality_level") VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
    "shop_item_rewards": """INSERT INTO "shop_item_rewards" ("reward_id", "item_id", "reward_type", "reward_item_id", "reward_quantity", "reward_refine_level", "quality_level") VALUES (?, ?, ?, ?, ?, ?, ?)""",
    "shop_items": """INSERT INTO "shop_items" ("item_id", "shop_id", "name", "description", "category", "stock_total", "stock_sold", "per_user_limit", "per_user_daily_limit", "is_active", "start_time", "end_time", "sort_order", "created_at", "updated_at") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "shop_purchase_records": """INSERT INTO "shop_purchase_records" ("record_id", "user_id", "item_id", "quantity", "timestamp") VALUES (?, ?, ?, ?, ?)""",
    "shops": """INSERT INTO "shops" ("shop_id", "name", "description", "shop_type", "is_active", "start_time", "end_time", "daily_start_time", "daily_end_time", "sort_order", "created_at", "updated_at") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "tax_debts": """INSERT INTO "tax_debts" ("user_id", "debt_amount", "created_at", "updated_at", "last_accrued_date") VALUES (?, ?, ?, ?, ?)""",
    "taxes": """INSERT INTO "taxes" ("tax_id", "user_id", "tax_amount", "tax_rate", "original_amount", "timestamp", "tax_type", "balance_after") VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
    "titles": """INSERT INTO "titles" ("title_id", "name", "description", "display_format") VALUES (?, ?, ?, ?)""",
    "user_accessories": """INSERT INTO "user_accessories" ("accessory_instance_id", "user_id", "accessory_id", "obtained_at", "is_equipped", "refine_level", "is_locked", "display_code") VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
    "user_achievement_progress": """INSERT INTO "user_achievement_progress" ("user_id", "achievement_id", "current_progress", "completed_at", "claimed_at") VALUES (?, ?, ?, ?, ?)""",
    "user_aquarium": """INSERT INTO "user_aquarium" ("user_id", "fish_id", "quality_level", "quantity", "added_at") VALUES (?, ?, ?, ?, ?)""",
    "user_bait_inventory": """INSERT INTO "user_bait_inventory" ("user_id", "bait_id", "quantity") VALUES (?, ?, ?)""",
    "user_buffs": """INSERT INTO "user_buffs" ("id", "user_id", "buff_type", "payload", "started_at", "expires_at") VALUES (?, ?, ?, ?, ?, ?)""",
    "user_commodities": """INSERT INTO "user_commodities" ("instance_id", "user_id", "commodity_id", "quantity", "purchase_price", "purchased_at", "expires_at") VALUES (?, ?, ?, ?, ?, ?, ?)""",
    "user_fish_inventory": """INSERT INTO "user_fish_inventory" ("user_id", "fish_id", "quality_level", "quantity", "no_sell_until") VALUES (?, ?, ?, ?, ?)""",
    "user_fish_stats": """INSERT INTO "user_fish_stats" ("user_id", "fish_id", "first_caught_at", "last_caught_at", "max_weight", "min_weight", "total_caught", "total_weight") VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
    "user_items": """INSERT INTO "user_items" ("user_id", "item_id", "quantity") VALUES (?, ?, ?)""",
    "user_rods": """INSERT INTO "user_rods" ("rod_instance_id", "user_id", "rod_id", "obtained_at", "current_durability", "is_equipped", "refine_level", "is_locked", "display_code") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "user_titles": """INSERT INTO "user_titles" ("user_id", "title_id", "unlocked_at") VALUES (?, ?, ?)""",
    "users": """INSERT INTO "users" ("user_id", "nickname", "coins", "premium_currency", "total_fishing_count", "total_weight_caught", "total_coins_earned", "equipped_rod_instance_id", "equipped_accessory_instance_id", "current_bait_id", "bait_start_time", "current_title_id", "auto_fishing_enabled", "last_fishing_time", "last_wipe_bomb_time", "last_steal_time", "created_at", "last_login_time", "consecutive_login_days", "fish_pond_capacity", "last_stolen_at", "fishing_zone_id", "wipe_bomb_forecast", "aquarium_capacity", "exchange_account_status", "last_electric_fish_time", "in_wheel_of_fate", "wof_current_level", "wof_current_prize", "wof_entry_fee", "last_wof_play_time", "wof_last_action_time", "wof_plays_today", "last_wof_date", "last_sicbo_time", "max_wipe_bomb_multiplier", "min_wipe_bomb_multiplier", "wipe_bomb_attempts_today", "last_wipe_bomb_date", "max_coins", "exchange_capacity_level") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    "wipe_bomb_log": """INSERT INTO "wipe_bomb_log" ("log_id", "user_id", "contribution_amount", "reward_multiplier", "reward_amount", "timestamp") VALUES (?, ?, ?, ?, ?, ?)""",
    "zone_fish_mapping": """INSERT INTO "zone_fish_mapping" ("zone_id", "fish_id") VALUES (?, ?)""",
}

TABLE_NAMES = [
    "accessories",
    "aquarium_upgrades",
    "baits",
    "bank_accounts",
    "bank_fixed_deposits",
    "bank_transactions",
    "bank_withdraw_reservations",
    "check_ins",
    "commodities",
    "exchange_prices",
    "fish",
    "fishing_records",
    "fishing_zones",
    "gacha_pool_items",
    "gacha_pools",
    "gacha_records",
    "items",
    "loans",
    "market",
    "red_packet_records",
    "red_packets",
    "rods",
    "shop_item_costs",
    "shop_item_rewards",
    "shop_items",
    "shop_purchase_records",
    "shops",
    "tax_debts",
    "taxes",
    "titles",
    "user_accessories",
    "user_achievement_progress",
    "user_aquarium",
    "user_bait_inventory",
    "user_buffs",
    "user_commodities",
    "user_fish_inventory",
    "user_fish_stats",
    "user_items",
    "user_rods",
    "user_titles",
    "users",
    "wipe_bomb_log",
    "zone_fish_mapping",
]

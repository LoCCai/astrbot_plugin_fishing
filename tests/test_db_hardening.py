"""数据库加固相关回归测试。

覆盖三块改动：
1. 九个仓储统一走 DatabaseConnectionManager（30s busy timeout、synchronous=NORMAL，
   且各仓储原有的 detect_types / 外键 / 行工厂语义不变）。
2. 钓鱼记录 30 天全局清理移出每钓写事务（settle 不再顺带清理他行的过期记录），
   改由 cleanup_old_fishing_records 低频执行。
3. 迁移 049 补齐 idx_fishing_records_timestamp 与 idx_users_auto_fishing。
"""

import contextlib
import gc
import importlib.util
import sqlite3
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "core" / "database" / "migrations"


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


def _install_astrbot_stub():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = _Logger()
    astrbot.api = api
    sys.modules.setdefault("astrbot", astrbot)
    sys.modules.setdefault("astrbot.api", api)


@contextlib.contextmanager
def _sqlite(db_path):
    """提交并关闭的连接助手（避免 Windows 上临时目录删除失败）。"""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class _ClosesConnectionsMixin:
    """测试结束前显式关闭仓储持有的线程本地连接。"""

    def setUp(self):
        super().setUp()
        self._tracked_closables = []

    def tearDown(self):
        self._close_tracked()
        super().tearDown()

    def _close_tracked(self):
        for closable in self._tracked_closables:
            try:
                closable.close_connection()
            except Exception:
                pass
        self._tracked_closables.clear()

    def _track(self, closable):
        self._tracked_closables.append(closable)
        return closable

    @contextlib.contextmanager
    def temp_workspace(self):
        """临时目录 + 退出前关闭其中打开的所有仓储连接。

        tearDown 里关来不及：那时 TemporaryDirectory 已经在删目录了
        （Windows 上 fish.db 仍被占用会报 PermissionError）。
        """
        with TemporaryDirectory() as temp_dir:
            try:
                yield temp_dir
            finally:
                self._close_tracked()


class ConnectionManagerUnificationTests(_ClosesConnectionsMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_astrbot_stub()
        cls.user_module = importlib.import_module("core.repositories.sqlite_user_repo")
        cls.loan_module = importlib.import_module("core.repositories.sqlite_loan_repo")
        cls.exchange_module = importlib.import_module("core.repositories.sqlite_exchange_repo")

    def test_repos_delegate_with_30s_busy_timeout(self):
        with TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "fish.db")
            user_repo = self._track(self.user_module.SqliteUserRepository(db_path))
            loan_repo = self._track(self.loan_module.SqliteLoanRepository(db_path))
            exchange_repo = self._track(self.exchange_module.SqliteExchangeRepository(db_path))

            self.assertEqual(user_repo._connection_manager.timeout, 30)
            self.assertEqual(loan_repo._connection_manager.timeout, 30)
            self.assertEqual(exchange_repo._connection_manager.timeout, 30)

    def test_loan_repo_keeps_detect_types_disabled(self):
        """借贷仓储依赖手动解析时间，detect_types 必须保持关闭。"""
        with TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "fish.db")
            loan_repo = self._track(self.loan_module.SqliteLoanRepository(db_path))
            self.assertEqual(loan_repo._connection_manager.detect_types, 0)

    def test_exchange_repo_keeps_tuple_rows_without_foreign_keys(self):
        """交易所仓储按位置解包元组且历史上未开启外键，语义必须保留。"""
        with TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "fish.db")
            exchange_repo = self._track(self.exchange_module.SqliteExchangeRepository(db_path))
            manager = exchange_repo._connection_manager
            self.assertIsNone(manager.row_factory)
            self.assertFalse(manager.foreign_keys)
            self.assertEqual(manager.detect_types, 0)

    def test_thread_local_connection_is_reused_and_usable(self):
        with TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "fish.db")
            user_repo = self._track(self.user_module.SqliteUserRepository(db_path))

            first = user_repo._get_connection()
            self.assertIs(first, user_repo._get_connection())

            with first as conn:
                conn.execute("CREATE TABLE t (v INTEGER)")
            with user_repo._get_connection() as conn:
                conn.execute("INSERT INTO t VALUES (1)")
            user_repo.close_connection()


class FishingRecordsCleanupTests(_ClosesConnectionsMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_astrbot_stub()
        cls.inventory_module = importlib.import_module(
            "core.repositories.sqlite_inventory_repo"
        )

    def _create_database(self, db_path: Path):
        with _sqlite(db_path) as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE users (
                    user_id TEXT PRIMARY KEY, coins INTEGER NOT NULL DEFAULT 0,
                    max_coins INTEGER NOT NULL DEFAULT 0,
                    auto_fishing_enabled INTEGER NOT NULL DEFAULT 0,
                    total_fishing_count INTEGER NOT NULL DEFAULT 0,
                    total_weight_caught INTEGER NOT NULL DEFAULT 0,
                    total_coins_earned INTEGER NOT NULL DEFAULT 0,
                    last_fishing_time DATETIME,
                    equipped_rod_instance_id INTEGER
                );
                CREATE TABLE fish (
                    fish_id INTEGER PRIMARY KEY, name TEXT, rarity INTEGER,
                    base_value INTEGER NOT NULL
                );
                CREATE TABLE user_fish_inventory (
                    user_id TEXT, fish_id INTEGER, quality_level INTEGER, quantity INTEGER,
                    PRIMARY KEY (user_id, fish_id, quality_level)
                );
                CREATE TABLE user_aquarium (
                    user_id TEXT, fish_id INTEGER, quality_level INTEGER, quantity INTEGER,
                    PRIMARY KEY (user_id, fish_id, quality_level)
                );
                CREATE TABLE user_rods (
                    rod_instance_id INTEGER PRIMARY KEY, user_id TEXT, rod_id INTEGER,
                    current_durability INTEGER, is_equipped INTEGER, is_locked INTEGER
                );
                CREATE TABLE user_accessories (
                    accessory_instance_id INTEGER PRIMARY KEY, user_id TEXT,
                    accessory_id INTEGER, is_equipped INTEGER, is_locked INTEGER
                );
                CREATE TABLE fishing_zones (
                    id INTEGER PRIMARY KEY, rare_fish_caught_today INTEGER
                );
                CREATE TABLE fishing_records (
                    record_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, fish_id INTEGER,
                    weight INTEGER, value INTEGER, rod_instance_id INTEGER,
                    accessory_instance_id INTEGER, bait_id INTEGER,
                    timestamp DATETIME, is_king_size INTEGER
                );
                CREATE TABLE user_fish_stats (
                    user_id TEXT, fish_id INTEGER, first_caught_at DATETIME,
                    last_caught_at DATETIME, max_weight INTEGER, min_weight INTEGER,
                    total_caught INTEGER, total_weight INTEGER,
                    PRIMARY KEY (user_id, fish_id)
                );
                INSERT INTO users (user_id, coins, max_coins) VALUES ('u1', 100, 100);
                INSERT INTO fish VALUES (1, 'fish', 4, 10);
                INSERT INTO fishing_zones VALUES (1, 0);
                """
            )

    def _insert_record(self, db_path: Path, user_id: str, timestamp: str):
        with _sqlite(db_path) as conn:
            conn.execute(
                "INSERT INTO fishing_records (user_id, fish_id, weight, value, timestamp, is_king_size)"
                " VALUES (?, 1, 1, 1, ?, 0)",
                (user_id, timestamp),
            )

    def test_settlement_keeps_expired_records_of_other_users(self):
        """过期清理已移出每钓事务：结算不得再顺带删除其他用户的过期记录。"""
        with self.temp_workspace() as temp_dir:
            db_path = Path(temp_dir) / "fish.db"
            self._create_database(db_path)
            self._insert_record(db_path, "legacy", "2000-01-01 00:00:00")

            repo = self._track(self.inventory_module.SqliteInventoryRepository(str(db_path)))
            self.assertTrue(
                repo.settle_fishing_catch(
                    user_id="u1", fish_id=1, total_catches=1, quality_level=0,
                    weight=20, base_value=10, earned_value=10, fishing_cost=10,
                    fish_pond_capacity=10, timestamp=datetime.now(),
                    zone_id=1, is_rare=False, rod_instance_id=None,
                    rod_durability=None, rod_broken=False,
                    accessory_instance_id=None, bait_id=None,
                )
            )

            with _sqlite(db_path) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM fishing_records WHERE user_id = 'legacy'"
                    ).fetchone()[0],
                    1,
                )

    def test_cleanup_old_fishing_records_only_deletes_expired_rows(self):
        with self.temp_workspace() as temp_dir:
            db_path = Path(temp_dir) / "fish.db"
            self._create_database(db_path)
            self._insert_record(db_path, "u1", "2000-01-01 00:00:00")
            recent = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            self._insert_record(db_path, "u1", recent)

            repo = self._track(self.inventory_module.SqliteInventoryRepository(str(db_path)))
            removed = repo.cleanup_old_fishing_records(days=30)

            self.assertEqual(removed, 1)
            with _sqlite(db_path) as conn:
                remaining = {
                    row[0] for row in conn.execute("SELECT timestamp FROM fishing_records")
                }
            self.assertEqual(remaining, {recent})


class Migration049Tests(unittest.TestCase):
    """迁移 049：热点索引建立且可重复执行。"""

    def _load_migration(self):
        migration_path = MIGRATIONS_DIR / "049_add_fishing_performance_indexes.py"
        spec = importlib.util.spec_from_file_location("fishing_migration_049", migration_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_up_creates_indexes_idempotently(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "fish.db"
            with _sqlite(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE users (
                        user_id TEXT PRIMARY KEY, auto_fishing_enabled INTEGER DEFAULT 0
                    );
                    CREATE TABLE fishing_records (
                        record_id INTEGER PRIMARY KEY, timestamp DATETIME
                    );
                    """
                )

            migration = self._load_migration()
            with _sqlite(db_path) as conn:
                migration.up(conn.cursor())
            # 二次执行不应报错（IF NOT EXISTS）
            with _sqlite(db_path) as conn:
                migration.up(conn.cursor())

            with _sqlite(db_path) as conn:
                names = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
                }
                self.assertIn("idx_fishing_records_timestamp", names)
                self.assertIn("idx_users_auto_fishing", names)
                partial_sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'idx_users_auto_fishing'"
                ).fetchone()[0]
                self.assertIn("auto_fishing_enabled = 1", partial_sql)

    def test_auto_fishing_query_uses_partial_index(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "fish.db"
            with _sqlite(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE users (
                        user_id TEXT PRIMARY KEY, auto_fishing_enabled INTEGER DEFAULT 0
                    );
                    CREATE TABLE fishing_records (
                        record_id INTEGER PRIMARY KEY, timestamp DATETIME
                    );
                    """
                )
            migration = self._load_migration()
            with _sqlite(db_path) as conn:
                migration.up(conn.cursor())
                plan = conn.execute(
                    "EXPLAIN QUERY PLAN SELECT user_id FROM users WHERE auto_fishing_enabled = 1"
                ).fetchall()
            self.assertTrue(any("idx_users_auto_fishing" in str(row) for row in plan))


class MigrationChainTests(unittest.TestCase):
    """在全新空库上从 001 顺序跑到 049，验证迁移链路完整可执行。"""

    def test_all_migrations_apply_on_fresh_database(self):
        _install_astrbot_stub()
        runner = importlib.import_module("core.database.migration")
        with TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "fish.db")
            runner.run_migrations(db_path, str(MIGRATIONS_DIR))
            # run_migrations 内部的连接依赖 GC 回收；Windows 上不回收的话
            # TemporaryDirectory 清理会因文件占用而失败
            gc.collect()

            with _sqlite(db_path) as conn:
                version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
                self.assertEqual(version, 49)
                names = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
                }
                self.assertIn("idx_fishing_records_timestamp", names)
                self.assertIn("idx_users_auto_fishing", names)


if __name__ == "__main__":
    unittest.main()

"""银行相关测试的公共脚手架。

表结构不手写，而是直接跑 041 与 045~048 号迁移的 up()，这样测试用的 schema
永远和真实迁移一致，迁移本身也顺带被覆盖到。
"""

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

MIGRATION_FILES = (
    "041_add_loan_system.py",
    "045_add_bank_system.py",
    "046_add_bank_fixed_deposits.py",
    "047_fix_bank_tax_and_reservations.py",
    "048_add_bank_ledger_and_debt_accrual.py",
)

BASE_SCHEMA = """
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    nickname TEXT,
    coins INTEGER NOT NULL DEFAULT 0,
    max_coins INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT '2026-01-01T00:00:00+08:00'
);
CREATE TABLE taxes (
    tax_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    tax_amount INTEGER NOT NULL,
    tax_rate REAL NOT NULL,
    original_amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    timestamp DATETIME NOT NULL,
    tax_type TEXT NOT NULL
);
"""


class _Logger:
    """吞掉日志，同时把 warning/error 留存下来供断言使用。"""

    def __init__(self):
        self.messages = {"info": [], "warning": [], "error": [], "debug": []}

    def info(self, *args, **kwargs):
        self.messages["info"].append(args[0] if args else "")

    def warning(self, *args, **kwargs):
        self.messages["warning"].append(args[0] if args else "")

    def error(self, *args, **kwargs):
        self.messages["error"].append(args[0] if args else "")

    def debug(self, *args, **kwargs):
        self.messages["debug"].append(args[0] if args else "")


def install_astrbot_stub() -> _Logger:
    logger = _Logger()
    astrbot = sys.modules.get("astrbot") or types.ModuleType("astrbot")
    api = sys.modules.get("astrbot.api") or types.ModuleType("astrbot.api")
    api.logger = logger
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    return logger


def _load_migration(path: Path):
    spec = importlib.util.spec_from_file_location(f"_mig_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_database(db_path: Path, users=(("u1", "测试玩家", 0, 0),)) -> None:
    """建库：基础表 + 真实迁移 + 测试用户。"""
    migrations_dir = Path(__file__).resolve().parents[1] / "core" / "database" / "migrations"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(BASE_SCHEMA)
        cursor = conn.cursor()
        for filename in MIGRATION_FILES:
            _load_migration(migrations_dir / filename).up(cursor)
        cursor.executemany(
            "INSERT INTO users (user_id, nickname, coins, max_coins) VALUES (?, ?, ?, ?)",
            users,
        )
        conn.commit()
    finally:
        conn.close()


def set_bank_state(db_path, user_id="u1", balance=0, today_withdrawn=0, locked=0, reset_date=None):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO bank_accounts
                (user_id, balance, locked_balance, today_withdrawn, last_withdraw_reset_date)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                balance = excluded.balance,
                locked_balance = excluded.locked_balance,
                today_withdrawn = excluded.today_withdrawn,
                last_withdraw_reset_date = excluded.last_withdraw_reset_date
            """,
            (user_id, balance, locked, today_withdrawn, reset_date),
        )
        conn.commit()
    finally:
        conn.close()


def set_wallet(db_path, user_id="u1", coins=0):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE users SET coins = ? WHERE user_id = ?", (coins, user_id))
        conn.commit()
    finally:
        conn.close()


def set_tax_debt(db_path, user_id="u1", debt_amount=0, last_accrued_date=None):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO tax_debts (user_id, debt_amount, last_accrued_date)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                debt_amount = excluded.debt_amount,
                last_accrued_date = excluded.last_accrued_date
            """,
            (user_id, debt_amount, last_accrued_date),
        )
        conn.commit()
    finally:
        conn.close()


def query_one(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def query_all(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def wallet_of(db_path, user_id="u1") -> int:
    return query_one(db_path, "SELECT coins FROM users WHERE user_id = ?", (user_id,))["coins"]


def bank_of(db_path, user_id="u1"):
    return query_one(db_path, "SELECT * FROM bank_accounts WHERE user_id = ?", (user_id,))


def debt_of(db_path, user_id="u1") -> int:
    row = query_one(db_path, "SELECT debt_amount FROM tax_debts WHERE user_id = ?", (user_id,))
    return row["debt_amount"] if row else 0

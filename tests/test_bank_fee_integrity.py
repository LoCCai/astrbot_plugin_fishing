"""银行手续费与违约金的计费时点回归测试。

覆盖两个已修复的计费缺陷：
1. 预约取款的手续费曾在下单时冻结，确认时不重算，导致同一份每日免费提现
   额度可以被预约和普通取款各用一次。
2. 手续费与违约金曾由服务层在事务外算好后传给仓储，仓储无条件采信。
"""

import importlib
import sqlite3
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _install_astrbot_stub():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = _Logger()
    astrbot.api = api
    sys.modules.setdefault("astrbot", astrbot)
    sys.modules.setdefault("astrbot.api", api)


FREE_LIMIT = 1_000_000
FEE_RATE = 0.03
RESET_DATE = "2026-07-30"


class BankFeeIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_astrbot_stub()
        cls.bank_module = importlib.import_module("core.repositories.sqlite_bank_repo")

    def _create_database(self, db_path: Path):
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE users (
                    user_id TEXT PRIMARY KEY,
                    nickname TEXT,
                    coins INTEGER NOT NULL DEFAULT 0,
                    max_coins INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE bank_accounts (
                    user_id TEXT PRIMARY KEY,
                    balance INTEGER NOT NULL DEFAULT 0,
                    locked_balance INTEGER NOT NULL DEFAULT 0,
                    today_withdrawn INTEGER NOT NULL DEFAULT 0,
                    last_withdraw_reset_date TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                );
                CREATE TABLE bank_withdraw_reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    fee_amount INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    ready_at DATETIME NOT NULL,
                    created_at DATETIME,
                    updated_at DATETIME
                );
                CREATE TABLE bank_fixed_deposits (
                    deposit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    principal INTEGER NOT NULL,
                    term_days INTEGER NOT NULL,
                    interest_rate REAL NOT NULL,
                    expected_interest INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    started_at DATETIME NOT NULL,
                    matures_at DATETIME NOT NULL,
                    completed_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                );
                CREATE TABLE tax_debts (
                    user_id TEXT PRIMARY KEY,
                    debt_amount INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                );
                INSERT INTO users (user_id, nickname, coins, max_coins)
                VALUES ('u1', '测试玩家', 0, 0);
                """
            )

    def _make_repo(self, temp_dir):
        db_path = Path(temp_dir) / "fish.db"
        self._create_database(db_path)
        return self.bank_module.SqliteBankRepository(str(db_path))

    def _set_bank_state(self, repo, balance, today_withdrawn=0, locked=0):
        with sqlite3.connect(repo.db_path) as conn:
            conn.execute(
                """
                INSERT INTO bank_accounts
                    (user_id, balance, locked_balance, today_withdrawn, last_withdraw_reset_date)
                VALUES ('u1', ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    balance = excluded.balance,
                    locked_balance = excluded.locked_balance,
                    today_withdrawn = excluded.today_withdrawn,
                    last_withdraw_reset_date = excluded.last_withdraw_reset_date
                """,
                (balance, locked, today_withdrawn, RESET_DATE),
            )

    def _wallet(self, repo):
        with sqlite3.connect(repo.db_path) as conn:
            return conn.execute("SELECT coins FROM users WHERE user_id = 'u1'").fetchone()[0]

    def test_reservation_fee_is_recalculated_when_confirmed(self):
        """预约时免费额度未用（预估费 120,000），确认时额度已被用光，应按 150,000 收取。"""
        with TemporaryDirectory() as temp_dir:
            repo = self._make_repo(temp_dir)
            self._set_bank_state(repo, balance=6_000_000, today_withdrawn=0)

            ok, _, reservation = repo.create_reservation(
                "u1",
                amount=5_000_000,
                fee_amount=120_000,  # 下单时的预估：(500万 - 100万免费) * 3%
                ready_at=datetime.now() - timedelta(minutes=1),
                max_pending=1,
            )
            self.assertTrue(ok)
            self.assertEqual(reservation.fee_amount, 120_000)

            # 确认前免费额度已被当日的普通取款用光
            self._set_bank_state(repo, balance=6_000_000, today_withdrawn=FREE_LIMIT, locked=5_000_000)

            ok, _, reservation, account, wallet_after, debt_paid = repo.complete_pending_reservation(
                "u1", RESET_DATE, FREE_LIMIT, FEE_RATE
            )

            self.assertTrue(ok)
            self.assertEqual(reservation.fee_amount, 150_000)
            self.assertEqual(wallet_after, 5_000_000 - 150_000)
            self.assertEqual(debt_paid, 0)
            self.assertEqual(account.balance, 1_000_000)
            self.assertEqual(account.locked_balance, 0)

    def test_reservation_fee_drops_when_daily_limit_resets(self):
        """预约时额度已用光（预估费 150,000），确认时跨日重置，应只收 120,000。"""
        with TemporaryDirectory() as temp_dir:
            repo = self._make_repo(temp_dir)
            self._set_bank_state(repo, balance=6_000_000, today_withdrawn=FREE_LIMIT)

            ok, _, _ = repo.create_reservation(
                "u1",
                amount=5_000_000,
                fee_amount=150_000,
                ready_at=datetime.now() - timedelta(minutes=1),
                max_pending=1,
            )
            self.assertTrue(ok)

            ok, _, reservation, _, wallet_after, _ = repo.complete_pending_reservation(
                "u1", "2026-07-31", FREE_LIMIT, FEE_RATE
            )

            self.assertTrue(ok)
            self.assertEqual(reservation.fee_amount, 120_000)
            self.assertEqual(wallet_after, 5_000_000 - 120_000)

    def test_consecutive_withdrawals_share_one_daily_free_limit(self):
        """两笔取款必须共用同一份免费额度，第二笔按事务内的 today_withdrawn 计费。"""
        with TemporaryDirectory() as temp_dir:
            repo = self._make_repo(temp_dir)
            self._set_bank_state(repo, balance=3_000_000, today_withdrawn=0)

            ok, _, account, _, first_fee, _ = repo.withdraw(
                "u1", 600_000, RESET_DATE, FREE_LIMIT, FEE_RATE
            )
            self.assertTrue(ok)
            self.assertEqual(first_fee, 0)
            self.assertEqual(account.today_withdrawn, 600_000)

            ok, _, account, wallet_after, second_fee, _ = repo.withdraw(
                "u1", 600_000, RESET_DATE, FREE_LIMIT, FEE_RATE
            )
            self.assertTrue(ok)
            # 剩余免费额度 400,000，应计费部分 200,000
            self.assertEqual(second_fee, 6_000)
            self.assertEqual(account.today_withdrawn, 1_200_000)
            self.assertEqual(wallet_after, 600_000 + 600_000 - 6_000)

    def test_withdraw_free_limit_resets_across_days(self):
        with TemporaryDirectory() as temp_dir:
            repo = self._make_repo(temp_dir)
            self._set_bank_state(repo, balance=3_000_000, today_withdrawn=FREE_LIMIT)

            ok, _, _, _, fee_same_day, _ = repo.withdraw(
                "u1", 500_000, RESET_DATE, FREE_LIMIT, FEE_RATE
            )
            self.assertTrue(ok)
            self.assertEqual(fee_same_day, 15_000)

            ok, _, account, _, fee_next_day, _ = repo.withdraw(
                "u1", 500_000, "2026-07-31", FREE_LIMIT, FEE_RATE
            )
            self.assertTrue(ok)
            self.assertEqual(fee_next_day, 0)
            self.assertEqual(account.today_withdrawn, 500_000)

    def _insert_fixed_deposit(self, repo, principal):
        now = datetime.now()
        with sqlite3.connect(repo.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO bank_fixed_deposits
                    (user_id, principal, term_days, interest_rate, expected_interest,
                     status, started_at, matures_at)
                VALUES ('u1', ?, 30, 0.05, ?, 'active', ?, ?)
                """,
                (principal, int(principal * 0.05), now, now + timedelta(days=30)),
            )
            return cursor.lastrowid

    def test_early_withdraw_penalty_uses_stored_principal(self):
        with TemporaryDirectory() as temp_dir:
            repo = self._make_repo(temp_dir)
            self._set_bank_state(repo, balance=0)
            deposit_id = self._insert_fixed_deposit(repo, 1_500_000)

            ok, _, deposit, account, penalty, _ = repo.cancel_fixed_deposit(
                "u1", deposit_id, penalty_rate=0.01, penalty_threshold=1_000_000
            )

            self.assertTrue(ok)
            self.assertEqual(penalty, 15_000)
            self.assertEqual(account.balance, 1_500_000 - 15_000)
            self.assertEqual(deposit.status, "cancelled")

    def test_early_withdraw_penalty_skipped_at_threshold(self):
        with TemporaryDirectory() as temp_dir:
            repo = self._make_repo(temp_dir)
            self._set_bank_state(repo, balance=0)
            deposit_id = self._insert_fixed_deposit(repo, 1_000_000)

            ok, _, _, account, penalty, _ = repo.cancel_fixed_deposit(
                "u1", deposit_id, penalty_rate=0.01, penalty_threshold=1_000_000
            )

            self.assertTrue(ok)
            self.assertEqual(penalty, 0)
            self.assertEqual(account.balance, 1_000_000)

    def test_early_withdraw_penalty_never_exceeds_principal(self):
        """即使配置了荒谬的违约金比例，也不能扣穿本金。"""
        with TemporaryDirectory() as temp_dir:
            repo = self._make_repo(temp_dir)
            self._set_bank_state(repo, balance=0)
            deposit_id = self._insert_fixed_deposit(repo, 2_000_000)

            ok, _, _, account, penalty, _ = repo.cancel_fixed_deposit(
                "u1", deposit_id, penalty_rate=5.0, penalty_threshold=1_000_000
            )

            self.assertTrue(ok)
            self.assertEqual(penalty, 2_000_000)
            self.assertEqual(account.balance, 0)
            self.assertEqual(self._wallet(repo), 0)


if __name__ == "__main__":
    unittest.main()

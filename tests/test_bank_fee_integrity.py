"""银行手续费与违约金的计费时点回归测试。

覆盖两个已修复的计费缺陷：
1. 预约取款的手续费曾在下单时冻结，确认时不重算，导致同一份每日免费提现
   额度可以被预约和普通取款各用一次。
2. 手续费与违约金曾由服务层在事务外算好后传给仓储，仓储无条件采信。
"""

import importlib
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bank_test_utils as helpers  # noqa: E402

FREE_LIMIT = 1_000_000
FEE_RATE = 0.03
RESET_DATE = "2026-07-30"


class BankFeeIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helpers.install_astrbot_stub()
        cls.bank_module = importlib.import_module("core.repositories.sqlite_bank_repo")
        cls.utils = importlib.import_module("core.utils")

    def setUp(self):
        self._temp = TemporaryDirectory()
        self.db_path = Path(self._temp.name) / "fish.db"
        helpers.create_database(self.db_path)
        self.repo = self.bank_module.SqliteBankRepository(str(self.db_path))

    def tearDown(self):
        # 连接是线程本地长连接，不关掉的话 Windows 上删不掉临时目录
        self.repo.close_connection()
        self._temp.cleanup()

    def _set_bank_state(self, balance, today_withdrawn=0, locked=0):
        helpers.set_bank_state(
            self.db_path, balance=balance, today_withdrawn=today_withdrawn,
            locked=locked, reset_date=RESET_DATE,
        )

    def _insert_fixed_deposit(self, principal, term_days=30):
        now = self.utils.get_now()
        rows = helpers.query_all(self.db_path, "SELECT 1")  # 保证库已建好
        del rows
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                INSERT INTO bank_fixed_deposits
                    (user_id, principal, term_days, interest_rate, expected_interest,
                     status, started_at, matures_at)
                VALUES ('u1', ?, ?, 0.05, ?, 'active', ?, ?)
                """,
                (principal, term_days, int(principal * 0.05), now, now + timedelta(days=term_days)),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def test_reservation_fee_is_recalculated_when_confirmed(self):
        """预约时免费额度未用（预估费 120,000），确认时额度已被用光，应按 150,000 收取。"""
        self._set_bank_state(balance=6_000_000, today_withdrawn=0)
        now = self.utils.get_now()

        ok, _, reservation = self.repo.create_reservation(
            "u1",
            amount=5_000_000,
            fee_amount=120_000,  # 下单时的预估：(500万 - 100万免费) * 3%
            ready_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=72),
            max_pending=1,
        )
        self.assertTrue(ok)
        self.assertEqual(reservation.fee_amount, 120_000)

        # 确认前免费额度已被当日的普通取款用光
        self._set_bank_state(balance=6_000_000, today_withdrawn=FREE_LIMIT, locked=5_000_000)

        ok, _, reservation, account, wallet_after, debt_paid = self.repo.complete_pending_reservation(
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
        self._set_bank_state(balance=6_000_000, today_withdrawn=FREE_LIMIT)
        now = self.utils.get_now()

        ok, _, _ = self.repo.create_reservation(
            "u1",
            amount=5_000_000,
            fee_amount=150_000,
            ready_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=72),
            max_pending=1,
        )
        self.assertTrue(ok)

        ok, _, reservation, _, wallet_after, _ = self.repo.complete_pending_reservation(
            "u1", "2026-07-31", FREE_LIMIT, FEE_RATE
        )

        self.assertTrue(ok)
        self.assertEqual(reservation.fee_amount, 120_000)
        self.assertEqual(wallet_after, 5_000_000 - 120_000)

    def test_consecutive_withdrawals_share_one_daily_free_limit(self):
        """两笔取款必须共用同一份免费额度，第二笔按事务内的 today_withdrawn 计费。"""
        self._set_bank_state(balance=3_000_000, today_withdrawn=0)

        ok, _, account, _, first_fee, _ = self.repo.withdraw(
            "u1", 600_000, RESET_DATE, FREE_LIMIT, FEE_RATE
        )
        self.assertTrue(ok)
        self.assertEqual(first_fee, 0)
        self.assertEqual(account.today_withdrawn, 600_000)

        ok, _, account, wallet_after, second_fee, _ = self.repo.withdraw(
            "u1", 600_000, RESET_DATE, FREE_LIMIT, FEE_RATE
        )
        self.assertTrue(ok)
        # 剩余免费额度 400,000，应计费部分 200,000
        self.assertEqual(second_fee, 6_000)
        self.assertEqual(account.today_withdrawn, 1_200_000)
        self.assertEqual(wallet_after, 600_000 + 600_000 - 6_000)

    def test_withdraw_free_limit_resets_across_days(self):
        self._set_bank_state(balance=3_000_000, today_withdrawn=FREE_LIMIT)

        ok, _, _, _, fee_same_day, _ = self.repo.withdraw(
            "u1", 500_000, RESET_DATE, FREE_LIMIT, FEE_RATE
        )
        self.assertTrue(ok)
        self.assertEqual(fee_same_day, 15_000)

        ok, _, account, _, fee_next_day, _ = self.repo.withdraw(
            "u1", 500_000, "2026-07-31", FREE_LIMIT, FEE_RATE
        )
        self.assertTrue(ok)
        self.assertEqual(fee_next_day, 0)
        self.assertEqual(account.today_withdrawn, 500_000)

    def test_early_withdraw_penalty_uses_stored_principal(self):
        self._set_bank_state(balance=0)
        deposit_id = self._insert_fixed_deposit(1_500_000)

        ok, _, deposit, account, penalty, _ = self.repo.cancel_fixed_deposit(
            "u1", deposit_id, penalty_rate=0.01, penalty_threshold=1_000_000
        )

        self.assertTrue(ok)
        self.assertEqual(penalty, 15_000)
        self.assertEqual(account.balance, 1_500_000 - 15_000)
        self.assertEqual(deposit.status, "cancelled")

    def test_early_withdraw_penalty_skipped_at_threshold(self):
        self._set_bank_state(balance=0)
        deposit_id = self._insert_fixed_deposit(1_000_000)

        ok, _, _, account, penalty, _ = self.repo.cancel_fixed_deposit(
            "u1", deposit_id, penalty_rate=0.01, penalty_threshold=1_000_000
        )

        self.assertTrue(ok)
        self.assertEqual(penalty, 0)
        self.assertEqual(account.balance, 1_000_000)

    def test_early_withdraw_penalty_never_exceeds_principal(self):
        """即使配置了荒谬的违约金比例，也不能扣穿本金。"""
        self._set_bank_state(balance=0)
        deposit_id = self._insert_fixed_deposit(2_000_000)

        ok, _, _, account, penalty, _ = self.repo.cancel_fixed_deposit(
            "u1", deposit_id, penalty_rate=5.0, penalty_threshold=1_000_000
        )

        self.assertTrue(ok)
        self.assertEqual(penalty, 2_000_000)
        self.assertEqual(account.balance, 0)
        self.assertEqual(helpers.wallet_of(self.db_path), 0)


if __name__ == "__main__":
    unittest.main()

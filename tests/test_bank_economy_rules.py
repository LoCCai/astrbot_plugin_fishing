"""银行与资产税的经济规则回归测试。

这批用例守的是「钱不能凭空消失、也不能凭空躲开」这条底线：
- 欠税必须能被清偿，且清偿顺序是先旧账后新账；
- 预约锁定的资金不能既取不出又扣不到税；
- 定期不能既免税又生息、也不能挡住借贷催收；
- 大额取款门槛不能靠拆单绕开。
"""

import importlib
import sqlite3
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
TODAY = "2026-07-30"
TOMORROW = "2026-07-31"


class BankEconomyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.logger = helpers.install_astrbot_stub()
        cls.bank_module = importlib.import_module("core.repositories.sqlite_bank_repo")
        cls.bank_sql = importlib.import_module("core.repositories.bank_sql")
        cls.utils = importlib.import_module("core.utils")

    def setUp(self):
        self._temp = TemporaryDirectory()
        self.db_path = Path(self._temp.name) / "fish.db"
        helpers.create_database(
            self.db_path,
            users=(("u1", "玩家1", 0, 0), ("u2", "玩家2", 0, 0)),
        )
        self.repo = self.bank_module.SqliteBankRepository(str(self.db_path))

    def tearDown(self):
        self.repo.close_connection()
        self._temp.cleanup()

    # --- 辅助 ---

    def _insert_fixed(self, user_id="u1", principal=1_000_000, term_days=30, matured=False):
        now = self.utils.get_now()
        matures_at = now - timedelta(minutes=1) if matured else now + timedelta(days=term_days)
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                INSERT INTO bank_fixed_deposits
                    (user_id, principal, term_days, interest_rate, expected_interest,
                     status, started_at, matures_at)
                VALUES (?, ?, ?, 0.05, ?, 'active', ?, ?)
                """,
                (user_id, principal, term_days, int(principal * 0.05), now, matures_at),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def _tx_types(self, user_id="u1"):
        return [
            row["tx_type"]
            for row in helpers.query_all(
                self.db_path,
                "SELECT tx_type FROM bank_transactions WHERE user_id = ? ORDER BY transaction_id",
                (user_id,),
            )
        ]


class TaxDebtLifecycleTests(BankEconomyTestCase):
    def test_shortfall_becomes_debt(self):
        helpers.set_wallet(self.db_path, coins=300)
        result = self.repo.collect_daily_tax("u1", 1_000, "wallet_bank", TODAY, 0.0)

        self.assertEqual(result["tax_paid"], 300)
        self.assertEqual(result["debt_added"], 700)
        self.assertEqual(helpers.debt_of(self.db_path), 700)
        self.assertEqual(helpers.wallet_of(self.db_path), 0)

    def test_old_debt_is_repaid_before_new_tax(self):
        """先旧账后新账：只加不减的话欠税会永远滚下去。"""
        helpers.set_tax_debt(self.db_path, debt_amount=700)
        helpers.set_wallet(self.db_path, coins=1_000)

        result = self.repo.collect_daily_tax("u1", 500, "wallet_bank", TOMORROW, 0.0)

        self.assertEqual(result["debt_repaid"], 700)
        self.assertEqual(result["tax_paid"], 300)
        self.assertEqual(result["debt_added"], 200)
        self.assertEqual(helpers.debt_of(self.db_path), 200)
        self.assertEqual(helpers.wallet_of(self.db_path), 0)

    def test_debt_can_be_fully_cleared(self):
        helpers.set_tax_debt(self.db_path, debt_amount=400)
        helpers.set_wallet(self.db_path, coins=10_000)

        result = self.repo.collect_daily_tax("u1", 100, "wallet_bank", TOMORROW, 0.0)

        self.assertEqual(result["debt_repaid"], 400)
        self.assertEqual(result["tax_paid"], 100)
        self.assertEqual(result["debt_after"], 0)
        self.assertIsNone(
            helpers.query_one(self.db_path, "SELECT 1 FROM tax_debts WHERE user_id = 'u1'")
        )

    def test_surcharge_accrues_once_per_day(self):
        helpers.set_tax_debt(self.db_path, debt_amount=1_000)
        helpers.set_wallet(self.db_path, coins=0)

        first = self.repo.collect_daily_tax("u1", 0, "wallet_bank", TOMORROW, 0.1)
        self.assertEqual(first["surcharge"], 100)
        self.assertEqual(helpers.debt_of(self.db_path), 1_100)

        # 同一个结算日重复执行不应再次计息
        second = self.repo.collect_daily_tax("u1", 0, "wallet_bank", TOMORROW, 0.1)
        self.assertEqual(second["surcharge"], 0)
        self.assertEqual(helpers.debt_of(self.db_path), 1_100)

    def test_zero_surcharge_rate_never_grows_debt(self):
        helpers.set_tax_debt(self.db_path, debt_amount=1_000)
        result = self.repo.collect_daily_tax("u1", 0, "wallet_bank", TOMORROW, 0.0)
        self.assertEqual(result["surcharge"], 0)
        self.assertEqual(helpers.debt_of(self.db_path), 1_000)

    def test_player_can_repay_debt_from_wallet(self):
        """欠税只在出金时补扣的话，钱全在钱包里的玩家想还也还不了。"""
        helpers.set_tax_debt(self.db_path, debt_amount=5_000)
        helpers.set_wallet(self.db_path, coins=3_000)

        ok, _, paid, debt_after, wallet_after = self.repo.repay_tax_debt_from_wallet("u1")

        self.assertTrue(ok)
        self.assertEqual(paid, 3_000)
        self.assertEqual(debt_after, 2_000)
        self.assertEqual(wallet_after, 0)

    def test_repay_never_exceeds_debt(self):
        helpers.set_tax_debt(self.db_path, debt_amount=500)
        helpers.set_wallet(self.db_path, coins=10_000)

        ok, _, paid, debt_after, wallet_after = self.repo.repay_tax_debt_from_wallet("u1", 9_999)

        self.assertTrue(ok)
        self.assertEqual(paid, 500)
        self.assertEqual(debt_after, 0)
        self.assertEqual(wallet_after, 9_500)

    def test_withdraw_deducts_outstanding_debt(self):
        helpers.set_bank_state(self.db_path, balance=1_000_000, reset_date=TODAY)
        helpers.set_tax_debt(self.db_path, debt_amount=200_000)

        ok, _, _, wallet_after, fee, debt_paid = self.repo.withdraw(
            "u1", 500_000, TODAY, FREE_LIMIT, FEE_RATE
        )

        self.assertTrue(ok)
        self.assertEqual(fee, 0)
        self.assertEqual(debt_paid, 200_000)
        self.assertEqual(wallet_after, 300_000)
        self.assertEqual(helpers.debt_of(self.db_path), 0)

    def test_admin_can_waive_debt(self):
        helpers.set_tax_debt(self.db_path, debt_amount=800)
        waived, debt_after = self.repo.waive_tax_debt("u1", 300)
        self.assertEqual(waived, 300)
        self.assertEqual(debt_after, 500)

        waived, debt_after = self.repo.waive_tax_debt("u1")
        self.assertEqual(waived, 500)
        self.assertEqual(debt_after, 0)


class LockedBalanceInvariantTests(BankEconomyTestCase):
    def _make_reservation(self, amount=5_000_000, ready_delta=timedelta(hours=24), expire_delta=timedelta(hours=96)):
        now = self.utils.get_now()
        return self.repo.create_reservation(
            "u1", amount=amount, fee_amount=0,
            ready_at=now + ready_delta, expires_at=now + expire_delta, max_pending=1,
        )

    def test_locked_funds_cannot_be_withdrawn(self):
        helpers.set_bank_state(self.db_path, balance=6_000_000, reset_date=TODAY)
        ok, _, _ = self._make_reservation(amount=5_000_000)
        self.assertTrue(ok)

        ok, message, _, _, _, _ = self.repo.withdraw(
            "u1", 2_000_000, TODAY, FREE_LIMIT, FEE_RATE
        )
        self.assertFalse(ok)
        self.assertEqual(message, "银行可用余额不足")

    def test_locked_funds_cannot_open_fixed_deposit(self):
        helpers.set_bank_state(self.db_path, balance=6_000_000, reset_date=TODAY)
        self._make_reservation(amount=5_000_000)

        ok, message, _, _ = self.repo.create_fixed_deposit(
            user_id="u1", principal=2_000_000, term_days=30, interest_rate=0.05,
            expected_interest=100_000, matures_at=self.utils.get_now() + timedelta(days=30),
            max_active=5,
        )
        self.assertFalse(ok)
        self.assertEqual(message, "银行活期可用余额不足")

    def test_locked_funds_are_not_taxable_and_become_debt(self):
        """锁定资金扣不到税，差额必须挂账，不能悄悄少收。"""
        helpers.set_bank_state(self.db_path, balance=6_000_000, reset_date=TODAY)
        self._make_reservation(amount=5_000_000)
        helpers.set_wallet(self.db_path, coins=0)

        result = self.repo.collect_daily_tax("u1", 2_000_000, "wallet_bank", TODAY, 0.0)

        self.assertEqual(result["tax_paid"], 1_000_000)
        self.assertEqual(result["debt_added"], 1_000_000)
        self.assertEqual(helpers.bank_of(self.db_path)["locked_balance"], 5_000_000)

    def test_cancel_releases_lock_exactly_once(self):
        helpers.set_bank_state(self.db_path, balance=6_000_000, reset_date=TODAY)
        self._make_reservation(amount=5_000_000)

        ok, _, _ = self.repo.cancel_pending_reservation("u1")
        self.assertTrue(ok)
        self.assertEqual(helpers.bank_of(self.db_path)["locked_balance"], 0)

        ok, message, _ = self.repo.cancel_pending_reservation("u1")
        self.assertFalse(ok)
        self.assertEqual(message, "没有待取消的大额取款预约")
        self.assertEqual(helpers.bank_of(self.db_path)["locked_balance"], 0)

    def test_expired_reservation_releases_lock(self):
        """一笔永不确认的预约不能把资金永久锁死。"""
        helpers.set_bank_state(self.db_path, balance=6_000_000, reset_date=TODAY)
        self._make_reservation(
            amount=5_000_000,
            ready_delta=-timedelta(hours=100),
            expire_delta=-timedelta(hours=1),
        )
        self.assertEqual(helpers.bank_of(self.db_path)["locked_balance"], 5_000_000)

        expired = self.repo.expire_stale_reservations()

        self.assertEqual(expired, 1)
        self.assertEqual(helpers.bank_of(self.db_path)["locked_balance"], 0)
        row = helpers.query_one(
            self.db_path, "SELECT status FROM bank_withdraw_reservations WHERE user_id = 'u1'"
        )
        self.assertEqual(row["status"], "expired")

    def test_unexpired_reservation_is_kept(self):
        helpers.set_bank_state(self.db_path, balance=6_000_000, reset_date=TODAY)
        self._make_reservation(amount=5_000_000)

        self.assertEqual(self.repo.expire_stale_reservations(), 0)
        self.assertEqual(helpers.bank_of(self.db_path)["locked_balance"], 5_000_000)


class WithdrawThresholdTests(BankEconomyTestCase):
    def test_cumulative_withdrawals_hit_the_reservation_threshold(self):
        """按单笔判定的话，连续取「门槛-1」就能无限出金。"""
        helpers.set_bank_state(self.db_path, balance=20_000_000, reset_date=TODAY)

        ok, _, _, _, _, _ = self.repo.withdraw(
            "u1", 4_000_000, TODAY, FREE_LIMIT, FEE_RATE, 5_000_000
        )
        self.assertTrue(ok)

        ok, message, _, _, _, _ = self.repo.withdraw(
            "u1", 4_000_000, TODAY, FREE_LIMIT, FEE_RATE, 5_000_000
        )
        self.assertFalse(ok)
        self.assertIn("预约", message)

    def test_threshold_resets_next_day(self):
        helpers.set_bank_state(self.db_path, balance=20_000_000, reset_date=TODAY)
        self.repo.withdraw("u1", 4_000_000, TODAY, FREE_LIMIT, FEE_RATE, 5_000_000)

        ok, _, account, _, _, _ = self.repo.withdraw(
            "u1", 4_000_000, TOMORROW, FREE_LIMIT, FEE_RATE, 5_000_000
        )
        self.assertTrue(ok)
        self.assertEqual(account.today_withdrawn, 4_000_000)


class FixedDepositTests(BankEconomyTestCase):
    def test_matured_deposit_is_auto_settled(self):
        helpers.set_bank_state(self.db_path, balance=0, reset_date=TODAY)
        self._insert_fixed(principal=1_000_000, matured=True)

        settled = self.repo.settle_matured_fixed_deposits()

        self.assertEqual(len(settled), 1)
        self.assertEqual(settled[0]["net_payout"], 1_050_000)
        self.assertEqual(helpers.bank_of(self.db_path)["balance"], 1_050_000)

    def test_unmatured_deposit_is_left_alone(self):
        helpers.set_bank_state(self.db_path, balance=0, reset_date=TODAY)
        self._insert_fixed(principal=1_000_000, matured=False)

        self.assertEqual(self.repo.settle_matured_fixed_deposits(), [])
        self.assertEqual(helpers.bank_of(self.db_path)["balance"], 0)

    def test_completing_twice_does_not_double_credit(self):
        helpers.set_bank_state(self.db_path, balance=0, reset_date=TODAY)
        deposit_id = self._insert_fixed(principal=1_000_000, matured=True)

        ok, _, _, account, _ = self.repo.complete_fixed_deposit("u1", deposit_id)
        self.assertTrue(ok)
        self.assertEqual(account.balance, 1_050_000)

        ok, message, _, _, _ = self.repo.complete_fixed_deposit("u1", deposit_id)
        self.assertFalse(ok)
        self.assertEqual(message, "未找到可领取的定期存款")
        self.assertEqual(helpers.bank_of(self.db_path)["balance"], 1_050_000)

    def test_stale_row_cannot_settle_the_same_deposit_twice(self):
        """并发保护：拿着过期的存单快照重复结算，第二次必须什么都不做。

        自动结算和玩家手动领取可能撞在一起，两边都读到 status='active' 的
        同一张存单。真正的守卫是 UPDATE 的 rowcount，不是先前那次 SELECT。
        """
        helpers.set_bank_state(self.db_path, balance=0, reset_date=TODAY)
        deposit_id = self._insert_fixed(principal=1_000_000, matured=True)

        stale_row = helpers.query_one(
            self.db_path, "SELECT * FROM bank_fixed_deposits WHERE deposit_id = ?", (deposit_id,)
        )
        now = self.utils.get_now()

        def settle(cursor):
            return self.repo._settle_deposit_row(cursor, stale_row, now)

        first = self.repo._conn_mgr.run_in_transaction(settle)
        second = self.repo._conn_mgr.run_in_transaction(settle)

        self.assertEqual(first, (1_050_000, 0))
        self.assertEqual(second, (0, 0))
        self.assertEqual(helpers.bank_of(self.db_path)["balance"], 1_050_000)

    def test_auto_settle_pays_outstanding_debt_first(self):
        helpers.set_bank_state(self.db_path, balance=0, reset_date=TODAY)
        helpers.set_tax_debt(self.db_path, debt_amount=50_000)
        self._insert_fixed(principal=1_000_000, matured=True)

        settled = self.repo.settle_matured_fixed_deposits()

        self.assertEqual(settled[0]["debt_paid"], 50_000)
        self.assertEqual(helpers.bank_of(self.db_path)["balance"], 1_000_000)
        self.assertEqual(helpers.debt_of(self.db_path), 0)


class TaxScopeTests(BankEconomyTestCase):
    def _setup_assets(self):
        helpers.set_wallet(self.db_path, "u1", 500_000)
        helpers.set_bank_state(self.db_path, "u1", balance=600_000, reset_date=TODAY)
        self._insert_fixed(principal=900_000)

    def test_wallet_scope_ignores_bank_and_fixed(self):
        self._setup_assets()
        subjects = self.repo.get_daily_tax_subjects(1_000_000, "wallet")
        self.assertEqual(subjects, [])

    def test_wallet_bank_scope_excludes_fixed(self):
        self._setup_assets()
        subjects = self.repo.get_daily_tax_subjects(1_000_000, "wallet_bank")
        self.assertEqual(len(subjects), 1)
        self.assertEqual(subjects[0]["assessed_assets"], 1_100_000)

    def test_wallet_bank_fixed_scope_covers_everything(self):
        """定期免税的话，玩家把钱滚进 1 天定期就能既避税又生息。"""
        self._setup_assets()
        subjects = self.repo.get_daily_tax_subjects(1_000_000, "wallet_bank_fixed")
        self.assertEqual(len(subjects), 1)
        self.assertEqual(subjects[0]["assessed_assets"], 2_000_000)

    def test_deduct_scope_wallet_never_touches_bank(self):
        helpers.set_wallet(self.db_path, coins=100)
        helpers.set_bank_state(self.db_path, balance=1_000_000, reset_date=TODAY)

        result = self.repo.collect_daily_tax("u1", 5_000, "wallet", TODAY, 0.0)

        self.assertEqual(result["tax_paid"], 100)
        self.assertEqual(result["debt_added"], 4_900)
        self.assertEqual(helpers.bank_of(self.db_path)["balance"], 1_000_000)

    def test_deduct_scope_bank_never_touches_wallet(self):
        helpers.set_wallet(self.db_path, coins=1_000_000)
        helpers.set_bank_state(self.db_path, balance=2_000, reset_date=TODAY)

        result = self.repo.collect_daily_tax("u1", 5_000, "bank", TODAY, 0.0)

        self.assertEqual(result["tax_paid"], 2_000)
        self.assertEqual(result["debt_added"], 3_000)
        self.assertEqual(helpers.wallet_of(self.db_path), 1_000_000)

    def test_deduct_scope_wallet_bank_drains_wallet_first(self):
        helpers.set_wallet(self.db_path, coins=3_000)
        helpers.set_bank_state(self.db_path, balance=10_000, reset_date=TODAY)

        result = self.repo.collect_daily_tax("u1", 5_000, "wallet_bank", TODAY, 0.0)

        self.assertEqual(result["tax_paid"], 5_000)
        self.assertEqual(helpers.wallet_of(self.db_path), 0)
        self.assertEqual(helpers.bank_of(self.db_path)["balance"], 8_000)


class LoanCollectionTests(BankEconomyTestCase):
    def _collect(self, amount, allow_fixed=True):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            collected = self.bank_sql.collect_for_loan(
                cursor, "u1", amount, allow_fixed=allow_fixed
            )
            conn.commit()
            return collected
        finally:
            conn.close()

    def test_collection_reaches_bank_balance(self):
        """只扣钱包的话，借款人存进银行就能让催收颗粒无收。"""
        helpers.set_wallet(self.db_path, coins=0)
        helpers.set_bank_state(self.db_path, balance=800_000, reset_date=TODAY)

        collected = self._collect(500_000)

        self.assertEqual(collected, 500_000)
        self.assertEqual(helpers.bank_of(self.db_path)["balance"], 300_000)

    def test_collection_drains_wallet_before_bank(self):
        helpers.set_wallet(self.db_path, coins=200_000)
        helpers.set_bank_state(self.db_path, balance=800_000, reset_date=TODAY)

        collected = self._collect(500_000)

        self.assertEqual(collected, 500_000)
        self.assertEqual(helpers.wallet_of(self.db_path), 0)
        self.assertEqual(helpers.bank_of(self.db_path)["balance"], 500_000)

    def test_collection_can_break_fixed_deposits(self):
        helpers.set_wallet(self.db_path, coins=0)
        helpers.set_bank_state(self.db_path, balance=0, reset_date=TODAY)
        self._insert_fixed(principal=1_000_000)

        collected = self._collect(600_000)

        self.assertEqual(collected, 600_000)
        row = helpers.query_one(
            self.db_path, "SELECT status FROM bank_fixed_deposits WHERE user_id = 'u1'"
        )
        self.assertEqual(row["status"], "cancelled")
        # 解约本金先入活期，扣走欠款后余下的留在活期
        self.assertEqual(helpers.bank_of(self.db_path)["balance"], 400_000)

    def test_collection_skips_fixed_when_disabled(self):
        helpers.set_wallet(self.db_path, coins=0)
        helpers.set_bank_state(self.db_path, balance=0, reset_date=TODAY)
        self._insert_fixed(principal=1_000_000)

        collected = self._collect(600_000, allow_fixed=False)

        self.assertEqual(collected, 0)
        row = helpers.query_one(
            self.db_path, "SELECT status FROM bank_fixed_deposits WHERE user_id = 'u1'"
        )
        self.assertEqual(row["status"], "active")

    def test_collection_never_touches_locked_funds(self):
        """预约锁定的钱是已经承诺给玩家的取款额，催收不能直接抢走。"""
        helpers.set_wallet(self.db_path, coins=0)
        helpers.set_bank_state(self.db_path, balance=1_000_000, locked=800_000, reset_date=TODAY)

        collected = self._collect(1_000_000)

        self.assertEqual(collected, 200_000)
        self.assertEqual(helpers.bank_of(self.db_path)["balance"], 800_000)


class LedgerTests(BankEconomyTestCase):
    def test_deposit_and_withdraw_are_recorded(self):
        helpers.set_wallet(self.db_path, coins=1_000_000)
        self.repo.deposit("u1", 600_000)
        self.repo.withdraw("u1", 500_000, TODAY, FREE_LIMIT, FEE_RATE)

        self.assertEqual(self._tx_types(), ["存款", "取款"])

    def test_withdraw_fee_is_recorded_as_destroyed(self):
        helpers.set_bank_state(self.db_path, balance=3_000_000, today_withdrawn=FREE_LIMIT, reset_date=TODAY)
        self.repo.withdraw("u1", 500_000, TODAY, FREE_LIMIT, FEE_RATE)

        rows = helpers.query_all(
            self.db_path,
            "SELECT * FROM bank_transactions WHERE tx_type = '取款手续费'",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 15_000)
        # 手续费是销毁，不产生任何余额位移
        self.assertEqual(rows[0]["wallet_delta"], 0)
        self.assertEqual(rows[0]["bank_delta"], 0)

    def test_early_withdraw_penalty_is_recorded(self):
        helpers.set_bank_state(self.db_path, balance=0, reset_date=TODAY)
        deposit_id = self._insert_fixed(principal=2_000_000)
        self.repo.cancel_fixed_deposit("u1", deposit_id, 0.01, 1_000_000)

        rows = helpers.query_all(
            self.db_path, "SELECT * FROM bank_transactions WHERE tx_type = '定期违约金'"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 20_000)

    def test_fixed_interest_is_recorded_as_minted(self):
        helpers.set_bank_state(self.db_path, balance=0, reset_date=TODAY)
        deposit_id = self._insert_fixed(principal=1_000_000, matured=True)
        self.repo.complete_fixed_deposit("u1", deposit_id)

        rows = helpers.query_all(
            self.db_path, "SELECT * FROM bank_transactions WHERE tx_type = '定期利息'"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 50_000)

    def test_ledger_snapshots_match_final_balances(self):
        helpers.set_wallet(self.db_path, coins=1_000_000)
        self.repo.deposit("u1", 400_000)

        row = helpers.query_all(
            self.db_path,
            "SELECT * FROM bank_transactions ORDER BY transaction_id DESC LIMIT 1",
        )[0]
        self.assertEqual(row["wallet_after"], helpers.wallet_of(self.db_path))
        self.assertEqual(row["bank_after"], helpers.bank_of(self.db_path)["balance"])


class TotalAssetsLeaderboardTests(BankEconomyTestCase):
    def test_bank_holdings_count_towards_ranking(self):
        """金币榜只看钱包，藏进银行的人会凭空从榜上消失。"""
        helpers.set_wallet(self.db_path, "u1", 100)
        helpers.set_bank_state(self.db_path, "u1", balance=5_000_000, reset_date=TODAY)
        self._insert_fixed("u1", principal=1_000_000)
        helpers.set_wallet(self.db_path, "u2", 1_000_000)

        rows = self.repo.get_top_users_by_total_assets(10)

        self.assertEqual(rows[0]["user_id"], "u1")
        self.assertEqual(rows[0]["total_assets"], 6_000_100)
        self.assertEqual(rows[1]["user_id"], "u2")


if __name__ == "__main__":
    unittest.main()

"""借贷强制收款穿透银行的端到端测试。

只扣钱包的话，借款人借完立刻把钱存进银行或押成定期，强制收款就颗粒无收，
等于凭空印钱。这里走真实的 LoanService.force_collect，确认它能收到银行里的钱。
"""

import importlib
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bank_test_utils as helpers  # noqa: E402


class LoanBankCollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helpers.install_astrbot_stub()
        cls.loan_service_module = importlib.import_module("core.services.loan_service")
        cls.loan_repo_module = importlib.import_module("core.repositories.sqlite_loan_repo")
        cls.user_repo_module = importlib.import_module("core.repositories.sqlite_user_repo")
        cls.bank_repo_module = importlib.import_module("core.repositories.sqlite_bank_repo")
        cls.utils = importlib.import_module("core.utils")

    def setUp(self):
        self._temp = TemporaryDirectory()
        self.db_path = Path(self._temp.name) / "fish.db"
        helpers.create_database(
            self.db_path,
            users=(("lender", "债主", 0, 0), ("borrower", "老赖", 0, 0)),
        )
        self.loan_repo = self.loan_repo_module.SqliteLoanRepository(str(self.db_path))
        self.user_repo = self.user_repo_module.SqliteUserRepository(str(self.db_path))
        self.bank_repo = self.bank_repo_module.SqliteBankRepository(str(self.db_path))

    def tearDown(self):
        for repo in (self.loan_repo, self.user_repo, self.bank_repo):
            repo.close_connection()
        self._temp.cleanup()

    def _service(self, collect_from_fixed=True):
        return self.loan_service_module.LoanService(
            self.loan_repo, self.user_repo, collect_from_fixed=collect_from_fixed
        )

    def _create_loan(self, principal=1_000_000, due_amount=1_050_000):
        now = datetime.now()
        conn = __import__("sqlite3").connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO loans (
                    lender_id, borrower_id, principal, interest_rate, borrowed_at,
                    due_amount, repaid_amount, status, due_date, created_at, updated_at
                ) VALUES ('lender', 'borrower', ?, 0.05, ?, ?, 0, 'active', ?, ?, ?)
                """,
                (principal, now, due_amount, now + timedelta(days=7), now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def _insert_fixed(self, principal):
        now = self.utils.get_now()
        conn = __import__("sqlite3").connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO bank_fixed_deposits
                    (user_id, principal, term_days, interest_rate, expected_interest,
                     status, started_at, matures_at)
                VALUES ('borrower', ?, 30, 0.05, ?, 'active', ?, ?)
                """,
                (principal, int(principal * 0.05), now, now + timedelta(days=30)),
            )
            conn.commit()
        finally:
            conn.close()

    def test_collect_reaches_money_hidden_in_bank(self):
        self._create_loan()
        helpers.set_wallet(self.db_path, "borrower", 0)
        helpers.set_bank_state(self.db_path, "borrower", balance=1_050_000)

        ok, message = self._service().force_collect("lender", "borrower", None)

        self.assertTrue(ok, message)
        self.assertEqual(helpers.wallet_of(self.db_path, "lender"), 1_050_000)
        self.assertEqual(helpers.bank_of(self.db_path, "borrower")["balance"], 0)
        loan = helpers.query_one(self.db_path, "SELECT status FROM loans LIMIT 1")
        self.assertEqual(loan["status"], "paid")

    def test_collect_breaks_fixed_deposits_when_allowed(self):
        self._create_loan()
        helpers.set_wallet(self.db_path, "borrower", 0)
        helpers.set_bank_state(self.db_path, "borrower", balance=0)
        self._insert_fixed(2_000_000)

        ok, message = self._service(collect_from_fixed=True).force_collect(
            "lender", "borrower", None
        )

        self.assertTrue(ok, message)
        self.assertEqual(helpers.wallet_of(self.db_path, "lender"), 1_050_000)
        deposit = helpers.query_one(self.db_path, "SELECT status FROM bank_fixed_deposits LIMIT 1")
        self.assertEqual(deposit["status"], "cancelled")
        # 解约本金多出来的部分留在借款人活期，不能被顺手扣走
        self.assertEqual(
            helpers.bank_of(self.db_path, "borrower")["balance"], 2_000_000 - 1_050_000
        )

    def test_collect_leaves_fixed_deposits_when_disabled(self):
        self._create_loan()
        helpers.set_wallet(self.db_path, "borrower", 0)
        helpers.set_bank_state(self.db_path, "borrower", balance=0)
        self._insert_fixed(2_000_000)

        ok, message = self._service(collect_from_fixed=False).force_collect(
            "lender", "borrower", None
        )

        self.assertFalse(ok)
        self.assertIn("无可扣资产", message)
        deposit = helpers.query_one(self.db_path, "SELECT status FROM bank_fixed_deposits LIMIT 1")
        self.assertEqual(deposit["status"], "active")

    def test_partial_collection_reports_shortfall(self):
        self._create_loan()
        helpers.set_wallet(self.db_path, "borrower", 50_000)
        helpers.set_bank_state(self.db_path, "borrower", balance=200_000)

        ok, message = self._service().force_collect("lender", "borrower", None)

        self.assertTrue(ok, message)
        self.assertIn("可扣资产不足", message)
        self.assertEqual(helpers.wallet_of(self.db_path, "lender"), 250_000)
        self.assertEqual(helpers.wallet_of(self.db_path, "borrower"), 0)
        self.assertEqual(helpers.bank_of(self.db_path, "borrower")["balance"], 0)

    def test_locked_funds_are_not_seized(self):
        """预约锁定的钱是已经承诺给玩家的取款额，催收不能直接抢走。"""
        self._create_loan()
        helpers.set_wallet(self.db_path, "borrower", 0)
        helpers.set_bank_state(self.db_path, "borrower", balance=1_000_000, locked=800_000)

        ok, message = self._service().force_collect("lender", "borrower", None)

        self.assertTrue(ok, message)
        self.assertEqual(helpers.wallet_of(self.db_path, "lender"), 200_000)
        self.assertEqual(helpers.bank_of(self.db_path, "borrower")["balance"], 800_000)


if __name__ == "__main__":
    unittest.main()

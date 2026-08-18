"""银行服务层的准入规则测试。

这层守的是「什么时候不许把钱往里放、什么时候必须允许取出来」：
- 银行停用只能封锁入金，已经存进去的钱必须还能取；
- 欠税未清时禁止继续藏钱，但要留一条主动还清的路；
- 大额取款门槛按当日累计判定。
"""

import importlib
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bank_test_utils as helpers  # noqa: E402

TODAY_RESET_HOUR = 0


class _FakeUserRepo:
    """只提供 BankService 用到的 get_by_id，钱包余额实时读库。"""

    def __init__(self, db_path):
        self.db_path = db_path

    def get_by_id(self, user_id):
        row = helpers.query_one(
            self.db_path, "SELECT user_id, nickname, coins FROM users WHERE user_id = ?", (user_id,)
        )
        if not row:
            return None
        return types.SimpleNamespace(**row)


class _FakeLogRepo:
    def __init__(self):
        self.tax_records = []

    def add_tax_record(self, record):
        self.tax_records.append(record)

    def types(self):
        return [record.tax_type for record in self.tax_records]


def _config(**overrides):
    config = {
        "daily_reset_hour": TODAY_RESET_HOUR,
        "bank": {
            "enabled": True,
            "daily_free_withdraw_limit": 1_000_000,
            "withdraw_fee_rate": 0.03,
            "reservation_threshold": 5_000_000,
            "reservation_delay_hours": 24,
            "reservation_expire_hours": 72,
            "max_pending_reservations": 1,
            "block_inflow_when_in_debt": True,
            "fixed_deposit": {
                "enabled": True,
                "min_amount": 100_000,
                "max_amount": 20_000_000,
                "max_active_deposits": 5,
                "auto_settle_matured": True,
                "early_withdraw_penalty_rate": 0.01,
                "early_withdraw_penalty_threshold": 1_000_000,
                "terms": {"1": 0.001, "7": 0.01, "30": 0.05},
            },
        },
        "tax": {},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


class BankServiceRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helpers.install_astrbot_stub()
        cls.bank_module = importlib.import_module("core.repositories.sqlite_bank_repo")
        cls.service_module = importlib.import_module("core.services.bank_service")
        cls.utils = importlib.import_module("core.utils")

    def setUp(self):
        self._temp = TemporaryDirectory()
        self.db_path = Path(self._temp.name) / "fish.db"
        helpers.create_database(self.db_path)
        self.repo = self.bank_module.SqliteBankRepository(str(self.db_path))
        self.log_repo = _FakeLogRepo()

    def tearDown(self):
        self.repo.close_connection()
        self._temp.cleanup()

    def _reset_date(self):
        """服务层每次操作前都会按这个日期重置当日提现额度。"""
        return self.utils.get_last_reset_time(TODAY_RESET_HOUR).date().isoformat()

    def _service(self, **config_overrides):
        return self.service_module.BankService(
            self.repo, _FakeUserRepo(self.db_path), self.log_repo, _config(**config_overrides)
        )

    # --- 停用语义 ---

    def test_disabled_bank_blocks_deposit(self):
        helpers.set_wallet(self.db_path, coins=1_000_000)
        service = self._service(bank={"enabled": False})

        result = service.deposit("u1", 500_000)

        self.assertFalse(result["success"])
        self.assertIn("只能取款", result["message"])

    def test_disabled_bank_still_allows_withdraw(self):
        """停用只封入金。否则玩家已经存进去的钱会被永久锁死在一个不可用的系统里。"""
        helpers.set_bank_state(self.db_path, balance=500_000)
        service = self._service(bank={"enabled": False})

        result = service.withdraw("u1", 300_000)

        self.assertTrue(result["success"], result["message"])
        self.assertEqual(helpers.wallet_of(self.db_path), 300_000)

    def test_disabled_bank_still_allows_fixed_deposit_payout(self):
        helpers.set_bank_state(self.db_path, balance=0)
        service_on = self._service()
        helpers.set_bank_state(self.db_path, balance=1_000_000)
        created = service_on.create_fixed_deposit("u1", 1_000_000, 30)
        self.assertTrue(created["success"], created["message"])

        service_off = self._service(bank={"enabled": False})
        result = service_off.cancel_fixed_deposit("u1", created["deposit"].deposit_id)

        self.assertTrue(result["success"], result["message"])

    def test_disabled_bank_blocks_new_fixed_deposit(self):
        helpers.set_bank_state(self.db_path, balance=5_000_000)
        service = self._service(bank={"enabled": False})

        result = service.create_fixed_deposit("u1", 1_000_000, 30)

        self.assertFalse(result["success"])
        self.assertIn("只能取款", result["message"])

    # --- 欠税准入 ---

    def test_debt_blocks_deposit(self):
        helpers.set_wallet(self.db_path, coins=1_000_000)
        helpers.set_tax_debt(self.db_path, debt_amount=5_000)
        service = self._service()

        result = service.deposit("u1", 500_000)

        self.assertFalse(result["success"])
        self.assertIn("欠税", result["message"])
        self.assertIn("还税", result["message"])

    def test_debt_blocks_new_fixed_deposit(self):
        helpers.set_bank_state(self.db_path, balance=5_000_000)
        helpers.set_tax_debt(self.db_path, debt_amount=5_000)
        service = self._service()

        result = service.create_fixed_deposit("u1", 1_000_000, 30)

        self.assertFalse(result["success"])
        self.assertIn("欠税", result["message"])

    def test_debt_does_not_block_withdraw(self):
        """出金是唯一能补扣欠税的路径，绝不能一起封掉。"""
        helpers.set_bank_state(self.db_path, balance=1_000_000)
        helpers.set_tax_debt(self.db_path, debt_amount=200_000)
        service = self._service()

        result = service.withdraw("u1", 500_000)

        self.assertTrue(result["success"], result["message"])
        self.assertEqual(result["debt_paid"], 200_000)
        self.assertIn("欠税补扣", self.log_repo.types())

    def test_repay_tax_debt_from_wallet(self):
        helpers.set_wallet(self.db_path, coins=8_000)
        helpers.set_tax_debt(self.db_path, debt_amount=5_000)
        service = self._service()

        result = service.repay_tax_debt("u1")

        self.assertTrue(result["success"], result["message"])
        self.assertEqual(result["paid"], 5_000)
        self.assertEqual(result["debt_after"], 0)
        self.assertEqual(helpers.wallet_of(self.db_path), 3_000)

    def test_deposit_allowed_again_after_repaying(self):
        helpers.set_wallet(self.db_path, coins=1_000_000)
        helpers.set_tax_debt(self.db_path, debt_amount=5_000)
        service = self._service()

        self.assertFalse(service.deposit("u1", 100_000)["success"])
        service.repay_tax_debt("u1")
        self.assertTrue(service.deposit("u1", 100_000)["success"])

    def test_debt_block_can_be_disabled(self):
        helpers.set_wallet(self.db_path, coins=1_000_000)
        helpers.set_tax_debt(self.db_path, debt_amount=5_000)
        service = self._service(bank={"block_inflow_when_in_debt": False})

        self.assertTrue(service.deposit("u1", 100_000)["success"])

    # --- 取款门槛 ---

    def test_single_withdrawal_at_threshold_needs_reservation(self):
        helpers.set_bank_state(
            self.db_path, balance=20_000_000, reset_date=self._reset_date()
        )
        service = self._service()

        result = service.withdraw("u1", 5_000_000)

        self.assertFalse(result["success"])
        self.assertIn("单笔取款", result["message"])
        self.assertIn("预约取款", result["message"])

    def test_sub_threshold_withdrawal_passes_regardless_of_daily_total(self):
        """PR #17 的既定设计：门槛只看单笔，不累计当日已取金额。"""
        helpers.set_bank_state(
            self.db_path, balance=20_000_000, today_withdrawn=4_900_000,
            reset_date=self._reset_date(),
        )
        service = self._service()

        result = service.withdraw("u1", 4_000_000)

        self.assertTrue(result["success"], result["message"])

    def test_reservation_rejected_below_threshold(self):
        helpers.set_bank_state(self.db_path, balance=20_000_000, today_withdrawn=0)
        service = self._service()

        result = service.create_reservation("u1", 1_000_000)

        self.assertFalse(result["success"])
        self.assertIn("无需预约", result["message"])

    def test_reservation_sets_expiry(self):
        helpers.set_bank_state(self.db_path, balance=20_000_000)
        service = self._service()

        result = service.create_reservation("u1", 6_000_000)

        self.assertTrue(result["success"], result["message"])
        reservation = result["reservation"]
        self.assertIsNotNone(reservation.expires_at)
        self.assertEqual(
            (reservation.expires_at - reservation.ready_at).total_seconds() / 3600, 72
        )

    # --- 记账 ---

    def test_early_withdraw_penalty_is_written_to_tax_records(self):
        """违约金此前只扣钱不记账，等于一个没有账的销毁口。"""
        helpers.set_bank_state(self.db_path, balance=2_000_000)
        service = self._service()
        created = service.create_fixed_deposit("u1", 2_000_000, 30)
        self.assertTrue(created["success"], created["message"])

        result = service.cancel_fixed_deposit("u1", created["deposit"].deposit_id)

        self.assertTrue(result["success"], result["message"])
        self.assertEqual(result["penalty_amount"], 20_000)
        self.assertIn("定期违约金", self.log_repo.types())

    def test_overview_exposes_locked_and_debt(self):
        helpers.set_bank_state(self.db_path, balance=20_000_000)
        helpers.set_tax_debt(self.db_path, debt_amount=1_234)
        service = self._service()
        service.create_reservation("u1", 6_000_000)

        overview = service.get_overview("u1")

        self.assertTrue(overview["success"])
        self.assertEqual(overview["locked_balance"], 6_000_000)
        self.assertEqual(overview["available_balance"], 14_000_000)
        self.assertEqual(overview["tax_debt"], 1_234)


if __name__ == "__main__":
    unittest.main()

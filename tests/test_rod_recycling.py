import importlib
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


class RodRecyclingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        package = types.ModuleType("fishing_recycle_tests")
        package.__path__ = [str(Path(__file__).resolve().parents[1])]
        astrbot = types.ModuleType("astrbot")
        api = types.ModuleType("astrbot.api")
        api.logger = Mock()
        cls.module_patch = patch.dict(sys.modules, {
            package.__name__: package, "astrbot": astrbot, "astrbot.api": api,
        })
        cls.module_patch.start()
        cls.pricing = importlib.import_module("fishing_recycle_tests.core.rod_recycling")
        cls.mechanics = importlib.import_module("fishing_recycle_tests.core.services.game_mechanics_service")
        cls.inventory_module = importlib.import_module("fishing_recycle_tests.core.services.inventory_service")
        cls.shop_module = importlib.import_module("fishing_recycle_tests.core.services.shop_service")
        cls.repository = importlib.import_module("fishing_recycle_tests.core.repositories.sqlite_inventory_repo")

    @classmethod
    def tearDownClass(cls):
        cls.module_patch.stop()

    def setUp(self):
        self.config = {"rod_recycling": {"discount": 0.5, "overrides": []},
                       "sell_prices": {"rod": {"2": 9999}, "accessory": {"2": 500},
                                       "refine_multiplier": {"1": 1.0, "10": 660.0}}}
        self.inventory = Mock()
        self.users = Mock()
        self.user = SimpleNamespace(user_id="test-player", coins=100, equipped_rod_instance_id=None)
        self.users.get_by_id.return_value = self.user
        self.templates = Mock()
        self.rods = {
            11: SimpleNamespace(rod_id=11, name="测试竹竿", rarity=2, purchase_cost=5000),
            12: SimpleNamespace(rod_id=12, name="测试碳竿", rarity=3, purchase_cost=50000),
            13: SimpleNamespace(rod_id=13, name="特殊鱼竿", rarity=4, purchase_cost=10**24),
        }
        self.templates.get_rod_by_id.side_effect = self.rods.get
        self.shop_repo = Mock()
        self.shop_repo.get_all_shops.return_value = [{"shop_id": 21, "name": "测试商店"}]
        self.shop_repo.get_shop_items.return_value = [
            {"item_id": 31, "name": "竹竿"}, {"item_id": 32, "name": "碳竿"}]
        self.shop_repo.get_item_rewards.side_effect = lambda item_id: [dict(
            reward_type="rod", reward_item_id=item_id - 20, reward_quantity=1)]
        self.shop_repo.get_item_costs.side_effect = lambda item_id: [dict(
            cost_type="coins", cost_amount={31: 23333, 32: 77777}[item_id])]
        self.shop = self.shop_module.ShopService(self.templates, self.inventory, self.users, self.shop_repo)
        self.game = self.mechanics.GameMechanicsService(
            self.users, Mock(), self.inventory, self.templates, Mock(), self.config, shop_service=self.shop)
        self.service = self.inventory_module.InventoryService(
            self.inventory, self.users, self.templates, Mock(), self.game, self.config, Mock())

    @staticmethod
    def instance(instance_id=1, rod_id=11, locked=False, equipped=False, refine=1):
        return SimpleNamespace(rod_instance_id=instance_id, rod_id=rod_id,
                               is_locked=locked, is_equipped=equipped, refine_level=refine)

    def quote(self, cost, rod_id=11):
        return self.pricing.calculate_rod_recycle_price(rod_id, cost, self.config)

    def test_default_half_cost_floors_actual_acquisition_costs(self):
        for cost, expected in ((2100, 1050), (23333, 11666), (77777, 38888), (105000, 52500),
                               (5000000, 2500000), (6500000, 3250000), (1001, 500)):
            with self.subTest(cost=cost):
                self.assertEqual(self.quote(cost), expected)

    def test_discount_range_and_live_updates(self):
        for discount, expected in ((0.4, 2000), (0.5, 2500), (0.6, 3000)):
            self.config["rod_recycling"]["discount"] = discount
            self.assertEqual(self.quote(5000), expected)
        for discount in (0.39, 0.61, "NaN", "Infinity"):
            with self.subTest(discount=discount), self.assertRaises(ValueError):
                self.config["rod_recycling"]["discount"] = discount
                self.quote(5000)

    def test_per_rod_override_wins_and_clear_restores_default(self):
        self.config["rod_recycling"]["overrides"] = [{"rod_id": 11, "price": 2200}]
        self.assertEqual(self.quote(5000), 2200)
        self.assertEqual(self.quote(5000, rod_id=12), 2500)
        self.assertEqual(self.quote(None), 2200)
        self.assertEqual(self.quote(10**24), 2200)
        self.config["rod_recycling"]["overrides"] = []
        self.assertEqual(self.quote(5000), 2500)

    def test_missing_invalid_and_oversize_costs_need_manual_price(self):
        for cost in (None, 0, -100, "NaN", "Infinity", "bad", 10**24):
            with self.subTest(cost=cost), self.assertRaises(ValueError):
                self.quote(cost)

    def test_override_validation(self):
        parse = self.pricing.parse_rod_recycle_override
        self.assertIsNone(parse(""))
        self.assertIsNone(parse(None))
        self.assertEqual(parse(" 1234 "), 1234)
        for value in (0, -1, True, "1.5", "1e4", self.pricing.MAX_COINS + 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse(value)

    def test_refinement_does_not_multiply_rod_price(self):
        for refine in (1, 5, 10):
            self.assertEqual(self.game.calculate_sell_price("rod", 2, refine, 5000, 11), 11666)
        self.assertEqual(self.game.calculate_sell_price("accessory", 2, 10), 330000)

    def test_missing_shop_never_falls_back_to_template_price(self):
        self.game.shop_service = None
        with self.assertRaises(ValueError):
            self.game.calculate_sell_price("rod", 2, 1, 5000, 11)
        self.config["rod_recycling"]["overrides"] = [{"rod_id": 11, "price": 2200}]
        self.assertEqual(self.game.calculate_sell_price("rod", 2, 1, 5000, 11), 2200)

    def test_single_sale_uses_shop_cost_and_responds_to_price_changes(self):
        self.inventory.get_user_rod_instances.return_value = [self.instance()]
        result = self.service.sell_rod("test-player", 1)
        self.assertTrue(result["success"], result)
        self.assertEqual(self.user.coins, 11766)
        self.shop_repo.get_item_costs.side_effect = lambda _: [dict(cost_type="coins", cost_amount=20000)]
        self.assertEqual(self.game.calculate_sell_price("rod", 2, 1, rod_id=11), 10000)

    @staticmethod
    def material(kind, amount, fish_id=None, quality=0, relation="and", group=None):
        return dict(cost_type=kind, cost_amount=amount, cost_item_id=fish_id,
                    quality_level=quality, cost_relation=relation, group_id=group)

    def exchange_quote(self, costs, quantity=1):
        self.shop_repo.get_shop_items.return_value = [{"item_id": 31, "name": "兑换鱼竿"}]
        self.shop_repo.get_item_rewards.side_effect = None
        self.shop_repo.get_item_rewards.return_value = [dict(
            reward_type="rod", reward_item_id=11, reward_quantity=quantity)]
        self.shop_repo.get_item_costs.side_effect = None
        self.shop_repo.get_item_costs.return_value = costs
        fish = {41: SimpleNamespace(name="测试鱼", base_value=5000),
                42: SimpleNamespace(name="测试大鱼", base_value=500000)}
        self.templates.get_fish_by_id.side_effect = fish.get
        return self.shop.get_rod_acquisition_costs()[11]

    def test_exchange_uses_fish_sale_value_and_cheapest_quality_option(self):
        for costs, expected_cost, expected_price in (
            ([self.material("fish", 21, 41, relation="or"),
              self.material("fish", 12, 41, quality=1, relation="or")], 105000, 52500),
            ([self.material("fish", 5, 42, quality=1, relation="or"),
              self.material("fish", 12, 42)], 5000000, 2500000),
            ([self.material("fish", 13, 42)], 6500000, 3250000),
        ):
            with self.subTest(costs=costs):
                quote = self.exchange_quote(costs)
                self.assertEqual(quote["cost"], expected_cost)
                self.assertEqual(self.quote(quote["cost"]), expected_price)
                structure = self.shop._get_cost_structure(costs, 1)
                self.assertEqual(len(structure["or_choices"]), 1 if len(costs) > 1 else 0)

    def test_and_groups_sum_and_or_groups_choose_separately(self):
        quote = self.exchange_quote([
            self.material("coins", 1000, group=1),
            self.material("fish", 2, 41, group=1),
            self.material("fish", 2, 42, quality=1, relation="or", group=2),
            self.material("coins", 100000, relation="or", group=2),
            self.material("fish", 1, 41, relation="or", group=3),
            self.material("coins", 10000, relation="or", group=3),
        ])
        self.assertEqual(quote["cost"], 116000)
        self.assertEqual(self.quote(quote["cost"]), 58000)

    def test_reward_quantity_is_apportioned_before_discount_and_rounding(self):
        quote = self.exchange_quote([self.material("coins", 1001)], quantity=3)
        self.assertEqual(self.quote(quote["cost"]), 166)
        self.assertIn("→ 3 根", quote["source"])

    def test_multiple_listings_use_lowest_cost_even_if_temporarily_disabled(self):
        self.shop_repo.get_item_rewards.side_effect = lambda _: [dict(
            reward_type="rod", reward_item_id=11, reward_quantity=1)]
        self.shop_repo.get_shop_items.return_value[0]["is_active"] = False
        self.assertEqual(self.game.calculate_sell_price("rod", 2, 1, rod_id=11), 11666)

    def test_unknown_materials_free_bundles_and_invalid_fish_need_manual_price(self):
        for costs in ([self.material("premium", 100)], [self.material("item", 2, 41)],
                      [self.material("fish", 1, 999)], [], [self.material("fish", -1, 41)],
                      [self.material("coins", 100, relation="or"),
                       self.material("premium", 1, relation="or")]):
            with self.subTest(costs=costs):
                quote = self.exchange_quote(costs)
                with self.assertRaises(ValueError):
                    self.quote(quote["cost"])
        self.exchange_quote([self.material("coins", 100)])
        self.shop_repo.get_item_rewards.return_value.append(dict(
            reward_type="coins", reward_quantity=1))
        quote = self.shop.get_rod_acquisition_costs()[11]
        self.assertIsNone(quote["cost"])
        self.assertIn("组合奖励", quote["error"])

    def test_single_sale_uses_cost_and_live_override(self):
        self.inventory.get_user_rod_instances.return_value = [self.instance(refine=10)]
        self.config["rod_recycling"]["overrides"] = [{"rod_id": 11, "price": 3000}]
        result = self.service.sell_rod("test-player", 1)
        self.assertTrue(result["success"], result)
        self.assertEqual(self.user.coins, 3100)
        self.inventory.delete_rod_instance.assert_called_once_with(1)

    def test_unpriceable_single_sale_does_not_delete_or_charge(self):
        self.inventory.get_user_rod_instances.return_value = [self.instance(rod_id=13)]
        result = self.service.sell_rod("test-player", 1)
        self.assertFalse(result["success"])
        self.assertIn("单独配置回收价", result["message"])
        self.inventory.delete_rod_instance.assert_not_called()
        self.users.update.assert_not_called()

    def test_bulk_sale_preserves_protected_high_star_and_unpriceable_rods(self):
        self.rods[14] = SimpleNamespace(rod_id=14, rarity=5, purchase_cost=100000)
        self.inventory.get_user_rod_instances.return_value = [
            self.instance(refine=10), self.instance(2, 12), self.instance(3, locked=True),
            self.instance(4, equipped=True), self.instance(5, 13), self.instance(6, 14),
        ]
        result = self.service.sell_all_rods("test-player")
        self.assertTrue(result["success"], result)
        self.assertEqual(self.user.coins, 50654)
        self.shop_repo.get_all_shops.assert_called_once()
        self.assertEqual(self.inventory.delete_rod_instance.call_args_list, [call(1), call(2)])
        self.assertIn("1 根未设置可用回收价", result["message"])

    def test_single_and_bulk_overflow_do_not_delete_inventory(self):
        self.inventory.get_user_rod_instances.return_value = [self.instance()]
        self.user.coins = self.pricing.MAX_COINS - 100
        for sell in (lambda: self.service.sell_rod("test-player", 1),
                     lambda: self.service.sell_all_rods("test-player")):
            self.assertFalse(sell()["success"])
            self.inventory.delete_rod_instance.assert_not_called()
            self.users.update.assert_not_called()

    def test_sell_everything_uses_same_price_and_keeps_unpriceable_rods(self):
        self.inventory.get_user_rod_instances.return_value = [
            self.instance(refine=10), self.instance(2, 13), self.instance(3, locked=True)]
        self.inventory.get_user_accessory_instances.return_value = []
        self.inventory.sell_everything_atomic.return_value = dict(
            total_value=11666, fish_count=0, rod_count=1, rod_value=11666, accessory_count=0)
        with patch.object(self.service, "_auto_consume_money_bags", return_value=0):
            result = self.service.sell_everything_except_locked("test-player")
        self.assertTrue(result["success"], result)
        self.inventory.sell_everything_atomic.assert_called_once_with("test-player", {1: 11666}, {})
        self.shop_repo.get_all_shops.assert_called_once()
        self.assertIn("1 根鱼竿未设置可用回收价", result["message"])

    def test_atomic_sale_rolls_back_on_wallet_overflow(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE users (user_id TEXT PRIMARY KEY, coins INTEGER, max_coins INTEGER);
                CREATE TABLE fish (fish_id INTEGER, base_value INTEGER);
                CREATE TABLE user_fish_inventory (user_id TEXT, fish_id INTEGER, quantity INTEGER, quality_level INTEGER);
                CREATE TABLE user_rods (rod_instance_id INTEGER, user_id TEXT, is_locked INTEGER, is_equipped INTEGER);
                CREATE TABLE user_accessories (accessory_instance_id INTEGER, user_id TEXT, is_locked INTEGER, is_equipped INTEGER);
                INSERT INTO user_rods VALUES (1, 'test-player', 0, 0);
            """)
            conn.execute("INSERT INTO users VALUES (?, ?, ?)",
                         ("test-player", self.pricing.MAX_COINS - 1, self.pricing.MAX_COINS - 1))
            conn.commit()
            repo = self.repository.SqliteInventoryRepository(str(path))
            try:
                with self.assertRaisesRegex(ValueError, "金币超出"):
                    repo.sell_everything_atomic("test-player", {1: 2500}, {})
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_rods").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT coins FROM users").fetchone()[0], self.pricing.MAX_COINS - 1)
            finally:
                repo.close_connection()
                conn.close()


if __name__ == "__main__":
    unittest.main()

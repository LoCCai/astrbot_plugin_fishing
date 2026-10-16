import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.services.shop_service import ShopService


def equipment(template_id, instance_id, *, locked=False, equipped=False, refine=1):
    return SimpleNamespace(
        rod_id=template_id, accessory_id=template_id,
        rod_instance_id=instance_id, accessory_instance_id=instance_id,
        is_locked=locked, is_equipped=equipped, refine_level=refine,
    )


def cost(kind, template_id, amount=1, group=1, relation="and"):
    return dict(cost_type=kind, cost_item_id=template_id, cost_amount=amount,
                group_id=group, cost_relation=relation)


class EquipmentRecyclingTests(unittest.TestCase):
    def setUp(self):
        self.templates = {
            11: SimpleNamespace(rarity=3, name="竹竿", durability=100),
            12: SimpleNamespace(rarity=3, name="木竿", durability=100),
            13: SimpleNamespace(rarity=4, name="铁竿", durability=100),
        }
        self.template_repo = Mock()
        self.template_repo.get_rod_by_id.side_effect = self.templates.get
        self.template_repo.get_accessory_by_id.side_effect = self.templates.get
        self.inventory = Mock()
        self.inventory.get_user_rod_instances.return_value = []
        self.inventory.get_user_accessory_instances.return_value = []
        self.inventory.get_user_item_inventory.return_value = {}
        self.inventory.get_fish_inventory.return_value = []
        self.inventory.get_aquarium_inventory.return_value = []
        self.user = SimpleNamespace(user_id="test-player", coins=100, premium_currency=10)
        self.users = Mock()
        self.users.get_by_id.return_value = self.user
        self.shop = Mock()
        self.shop.get_shop_by_id.return_value = {"is_active": True}
        self.shop.get_shop_item_by_id.return_value = {"item_id": 1, "shop_id": 1, "name": "回收", "is_active": True}
        self.shop.get_item_rewards.return_value = [{"reward_type": "rod", "reward_item_id": 13}]
        self.service = ShopService(self.template_repo, self.inventory, self.users, self.shop)

    def purchase(self, costs, quantity=1):
        self.shop.get_item_costs.return_value = costs
        return self.service.purchase_item(self.user.user_id, 1, quantity)

    def assert_no_transaction(self):
        self.assertEqual(self.user.coins, 100)
        self.users.update.assert_not_called()
        self.inventory.delete_rod_instance.assert_not_called()
        self.inventory.delete_accessory_instance.assert_not_called()
        self.inventory.add_rod_instance.assert_not_called()
        self.shop.add_purchase_record.assert_not_called()

    def test_same_star_different_names_and_low_refinement_first(self):
        self.inventory.get_user_rod_instances.return_value = [
            equipment(11, 1, refine=8), equipment(12, 2), equipment(11, 3, refine=2),
            equipment(12, 4, locked=True), equipment(12, 5, equipped=True), equipment(13, 6),
        ]
        result = self.purchase([cost("rod", 11, 2)])
        self.assertTrue(result["success"], result)
        self.assertEqual(self.inventory.delete_rod_instance.call_args_list, [call(2), call(3)])
        self.inventory.delete_accessory_instance.assert_not_called()
        self.inventory.add_rod_instance.assert_called_once()

    def test_accessories_use_same_star_without_consuming_rods(self):
        self.inventory.get_user_accessory_instances.return_value = [equipment(12, 1)]
        self.inventory.get_user_rod_instances.return_value = [equipment(11, 2)]
        self.assertTrue(self.purchase([cost("accessory", 11)])["success"])
        self.inventory.delete_accessory_instance.assert_called_once_with(1)
        self.inventory.delete_rod_instance.assert_not_called()

    def test_types_do_not_mix(self):
        self.inventory.get_user_accessory_instances.return_value = [equipment(11, 1)]
        result = self.purchase([cost("coins", None, 10), cost("rod", 11)])
        self.assertFalse(result["success"])
        self.assertIn("3星鱼竿", result["message"])
        self.assert_no_transaction()

    def test_other_stars_locked_and_equipped_do_not_pay(self):
        self.inventory.get_user_rod_instances.return_value = [
            equipment(11, 1, locked=True), equipment(12, 2, equipped=True), equipment(13, 3),
        ]
        self.assertFalse(self.purchase([cost("rod", 11)])["success"])
        self.assert_no_transaction()

    def test_same_star_and_costs_are_added(self):
        self.inventory.get_user_rod_instances.return_value = [equipment(12, 1)]
        self.assertFalse(self.purchase([cost("rod", 11), cost("rod", 12)])["success"])
        self.assert_no_transaction()

    def test_or_backtracks_without_spending_an_and_material_twice(self):
        self.inventory.get_user_rod_instances.return_value = [equipment(12, 1)]
        costs = [cost("rod", 11), cost("rod", 12, group=2, relation="or"),
                 cost("coins", None, 10, group=2, relation="or")]
        self.assertTrue(self.purchase(costs)["success"])
        self.inventory.delete_rod_instance.assert_called_once_with(1)
        self.assertEqual(self.user.coins, 90)

    def test_different_or_groups_cannot_reuse_one_material(self):
        self.inventory.get_user_rod_instances.return_value = [equipment(11, 1)]
        costs = [cost("rod", 11, group=1, relation="or"), cost("rod", 12, group=1, relation="or"),
                 cost("rod", 11, group=2, relation="or"), cost("rod", 12, group=2, relation="or")]
        self.assertFalse(self.purchase(costs)["success"])
        self.assert_no_transaction()

    def test_quantity_multiplies_materials(self):
        self.inventory.get_user_rod_instances.return_value = [equipment(11, 1), equipment(12, 2)]
        self.assertTrue(self.purchase([cost("rod", 11)], quantity=2)["success"])
        self.assertEqual(self.inventory.delete_rod_instance.call_count, 2)
        self.assertEqual(self.inventory.add_rod_instance.call_count, 2)

    def test_missing_template_fails_without_charging_or_reward(self):
        self.assertFalse(self.purchase([cost("coins", None, 10), cost("rod", 999)])["success"])
        self.assert_no_transaction()

    def test_inventory_change_before_deduction_does_not_charge(self):
        self.inventory.get_user_rod_instances.side_effect = [[equipment(11, 1)], []]
        self.assertFalse(self.purchase([cost("coins", None, 10), cost("rod", 11)])["success"])
        self.assert_no_transaction()

    def test_shop_views_include_equipment_stars_but_rewards_keep_template(self):
        self.shop.get_shop_items.return_value = [self.shop.get_shop_item_by_id.return_value]
        self.shop.get_item_costs.return_value = [cost("rod", 12)]
        view = self.service.get_shop_details(1)["items"][0]
        self.assertEqual(view["costs"][0]["equipment_rarity"], 3)
        self.assertEqual(view["rewards"][0]["reward_item_id"], 13)


if __name__ == "__main__":
    unittest.main()

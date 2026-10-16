import asyncio
import importlib
import re
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class Plain:
    def __init__(self, text):
        self.text = text


class Node:
    def __init__(self, *, uin, name, content):
        self.content = content


class Nodes:
    def __init__(self, nodes):
        self.nodes = nodes


class Event:
    def plain_result(self, text):
        return text

    def chain_result(self, components):
        return components[0]

    def get_self_id(self):
        return "test-bot"


def result_text(results):
    return "\n".join(result if isinstance(result, str) else
                     "\n".join(node.content[0].text for node in result.nodes)
                     for result in results)


class InventoryListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        modules = {}
        for name, path in (("fishing_inventory_tests", root),
                           ("fishing_inventory_tests.handlers", root / "handlers"),
                           ("fishing_inventory_tests.draw", root / "draw")):
            modules[name] = types.ModuleType(name)
            modules[name].__path__ = [str(path)]
        for name in ("astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.message_components",
                     "astrbot.core", "astrbot.core.message", "astrbot.core.message.components"):
            modules[name] = types.ModuleType(name)
        modules["astrbot.api"].logger = Mock()
        modules["astrbot.api.event"].filter = Mock()
        modules["astrbot.api.event"].AstrMessageEvent = Event
        modules["astrbot.core.message.components"].At = type("At", (), {})
        components = modules["astrbot.api.message_components"]
        components.Plain, components.Node, components.Nodes = Plain, Node, Nodes
        cls.module_patch = patch.dict(sys.modules, modules)
        cls.module_patch.start()
        cls.utils = importlib.import_module("fishing_inventory_tests.utils")
        cls.handlers = importlib.import_module("fishing_inventory_tests.handlers.inventory_handlers")
        cls.backpack = importlib.import_module("fishing_inventory_tests.draw.backpack")

    @classmethod
    def tearDownClass(cls):
        cls.module_patch.stop()

    def setUp(self):
        self.event = Event()
        self.plugin = Mock()
        self.plugin._get_effective_user_id.return_value = "test-player"

    def collect(self, handler):
        async def collect():
            return [result async for result in handler(self.plugin, self.event)]
        return asyncio.run(collect())

    def test_short_list_is_plain_and_long_list_is_batched_without_loss(self):
        self.assertEqual(self.utils.inventory_list_results(self.event, "清单", ["短条目"]), ["清单\n短条目"])
        entries = [f"条目{i}:" + "数据" * 550 + "\n" for i in range(161)]
        results = self.utils.inventory_list_results(self.event, "清单", entries)
        self.assertEqual([len(result.nodes) for result in results], [80, 80, 1])
        text = result_text(results)
        for entry in entries:
            self.assertIn(entry.rstrip(), text)

    def test_oversize_entry_is_split_without_truncation(self):
        results = self.utils.inventory_list_results(self.event, "清单", ["前" * 2000 + "后" * 2000])
        text = result_text(results)
        self.assertEqual(text.count("前"), 2000)
        self.assertEqual(text.count("后"), 2000)
        self.assertTrue(all(len(node.content[0].text) < 1600 for result in results for node in result.nodes))

    def test_all_120_equipment_are_listed_including_low_stars_and_states(self):
        equipment = [dict(instance_id=i, display_code=f"R{i}", name=f"装备{i}",
                          rarity=5 if i == 119 else 1, is_locked=i == 0, is_equipped=i == 1)
                     for i in range(120)]
        self.plugin.inventory_service.get_user_rod_inventory.return_value = {"rods": equipment}
        self.plugin.inventory_service.get_user_accessory_inventory.return_value = {"accessories": equipment}
        for handler in (self.handlers.rod, self.handlers.accessories):
            with self.subTest(handler=handler.__name__):
                results = self.collect(handler)
                self.assertIsInstance(results[0], Nodes)
                text = result_text(results)
                for item in equipment:
                    self.assertEqual(text.count(f" - ID: {item['display_code']}\n"), 1)
                self.assertIn("🔒 已锁定", text)
                self.assertIn("✅ 已装备", text)
                self.assertNotIn("截断", text)

    def test_pond_and_peek_preserve_fish_quality_codes_totals_and_values(self):
        fish = [dict(fish_id=i + 1, name=f"鱼{i}", rarity=3, quality_level=i % 2,
                     quantity=2, actual_value=10) for i in range(120)]
        self.plugin.inventory_service.get_user_fish_pond.return_value = {
            "fishes": fish, "stats": {"total_count": 240, "total_value": 2400}}
        self.event.message_obj = SimpleNamespace(message=[], self_id="test-bot")
        self.event.message_str = "偷看鱼塘 test-neighbor"
        self.plugin.user_repo.get_by_id.return_value = SimpleNamespace(nickname="测试邻居")
        for handler in (self.handlers.pond, self.handlers.peek_pond):
            with self.subTest(handler=handler.__name__):
                results = self.collect(handler)
                self.assertIsInstance(results[0], Nodes)
                text = result_text(results)
                codes = re.findall(r"ID: (F\d+H?)(?=\n|$)", text)
                for item in fish:
                    suffix = 'H' if item['quality_level'] else ''
                    self.assertEqual(codes.count(f"F{item['fish_id']}{suffix}"), 1)
                self.assertIn("总鱼数：240", text)
                self.assertIn("总价值：2400", text)
                self.assertIn("10金币 / 个", text)

    def test_backpack_preview_does_not_hide_low_stars(self):
        rods = [dict(rarity=1, instance_id=i) for i in range(120)]
        rods[-1]["rarity"] = 5
        inventory = self.plugin.inventory_service
        inventory.get_user_rod_inventory.return_value = {"success": True, "rods": rods}
        inventory.get_user_accessory_inventory.return_value = {"success": True, "accessories": rods}
        inventory.get_user_bait_inventory.return_value = {"success": True, "baits": []}
        inventory.get_user_item_inventory.return_value = {"success": True, "items": []}
        data = self.backpack.get_user_backpack_data(inventory, "test-player")
        self.assertEqual(data["total_rods"], 120)
        self.assertEqual(len(data["rods"]), 50)
        self.assertEqual(len(data["accessories"]), 50)
        self.assertEqual(sum(item["rarity"] == 1 for item in data["rods"]), 49)
        self.assertTrue(data["is_truncated"])


if __name__ == "__main__":
    unittest.main()

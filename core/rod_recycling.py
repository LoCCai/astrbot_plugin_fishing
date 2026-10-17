from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


MAX_COINS = 2**63 - 1


def parse_rod_recycle_override(value: Any) -> int | None:
    """留空使用默认折扣，其余必须是可以结算的正整数金币。"""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not text.isascii() or not text.isdigit():
        raise ValueError("单独回收价须为正整数，留空可恢复默认五折")
    price = int(text)
    if not 1 <= price <= MAX_COINS:
        raise ValueError("单独回收价超出可结算范围")
    return price


def get_rod_recycle_override(rod_id: int | None, game_config: Mapping[str, Any]) -> int | None:
    overrides = game_config.get("rod_recycling", {}).get("overrides", [])
    for entry in reversed(overrides):
        if isinstance(entry, Mapping) and str(entry.get("rod_id")) == str(rod_id):
            return parse_rod_recycle_override(entry.get("price"))
    return None


def calculate_rod_recycle_price(
    rod_id: int | None, purchase_cost: Any, game_config: Mapping[str, Any]
) -> int:
    """鱼竿使用商店实际获得成本的折扣或单独定价，不叠加精炼倍率。"""
    settings = game_config.get("rod_recycling", {})
    override = get_rod_recycle_override(rod_id, game_config)
    if override is not None:
        return override
    try:
        cost = Decimal(str(purchase_cost))
        discount = Decimal(str(settings.get("discount", 0.5)))
        if not cost.is_finite() or cost <= 0:
            raise ValueError("未设置有效的基础获得价，请管理员单独配置回收价")
        if not discount.is_finite() or not Decimal("0.4") <= discount <= Decimal("0.6"):
            raise ValueError("鱼竿默认回收比例须在四至六折之间")
        price = cost * discount
        if price < 1 or price > MAX_COINS:
            raise ValueError("基础价格超出可结算范围，请管理员单独配置回收价")
        return int(price)
    except InvalidOperation as exc:
        raise ValueError("未设置有效的基础获得价，请管理员单独配置回收价") from exc

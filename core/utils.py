import random
from datetime import datetime, date, timedelta, timezone
from typing import List, Optional, Tuple, Any, Union

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

UTC8 = timezone(timedelta(hours=8))

# 获取当前的UTC+8时间
def get_now() -> datetime:
    return datetime.now(UTC8)

def ensure_aware(value: Union[str, datetime, None]) -> Optional[datetime]:
    """把库里读到的时间统一成 UTC+8 的 aware datetime。

    历史数据是用 naive 的 datetime.now()（服务器本地时区）写入的，直接和
    get_now() 比较会抛 "can't compare offset-naive and offset-aware"。
    这里按服务器本地时区解释 naive 值再换算到 UTC+8，UTC 部署的老数据也能
    正确对齐。
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            try:
                value = datetime.strptime(value, DATETIME_FORMAT)
            except ValueError:
                return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        # naive 值按服务器本地时区解释
        value = value.astimezone()
    return value.astimezone(UTC8)

def get_today() -> date:
    return get_now().date()

def get_last_reset_time(reset_hour: int = 0) -> datetime:
    """
    获取最近一次刷新时间点
    
    Args:
        reset_hour: 每日刷新的小时数（0-23），默认为0表示0点刷新
    
    Returns:
        最近一次刷新的时间点（datetime对象）
    
    Example:
        如果 reset_hour=6，当前时间是今天8点，返回今天6点
        如果 reset_hour=6，当前时间是今天5点，返回昨天6点
    """
    now = get_now()
    # 创建今天的刷新时间点
    today_reset = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    
    # 如果当前时间已经过了今天的刷新时间点，返回今天的刷新时间点
    if now >= today_reset:
        return today_reset
    else:
        # 否则返回昨天的刷新时间点
        return today_reset - timedelta(days=1)

def calculate_after_refine(before_value: float, refine_level: int, rarity: int = None) -> float:
    """
    计算经过精炼后的值
    根据装备稀有度使用不同的精炼加成比例
    
    精炼加成比例：
    - 1-2★装备: 15%/级 (让低星装备有更多成长空间)
    - 3★装备: 15%/级
    - 4★装备: 12%/级
    - 5★装备: 8%/级
    - 6★装备: 5%/级
    - 7★+装备: 3%/级
    
    Args:
        before_value: 精炼前的值
        refine_level: 精炼等级 (1-10)
        rarity: 装备稀有度 (如果不提供则使用默认10%)
    
    Returns:
        精炼后的值
    """
    # 如果没有提供稀有度，使用旧的10%逻辑保持兼容性
    if rarity is None:
        bonus_per_level = 0.1
    else:
        # 基于稀有度的差异化加成
        if rarity <= 3:
            bonus_per_level = 0.15  # 15%/级
        elif rarity == 4:
            bonus_per_level = 0.12  # 12%/级
        elif rarity == 5:
            bonus_per_level = 0.08  # 8%/级
        elif rarity == 6:
            bonus_per_level = 0.05  # 5%/级
        else:  # 7星+
            bonus_per_level = 0.03  # 3%/级
    
    # 计算总加成
    effective_refine_level = refine_level - 1 if refine_level <= 10 else 9
    total_bonus = bonus_per_level * effective_refine_level
    
    # 应用加成
    if before_value < 1:
        return before_value * (1 + total_bonus)
    return (before_value - 1) * (1 + total_bonus) + 1
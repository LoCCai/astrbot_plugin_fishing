"""
拉杆机(Slot Machine)游戏服务
提供多档位拉杆机玩法，支持累积奖池、连转模式、保底与幸运时段
"""

import json
import os
import random
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Callable

from astrbot.api import logger

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
try:
    from ..utils import get_now
except ImportError:
    def get_now() -> datetime:
        from datetime import timezone, timedelta as td
        return datetime.now(timezone(td(hours=8))).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 符号定义
# ---------------------------------------------------------------------------

class SlotSymbol(Enum):
    """拉杆机符号 —— 使用海洋/钓鱼主题以符合插件风格"""
    FISH = ("🐟", "小鱼", 1)         # 最常见
    CRAB = ("🦀", "螃蟹", 2)
    OCTOPUS = ("🐙", "章鱼", 3)
    SHARK = ("🦈", "鲨鱼", 4)
    WHALE = ("🐳", "鲸鱼", 5)
    GEM = ("💎", "宝石", 6)
    STAR = ("🌟", "海星", 7)         # 最稀有

    def __init__(self, emoji: str, label: str, tier: int):
        self.emoji = emoji
        self.label = label
        self.tier = tier


# 符号权重 (越小越稀有，总和 = 1000)
SYMBOL_WEIGHTS: Dict[SlotSymbol, int] = {
    SlotSymbol.FISH:    300,   # 30.0%
    SlotSymbol.CRAB:    250,   # 25.0%
    SlotSymbol.OCTOPUS: 190,   # 19.0%
    SlotSymbol.SHARK:   120,   # 12.0%
    SlotSymbol.WHALE:    75,   #  7.5%
    SlotSymbol.GEM:      45,   #  4.5%
    SlotSymbol.STAR:     20,   #  2.0%
}

# 三同赔率
TRIPLE_PAYOUTS: Dict[SlotSymbol, int] = {
    SlotSymbol.FISH:      5,    # 三鱼   ×5
    SlotSymbol.CRAB:      8,    # 三蟹   ×8
    SlotSymbol.OCTOPUS:  15,    # 三章鱼 ×15
    SlotSymbol.SHARK:    30,    # 三鲨鱼 ×30
    SlotSymbol.WHALE:    60,    # 三鲸鱼 ×60
    SlotSymbol.GEM:     120,    # 三宝石 ×120
    SlotSymbol.STAR:    250,    # 三海星 ×250
}

# 两同赔率
# 理论期望回报率 ≈ 96%（含三同 ~45% + 两同 ~51%），略有庄家优势
# 低级符号对子仅返还本金（×1），高级符号有额外奖励
TWO_SAME_PAYOUTS: Dict[SlotSymbol, int] = {
    SlotSymbol.FISH:     1,     # 两鱼   ×1 (返还本金)
    SlotSymbol.CRAB:     1,     # 两蟹   ×1
    SlotSymbol.OCTOPUS:  1,     # 两章鱼 ×1
    SlotSymbol.SHARK:    1,     # 两鲨鱼 ×1
    SlotSymbol.WHALE:    2,     # 两鲸鱼 ×2
    SlotSymbol.GEM:      3,     # 两宝石 ×3
    SlotSymbol.STAR:     5,     # 两海星 ×5
}


# ---------------------------------------------------------------------------
# 档位定义
# ---------------------------------------------------------------------------

@dataclass
class SlotTier:
    """拉杆机档位"""
    name: str
    cost: int
    jackpot_contribution: float  # 进入累积奖池的比例
    jackpot_trigger_chance: float  # 触发 Jackpot 的概率 (0~1)
    mini_jackpot_chance: float  # 触发 Mini Jackpot 的概率

DEFAULT_TIERS: Dict[str, SlotTier] = {
    "铜": SlotTier("铜桌", 10000, 0.02, 0.0, 0.0),
    "银": SlotTier("银桌", 100000, 0.03, 0.0, 0.0001),
    "金": SlotTier("金桌", 1000000, 0.03, 0.0001, 0.0003),
    "至尊": SlotTier("至尊桌", 5000000, 0.05, 0.0005, 0.001),
}


# ---------------------------------------------------------------------------
# 单次结果
# ---------------------------------------------------------------------------

@dataclass
class SpinResult:
    """单次拉杆结果"""
    symbols: List[SlotSymbol]         # 三个符号
    cost: int                         # 本次花费
    payout: int                       # 本次获得 (含本金返还)
    net: int                          # 净盈亏 = payout - cost
    payout_multiplier: float          # 赔率倍数 (0 表示无中奖)
    match_type: str                   # "triple" / "double" / "none" / "jackpot" / "mini_jackpot"
    match_desc: str                   # 人类可读描述
    jackpot_win: int = 0              # Jackpot 获得金额 (仅特殊触发)
    is_lucky_hour: bool = False       # 是否幸运时段


# ---------------------------------------------------------------------------
# SlotService
# ---------------------------------------------------------------------------

class SlotService:
    """拉杆机游戏服务"""

    def __init__(self, user_repo, log_repo, config: Dict[str, Any],
                 data_dir: Optional[str] = None):
        self.user_repo = user_repo
        self.log_repo = log_repo
        self.config = config
        self._data_dir = data_dir

        slot_config = config.get("slot", {})
        self.daily_limit: int = slot_config.get("daily_limit", 50)
        self.max_multi_spin: int = slot_config.get("max_multi_spin", 10)
        self.streak_protection: int = slot_config.get("streak_protection", 20)  # 连续N次无两同以上保底
        self.message_mode: str = slot_config.get("message_mode", "image")

        # 累积奖池（全局共享）
        self.jackpot_pool: int = slot_config.get("initial_jackpot", 0)

        # 使用 secrets 模块作为 CSPRNG 种子源，再用 random.Random 保证高效采样
        # 必须在 _refresh_lucky_hour 之前初始化
        self._rng = random.Random(secrets.randbits(256))

        # 幸运时段：每天随机 2 小时
        self._lucky_hour_start: Optional[int] = None
        self._lucky_hour_date: Optional[str] = None
        self._refresh_lucky_hour()

        # 每日使用次数跟踪  {user_id: {"date": "2026-04-01", "count": 5}}
        self._daily_usage: Dict[str, Dict[str, Any]] = {}

        # 连败计数  {user_id: int}
        self._lose_streak: Dict[str, int] = {}

        # 拉杆记录  {user_id: [SpinResult, ...]}  最近 50 条
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        self._max_history: int = 50

        # 读博记录回调
        self._gambling_record_callback: Optional[Callable] = None

        # 从文件恢复持久化状态（每日次数、奖池、连败计数）
        self._load_state()

    # ----- 配置方法 -----

    def set_gambling_record_callback(self, callback: Callable):
        """设置读博记录回调"""
        self._gambling_record_callback = callback

    def is_image_mode(self) -> bool:
        return self.message_mode == "image"

    def get_message_mode(self) -> str:
        return self.message_mode

    def set_message_mode(self, mode: str) -> Dict[str, Any]:
        if mode not in ("image", "text"):
            return {"success": False, "message": "❌ 无效模式，请使用 image 或 text"}
        self.message_mode = mode
        mode_name = "图片模式" if mode == "image" else "文本模式"
        return {"success": True, "message": f"✅ 拉杆机消息模式已切换为：{mode_name}"}

    # ----- 状态持久化 -----

    @property
    def _state_file_path(self) -> Optional[str]:
        if not self._data_dir:
            return None
        # 规范化并校验路径不越出数据目录（文件名为固定常量）
        base = os.path.normpath(self._data_dir)
        resolved = os.path.normpath(os.path.join(base, "slot_state.json"))
        if not resolved.startswith(base + os.sep):
            return None
        return resolved

    def _load_state(self):
        """从文件恢复 daily_usage / jackpot_pool / lose_streak"""
        path = self._state_file_path
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "daily_usage" in data and isinstance(data["daily_usage"], dict):
                self._daily_usage = data["daily_usage"]
            if "jackpot_pool" in data and isinstance(data["jackpot_pool"], (int, float)):
                self.jackpot_pool = int(data["jackpot_pool"])
            if "lose_streak" in data and isinstance(data["lose_streak"], dict):
                self._lose_streak = data["lose_streak"]
            logger.info(f"🎰 拉杆机状态已恢复：奖池 {self.jackpot_pool:,}，{len(self._daily_usage)} 位用户的每日记录")
        except Exception as e:
            logger.warning(f"🎰 拉杆机状态恢复失败: {e}")

    def _save_state(self):
        """将 daily_usage / jackpot_pool / lose_streak 持久化到文件"""
        path = self._state_file_path
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = {
                "daily_usage": self._daily_usage,
                "jackpot_pool": self.jackpot_pool,
                "lose_streak": self._lose_streak,
            }
            # mkstemp 在数据目录内生成固定目录+随机文件名的临时文件（无路径拼接），
            # 写完后原子替换，防止写入中途崩溃导致数据损坏
            fd, tmp_path = tempfile.mkstemp(
                dir=os.path.dirname(path), prefix="slot_state_", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                if os.path.exists(path):
                    os.replace(tmp_path, path)
                else:
                    os.rename(tmp_path, path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.warning(f"🎰 拉杆机状态保存失败: {e}")

    # ----- 幸运时段 -----

    def _refresh_lucky_hour(self):
        """每天重新随机幸运时段 (2 小时连续)"""
        today = get_now().strftime("%Y-%m-%d")
        if self._lucky_hour_date == today:
            return
        self._lucky_hour_date = today
        # 在 0~22 之间随机一个起始小时
        self._lucky_hour_start = self._rng.randint(0, 21)
        logger.info(f"🎰 今日幸运时段：{self._lucky_hour_start}:00 - {self._lucky_hour_start + 2}:00")

    def is_lucky_hour(self) -> bool:
        self._refresh_lucky_hour()
        if self._lucky_hour_start is None:
            return False
        current_hour = get_now().hour
        return self._lucky_hour_start <= current_hour < self._lucky_hour_start + 2

    def get_lucky_hour_info(self) -> str:
        self._refresh_lucky_hour()
        if self._lucky_hour_start is not None:
            return f"{self._lucky_hour_start:02d}:00 - {self._lucky_hour_start + 2:02d}:00"
        return "未知"

    # ----- 每日限额 -----

    def _get_daily_usage(self, user_id: str) -> int:
        today = get_now().strftime("%Y-%m-%d")
        usage = self._daily_usage.get(user_id)
        if usage and usage.get("date") == today:
            return usage.get("count", 0)
        return 0

    def _increment_usage(self, user_id: str, count: int = 1):
        today = get_now().strftime("%Y-%m-%d")
        usage = self._daily_usage.get(user_id)
        if usage and usage.get("date") == today:
            usage["count"] = usage.get("count", 0) + count
        else:
            self._daily_usage[user_id] = {"date": today, "count": count}

    def get_remaining_spins(self, user_id: str) -> int:
        return max(0, self.daily_limit - self._get_daily_usage(user_id))

    # ----- 核心算法 -----

    def _weighted_spin(self, lucky: bool = False) -> List[SlotSymbol]:
        """
        加权随机抽取 3 个符号。

        算法：使用 random.choices + 累积权重，时间复杂度 O(k·log n)，
        k=3, n=7 符号。
        底层 PRNG 使用 Mersenne Twister (random.Random)，初始种子来自
        secrets.randbits(256) (CSPRNG)。保证速度的同时有足够随机性。

        幸运时段：三同概率提升 50% → 通过增加高级符号权重实现。
        """
        symbols = list(SYMBOL_WEIGHTS.keys())
        weights = list(SYMBOL_WEIGHTS.values())

        if lucky:
            # 幸运时段：提高高级符号权重 (WHALE, GEM, STAR 权重 ×1.5)
            adjusted = []
            for s, w in zip(symbols, weights):
                if s.tier >= 5:
                    adjusted.append(int(w * 1.5))
                else:
                    adjusted.append(w)
            weights = adjusted

        return self._rng.choices(symbols, weights=weights, k=3)

    def _evaluate(self, symbols: List[SlotSymbol], cost: int,
                  tier: SlotTier, user_id: str) -> SpinResult:
        """评估结果并计算赔付"""
        lucky = self.is_lucky_hour()

        # 1) 检查三同
        if symbols[0] == symbols[1] == symbols[2]:
            sym = symbols[0]
            payout_mult = TRIPLE_PAYOUTS[sym]
            payout = cost * payout_mult
            self._lose_streak[user_id] = 0
            return SpinResult(
                symbols=symbols, cost=cost, payout=payout,
                net=payout - cost,
                payout_multiplier=payout_mult,
                match_type="triple",
                match_desc=f"三{sym.label}！×{payout_mult}",
                is_lucky_hour=lucky,
            )

        # 2) 检查两同
        pairs = self._find_pair(symbols)
        if pairs:
            pair_sym, _ = pairs
            payout_mult = TWO_SAME_PAYOUTS[pair_sym]
            payout = cost * payout_mult
            self._lose_streak[user_id] = 0
            return SpinResult(
                symbols=symbols, cost=cost, payout=payout,
                net=payout - cost,
                payout_multiplier=payout_mult,
                match_type="double",
                match_desc=f"两{pair_sym.label}！×{payout_mult}",
                is_lucky_hour=lucky,
            )

        # 3) 无匹配
        streak = self._lose_streak.get(user_id, 0) + 1
        self._lose_streak[user_id] = streak

        # 保底检查
        if streak >= self.streak_protection:
            # 保底触发：给一个两同结果
            self._lose_streak[user_id] = 0
            # 选择出现过的最高级符号做对子
            best = max(symbols, key=lambda s: s.tier)
            symbols = [best, best, self._rng.choices(
                list(SYMBOL_WEIGHTS.keys()),
                weights=list(SYMBOL_WEIGHTS.values()), k=1
            )[0]]
            # 确保第三个不等于前两个（否则变三同）
            while symbols[2] == best:
                symbols[2] = self._rng.choices(
                    list(SYMBOL_WEIGHTS.keys()),
                    weights=list(SYMBOL_WEIGHTS.values()), k=1
                )[0]
            payout_mult = TWO_SAME_PAYOUTS[best]
            payout = cost * payout_mult
            return SpinResult(
                symbols=symbols, cost=cost, payout=payout,
                net=payout - cost,
                payout_multiplier=payout_mult,
                match_type="double",
                match_desc=f"保底！两{best.label}！×{payout_mult}",
                is_lucky_hour=lucky,
            )

        return SpinResult(
            symbols=symbols, cost=cost, payout=0,
            net=-cost,
            payout_multiplier=0,
            match_type="none",
            match_desc="未中奖",
            is_lucky_hour=lucky,
        )

    def _find_pair(self, symbols: List[SlotSymbol]) -> Optional[Tuple[SlotSymbol, int]]:
        """找到对子符号及出现次数，返回 (符号, 出现次数) 或 None"""
        from collections import Counter
        counter = Counter(symbols)
        for sym, cnt in counter.most_common():
            if cnt >= 2:
                return (sym, cnt)
        return None

    def _check_jackpot(self, tier: SlotTier) -> Tuple[bool, bool]:
        """检查是否触发 Jackpot / Mini Jackpot，返回 (is_jackpot, is_mini)"""
        if self.jackpot_pool <= 0:
            return False, False

        roll = self._rng.random()
        if roll < tier.jackpot_trigger_chance:
            return True, False
        if roll < tier.jackpot_trigger_chance + tier.mini_jackpot_chance:
            return False, True
        return False, False

    # ----- 公开 API -----

    def spin(self, user_id: str, tier_name: str = "铜") -> Dict[str, Any]:
        """
        执行一次拉杆。

        Returns:
            dict with keys: success, message, result (SpinResult dict), ...
        """
        # 1) 验证档位
        tier_name_map = {"铜": "铜", "银": "银", "金": "金", "至尊": "至尊",
                         "copper": "铜", "silver": "银", "gold": "金", "supreme": "至尊"}
        normalized = tier_name_map.get(tier_name, tier_name)
        tier = DEFAULT_TIERS.get(normalized)
        if not tier:
            return {"success": False, "message": f"❌ 未知档位 '{tier_name}'，可选：铜/银/金/至尊"}

        cost = tier.cost

        # 2) 每日限额
        remaining = self.get_remaining_spins(user_id)
        if remaining <= 0:
            return {"success": False,
                    "message": f"❌ 今日拉杆次数已用完（{self.daily_limit}/{self.daily_limit}），明天再来吧！"}

        # 3) 检查余额
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "❌ 请先注册（/注册）"}
        if user.coins < cost:
            return {"success": False,
                    "message": f"❌ 金币不足！{tier.name}需要 {cost:,} 金币，你只有 {user.coins:,}"}

        # 4) 扣费
        user.coins -= cost
        self.user_repo.update(user)

        # 5) 累积奖池
        contribution = int(cost * tier.jackpot_contribution)
        self.jackpot_pool += contribution

        # 6) 检查 Jackpot
        is_jackpot, is_mini = self._check_jackpot(tier)

        # 7) 拉杆
        lucky = self.is_lucky_hour()
        symbols = self._weighted_spin(lucky=lucky)
        result = self._evaluate(symbols, cost, tier, user_id)

        # 8) 处理 Jackpot
        if is_jackpot and self.jackpot_pool > 0:
            jp_win = self.jackpot_pool
            self.jackpot_pool = 0
            result.jackpot_win = jp_win
            result.match_type = "jackpot"
            result.match_desc = f"🎉 JACKPOT！！！独得累积奖池 {jp_win:,} 金币！"
            result.payout += jp_win
            result.net = result.payout - cost
        elif is_mini and self.jackpot_pool > 0:
            mini_win = max(1, self.jackpot_pool // 10)
            self.jackpot_pool -= mini_win
            result.jackpot_win = mini_win
            result.match_type = "mini_jackpot"
            result.match_desc += f" + 🎊 Mini Jackpot {mini_win:,}"
            result.payout += mini_win
            result.net = result.payout - cost

        # 9) 发放奖金
        if result.payout > 0:
            payout_user = self.user_repo.get_by_id(user_id)
            if payout_user:
                payout_user.coins += result.payout
                self.user_repo.update(payout_user)

        # 10) 更新使用次数
        self._increment_usage(user_id)

        # 10.5) 持久化状态
        self._save_state()

        # 11) 记录历史
        self._add_history(user_id, result, tier.name)

        # 12) 写入统一读博记录
        if self._gambling_record_callback:
            try:
                nickname = user.nickname if user.nickname else "未知"
                self._gambling_record_callback(
                    "拉杆机", f"slot_{int(time.time()*1000)}",
                    user_id, nickname, cost, result.net, 
                    f"{tier.name} | {result.match_desc}"
                )
            except Exception as e:
                logger.error(f"拉杆机写入读博记录失败: {e}")

        # 13) 构建返回
        updated_user = self.user_repo.get_by_id(user_id)
        return {
            "success": True,
            "result": self._result_to_dict(result),
            "tier": tier.name,
            "cost": cost,
            "payout": result.payout,
            "net": result.net,
            "balance": updated_user.coins if updated_user else 0,
            "jackpot_pool": self.jackpot_pool,
            "remaining_spins": self.get_remaining_spins(user_id),
            "daily_limit": self.daily_limit,
            "is_lucky_hour": lucky,
            "lucky_hour_info": self.get_lucky_hour_info(),
            "message": self._build_text_result(result, tier, updated_user),
        }

    def multi_spin(self, user_id: str, tier_name: str, count: int) -> Dict[str, Any]:
        """连续拉杆多次"""
        requested_count = count
        count = max(1, min(count, self.max_multi_spin))

        # 预检查
        remaining = self.get_remaining_spins(user_id)
        if remaining <= 0:
            return {"success": False,
                    "message": f"❌ 今日拉杆次数已用完（{self.daily_limit}/{self.daily_limit}）"}

        actual_count = min(count, remaining)

        tier_name_map = {"铜": "铜", "银": "银", "金": "金", "至尊": "至尊",
                         "copper": "铜", "silver": "银", "gold": "金", "supreme": "至尊"}
        normalized = tier_name_map.get(tier_name, tier_name)
        tier = DEFAULT_TIERS.get(normalized)
        if not tier:
            return {"success": False, "message": f"❌ 未知档位 '{tier_name}'"}

        total_cost = tier.cost * actual_count
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "❌ 请先注册（/注册）"}
        if user.coins < total_cost:
            affordable = user.coins // tier.cost
            if affordable <= 0:
                return {"success": False,
                        "message": f"❌ 金币不足！{tier.name}需要 {tier.cost:,}/次，你只有 {user.coins:,}"}
            actual_count = affordable

        results: List[Dict[str, Any]] = []
        total_payout = 0
        total_net = 0

        for i in range(actual_count):
            r = self.spin(user_id, normalized)
            if not r["success"]:
                break
            results.append(r)
            total_payout += r["payout"]
            total_net += r["net"]

        if not results:
            return {"success": False, "message": "❌ 拉杆失败"}

        updated_user = self.user_repo.get_by_id(user_id)

        # 构建信息提示
        cap_notice = ""
        if requested_count > self.max_multi_spin:
            cap_notice = f"⚠️ 单次连转上限为 {self.max_multi_spin} 次，已自动调整\n"

        text_msg = self._build_multi_text(results, tier, total_net, updated_user)
        if cap_notice:
            text_msg = cap_notice + text_msg

        return {
            "success": True,
            "results": results,
            "count": len(results),
            "tier": tier.name,
            "total_cost": sum(r["cost"] for r in results),
            "total_payout": total_payout,
            "total_net": total_net,
            "balance": updated_user.coins if updated_user else 0,
            "jackpot_pool": self.jackpot_pool,
            "remaining_spins": self.get_remaining_spins(user_id),
            "daily_limit": self.daily_limit,
            "cap_notice": cap_notice,
            "message": text_msg,
        }

    def get_jackpot_info(self) -> Dict[str, Any]:
        """获取奖池信息"""
        return {
            "success": True,
            "jackpot_pool": self.jackpot_pool,
            "lucky_hour_info": self.get_lucky_hour_info(),
            "is_lucky_hour": self.is_lucky_hour(),
            "tiers": {name: {"name": t.name, "cost": t.cost,
                             "jackpot_chance": f"{t.jackpot_trigger_chance * 100:.2f}%",
                             "mini_chance": f"{t.mini_jackpot_chance * 100:.2f}%"}
                      for name, t in DEFAULT_TIERS.items()},
        }

    def get_user_history(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """获取用户拉杆历史"""
        records = self._history.get(user_id, [])
        return records[-limit:]

    # ----- 内部方法 -----

    def _result_to_dict(self, result: SpinResult) -> Dict[str, Any]:
        return {
            "symbols": [s.emoji for s in result.symbols],
            "symbol_labels": [s.label for s in result.symbols],
            "cost": result.cost,
            "payout": result.payout,
            "net": result.net,
            "payout_multiplier": result.payout_multiplier,
            "match_type": result.match_type,
            "match_desc": result.match_desc,
            "jackpot_win": result.jackpot_win,
            "is_lucky_hour": result.is_lucky_hour,
        }

    def _add_history(self, user_id: str, result: SpinResult, tier_name: str):
        if user_id not in self._history:
            self._history[user_id] = []
        self._history[user_id].append({
            "time": get_now().strftime("%H:%M:%S"),
            "tier": tier_name,
            "symbols": [s.emoji for s in result.symbols],
            "net": result.net,
            "match_desc": result.match_desc,
        })
        if len(self._history[user_id]) > self._max_history:
            self._history[user_id] = self._history[user_id][-self._max_history:]

    def _build_text_result(self, result: SpinResult, tier: SlotTier, user) -> str:
        """构建文本模式结果"""
        syms = " ".join(s.emoji for s in result.symbols)
        lines = [
            f"🎰 拉杆机 · {tier.name}",
            f"┏━━━━━━━━━━━┓",
            f"┃  {syms}  ┃",
            f"┗━━━━━━━━━━━┛",
        ]

        if result.match_type == "jackpot":
            lines.append(f"🎉🎉🎉 JACKPOT！！！🎉🎉🎉")
            lines.append(f"🏆 独得累积奖池：{result.jackpot_win:,} 金币")
        elif result.match_type == "mini_jackpot":
            lines.append(f"🎊 {result.match_desc}")
        elif result.match_type in ("triple", "double"):
            lines.append(f"🏆 {result.match_desc}")
        else:
            lines.append(f"💨 {result.match_desc}")

        if result.net > 0:
            lines.append(f"💰 赢得：+{result.net:,} 金币")
        elif result.net < 0:
            lines.append(f"💸 花费：{result.net:,} 金币")
        else:
            lines.append(f"⚖️ 持平")

        lines.append(f"💳 余额：{user.coins:,} 金币")
        lines.append(f"🎰 今日剩余：{self.get_remaining_spins(user.user_id)}/{self.daily_limit}")
        lines.append(f"🏆 累积奖池：{self.jackpot_pool:,}")

        if result.is_lucky_hour:
            lines.append(f"🍀 当前处于幸运时段！")

        return "\n".join(lines)

    def _build_multi_text(self, results: List[Dict], tier: SlotTier, total_net: int, user) -> str:
        """构建连转文本模式结果"""
        lines = [f"🎰 拉杆机连转 × {len(results)}  ·  {tier.name}", ""]

        for i, r in enumerate(results, 1):
            rd = r["result"]
            syms = " ".join(rd["symbols"])
            net_str = f"+{rd['net']:,}" if rd["net"] > 0 else f"{rd['net']:,}"
            desc = rd["match_desc"]
            lines.append(f"#{i}  {syms}  {desc}  {net_str}")

        lines.append("")
        net_str = f"+{total_net:,}" if total_net > 0 else f"{total_net:,}"
        lines.append(f"📊 合计盈亏：{net_str} 金币")
        lines.append(f"💳 余额：{user.coins:,} 金币")
        lines.append(f"🎰 今日剩余：{self.get_remaining_spins(user.user_id)}/{self.daily_limit}")
        lines.append(f"🏆 累积奖池：{self.jackpot_pool:,}")

        return "\n".join(lines)

"""
银行系统领域模型
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class BankAccount:
    user_id: str
    balance: int = 0
    locked_balance: int = 0
    today_withdrawn: int = 0
    last_withdraw_reset_date: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class BankWithdrawReservation:
    reservation_id: Optional[int]
    user_id: str
    amount: int
    fee_amount: int
    status: str
    ready_at: datetime
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class BankFixedDeposit:
    deposit_id: Optional[int]
    user_id: str
    principal: int
    term_days: int
    interest_rate: float
    expected_interest: int
    status: str
    started_at: datetime
    matures_at: datetime
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def calculate_withdraw_fee(today_withdrawn: int, amount: int, free_limit: int, fee_rate: float) -> int:
    """按当日已提现额度计算取款手续费。

    免费额度按天重置，超出免费额度的部分才收费。取款与确认预约都必须用
    扣款当时的 today_withdrawn 计算，否则同一份免费额度会被重复使用。
    """
    free_remaining = max(free_limit - max(today_withdrawn, 0), 0)
    taxable_amount = max(amount - free_remaining, 0)
    return max(int(taxable_amount * fee_rate), 0)


def calculate_early_withdraw_penalty(principal: int, penalty_rate: float, penalty_threshold: int) -> int:
    """计算定期存款提前取出的违约金，结果始终落在 [0, principal] 内。"""
    if principal <= penalty_threshold:
        return 0
    return max(0, min(int(principal * penalty_rate), principal))

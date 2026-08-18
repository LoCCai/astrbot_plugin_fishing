"""银行系统的 cursor 级原语。

这些函数都接收一个已经处于写事务中的 cursor，自身不开启也不提交事务。
这样银行仓储和借贷仓储可以在同一个事务里组合调用——例如逾期催收需要同时
动钱包、银行活期甚至强制解约定期，跨仓储也必须保持原子性。
"""

import sqlite3
from datetime import datetime
from typing import Dict, Optional, Tuple

from ..utils import ensure_aware, get_now

# --- 流水类型常量 ---
TX_DEPOSIT = "存款"
TX_WITHDRAW = "取款"
TX_WITHDRAW_FEE = "取款手续费"
TX_RESERVATION_HOLD = "预约锁定"
TX_RESERVATION_RELEASE = "预约释放"
TX_RESERVATION_EXPIRED = "预约过期释放"
TX_RESERVATION_WITHDRAW = "预约取款"
TX_FIXED_OPEN = "开立定期"
TX_FIXED_SETTLE = "定期到期领取"
TX_FIXED_INTEREST = "定期利息"
TX_FIXED_CANCEL = "定期提前取出"
TX_FIXED_PENALTY = "定期违约金"
TX_TAX_DAILY = "每日资产税"
TX_TAX_DEBT_ADD = "欠税挂账"
TX_TAX_DEBT_SURCHARGE = "欠税滞纳金"
TX_TAX_DEBT_REPAY = "欠税补扣"
TX_TAX_DEBT_WAIVE = "欠税减免"
TX_LOAN_COLLECT = "借贷催收扣款"
TX_ADMIN_ADJUST = "管理员调整"


def _now(now: Optional[datetime] = None) -> datetime:
    return now or get_now()


def ensure_account(cursor: sqlite3.Cursor, user_id: str, now: Optional[datetime] = None) -> None:
    """确保银行账户存在。用户不存在时会因外键约束抛错，由调用方先行校验。"""
    stamp = _now(now)
    cursor.execute("""
        INSERT OR IGNORE INTO bank_accounts
            (user_id, balance, locked_balance, today_withdrawn, last_withdraw_reset_date, created_at, updated_at)
        VALUES (?, 0, 0, 0, NULL, ?, ?)
    """, (user_id, stamp, stamp))


def get_account_row(cursor: sqlite3.Cursor, user_id: str) -> Optional[sqlite3.Row]:
    cursor.execute("SELECT * FROM bank_accounts WHERE user_id = ?", (user_id,))
    return cursor.fetchone()


def available_balance(row: Optional[sqlite3.Row]) -> int:
    """银行可动用余额 = 总余额 - 预约锁定额。"""
    if not row:
        return 0
    return max((row["balance"] or 0) - (row["locked_balance"] or 0), 0)


def get_wallet(cursor: sqlite3.Cursor, user_id: str) -> Optional[int]:
    cursor.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return row["coins"] if row else None


def user_exists(cursor: sqlite3.Cursor, user_id: str) -> bool:
    cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None


def bump_max_coins(cursor: sqlite3.Cursor, user_id: str) -> None:
    cursor.execute(
        "UPDATE users SET max_coins = coins WHERE user_id = ? AND coins > max_coins",
        (user_id,),
    )


def record_transaction(
    cursor: sqlite3.Cursor,
    user_id: str,
    tx_type: str,
    amount: int,
    wallet_delta: int = 0,
    bank_delta: int = 0,
    ref_id: Optional[int] = None,
    remark: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """写一条银行流水。必须在余额更新之后调用，以便记录期末快照。

    amount 记录这笔动作的名义金额（正数），wallet_delta / bank_delta 记录
    实际的余额变化方向。手续费、违约金这类被销毁的金额 bank_delta 为负而
    没有对应的 wallet_delta，利息则相反，账面上因此能看出造币与销毁。
    """
    account = get_account_row(cursor, user_id)
    wallet_after = get_wallet(cursor, user_id) or 0
    cursor.execute("""
        INSERT INTO bank_transactions (
            user_id, tx_type, amount, wallet_delta, bank_delta,
            wallet_after, bank_after, locked_after, ref_id, remark, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        tx_type,
        int(amount),
        int(wallet_delta),
        int(bank_delta),
        int(wallet_after),
        int(account["balance"] or 0) if account else 0,
        int(account["locked_balance"] or 0) if account else 0,
        ref_id,
        remark,
        _now(now),
    ))


# --- 欠税 ---

def get_tax_debt(cursor: sqlite3.Cursor, user_id: str) -> int:
    cursor.execute("SELECT debt_amount FROM tax_debts WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    return max(row["debt_amount"] or 0, 0) if row else 0


def add_tax_debt(
    cursor: sqlite3.Cursor, user_id: str, debt_amount: int, now: Optional[datetime] = None
) -> int:
    """挂账欠税，返回挂账后的欠税总额。"""
    debt_amount = max(int(debt_amount), 0)
    if debt_amount <= 0:
        return get_tax_debt(cursor, user_id)
    stamp = _now(now)
    cursor.execute("""
        INSERT INTO tax_debts (user_id, debt_amount, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            debt_amount = tax_debts.debt_amount + excluded.debt_amount,
            updated_at = excluded.updated_at
    """, (user_id, debt_amount, stamp, stamp))
    return get_tax_debt(cursor, user_id)


def reduce_tax_debt(
    cursor: sqlite3.Cursor, user_id: str, amount: int, now: Optional[datetime] = None
) -> Tuple[int, int]:
    """冲抵欠税，返回 (实际冲抵额, 冲抵后欠税)。"""
    amount = max(int(amount), 0)
    debt_amount = get_tax_debt(cursor, user_id)
    paid = min(amount, debt_amount)
    if paid <= 0:
        return 0, debt_amount

    debt_after = debt_amount - paid
    stamp = _now(now)
    if debt_after > 0:
        cursor.execute(
            "UPDATE tax_debts SET debt_amount = ?, updated_at = ? WHERE user_id = ?",
            (debt_after, stamp, user_id),
        )
    else:
        cursor.execute("DELETE FROM tax_debts WHERE user_id = ?", (user_id,))
    return paid, debt_after


def collect_tax_debt_from_amount(
    cursor: sqlite3.Cursor, user_id: str, amount: int, now: Optional[datetime] = None
) -> Tuple[int, int, int]:
    """从一笔待入账金额中优先补扣欠税。

    返回 (扣除欠税后的剩余金额, 补扣额, 补扣后欠税)。
    """
    amount = max(int(amount), 0)
    paid, debt_after = reduce_tax_debt(cursor, user_id, amount, now=now)
    return amount - paid, paid, debt_after


def accrue_debt_surcharge(
    cursor: sqlite3.Cursor,
    user_id: str,
    surcharge_rate: float,
    accrual_date: str,
    now: Optional[datetime] = None,
) -> int:
    """按天累计欠税滞纳金，同一天只累计一次，返回本次新增的滞纳金。"""
    surcharge_rate = max(float(surcharge_rate), 0.0)
    if surcharge_rate <= 0:
        return 0

    cursor.execute(
        "SELECT debt_amount, last_accrued_date FROM tax_debts WHERE user_id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    if not row:
        return 0
    debt_amount = max(row["debt_amount"] or 0, 0)
    if debt_amount <= 0 or row["last_accrued_date"] == accrual_date:
        return 0

    surcharge = int(debt_amount * surcharge_rate)
    stamp = _now(now)
    cursor.execute("""
        UPDATE tax_debts
        SET debt_amount = debt_amount + ?,
            last_accrued_date = ?,
            updated_at = ?
        WHERE user_id = ?
    """, (surcharge, accrual_date, stamp, user_id))
    if surcharge > 0:
        record_transaction(
            cursor, user_id, TX_TAX_DEBT_SURCHARGE, surcharge,
            remark=f"滞纳金率 {surcharge_rate:.4f}", now=stamp,
        )
    return surcharge


# --- 通用扣款 ---

def cancel_fixed_deposits_for_collection(
    cursor: sqlite3.Cursor, user_id: str, needed: int, now: Optional[datetime] = None
) -> Tuple[int, int]:
    """为强制扣款解约进行中的定期，本金入账后立即取用。

    强制解约不额外收违约金——被催收本身已是惩罚，再罚一次会双重扣款。
    按到期时间最远的先解，尽量保住玩家快到期的存单。
    返回 (取得的金额, 解约笔数)。
    """
    needed = max(int(needed), 0)
    if needed <= 0:
        return 0, 0

    stamp = _now(now)
    cursor.execute("""
        SELECT deposit_id, principal FROM bank_fixed_deposits
        WHERE user_id = ? AND status = 'active'
        ORDER BY matures_at DESC, deposit_id DESC
    """, (user_id,))
    deposits = cursor.fetchall()

    collected = 0
    cancelled = 0
    for deposit in deposits:
        if collected >= needed:
            break
        principal = max(deposit["principal"] or 0, 0)
        cursor.execute("""
            UPDATE bank_fixed_deposits
            SET status = 'cancelled', completed_at = ?, updated_at = ?
            WHERE deposit_id = ? AND status = 'active'
        """, (stamp, stamp, deposit["deposit_id"]))
        if cursor.rowcount <= 0:
            continue
        collected += principal
        cancelled += 1

    if collected > 0:
        # 先把解约本金落到活期，再由调用方从活期取走，账面上不会凭空出现资金
        cursor.execute(
            "UPDATE bank_accounts SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
            (collected, stamp, user_id),
        )
        record_transaction(
            cursor, user_id, TX_FIXED_CANCEL, collected, bank_delta=collected,
            remark=f"强制解约 {cancelled} 笔定期用于扣款", now=stamp,
        )
    return collected, cancelled


def collect_funds(
    cursor: sqlite3.Cursor,
    user_id: str,
    amount: int,
    from_wallet: bool = True,
    from_bank: bool = True,
    from_fixed: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """按 钱包 → 银行活期可用 → 定期(强制解约) 的顺序尽力扣款。

    只扣得到多少算多少，不足部分由调用方决定是挂账还是放弃。
    预约锁定的资金不参与，避免把已经承诺给玩家的取款额扣走。
    """
    amount = max(int(amount), 0)
    stamp = _now(now)
    result = {
        "requested": amount,
        "collected": 0,
        "wallet_taken": 0,
        "bank_taken": 0,
        "fixed_taken": 0,
        "cancelled_deposits": 0,
    }
    if amount <= 0:
        return result

    ensure_account(cursor, user_id, now=stamp)
    wallet_balance = get_wallet(cursor, user_id) or 0
    account = get_account_row(cursor, user_id)
    bank_avail = available_balance(account)

    remaining = amount
    wallet_taken = min(wallet_balance, remaining) if from_wallet else 0
    remaining -= wallet_taken

    bank_taken = min(bank_avail, remaining) if from_bank else 0
    remaining -= bank_taken

    fixed_taken = 0
    cancelled = 0
    if from_fixed and remaining > 0:
        released, cancelled = cancel_fixed_deposits_for_collection(
            cursor, user_id, remaining, now=stamp
        )
        if released > 0:
            # 解约本金已进入活期，从活期一并取走
            fixed_taken = min(released, remaining)
            bank_taken += fixed_taken
            remaining -= fixed_taken

    if wallet_taken > 0:
        cursor.execute(
            "UPDATE users SET coins = coins - ? WHERE user_id = ?", (wallet_taken, user_id)
        )
    if bank_taken > 0:
        cursor.execute(
            "UPDATE bank_accounts SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
            (bank_taken, stamp, user_id),
        )

    result["wallet_taken"] = wallet_taken
    result["bank_taken"] = bank_taken
    result["fixed_taken"] = fixed_taken
    result["cancelled_deposits"] = cancelled
    result["collected"] = wallet_taken + bank_taken
    return result


def collect_for_loan(
    cursor: sqlite3.Cursor,
    user_id: str,
    amount: int,
    allow_fixed: bool = True,
    now: Optional[datetime] = None,
) -> int:
    """逾期催收专用：穿透钱包、银行活期与定期扣款，返回实际扣得的金额。

    没有这条路径的话，借款人只要把钱存进银行就能让强制催收颗粒无收。
    """
    stamp = _now(now)
    outcome = collect_funds(
        cursor, user_id, amount,
        from_wallet=True, from_bank=True, from_fixed=allow_fixed, now=stamp,
    )
    collected = outcome["collected"]
    if collected > 0:
        record_transaction(
            cursor, user_id, TX_LOAN_COLLECT, collected,
            wallet_delta=-outcome["wallet_taken"],
            bank_delta=-outcome["bank_taken"],
            remark=(
                f"催收 钱包{outcome['wallet_taken']:,}/活期{outcome['bank_taken']:,}"
                + (f"/解约{outcome['cancelled_deposits']}笔定期" if outcome["cancelled_deposits"] else "")
            ),
            now=stamp,
        )
    return collected


# --- 预约 ---

def expire_stale_reservations(
    cursor: sqlite3.Cursor, user_id: Optional[str] = None, now: Optional[datetime] = None
) -> int:
    """把已过期仍未确认的预约置为 expired 并释放锁定额，返回处理笔数。

    不做这件事的话，一笔永不确认的预约会把资金永久锁死：既取不出来，也扣
    不到税，只会不断累积欠税。
    """
    stamp = _now(now)
    params: list = []
    condition = "status = 'pending' AND expires_at IS NOT NULL"
    if user_id:
        condition += " AND user_id = ?"
        params.append(user_id)
    cursor.execute(f"SELECT * FROM bank_withdraw_reservations WHERE {condition}", params)

    expired = 0
    for row in cursor.fetchall():
        expires_at = ensure_aware(row["expires_at"])
        if not expires_at or expires_at > stamp:
            continue
        cursor.execute("""
            UPDATE bank_withdraw_reservations
            SET status = 'expired', updated_at = ?
            WHERE reservation_id = ? AND status = 'pending'
        """, (stamp, row["reservation_id"]))
        if cursor.rowcount <= 0:
            continue
        cursor.execute("""
            UPDATE bank_accounts
            SET locked_balance = MAX(locked_balance - ?, 0), updated_at = ?
            WHERE user_id = ?
        """, (row["amount"], stamp, row["user_id"]))
        record_transaction(
            cursor, row["user_id"], TX_RESERVATION_EXPIRED, row["amount"],
            ref_id=row["reservation_id"], remark="预约超时未确认，已释放锁定", now=stamp,
        )
        expired += 1
    return expired


def reset_daily_withdrawal(
    cursor: sqlite3.Cursor, user_id: str, reset_date: str, now: Optional[datetime] = None
) -> None:
    cursor.execute("""
        UPDATE bank_accounts
        SET today_withdrawn = 0,
            last_withdraw_reset_date = ?,
            updated_at = ?
        WHERE user_id = ?
          AND (last_withdraw_reset_date IS NULL OR last_withdraw_reset_date != ?)
    """, (reset_date, _now(now), user_id, reset_date))

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger

from . import bank_sql
from ..database.connection_manager import DatabaseConnectionManager
from ..domain.bank_models import (
    BankAccount,
    BankFixedDeposit,
    BankTransaction,
    BankWithdrawReservation,
    calculate_early_withdraw_penalty,
    calculate_withdraw_fee,
)
from ..domain.models import User
from ..utils import ensure_aware, get_now


class SqliteBankRepository:
    """银行系统 SQLite 仓储。

    所有写操作都通过 DatabaseConnectionManager.run_in_transaction 执行：
    单事务 + 锁冲突自动重放，连接由管理器按线程复用，不再每次调用新建。
    时间一律使用 get_now()（UTC+8），与插件其它模块保持同一基准。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn_mgr = DatabaseConnectionManager(db_path)

    def close_connection(self) -> None:
        self._conn_mgr.close_connection()

    # --- 行 -> 模型 ---

    def _row_to_account(self, row: Optional[sqlite3.Row]) -> Optional[BankAccount]:
        if not row:
            return None
        allowed = set(BankAccount.__dataclass_fields__.keys())
        data = {key: value for key, value in dict(row).items() if key in allowed}
        data.setdefault("locked_balance", 0)
        for key in ("created_at", "updated_at"):
            if key in data:
                data[key] = ensure_aware(data[key])
        return BankAccount(**data)

    def _row_to_user(self, row: Optional[sqlite3.Row]) -> Optional[User]:
        if not row:
            return None
        allowed_keys = set(User.__dataclass_fields__.keys())
        data = {key: value for key, value in dict(row).items() if key in allowed_keys}
        # users 表的时间戳仍由其它仓储以 naive 写入，这里保持原样解析，
        # 避免把 aware 时间混进只认 naive 的调用方。
        for key in (
            "created_at",
            "last_login_time",
            "last_fishing_time",
            "last_wipe_bomb_time",
            "last_steal_time",
            "last_electric_fish_time",
            "last_stolen_at",
            "bait_start_time",
            "last_wof_play_time",
            "wof_last_action_time",
            "last_sicbo_time",
        ):
            if isinstance(data.get(key), str):
                try:
                    data[key] = datetime.fromisoformat(data[key])
                except ValueError:
                    pass
        return User(**data)

    def _row_to_reservation(self, row: Optional[sqlite3.Row]) -> Optional[BankWithdrawReservation]:
        if not row:
            return None
        allowed = set(BankWithdrawReservation.__dataclass_fields__.keys())
        data = {key: value for key, value in dict(row).items() if key in allowed}
        for key in ("ready_at", "expires_at", "created_at", "updated_at"):
            if key in data:
                data[key] = ensure_aware(data[key])
        return BankWithdrawReservation(**data)

    def _row_to_fixed_deposit(self, row: Optional[sqlite3.Row]) -> Optional[BankFixedDeposit]:
        if not row:
            return None
        allowed = set(BankFixedDeposit.__dataclass_fields__.keys())
        data = {key: value for key, value in dict(row).items() if key in allowed}
        for key in ("started_at", "matures_at", "completed_at", "created_at", "updated_at"):
            if key in data:
                data[key] = ensure_aware(data[key])
        return BankFixedDeposit(**data)

    def _row_to_transaction(self, row: Optional[sqlite3.Row]) -> Optional[BankTransaction]:
        if not row:
            return None
        allowed = set(BankTransaction.__dataclass_fields__.keys())
        data = {key: value for key, value in dict(row).items() if key in allowed}
        if "created_at" in data:
            data["created_at"] = ensure_aware(data["created_at"])
        return BankTransaction(**data)

    # --- 账户 ---

    def ensure_account(self, user_id: str) -> Optional[BankAccount]:
        def _op(cursor):
            bank_sql.ensure_account(cursor, user_id)
            return self._row_to_account(bank_sql.get_account_row(cursor, user_id))
        return self._conn_mgr.run_in_transaction(_op)

    def get_account(self, user_id: str) -> Optional[BankAccount]:
        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            return self._row_to_account(bank_sql.get_account_row(cursor, user_id))

    def reset_daily_withdrawal_if_needed(self, user_id: str, reset_date: str) -> Optional[BankAccount]:
        def _op(cursor):
            bank_sql.ensure_account(cursor, user_id)
            bank_sql.reset_daily_withdrawal(cursor, user_id, reset_date)
            bank_sql.expire_stale_reservations(cursor, user_id)
            return self._row_to_account(bank_sql.get_account_row(cursor, user_id))
        return self._conn_mgr.run_in_transaction(_op)

    def get_pending_reservation(self, user_id: str) -> Optional[BankWithdrawReservation]:
        """读取待确认预约。读取前先惰性回收已过期的预约。"""
        def _op(cursor):
            bank_sql.expire_stale_reservations(cursor, user_id)
            return self._pending_reservation(cursor, user_id)
        return self._conn_mgr.run_in_transaction(_op)

    def _pending_reservation(self, cursor, user_id: str) -> Optional[BankWithdrawReservation]:
        cursor.execute("""
            SELECT * FROM bank_withdraw_reservations
            WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at DESC, reservation_id DESC
            LIMIT 1
        """, (user_id,))
        return self._row_to_reservation(cursor.fetchone())

    def expire_stale_reservations(self, user_id: Optional[str] = None) -> int:
        return self._conn_mgr.run_in_transaction(
            lambda cursor: bank_sql.expire_stale_reservations(cursor, user_id)
        )

    # --- 存取款 ---

    def deposit(self, user_id: str, amount: int) -> Tuple[bool, str, Optional[BankAccount], int]:
        def _op(cursor):
            wallet = bank_sql.get_wallet(cursor, user_id)
            if wallet is None:
                return False, "用户不存在，请先注册", None, 0
            if wallet < amount:
                return False, "钱包余额不足", None, wallet

            now = get_now()
            bank_sql.ensure_account(cursor, user_id, now=now)
            cursor.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (amount, user_id))
            cursor.execute(
                "UPDATE bank_accounts SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                (amount, now, user_id),
            )
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_DEPOSIT, amount,
                wallet_delta=-amount, bank_delta=amount, now=now,
            )
            account = self._row_to_account(bank_sql.get_account_row(cursor, user_id))
            return True, "ok", account, bank_sql.get_wallet(cursor, user_id) or 0

        try:
            return self._conn_mgr.run_in_transaction(_op)
        except Exception as e:
            logger.error(f"银行存款失败: {e}")
            raise

    def withdraw(
        self,
        user_id: str,
        amount: int,
        reset_date: str,
        free_limit: int,
        fee_rate: float,
        reservation_threshold: Optional[int] = None,
    ) -> Tuple[bool, str, Optional[BankAccount], int, int, int]:
        def _op(cursor):
            wallet = bank_sql.get_wallet(cursor, user_id)
            if wallet is None:
                return False, "用户不存在，请先注册", None, 0, 0, 0

            now = get_now()
            bank_sql.ensure_account(cursor, user_id, now=now)
            bank_sql.reset_daily_withdrawal(cursor, user_id, reset_date, now=now)
            bank_sql.expire_stale_reservations(cursor, user_id, now=now)
            account_row = bank_sql.get_account_row(cursor, user_id)
            if bank_sql.available_balance(account_row) < amount:
                return False, "银行可用余额不足", self._row_to_account(account_row), wallet, 0, 0

            today_withdrawn = account_row["today_withdrawn"] or 0
            # 门槛按单笔判定（PR #17 的原始设计）。连续多笔小额取款不受限制，
            # 这是刻意留出的口子；需要收紧的服务器可以直接调低门槛。
            if reservation_threshold and amount >= reservation_threshold:
                return (
                    False,
                    f"单笔取款达到 {reservation_threshold:,} 金币需要预约",
                    self._row_to_account(account_row),
                    wallet,
                    0,
                    0,
                )

            # 手续费必须在事务内按刚重置过的 today_withdrawn 计算，
            # 调用方事务外读到的额度可能已经过期。
            fee_amount = calculate_withdraw_fee(today_withdrawn, amount, free_limit, fee_rate)
            net_amount = amount - fee_amount
            if net_amount < 0:
                return False, "手续费不能超过取款金额", self._row_to_account(account_row), wallet, 0, 0
            net_amount, debt_paid, _ = bank_sql.collect_tax_debt_from_amount(
                cursor, user_id, net_amount, now=now
            )

            cursor.execute("""
                UPDATE bank_accounts
                SET balance = balance - ?,
                    today_withdrawn = today_withdrawn + ?,
                    updated_at = ?
                WHERE user_id = ?
            """, (amount, amount, now, user_id))
            cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (net_amount, user_id))
            bank_sql.bump_max_coins(cursor, user_id)
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_WITHDRAW, amount,
                wallet_delta=net_amount, bank_delta=-amount, now=now,
            )
            self._record_fee_and_debt(cursor, user_id, fee_amount, debt_paid, now)

            account = self._row_to_account(bank_sql.get_account_row(cursor, user_id))
            wallet_after = bank_sql.get_wallet(cursor, user_id) or 0
            return True, "ok", account, wallet_after, fee_amount, debt_paid

        try:
            return self._conn_mgr.run_in_transaction(_op)
        except Exception as e:
            logger.error(f"银行取款失败: {e}")
            raise

    def _record_fee_and_debt(self, cursor, user_id: str, fee_amount: int, debt_paid: int, now) -> None:
        if fee_amount > 0:
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_WITHDRAW_FEE, fee_amount,
                bank_delta=0, remark="超出免费额度部分的手续费（销毁）", now=now,
            )
        if debt_paid > 0:
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_TAX_DEBT_REPAY, debt_paid,
                remark="出金时优先补扣欠税", now=now,
            )

    # --- 预约 ---

    def create_reservation(
        self,
        user_id: str,
        amount: int,
        fee_amount: int,
        ready_at: datetime,
        expires_at: datetime,
        max_pending: int,
    ) -> Tuple[bool, str, Optional[BankWithdrawReservation]]:
        def _op(cursor):
            if not bank_sql.user_exists(cursor, user_id):
                return False, "用户不存在，请先注册", None

            now = get_now()
            bank_sql.ensure_account(cursor, user_id, now=now)
            bank_sql.expire_stale_reservations(cursor, user_id, now=now)
            account_row = bank_sql.get_account_row(cursor, user_id)
            if bank_sql.available_balance(account_row) < amount:
                return False, "银行可用余额不足", None

            cursor.execute("""
                SELECT COUNT(*) AS cnt FROM bank_withdraw_reservations
                WHERE user_id = ? AND status = 'pending'
            """, (user_id,))
            if cursor.fetchone()["cnt"] >= max_pending:
                return False, "已有待确认的大额取款预约", None

            cursor.execute("""
                UPDATE bank_accounts
                SET locked_balance = locked_balance + ?, updated_at = ?
                WHERE user_id = ?
            """, (amount, now, user_id))
            cursor.execute("""
                INSERT INTO bank_withdraw_reservations
                    (user_id, amount, fee_amount, status, ready_at, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
            """, (user_id, amount, fee_amount, ready_at, expires_at, now, now))
            reservation_id = cursor.lastrowid
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_RESERVATION_HOLD, amount,
                ref_id=reservation_id, remark="大额取款预约锁定", now=now,
            )
            cursor.execute(
                "SELECT * FROM bank_withdraw_reservations WHERE reservation_id = ?",
                (reservation_id,),
            )
            return True, "ok", self._row_to_reservation(cursor.fetchone())

        try:
            return self._conn_mgr.run_in_transaction(_op)
        except Exception as e:
            logger.error(f"创建银行取款预约失败: {e}")
            raise

    def complete_pending_reservation(
        self, user_id: str, reset_date: str, free_limit: int, fee_rate: float
    ) -> Tuple[bool, str, Optional[BankWithdrawReservation], Optional[BankAccount], int, int]:
        def _op(cursor):
            wallet = bank_sql.get_wallet(cursor, user_id)
            if wallet is None:
                return False, "用户不存在，请先注册", None, None, 0, 0

            now = get_now()
            bank_sql.expire_stale_reservations(cursor, user_id, now=now)
            reservation = self._pending_reservation(cursor, user_id)
            if not reservation:
                return False, "没有待确认的大额取款预约", None, None, wallet, 0
            if reservation.ready_at and reservation.ready_at > now:
                return False, "预约尚未到可取时间", reservation, None, wallet, 0

            bank_sql.reset_daily_withdrawal(cursor, user_id, reset_date, now=now)
            account_row = bank_sql.get_account_row(cursor, user_id)
            if not account_row or (account_row["balance"] or 0) < reservation.amount:
                return (
                    False, "银行余额不足，无法完成预约取款", reservation,
                    self._row_to_account(account_row), wallet, 0,
                )

            # 预约时存的 fee_amount 只是下单当时的预估。免费额度按天重置，
            # 确认时必须按当前 today_withdrawn 重算，否则同一份免费额度会被
            # 预约和普通取款各用一次。
            fee_amount = calculate_withdraw_fee(
                account_row["today_withdrawn"] or 0, reservation.amount, free_limit, fee_rate
            )
            net_amount = reservation.amount - fee_amount
            if net_amount < 0:
                return (
                    False, "手续费不能超过取款金额", reservation,
                    self._row_to_account(account_row), wallet, 0,
                )
            net_amount, debt_paid, _ = bank_sql.collect_tax_debt_from_amount(
                cursor, user_id, net_amount, now=now
            )

            locked_deduction = min(account_row["locked_balance"] or 0, reservation.amount)
            cursor.execute("""
                UPDATE bank_accounts
                SET balance = balance - ?,
                    locked_balance = MAX(locked_balance - ?, 0),
                    today_withdrawn = today_withdrawn + ?,
                    updated_at = ?
                WHERE user_id = ?
            """, (reservation.amount, locked_deduction, reservation.amount, now, user_id))
            cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (net_amount, user_id))
            bank_sql.bump_max_coins(cursor, user_id)
            cursor.execute("""
                UPDATE bank_withdraw_reservations
                SET status = 'completed', fee_amount = ?, updated_at = ?
                WHERE reservation_id = ?
            """, (fee_amount, now, reservation.reservation_id))
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_RESERVATION_WITHDRAW, reservation.amount,
                wallet_delta=net_amount, bank_delta=-reservation.amount,
                ref_id=reservation.reservation_id, now=now,
            )
            self._record_fee_and_debt(cursor, user_id, fee_amount, debt_paid, now)

            cursor.execute(
                "SELECT * FROM bank_withdraw_reservations WHERE reservation_id = ?",
                (reservation.reservation_id,),
            )
            reservation = self._row_to_reservation(cursor.fetchone())
            account = self._row_to_account(bank_sql.get_account_row(cursor, user_id))
            wallet_after = bank_sql.get_wallet(cursor, user_id) or 0
            return True, "ok", reservation, account, wallet_after, debt_paid

        try:
            return self._conn_mgr.run_in_transaction(_op)
        except Exception as e:
            logger.error(f"确认银行取款预约失败: {e}")
            raise

    def cancel_pending_reservation(
        self, user_id: str, reservation_id: Optional[int] = None
    ) -> Tuple[bool, str, Optional[BankWithdrawReservation]]:
        def _op(cursor):
            if not bank_sql.user_exists(cursor, user_id):
                return False, "用户不存在，请先注册", None

            now = get_now()
            if reservation_id is not None:
                cursor.execute("""
                    SELECT * FROM bank_withdraw_reservations
                    WHERE reservation_id = ? AND user_id = ? AND status = 'pending'
                """, (reservation_id, user_id))
                reservation = self._row_to_reservation(cursor.fetchone())
            else:
                reservation = self._pending_reservation(cursor, user_id)
            if not reservation:
                return False, "没有待取消的大额取款预约", None

            cursor.execute("""
                UPDATE bank_accounts
                SET locked_balance = MAX(locked_balance - ?, 0), updated_at = ?
                WHERE user_id = ?
            """, (reservation.amount, now, user_id))
            cursor.execute("""
                UPDATE bank_withdraw_reservations
                SET status = 'cancelled', updated_at = ?
                WHERE reservation_id = ?
            """, (now, reservation.reservation_id))
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_RESERVATION_RELEASE, reservation.amount,
                ref_id=reservation.reservation_id, remark="取消预约，释放锁定", now=now,
            )
            cursor.execute(
                "SELECT * FROM bank_withdraw_reservations WHERE reservation_id = ?",
                (reservation.reservation_id,),
            )
            return True, "ok", self._row_to_reservation(cursor.fetchone())

        try:
            return self._conn_mgr.run_in_transaction(_op)
        except Exception as e:
            logger.error(f"取消银行取款预约失败: {e}")
            raise

    # --- 定期存款 ---

    def get_fixed_deposits(self, user_id: str, limit: int = 10) -> List[BankFixedDeposit]:
        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM bank_fixed_deposits
                WHERE user_id = ?
                ORDER BY
                    CASE status WHEN 'active' THEN 0 WHEN 'completed' THEN 1 ELSE 2 END,
                    matures_at ASC,
                    deposit_id DESC
                LIMIT ?
            """, (user_id, limit))
            return [self._row_to_fixed_deposit(row) for row in cursor.fetchall()]

    def get_active_fixed_deposit_count(self, user_id: str) -> int:
        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) AS cnt FROM bank_fixed_deposits
                WHERE user_id = ? AND status = 'active'
            """, (user_id,))
            return cursor.fetchone()["cnt"]

    def create_fixed_deposit(
        self,
        user_id: str,
        principal: int,
        term_days: int,
        interest_rate: float,
        expected_interest: int,
        matures_at: datetime,
        max_active: int,
    ) -> Tuple[bool, str, Optional[BankFixedDeposit], Optional[BankAccount]]:
        def _op(cursor):
            if not bank_sql.user_exists(cursor, user_id):
                return False, "用户不存在，请先注册", None, None

            now = get_now()
            bank_sql.ensure_account(cursor, user_id, now=now)
            bank_sql.expire_stale_reservations(cursor, user_id, now=now)
            account_row = bank_sql.get_account_row(cursor, user_id)
            if bank_sql.available_balance(account_row) < principal:
                return False, "银行活期可用余额不足", None, self._row_to_account(account_row)

            cursor.execute("""
                SELECT COUNT(*) AS cnt FROM bank_fixed_deposits
                WHERE user_id = ? AND status = 'active'
            """, (user_id,))
            if cursor.fetchone()["cnt"] >= max_active:
                return False, "进行中的定期存款数量已达上限", None, self._row_to_account(account_row)

            cursor.execute(
                "UPDATE bank_accounts SET balance = balance - ?, updated_at = ? WHERE user_id = ?",
                (principal, now, user_id),
            )
            cursor.execute("""
                INSERT INTO bank_fixed_deposits (
                    user_id, principal, term_days, interest_rate, expected_interest,
                    status, started_at, matures_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """, (
                user_id, principal, term_days, interest_rate, expected_interest,
                now, matures_at, now, now,
            ))
            deposit_id = cursor.lastrowid
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_FIXED_OPEN, principal,
                bank_delta=-principal, ref_id=deposit_id,
                remark=f"{term_days}天定期，利率 {interest_rate:.4f}", now=now,
            )
            cursor.execute("SELECT * FROM bank_fixed_deposits WHERE deposit_id = ?", (deposit_id,))
            deposit = self._row_to_fixed_deposit(cursor.fetchone())
            account = self._row_to_account(bank_sql.get_account_row(cursor, user_id))
            return True, "ok", deposit, account

        try:
            return self._conn_mgr.run_in_transaction(_op)
        except Exception as e:
            logger.error(f"创建银行定期存款失败: {e}")
            raise

    def _settle_deposit_row(self, cursor, row, now, force: bool = False) -> Tuple[int, int]:
        """结算一笔到期定期，返回 (入账活期金额, 欠税补扣额)。"""
        deposit_id = row["deposit_id"]
        user_id = row["user_id"]
        principal = row["principal"] or 0
        interest = row["expected_interest"] or 0
        payout = principal + interest

        cursor.execute("""
            UPDATE bank_fixed_deposits
            SET status = 'completed', completed_at = ?, updated_at = ?
            WHERE deposit_id = ? AND status = 'active'
        """, (now, now, deposit_id))
        if cursor.rowcount <= 0:
            # 已被并发领取，直接跳过，避免重复入账
            return 0, 0

        net_payout, debt_paid, _ = bank_sql.collect_tax_debt_from_amount(
            cursor, user_id, payout, now=now
        )
        cursor.execute(
            "UPDATE bank_accounts SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
            (net_payout, now, user_id),
        )
        bank_sql.record_transaction(
            cursor, user_id, bank_sql.TX_FIXED_SETTLE, principal,
            bank_delta=net_payout, ref_id=deposit_id,
            remark="定期到期结算" + ("（自动）" if force else ""), now=now,
        )
        if interest > 0:
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_FIXED_INTEREST, interest,
                ref_id=deposit_id, remark="定期利息（新增货币）", now=now,
            )
        if debt_paid > 0:
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_TAX_DEBT_REPAY, debt_paid,
                ref_id=deposit_id, remark="定期结算时补扣欠税", now=now,
            )
        return net_payout, debt_paid

    def complete_fixed_deposit(
        self, user_id: str, deposit_id: int
    ) -> Tuple[bool, str, Optional[BankFixedDeposit], Optional[BankAccount], int]:
        def _op(cursor):
            now = get_now()
            bank_sql.ensure_account(cursor, user_id, now=now)
            cursor.execute("""
                SELECT * FROM bank_fixed_deposits
                WHERE deposit_id = ? AND user_id = ? AND status = 'active'
            """, (deposit_id, user_id))
            row = cursor.fetchone()
            if not row:
                return False, "未找到可领取的定期存款", None, None, 0

            deposit = self._row_to_fixed_deposit(row)
            if deposit.matures_at and deposit.matures_at > now:
                return False, "定期存款尚未到期", deposit, None, 0

            _, debt_paid = self._settle_deposit_row(cursor, row, now)
            cursor.execute("SELECT * FROM bank_fixed_deposits WHERE deposit_id = ?", (deposit_id,))
            deposit = self._row_to_fixed_deposit(cursor.fetchone())
            account = self._row_to_account(bank_sql.get_account_row(cursor, user_id))
            return True, "ok", deposit, account, debt_paid

        try:
            return self._conn_mgr.run_in_transaction(_op)
        except Exception as e:
            logger.error(f"领取银行定期存款失败: {e}")
            raise

    def settle_matured_fixed_deposits(self, limit: int = 500) -> List[Dict[str, Any]]:
        """自动结算所有已到期但未领取的定期，返回结算明细。"""
        def _op(cursor):
            now = get_now()
            cursor.execute("""
                SELECT * FROM bank_fixed_deposits
                WHERE status = 'active'
                ORDER BY matures_at ASC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()

            settled = []
            for row in rows:
                matures_at = ensure_aware(row["matures_at"])
                if not matures_at or matures_at > now:
                    continue
                bank_sql.ensure_account(cursor, row["user_id"], now=now)
                net_payout, debt_paid = self._settle_deposit_row(cursor, row, now, force=True)
                if net_payout <= 0 and debt_paid <= 0:
                    continue
                settled.append({
                    "user_id": row["user_id"],
                    "deposit_id": row["deposit_id"],
                    "principal": row["principal"] or 0,
                    "interest": row["expected_interest"] or 0,
                    "net_payout": net_payout,
                    "debt_paid": debt_paid,
                })
            return settled

        try:
            return self._conn_mgr.run_in_transaction(_op)
        except Exception as e:
            logger.error(f"自动结算到期定期存款失败: {e}")
            raise

    def cancel_fixed_deposit(
        self, user_id: str, deposit_id: int, penalty_rate: float, penalty_threshold: int
    ) -> Tuple[bool, str, Optional[BankFixedDeposit], Optional[BankAccount], int, int]:
        def _op(cursor):
            now = get_now()
            bank_sql.ensure_account(cursor, user_id, now=now)
            cursor.execute("""
                SELECT * FROM bank_fixed_deposits
                WHERE deposit_id = ? AND user_id = ? AND status = 'active'
            """, (deposit_id, user_id))
            row = cursor.fetchone()
            if not row:
                return False, "未找到可提前取出的定期存款", None, None, 0, 0

            deposit = self._row_to_fixed_deposit(row)
            # 违约金按事务内读到的本金计算，避免调用方先查一次再传值。
            penalty_amount = calculate_early_withdraw_penalty(
                deposit.principal, penalty_rate, penalty_threshold
            )
            payout = deposit.principal - penalty_amount
            net_payout, debt_paid, _ = bank_sql.collect_tax_debt_from_amount(
                cursor, user_id, payout, now=now
            )
            cursor.execute("""
                UPDATE bank_fixed_deposits
                SET status = 'cancelled', completed_at = ?, updated_at = ?
                WHERE deposit_id = ? AND status = 'active'
            """, (now, now, deposit_id))
            if cursor.rowcount <= 0:
                return False, "未找到可提前取出的定期存款", None, None, 0, 0

            cursor.execute(
                "UPDATE bank_accounts SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
                (net_payout, now, user_id),
            )
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_FIXED_CANCEL, deposit.principal,
                bank_delta=net_payout, ref_id=deposit_id, remark="定期提前取出", now=now,
            )
            if penalty_amount > 0:
                bank_sql.record_transaction(
                    cursor, user_id, bank_sql.TX_FIXED_PENALTY, penalty_amount,
                    ref_id=deposit_id, remark="提前取出违约金（销毁）", now=now,
                )
            if debt_paid > 0:
                bank_sql.record_transaction(
                    cursor, user_id, bank_sql.TX_TAX_DEBT_REPAY, debt_paid,
                    ref_id=deposit_id, remark="提前取出时补扣欠税", now=now,
                )

            cursor.execute("SELECT * FROM bank_fixed_deposits WHERE deposit_id = ?", (deposit_id,))
            deposit = self._row_to_fixed_deposit(cursor.fetchone())
            account = self._row_to_account(bank_sql.get_account_row(cursor, user_id))
            return True, "ok", deposit, account, penalty_amount, debt_paid

        try:
            return self._conn_mgr.run_in_transaction(_op)
        except Exception as e:
            logger.error(f"提前取出银行定期存款失败: {e}")
            raise

    # --- 欠税 ---

    def get_tax_debt(self, user_id: str) -> int:
        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            return bank_sql.get_tax_debt(cursor, user_id)

    def add_tax_debt(self, user_id: str, debt_amount: int) -> int:
        def _op(cursor):
            now = get_now()
            debt_after = bank_sql.add_tax_debt(cursor, user_id, debt_amount, now=now)
            if debt_amount > 0:
                bank_sql.record_transaction(
                    cursor, user_id, bank_sql.TX_TAX_DEBT_ADD, debt_amount,
                    remark="可扣资产不足，转为欠税", now=now,
                )
            return debt_after
        return self._conn_mgr.run_in_transaction(_op)

    def repay_tax_debt_from_wallet(
        self, user_id: str, amount: Optional[int] = None
    ) -> Tuple[bool, str, int, int, int]:
        """玩家主动还税。返回 (成功, 消息, 实还金额, 剩余欠税, 钱包余额)。

        欠税原本只在从银行出金时才会被补扣，钱全在钱包里的玩家想还也还不了。
        """
        def _op(cursor):
            wallet = bank_sql.get_wallet(cursor, user_id)
            if wallet is None:
                return False, "用户不存在，请先注册", 0, 0, 0

            now = get_now()
            debt = bank_sql.get_tax_debt(cursor, user_id)
            if debt <= 0:
                return False, "你当前没有欠税", 0, 0, wallet

            target = debt if amount is None else max(int(amount), 0)
            payable = min(target, debt, wallet)
            if payable <= 0:
                return False, "钱包余额不足，无法还税", 0, debt, wallet

            cursor.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (payable, user_id))
            paid, debt_after = bank_sql.reduce_tax_debt(cursor, user_id, payable, now=now)
            bank_sql.ensure_account(cursor, user_id, now=now)
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_TAX_DEBT_REPAY, paid,
                wallet_delta=-paid, remark="玩家主动还税", now=now,
            )
            return True, "ok", paid, debt_after, bank_sql.get_wallet(cursor, user_id) or 0

        return self._conn_mgr.run_in_transaction(_op)

    def waive_tax_debt(self, user_id: str, amount: Optional[int] = None) -> Tuple[int, int]:
        """管理员减免欠税，返回 (减免额, 剩余欠税)。"""
        def _op(cursor):
            now = get_now()
            debt = bank_sql.get_tax_debt(cursor, user_id)
            target = debt if amount is None else max(int(amount), 0)
            waived, debt_after = bank_sql.reduce_tax_debt(cursor, user_id, target, now=now)
            if waived > 0:
                bank_sql.ensure_account(cursor, user_id, now=now)
                bank_sql.record_transaction(
                    cursor, user_id, bank_sql.TX_TAX_DEBT_WAIVE, waived,
                    remark="管理员减免", now=now,
                )
            return waived, debt_after
        return self._conn_mgr.run_in_transaction(_op)

    # --- 每日资产税 ---

    def get_daily_tax_subjects(self, threshold: int, asset_scope: str) -> List[Dict[str, Any]]:
        include_bank = asset_scope in ("wallet_bank", "wallet_bank_fixed")
        include_fixed = asset_scope == "wallet_bank_fixed"
        bank_expr = "COALESCE(a.balance, 0)" if include_bank else "0"
        fixed_expr = "COALESCE(fd.active_fixed_principal, 0)" if include_fixed else "0"
        assessed_expr = f"(COALESCE(u.coins, 0) + {bank_expr} + {fixed_expr})"

        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT
                    u.*,
                    COALESCE(a.balance, 0) AS bank_balance,
                    COALESCE(a.locked_balance, 0) AS locked_balance,
                    COALESCE(fd.active_fixed_principal, 0) AS active_fixed_principal,
                    {assessed_expr} AS assessed_assets
                FROM users u
                LEFT JOIN bank_accounts a ON a.user_id = u.user_id
                LEFT JOIN (
                    SELECT user_id, SUM(principal) AS active_fixed_principal
                    FROM bank_fixed_deposits
                    WHERE status = 'active'
                    GROUP BY user_id
                ) fd ON fd.user_id = u.user_id
                WHERE {assessed_expr} >= ?
            """, (threshold,))
            subjects = []
            for row in cursor.fetchall():
                subjects.append({
                    "user": self._row_to_user(row),
                    "wallet_balance": row["coins"] or 0,
                    "bank_balance": row["bank_balance"] or 0,
                    "locked_balance": row["locked_balance"] or 0,
                    "active_fixed_principal": row["active_fixed_principal"] or 0,
                    "assessed_assets": row["assessed_assets"] or 0,
                })
            return subjects

    def collect_daily_tax(
        self,
        user_id: str,
        tax_amount: int,
        deduct_scope: str = "wallet",
        reset_date: Optional[str] = None,
        surcharge_rate: float = 0.0,
    ) -> Dict[str, int]:
        """征收当日资产税，并顺带清偿历史欠税。

        清偿顺序是「先旧账后新账」：先累计滞纳金，再用可扣资产冲抵历史欠税，
        剩下的才用来缴当日税，仍不足的部分转为新欠税。只加不减的话欠税会永远
        滚下去，玩家也没有任何还清的路径。
        """
        def _op(cursor):
            now = get_now()
            result = {
                "requested_tax": max(int(tax_amount), 0),
                "surcharge": 0,
                "debt_before": 0,
                "debt_repaid": 0,
                "tax_paid": 0,
                "debt_added": 0,
                "debt_after": 0,
                "wallet_after": 0,
                "bank_after": 0,
                "assessed_balance_after": 0,
            }
            if not bank_sql.user_exists(cursor, user_id):
                return result

            bank_sql.ensure_account(cursor, user_id, now=now)
            bank_sql.expire_stale_reservations(cursor, user_id, now=now)

            if reset_date:
                result["surcharge"] = bank_sql.accrue_debt_surcharge(
                    cursor, user_id, surcharge_rate, reset_date, now=now
                )
            debt_before = bank_sql.get_tax_debt(cursor, user_id)
            result["debt_before"] = debt_before

            from_wallet = deduct_scope in ("wallet", "wallet_bank")
            from_bank = deduct_scope in ("bank", "wallet_bank")
            total_due = debt_before + result["requested_tax"]

            outcome = bank_sql.collect_funds(
                cursor, user_id, total_due,
                from_wallet=from_wallet, from_bank=from_bank, now=now,
            )
            collected = outcome["collected"]

            debt_repaid = min(collected, debt_before)
            if debt_repaid > 0:
                bank_sql.reduce_tax_debt(cursor, user_id, debt_repaid, now=now)
            tax_paid = collected - debt_repaid
            debt_added = result["requested_tax"] - tax_paid
            if debt_added > 0:
                bank_sql.add_tax_debt(cursor, user_id, debt_added, now=now)

            if collected > 0:
                bank_sql.record_transaction(
                    cursor, user_id, bank_sql.TX_TAX_DAILY, collected,
                    wallet_delta=-outcome["wallet_taken"],
                    bank_delta=-outcome["bank_taken"],
                    remark=f"当日税 {tax_paid:,} + 补缴欠税 {debt_repaid:,}",
                    now=now,
                )
            if debt_added > 0:
                bank_sql.record_transaction(
                    cursor, user_id, bank_sql.TX_TAX_DEBT_ADD, debt_added,
                    remark="可扣资产不足，转为欠税", now=now,
                )

            account_row = bank_sql.get_account_row(cursor, user_id)
            wallet_after = bank_sql.get_wallet(cursor, user_id) or 0
            bank_after = (account_row["balance"] or 0) if account_row else 0
            result.update({
                "debt_repaid": debt_repaid,
                "tax_paid": tax_paid,
                "debt_added": max(debt_added, 0),
                "debt_after": bank_sql.get_tax_debt(cursor, user_id),
                "wallet_after": wallet_after,
                "bank_after": bank_after,
                "assessed_balance_after": wallet_after + bank_after,
            })
            return result

        try:
            return self._conn_mgr.run_in_transaction(_op)
        except Exception as e:
            logger.error(f"每日资产税扣款失败: {e}")
            raise

    # --- 管理端查询 ---

    def get_admin_summary_for_users(self, user_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not user_ids:
            return {}

        summaries = {
            user_id: {
                "account_balance": 0,
                "locked_balance": 0,
                "available_balance": 0,
                "today_withdrawn": 0,
                "pending_reservation_amount": 0,
                "active_fixed_count": 0,
                "active_fixed_principal": 0,
                "active_expected_interest": 0,
                "next_maturity": None,
                "completed_fixed_count": 0,
                "cancelled_fixed_count": 0,
                "total_fixed_count": 0,
                "tax_debt": 0,
            }
            for user_id in user_ids
        }
        placeholders = ",".join(["?"] * len(user_ids))

        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT user_id, balance, locked_balance, today_withdrawn
                FROM bank_accounts
                WHERE user_id IN ({placeholders})
            """, user_ids)
            for row in cursor.fetchall():
                summary = summaries[row["user_id"]]
                summary["account_balance"] = row["balance"] or 0
                summary["locked_balance"] = row["locked_balance"] or 0
                summary["available_balance"] = max(summary["account_balance"] - summary["locked_balance"], 0)
                summary["today_withdrawn"] = row["today_withdrawn"] or 0

            cursor.execute(f"""
                SELECT user_id, COALESCE(SUM(amount), 0) AS pending_amount
                FROM bank_withdraw_reservations
                WHERE user_id IN ({placeholders}) AND status = 'pending'
                GROUP BY user_id
            """, user_ids)
            for row in cursor.fetchall():
                summaries[row["user_id"]]["pending_reservation_amount"] = row["pending_amount"] or 0

            cursor.execute(f"""
                SELECT
                    user_id,
                    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_fixed_count,
                    SUM(CASE WHEN status = 'active' THEN principal ELSE 0 END) AS active_fixed_principal,
                    SUM(CASE WHEN status = 'active' THEN expected_interest ELSE 0 END) AS active_expected_interest,
                    MIN(CASE WHEN status = 'active' THEN matures_at ELSE NULL END) AS next_maturity,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_fixed_count,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_fixed_count,
                    COUNT(*) AS total_fixed_count
                FROM bank_fixed_deposits
                WHERE user_id IN ({placeholders})
                GROUP BY user_id
            """, user_ids)
            for row in cursor.fetchall():
                summary = summaries[row["user_id"]]
                for key in (
                    "active_fixed_count",
                    "active_fixed_principal",
                    "active_expected_interest",
                    "completed_fixed_count",
                    "cancelled_fixed_count",
                    "total_fixed_count",
                ):
                    summary[key] = row[key] or 0
                summary["next_maturity"] = row["next_maturity"]

            cursor.execute(f"""
                SELECT user_id, debt_amount FROM tax_debts
                WHERE user_id IN ({placeholders})
            """, user_ids)
            for row in cursor.fetchall():
                summaries[row["user_id"]]["tax_debt"] = row["debt_amount"] or 0

        return summaries

    def get_admin_totals(self) -> Dict[str, int]:
        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COALESCE(SUM(balance), 0) AS total_account_balance,
                    COALESCE(SUM(locked_balance), 0) AS total_locked_balance,
                    COUNT(*) AS account_count
                FROM bank_accounts
            """)
            account_row = cursor.fetchone()
            cursor.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN status = 'active' THEN principal ELSE 0 END), 0) AS active_fixed_principal,
                    COALESCE(SUM(CASE WHEN status = 'active' THEN expected_interest ELSE 0 END), 0) AS active_expected_interest,
                    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_fixed_count,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_fixed_count,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_fixed_count,
                    COUNT(*) AS total_fixed_count
                FROM bank_fixed_deposits
            """)
            fixed_row = cursor.fetchone()
            cursor.execute("""
                SELECT COALESCE(SUM(debt_amount), 0) AS total_debt, COUNT(*) AS debt_user_count
                FROM tax_debts WHERE debt_amount > 0
            """)
            debt_row = cursor.fetchone()
            return {
                "total_account_balance": account_row["total_account_balance"] or 0,
                "total_locked_balance": account_row["total_locked_balance"] or 0,
                "total_available_balance": max(
                    (account_row["total_account_balance"] or 0) - (account_row["total_locked_balance"] or 0),
                    0,
                ),
                "account_count": account_row["account_count"] or 0,
                "active_fixed_principal": fixed_row["active_fixed_principal"] or 0,
                "active_expected_interest": fixed_row["active_expected_interest"] or 0,
                "active_fixed_count": fixed_row["active_fixed_count"] or 0,
                "completed_fixed_count": fixed_row["completed_fixed_count"] or 0,
                "cancelled_fixed_count": fixed_row["cancelled_fixed_count"] or 0,
                "total_fixed_count": fixed_row["total_fixed_count"] or 0,
                "total_tax_debt": debt_row["total_debt"] or 0,
                "debt_user_count": debt_row["debt_user_count"] or 0,
            }

    def get_fixed_deposits_for_admin(
        self, search: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        params: List[Any] = []
        where = ""
        if search:
            where = "WHERE d.user_id LIKE ? OR COALESCE(u.nickname, '') LIKE ?"
            keyword = f"%{search}%"
            params.extend([keyword, keyword])
        params.append(limit)

        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT
                    d.*,
                    COALESCE(u.nickname, '') AS nickname,
                    COALESCE(u.coins, 0) AS wallet_balance,
                    COALESCE(a.balance, 0) AS account_balance,
                    COALESCE(a.locked_balance, 0) AS locked_balance
                FROM bank_fixed_deposits d
                LEFT JOIN users u ON u.user_id = d.user_id
                LEFT JOIN bank_accounts a ON a.user_id = d.user_id
                {where}
                ORDER BY
                    CASE d.status WHEN 'active' THEN 0 WHEN 'completed' THEN 1 ELSE 2 END,
                    d.matures_at ASC,
                    d.deposit_id DESC
                LIMIT ?
            """, params)
            deposits = []
            for row in cursor.fetchall():
                data = dict(row)
                deposits.append({
                    "deposit": self._row_to_fixed_deposit(row),
                    "nickname": data.get("nickname"),
                    "wallet_balance": data.get("wallet_balance", 0),
                    "account_balance": data.get("account_balance", 0),
                    "locked_balance": data.get("locked_balance", 0),
                })
            return deposits

    def get_tax_debt_summary(self, user_id: Optional[str] = None) -> Dict[str, int]:
        conditions = ["debt_amount > 0"]
        params: List[Any] = []
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        where_sql = "WHERE " + " AND ".join(conditions)
        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT
                    COALESCE(SUM(debt_amount), 0) AS total_debt,
                    COUNT(*) AS debt_user_count
                FROM tax_debts
                {where_sql}
            """, params)
            row = cursor.fetchone()
            return {
                "total_debt": row["total_debt"] or 0,
                "debt_user_count": row["debt_user_count"] or 0,
            }

    def get_tax_debts_for_admin(
        self, user_id: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        conditions = ["d.debt_amount > 0"]
        params: List[Any] = []
        if user_id:
            conditions.append("d.user_id = ?")
            params.append(user_id)
        where_sql = "WHERE " + " AND ".join(conditions)
        params.append(limit)
        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT
                    d.*,
                    COALESCE(u.nickname, '') AS nickname,
                    COALESCE(u.coins, 0) AS wallet_balance,
                    COALESCE(a.balance, 0) AS account_balance,
                    COALESCE(a.locked_balance, 0) AS locked_balance
                FROM tax_debts d
                LEFT JOIN users u ON u.user_id = d.user_id
                LEFT JOIN bank_accounts a ON a.user_id = d.user_id
                {where_sql}
                ORDER BY d.debt_amount DESC, d.updated_at DESC
                LIMIT ?
            """, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_transactions(
        self, user_id: Optional[str] = None, tx_type: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        conditions = []
        params: List[Any] = []
        if user_id:
            conditions.append("t.user_id = ?")
            params.append(user_id)
        if tx_type:
            conditions.append("t.tx_type = ?")
            params.append(tx_type)
        where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT t.*, COALESCE(u.nickname, '') AS nickname
                FROM bank_transactions t
                LEFT JOIN users u ON u.user_id = t.user_id
                {where_sql}
                ORDER BY t.created_at DESC, t.transaction_id DESC
                LIMIT ?
            """, params)
            records = []
            for row in cursor.fetchall():
                data = dict(row)
                records.append({
                    "transaction": self._row_to_transaction(row),
                    "nickname": data.get("nickname"),
                })
            return records

    def get_top_users_by_total_assets(self, limit: int = 10) -> List[Dict[str, Any]]:
        """总资产榜：钱包 + 银行活期 + 进行中定期本金。

        金币榜只看钱包，银行上线后越有钱的人越会把钱藏进银行，榜单会失真。
        """
        with self._conn_mgr.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    u.user_id,
                    u.nickname,
                    u.coins,
                    u.max_coins,
                    COALESCE(a.balance, 0) AS bank_balance,
                    COALESCE(fd.active_fixed_principal, 0) AS fixed_principal,
                    (COALESCE(u.coins, 0) + COALESCE(a.balance, 0)
                        + COALESCE(fd.active_fixed_principal, 0)) AS total_assets
                FROM users u
                LEFT JOIN bank_accounts a ON a.user_id = u.user_id
                LEFT JOIN (
                    SELECT user_id, SUM(principal) AS active_fixed_principal
                    FROM bank_fixed_deposits
                    WHERE status = 'active'
                    GROUP BY user_id
                ) fd ON fd.user_id = u.user_id
                ORDER BY total_assets DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    # --- 管理端写操作 ---

    def admin_adjust_balance(self, user_id: str, delta: int, remark: str = "") -> Tuple[bool, str, int]:
        """管理员直接增减银行活期余额，返回 (成功, 消息, 调整后余额)。"""
        def _op(cursor):
            if not bank_sql.user_exists(cursor, user_id):
                return False, "用户不存在", 0
            now = get_now()
            bank_sql.ensure_account(cursor, user_id, now=now)
            account_row = bank_sql.get_account_row(cursor, user_id)
            balance = account_row["balance"] or 0
            locked = account_row["locked_balance"] or 0
            new_balance = balance + int(delta)
            if new_balance < locked:
                return False, f"调整后余额不能低于锁定额 {locked:,}", balance

            cursor.execute(
                "UPDATE bank_accounts SET balance = ?, updated_at = ? WHERE user_id = ?",
                (new_balance, now, user_id),
            )
            bank_sql.record_transaction(
                cursor, user_id, bank_sql.TX_ADMIN_ADJUST, abs(int(delta)),
                bank_delta=int(delta), remark=remark or "管理员调整活期余额", now=now,
            )
            return True, "ok", new_balance
        return self._conn_mgr.run_in_transaction(_op)

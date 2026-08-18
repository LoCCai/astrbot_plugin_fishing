from datetime import timedelta
from typing import Any, Dict, Optional

from ..domain.bank_models import calculate_withdraw_fee
from ..domain.models import TaxRecord
from ..utils import get_last_reset_time, get_now


class BankService:
    """银行服务：处理存款、取款、预约、定期与欠税。"""

    def __init__(self, bank_repo, user_repo, log_repo, config: Dict[str, Any]):
        self.bank_repo = bank_repo
        self.user_repo = user_repo
        self.log_repo = log_repo
        self.config = config

    @property
    def bank_config(self) -> Dict[str, Any]:
        return self.config.get("bank", {})

    @property
    def tax_config(self) -> Dict[str, Any]:
        return self.config.get("tax", {})

    def is_enabled(self) -> bool:
        return self.bank_config.get("enabled", True)

    def _daily_free_limit(self) -> int:
        return int(self.bank_config.get("daily_free_withdraw_limit", 1_000_000))

    def _withdraw_fee_rate(self) -> float:
        return float(self.bank_config.get("withdraw_fee_rate", 0.03))

    def _reservation_threshold(self) -> int:
        return int(self.bank_config.get("reservation_threshold", 5_000_000))

    def _reservation_delay_hours(self) -> int:
        return int(self.bank_config.get("reservation_delay_hours", 24))

    def _reservation_expire_hours(self) -> int:
        return int(self.bank_config.get("reservation_expire_hours", 72))

    def _max_pending_reservations(self) -> int:
        return int(self.bank_config.get("max_pending_reservations", 1))

    def _block_inflow_when_in_debt(self) -> bool:
        return bool(self.bank_config.get("block_inflow_when_in_debt", True))

    def _fixed_deposit_config(self) -> Dict[str, Any]:
        return self.bank_config.get("fixed_deposit", {})

    def _fixed_deposit_enabled(self) -> bool:
        return self._fixed_deposit_config().get("enabled", True)

    def _fixed_min_amount(self) -> int:
        return int(self._fixed_deposit_config().get("min_amount", 100_000))

    def _fixed_max_amount(self) -> int:
        return int(self._fixed_deposit_config().get("max_amount", 20_000_000))

    def _fixed_max_active(self) -> int:
        return int(self._fixed_deposit_config().get("max_active_deposits", 5))

    def _fixed_terms(self) -> Dict[str, float]:
        return self._fixed_deposit_config().get("terms", {"1": 0.001, "3": 0.004, "7": 0.01, "30": 0.05})

    def _auto_settle_matured(self) -> bool:
        return bool(self._fixed_deposit_config().get("auto_settle_matured", True))

    def _early_withdraw_penalty_rate(self) -> float:
        return float(self._fixed_deposit_config().get("early_withdraw_penalty_rate", 0.01))

    def _early_withdraw_penalty_threshold(self) -> int:
        return int(self._fixed_deposit_config().get("early_withdraw_penalty_threshold", 1_000_000))

    def _reset_date(self) -> str:
        reset_hour = self.config.get("daily_reset_hour", 0)
        return get_last_reset_time(reset_hour).date().isoformat()

    # --- 前置校验 ---

    def _require_inflow_open(self) -> Optional[Dict[str, Any]]:
        """入金类操作（存款、开定期）的准入校验。

        银行被关停时只封锁入金，出金一律放行——否则玩家已经存进去的钱会被
        永久锁死在一个不可用的系统里。
        """
        if not self.is_enabled():
            return {"success": False, "message": "银行系统暂未启用，当前只能取款"}
        return None

    def _require_user(self, user_id: str):
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return None, {"success": False, "message": "用户不存在，请先注册"}
        return user, None

    def _require_no_debt(self, user_id: str) -> Optional[Dict[str, Any]]:
        """欠税未清时禁止把钱往银行里藏。"""
        if not self._block_inflow_when_in_debt():
            return None
        debt = self.bank_repo.get_tax_debt(user_id)
        if debt <= 0:
            return None
        return {
            "success": False,
            "message": (
                f"❌ 你还有 {debt:,} 金币欠税未缴，无法继续存入。\n"
                f"💡 请先使用：/钓鱼银行 还税"
            ),
        }

    def _estimate_fee(self, account, amount: int) -> int:
        """仅用于展示的手续费预估；实际扣费一律由仓储在事务内重算。"""
        return calculate_withdraw_fee(
            account.today_withdrawn, amount, self._daily_free_limit(), self._withdraw_fee_rate()
        )

    def _refresh_account(self, user_id: str):
        return self.bank_repo.reset_daily_withdrawal_if_needed(user_id, self._reset_date())

    # --- 查询 ---

    def get_overview(self, user_id: str) -> Dict[str, Any]:
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return {"success": False, "message": "用户不存在，请先注册"}
        account = self._refresh_account(user_id)
        pending = self.bank_repo.get_pending_reservation(user_id)
        fixed_count = self.bank_repo.get_active_fixed_deposit_count(user_id)
        free_remaining = max(self._daily_free_limit() - account.today_withdrawn, 0)
        available = max((account.balance or 0) - (account.locked_balance or 0), 0)
        return {
            "success": True,
            "user": user,
            "account": account,
            "pending": pending,
            "fixed_count": fixed_count,
            "available_balance": available,
            "locked_balance": account.locked_balance or 0,
            "tax_debt": self.bank_repo.get_tax_debt(user_id),
            "free_remaining": free_remaining,
            "daily_free_limit": self._daily_free_limit(),
            "withdraw_fee_rate": self._withdraw_fee_rate(),
            "reservation_threshold": self._reservation_threshold(),
            "reservation_delay_hours": self._reservation_delay_hours(),
            "reservation_expire_hours": self._reservation_expire_hours(),
            "today_withdrawn": account.today_withdrawn or 0,
            "bank_enabled": self.is_enabled(),
        }

    # --- 活期 ---

    def deposit(self, user_id: str, amount: int) -> Dict[str, Any]:
        if error := self._require_inflow_open():
            return error
        _, error = self._require_user(user_id)
        if error:
            return error
        if amount <= 0:
            return {"success": False, "message": "存款金额必须大于0"}
        if error := self._require_no_debt(user_id):
            return error

        success, message, account, wallet_after = self.bank_repo.deposit(user_id, amount)
        if not success:
            return {"success": False, "message": message}
        return {
            "success": True,
            "message": (
                f"✅ 存款成功！\n"
                f"💰 存入：{amount:,} 金币\n"
                f"🏦 银行余额：{account.balance:,} 金币\n"
                f"👛 钱包余额：{wallet_after:,} 金币"
            ),
            "account": account,
            "wallet_after": wallet_after,
        }

    def withdraw(self, user_id: str, amount: int) -> Dict[str, Any]:
        _, error = self._require_user(user_id)
        if error:
            return error
        if amount <= 0:
            return {"success": False, "message": "取款金额必须大于0"}

        threshold = self._reservation_threshold()
        account = self._refresh_account(user_id)
        today_withdrawn = account.today_withdrawn or 0
        # 门槛按当日累计判定：只看单笔的话，连续取 (门槛-1) 就能无限出金。
        if today_withdrawn + amount >= threshold:
            remaining = max(threshold - today_withdrawn - 1, 0)
            hint = (
                f"💡 今日还可直接取款 {remaining:,} 金币，更多请走预约：\n"
                f"   /钓鱼银行 预约取款 {amount}"
            ) if remaining > 0 else f"💡 请使用：/钓鱼银行 预约取款 {amount}"
            return {
                "success": False,
                "message": (
                    f"❌ 当日累计取款达到 {threshold:,} 金币需要预约。\n"
                    f"📊 今日已取：{today_withdrawn:,} 金币\n{hint}"
                ),
            }

        success, message, account, wallet_after, fee_amount, debt_paid = self.bank_repo.withdraw(
            user_id,
            amount,
            self._reset_date(),
            self._daily_free_limit(),
            self._withdraw_fee_rate(),
            threshold,
        )
        if not success:
            return {"success": False, "message": message}

        self._record_withdraw_fee(user_id, fee_amount, amount, wallet_after)
        self._record_tax_debt_payment(user_id, debt_paid, amount, wallet_after)
        net_after_debt = amount - fee_amount - debt_paid
        return {
            "success": True,
            "message": self._format_withdraw_success(
                amount, fee_amount, net_after_debt, account.balance, wallet_after, debt_paid=debt_paid
            ),
            "account": account,
            "wallet_after": wallet_after,
            "fee_amount": fee_amount,
            "debt_paid": debt_paid,
        }

    def repay_tax_debt(self, user_id: str, amount: Optional[int] = None) -> Dict[str, Any]:
        """主动还税。欠税只在出金时补扣的话，钱在钱包里的玩家想还也还不了。"""
        _, error = self._require_user(user_id)
        if error:
            return error
        if amount is not None and amount <= 0:
            return {"success": False, "message": "还税金额必须大于0"}

        success, message, paid, debt_after, wallet_after = self.bank_repo.repay_tax_debt_from_wallet(
            user_id, amount
        )
        if not success:
            return {"success": False, "message": f"❌ {message}"}

        self._record_tax_debt_payment(user_id, paid, paid, wallet_after)
        return {
            "success": True,
            "message": (
                f"✅ 还税成功！\n"
                f"🧾 本次缴纳：{paid:,} 金币\n"
                f"📌 剩余欠税：{debt_after:,} 金币\n"
                f"👛 钱包余额：{wallet_after:,} 金币"
            ),
            "paid": paid,
            "debt_after": debt_after,
        }

    # --- 大额预约 ---

    def create_reservation(self, user_id: str, amount: int) -> Dict[str, Any]:
        _, error = self._require_user(user_id)
        if error:
            return error
        if amount <= 0:
            return {"success": False, "message": "预约取款金额必须大于0"}

        account = self._refresh_account(user_id)
        threshold = self._reservation_threshold()
        today_withdrawn = account.today_withdrawn or 0
        if today_withdrawn + amount < threshold:
            return {
                "success": False,
                "message": (
                    f"❌ 当日累计取款未达 {threshold:,} 金币无需预约。\n"
                    f"💡 请直接使用：/钓鱼银行 取款 {amount}"
                ),
            }

        fee_amount = self._estimate_fee(account, amount)
        now = get_now()
        ready_at = now + timedelta(hours=self._reservation_delay_hours())
        expires_at = ready_at + timedelta(hours=self._reservation_expire_hours())
        success, message, reservation = self.bank_repo.create_reservation(
            user_id,
            amount,
            fee_amount,
            ready_at,
            expires_at,
            self._max_pending_reservations(),
        )
        if not success:
            return {"success": False, "message": message}
        return {
            "success": True,
            "message": (
                f"✅ 大额取款预约成功！\n"
                f"💰 预约金额：{amount:,} 金币（预约期间该笔资金锁定）\n"
                f"💸 预计手续费：{fee_amount:,} 金币（确认时按当日剩余免费额度重算）\n"
                f"⏱️ 可确认时间：{ready_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"⌛ 过期时间：{expires_at.strftime('%Y-%m-%d %H:%M:%S')}（逾期自动作废并解锁）\n"
                f"💡 到时使用：/钓鱼银行 确认预约"
            ),
            "reservation": reservation,
        }

    def confirm_reservation(self, user_id: str) -> Dict[str, Any]:
        _, error = self._require_user(user_id)
        if error:
            return error

        success, message, reservation, account, wallet_after, debt_paid = (
            self.bank_repo.complete_pending_reservation(
                user_id,
                self._reset_date(),
                self._daily_free_limit(),
                self._withdraw_fee_rate(),
            )
        )
        if not success:
            if reservation and message == "预约尚未到可取时间":
                return {
                    "success": False,
                    "message": (
                        f"❌ 预约尚未到可取时间。\n"
                        f"⏱️ 可确认时间：{reservation.ready_at.strftime('%Y-%m-%d %H:%M:%S')}"
                    ),
                }
            return {"success": False, "message": message}

        self._record_withdraw_fee(user_id, reservation.fee_amount, reservation.amount, wallet_after)
        self._record_tax_debt_payment(user_id, debt_paid, reservation.amount, wallet_after)
        net_amount = reservation.amount - reservation.fee_amount - debt_paid
        return {
            "success": True,
            "message": self._format_withdraw_success(
                reservation.amount,
                reservation.fee_amount,
                net_amount,
                account.balance,
                wallet_after,
                prefix="✅ 预约取款完成！",
                debt_paid=debt_paid,
            ),
            "reservation": reservation,
            "account": account,
            "wallet_after": wallet_after,
        }

    def cancel_reservation(self, user_id: str) -> Dict[str, Any]:
        _, error = self._require_user(user_id)
        if error:
            return error
        success, message, reservation = self.bank_repo.cancel_pending_reservation(user_id)
        if not success:
            return {"success": False, "message": message}
        return {
            "success": True,
            "message": f"✅ 已取消大额取款预约 #{reservation.reservation_id}，锁定资金已释放。",
            "reservation": reservation,
        }

    # --- 定期 ---

    def get_fixed_terms(self) -> Dict[str, Any]:
        if not self._fixed_deposit_enabled():
            return {"success": False, "message": "银行定期存款暂未启用"}
        terms = {int(days): float(rate) for days, rate in self._fixed_terms().items()}
        return {
            "success": True,
            "terms": terms,
            "min_amount": self._fixed_min_amount(),
            "max_amount": self._fixed_max_amount(),
            "max_active": self._fixed_max_active(),
            "early_withdraw_penalty_rate": self._early_withdraw_penalty_rate(),
            "early_withdraw_penalty_threshold": self._early_withdraw_penalty_threshold(),
        }

    def create_fixed_deposit(self, user_id: str, amount: int, term_days: int) -> Dict[str, Any]:
        if error := self._require_inflow_open():
            return error
        if not self._fixed_deposit_enabled():
            return {"success": False, "message": "银行定期存款暂未启用"}
        _, error = self._require_user(user_id)
        if error:
            return error
        if amount <= 0:
            return {"success": False, "message": "定期存款金额必须大于0"}
        if amount < self._fixed_min_amount():
            return {"success": False, "message": f"定期存款最低金额为 {self._fixed_min_amount():,} 金币"}
        if amount > self._fixed_max_amount():
            return {"success": False, "message": f"定期存款最高金额为 {self._fixed_max_amount():,} 金币"}
        if error := self._require_no_debt(user_id):
            return error

        terms = self._fixed_terms()
        term_key = str(term_days)
        if term_key not in terms:
            available = "、".join(sorted(terms.keys(), key=lambda x: int(x)))
            return {"success": False, "message": f"不支持的定期天数，可选：{available} 天"}

        interest_rate = float(terms[term_key])
        expected_interest = int(amount * interest_rate)
        matures_at = get_now() + timedelta(days=term_days)
        success, message, deposit, account = self.bank_repo.create_fixed_deposit(
            user_id=user_id,
            principal=amount,
            term_days=term_days,
            interest_rate=interest_rate,
            expected_interest=expected_interest,
            matures_at=matures_at,
            max_active=self._fixed_max_active(),
        )
        if not success:
            return {"success": False, "message": message}
        return {
            "success": True,
            "message": (
                f"✅ 定期存款创建成功！\n"
                f"🧾 编号：#{deposit.deposit_id}\n"
                f"💰 本金：{amount:,} 金币\n"
                f"📈 到期收益：{expected_interest:,} 金币（{interest_rate * 100:.2f}%）\n"
                f"⏱️ 到期时间：{matures_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"🏦 活期余额：{account.balance:,} 金币"
            ),
            "deposit": deposit,
            "account": account,
        }

    def list_fixed_deposits(self, user_id: str) -> Dict[str, Any]:
        if not self._fixed_deposit_enabled():
            return {"success": False, "message": "银行定期存款暂未启用"}
        _, error = self._require_user(user_id)
        if error:
            return error
        return {
            "success": True,
            "deposits": self.bank_repo.get_fixed_deposits(user_id),
            "auto_settle": self._auto_settle_matured(),
        }

    def complete_fixed_deposit(self, user_id: str, deposit_id: int) -> Dict[str, Any]:
        if not self._fixed_deposit_enabled():
            return {"success": False, "message": "银行定期存款暂未启用"}
        _, error = self._require_user(user_id)
        if error:
            return error
        success, message, deposit, account, debt_paid = self.bank_repo.complete_fixed_deposit(
            user_id, deposit_id
        )
        if not success:
            if deposit and message == "定期存款尚未到期":
                return {
                    "success": False,
                    "message": (
                        f"❌ 定期存款尚未到期。\n"
                        f"⏱️ 到期时间：{deposit.matures_at.strftime('%Y-%m-%d %H:%M:%S')}"
                    ),
                }
            return {"success": False, "message": message}

        payout = deposit.principal + deposit.expected_interest
        net_payout = payout - debt_paid
        self._record_tax_debt_payment(user_id, debt_paid, payout, account.balance)
        message_lines = [
            "✅ 定期存款领取成功！",
            f"🧾 编号：#{deposit.deposit_id}",
            f"💰 本金：{deposit.principal:,} 金币",
            f"📈 收益：{deposit.expected_interest:,} 金币",
            f"📥 入账活期：{net_payout:,} 金币",
        ]
        if debt_paid > 0:
            message_lines.append(f"🧾 欠税补扣：{debt_paid:,} 金币")
        message_lines.append(f"🏦 活期余额：{account.balance:,} 金币")
        return {
            "success": True,
            "message": "\n".join(message_lines),
            "deposit": deposit,
            "account": account,
        }

    def cancel_fixed_deposit(self, user_id: str, deposit_id: int) -> Dict[str, Any]:
        if not self._fixed_deposit_enabled():
            return {"success": False, "message": "银行定期存款暂未启用"}
        _, error = self._require_user(user_id)
        if error:
            return error
        success, message, deposit, account, penalty_amount, debt_paid = (
            self.bank_repo.cancel_fixed_deposit(
                user_id,
                deposit_id,
                self._early_withdraw_penalty_rate(),
                self._early_withdraw_penalty_threshold(),
            )
        )
        if not success:
            return {"success": False, "message": message}

        returned_amount = deposit.principal - penalty_amount
        net_returned = returned_amount - debt_paid
        self._record_early_withdraw_penalty(user_id, penalty_amount, deposit.principal, account.balance)
        self._record_tax_debt_payment(user_id, debt_paid, returned_amount, account.balance)
        message_lines = [
            "✅ 定期存款已提前取出。",
            f"🧾 编号：#{deposit.deposit_id}",
            f"💰 返还本金：{net_returned:,} 金币",
            "📈 到期收益：0 金币",
            f"💸 违约金：{penalty_amount:,} 金币",
        ]
        if debt_paid > 0:
            message_lines.append(f"🧾 欠税补扣：{debt_paid:,} 金币")
        message_lines.append(f"🏦 活期余额：{account.balance:,} 金币")
        return {
            "success": True,
            "message": "\n".join(message_lines),
            "deposit": deposit,
            "account": account,
            "penalty_amount": penalty_amount,
        }

    def settle_matured_fixed_deposits(self) -> list:
        """每日任务调用：自动结算已到期的定期，避免本金收益一直躺着不动。"""
        if not self._fixed_deposit_enabled() or not self._auto_settle_matured():
            return []
        settled = self.bank_repo.settle_matured_fixed_deposits()
        for item in settled:
            if item.get("debt_paid"):
                self._record_tax_debt_payment(
                    item["user_id"],
                    item["debt_paid"],
                    item["principal"] + item["interest"],
                    item["net_payout"],
                )
        return settled

    def expire_stale_reservations(self) -> int:
        """每日任务调用：回收超时未确认的预约，释放被锁死的资金。"""
        return self.bank_repo.expire_stale_reservations()

    # --- 管理端 ---

    def get_admin_summary_for_users(self, users) -> Dict[str, Dict[str, Any]]:
        return self.bank_repo.get_admin_summary_for_users([user.user_id for user in users])

    def get_admin_totals(self) -> Dict[str, int]:
        return self.bank_repo.get_admin_totals()

    def get_fixed_deposits_for_admin(self, search: str = None, limit: int = 100):
        return self.bank_repo.get_fixed_deposits_for_admin(search=search or None, limit=limit)

    def get_tax_debt_summary(self, user_id: str = None) -> Dict[str, int]:
        return self.bank_repo.get_tax_debt_summary(user_id=user_id or None)

    def get_tax_debts_for_admin(self, user_id: str = None, limit: int = 50):
        return self.bank_repo.get_tax_debts_for_admin(user_id=user_id or None, limit=limit)

    def get_transactions(self, user_id: str = None, tx_type: str = None, limit: int = 100):
        return self.bank_repo.get_transactions(
            user_id=user_id or None, tx_type=tx_type or None, limit=limit
        )

    def admin_waive_tax_debt(self, user_id: str, amount: Optional[int] = None) -> Dict[str, Any]:
        waived, debt_after = self.bank_repo.waive_tax_debt(user_id, amount)
        if waived <= 0:
            return {"success": False, "message": "该用户没有可减免的欠税"}
        return {
            "success": True,
            "message": f"已减免 {waived:,} 金币欠税，剩余 {debt_after:,}",
            "waived": waived,
            "debt_after": debt_after,
        }

    def admin_adjust_balance(self, user_id: str, delta: int, remark: str = "") -> Dict[str, Any]:
        success, message, balance = self.bank_repo.admin_adjust_balance(user_id, delta, remark)
        if not success:
            return {"success": False, "message": message}
        return {
            "success": True,
            "message": f"已调整银行活期余额至 {balance:,}",
            "balance": balance,
        }

    def admin_cancel_reservation(self, user_id: str, reservation_id: int) -> Dict[str, Any]:
        success, message, reservation = self.bank_repo.cancel_pending_reservation(
            user_id, reservation_id
        )
        if not success:
            return {"success": False, "message": message}
        return {
            "success": True,
            "message": f"已取消预约 #{reservation.reservation_id} 并释放锁定资金",
        }

    # --- 记账 ---

    def _add_tax_record(
        self, user_id: str, amount: int, rate: float, original_amount: int,
        balance_after: int, tax_type: str,
    ) -> None:
        if amount <= 0:
            return
        self.log_repo.add_tax_record(TaxRecord(
            tax_id=0,
            user_id=user_id,
            tax_amount=amount,
            tax_rate=rate,
            original_amount=original_amount,
            balance_after=balance_after,
            timestamp=get_now(),
            tax_type=tax_type,
        ))

    def _record_withdraw_fee(self, user_id: str, fee_amount: int, amount: int, wallet_after: int) -> None:
        self._add_tax_record(
            user_id, fee_amount, self._withdraw_fee_rate(), amount, wallet_after, "银行取款手续费"
        )

    def _record_early_withdraw_penalty(
        self, user_id: str, penalty_amount: int, principal: int, balance_after: int
    ) -> None:
        self._add_tax_record(
            user_id, penalty_amount, self._early_withdraw_penalty_rate(),
            principal, balance_after, "定期违约金",
        )

    def _record_tax_debt_payment(
        self, user_id: str, debt_paid: int, original_amount: int, balance_after: int
    ) -> None:
        self._add_tax_record(user_id, debt_paid, 0.0, original_amount, balance_after, "欠税补扣")

    def _format_withdraw_success(
        self,
        amount: int,
        fee_amount: int,
        net_amount: int,
        bank_balance: int,
        wallet_after: int,
        prefix: str = "✅ 取款成功！",
        debt_paid: int = 0,
    ) -> str:
        message = (
            f"{prefix}\n"
            f"💰 取款金额：{amount:,} 金币\n"
            f"📥 实际到账：{net_amount:,} 金币\n"
            f"💸 取款手续费：{fee_amount:,} 金币\n"
        )
        if debt_paid > 0:
            message += f"🧾 欠税补扣：{debt_paid:,} 金币\n"
        message += (
            f"🏦 银行余额：{bank_balance:,} 金币\n"
            f"👛 钱包余额：{wallet_after:,} 金币"
        )
        return message

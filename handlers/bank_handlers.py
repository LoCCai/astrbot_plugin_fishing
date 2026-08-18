from typing import TYPE_CHECKING, Optional, Tuple

from astrbot.api.event import AstrMessageEvent

from ..utils import parse_amount, safe_datetime_handler

if TYPE_CHECKING:
    from ..main import FishingPlugin

ALL_KEYWORDS = ("全部", "全部金币", "所有", "梭哈", "all")


def _split_args(event: AstrMessageEvent):
    return event.message_str.strip().split()


async def bank_main(plugin: "FishingPlugin", event: AstrMessageEvent):
    """银行主命令。"""
    args = _split_args(event)
    user_id = plugin._get_effective_user_id(event)
    arg2 = args[2] if len(args) >= 3 else None

    if len(args) == 1:
        result = plugin.bank_service.get_overview(user_id)
        yield event.plain_result(_format_overview(result))
        return

    action = args[1]
    if action in ("存款", "存", "deposit"):
        async for r in deposit(plugin, event, amount_arg=arg2):
            yield r
    elif action in ("取款", "取", "withdraw"):
        async for r in withdraw(plugin, event, amount_arg=arg2):
            yield r
    elif action in ("预约取款", "预约", "大额取款"):
        async for r in reserve_withdraw(plugin, event, amount_arg=arg2):
            yield r
    elif action in ("确认预约", "确认取款", "确认"):
        result = plugin.bank_service.confirm_reservation(user_id)
        yield event.plain_result(result["message"])
    elif action in ("取消预约", "取消取款", "取消"):
        result = plugin.bank_service.cancel_reservation(user_id)
        yield event.plain_result(result["message"])
    elif action in ("还税", "缴税", "补税"):
        async for r in repay_tax(plugin, event, amount_arg=arg2):
            yield r
    elif action in ("流水", "账单", "明细"):
        result = plugin.bank_service.get_transactions(user_id=user_id, limit=10)
        yield event.plain_result(_format_transactions(result))
    elif action in ("定期", "定期帮助"):
        result = plugin.bank_service.get_fixed_terms()
        yield event.plain_result(_format_fixed_terms(result))
    elif action in ("定期存款", "存定期"):
        amount, error = _parse_amount_arg(plugin, event, arg2, "定期存款", require_explicit=True)
        if error:
            yield event.plain_result(error)
            return
        term_days, error = _parse_term_days(args[3] if len(args) >= 4 else None)
        if error:
            yield event.plain_result(error)
            return
        result = plugin.bank_service.create_fixed_deposit(user_id, amount, term_days)
        yield event.plain_result(result["message"])
    elif action in ("定期列表", "我的定期"):
        result = plugin.bank_service.list_fixed_deposits(user_id)
        yield event.plain_result(_format_fixed_deposits(result))
    elif action in ("定期取出", "领取定期", "取出定期"):
        deposit_id, error = _parse_deposit_id(arg2)
        if error:
            yield event.plain_result(error)
            return
        result = plugin.bank_service.complete_fixed_deposit(user_id, deposit_id)
        yield event.plain_result(result["message"])
    elif action in ("提前取出", "取消定期"):
        deposit_id, error = _parse_deposit_id(arg2)
        if error:
            yield event.plain_result(error)
            return
        result = plugin.bank_service.cancel_fixed_deposit(user_id, deposit_id)
        yield event.plain_result(result["message"])
    else:
        yield event.plain_result(_usage())


async def deposit(plugin: "FishingPlugin", event: AstrMessageEvent, amount_arg: str = None):
    user_id = plugin._get_effective_user_id(event)
    amount, error = _parse_amount_arg(plugin, event, amount_arg, "存款")
    if error:
        yield event.plain_result(error)
        return
    result = plugin.bank_service.deposit(user_id, amount)
    yield event.plain_result(result["message"])


async def withdraw(plugin: "FishingPlugin", event: AstrMessageEvent, amount_arg: str = None):
    user_id = plugin._get_effective_user_id(event)
    amount, error = _parse_amount_arg(plugin, event, amount_arg, "取款")
    if error:
        yield event.plain_result(error)
        return
    result = plugin.bank_service.withdraw(user_id, amount)
    yield event.plain_result(result["message"])


async def reserve_withdraw(plugin: "FishingPlugin", event: AstrMessageEvent, amount_arg: str = None):
    user_id = plugin._get_effective_user_id(event)
    amount, error = _parse_amount_arg(plugin, event, amount_arg, "预约取款")
    if error:
        yield event.plain_result(error)
        return
    result = plugin.bank_service.create_reservation(user_id, amount)
    yield event.plain_result(result["message"])


async def repay_tax(plugin: "FishingPlugin", event: AstrMessageEvent, amount_arg: str = None):
    """主动还税。不给这条路径的话，钱在钱包里的玩家想还也还不了。"""
    user_id = plugin._get_effective_user_id(event)
    amount = None
    if amount_arg and amount_arg not in ALL_KEYWORDS:
        try:
            amount = parse_amount(amount_arg)
        except ValueError as e:
            yield event.plain_result(f"❌ 还税金额格式错误：{e}")
            return
    result = plugin.bank_service.repay_tax_debt(user_id, amount)
    yield event.plain_result(result["message"])


def _resolve_all_amount(plugin: "FishingPlugin", user_id: str, action_name: str) -> Tuple[Optional[int], Optional[str]]:
    """把「全部」换算成具体金额：存款看钱包，取款看银行可用余额。"""
    overview = plugin.bank_service.get_overview(user_id)
    if not overview.get("success"):
        return None, overview.get("message", "❌ 查询账户失败")
    if action_name == "存款":
        amount = overview["user"].coins
        if amount <= 0:
            return None, "❌ 钱包里没有金币可存"
        return amount, None

    amount = overview.get("available_balance", 0)
    if amount <= 0:
        return None, "❌ 银行没有可取的余额"
    return amount, None


def _parse_amount_arg(
    plugin: "FishingPlugin",
    event: AstrMessageEvent,
    amount_arg: str,
    action_name: str,
    require_explicit: bool = False,
):
    if amount_arg is None and not require_explicit:
        # 形如 /钓鱼存款 100万 的快捷命令，金额在第二段
        args = _split_args(event)
        amount_arg = args[1] if len(args) >= 2 else None
    if not amount_arg:
        return None, f"❌ 请指定{action_name}金额，例如：/钓鱼银行 {action_name} 100万（也可写「全部」）"

    if amount_arg in ALL_KEYWORDS:
        return _resolve_all_amount(plugin, plugin._get_effective_user_id(event), action_name)

    try:
        amount = parse_amount(amount_arg)
    except ValueError:
        return None, (
            f"❌ 无法识别的{action_name}金额：{amount_arg}\n"
            f"💡 支持 1000000 / 100万 / 1千万 / 全部"
        )
    return amount, None


def _parse_term_days(term_arg: str):
    if not term_arg:
        return None, "❌ 请指定定期天数，例如：/钓鱼银行 定期存款 100万 7"
    try:
        term_days = int(term_arg.replace("天", ""))
    except ValueError:
        return None, "❌ 定期天数格式错误，请使用 /钓鱼银行 定期 查看可选档位"
    return term_days, None


def _parse_deposit_id(deposit_id_arg: str):
    if not deposit_id_arg:
        return None, "❌ 请指定定期编号，例如：/钓鱼银行 定期取出 1"
    try:
        deposit_id = int(deposit_id_arg.lstrip("#"))
    except ValueError:
        return None, "❌ 定期编号格式错误"
    return deposit_id, None


def _format_overview(result):
    if not result.get("success"):
        return result.get("message", "查看银行失败")

    user = result["user"]
    account = result["account"]
    pending = result.get("pending")
    locked = result.get("locked_balance", 0)
    tax_debt = result.get("tax_debt", 0)

    message = (
        "【🏦 银行账户】\n"
        f"👛 钱包余额：{user.coins:,} 金币\n"
        f"🏦 银行余额：{account.balance:,} 金币\n"
        f"✅ 可用余额：{result.get('available_balance', 0):,} 金币\n"
    )
    if locked > 0:
        message += f"🔒 预约锁定：{locked:,} 金币（确认或取消预约后释放）\n"
    if tax_debt > 0:
        message += (
            f"🧾 欠税未缴：{tax_debt:,} 金币\n"
            f"   ⚠️ 出金时会优先补扣，且无法继续存入\n"
            f"   💡 可用 /钓鱼银行 还税 直接从钱包缴清\n"
        )
    message += (
        f"📄 进行中定期：{result.get('fixed_count', 0)} 笔\n"
        f"🆓 今日免费提现剩余：{result['free_remaining']:,}/{result['daily_free_limit']:,} 金币\n"
        f"💸 超额取款手续费：{result['withdraw_fee_rate'] * 100:.1f}%\n"
        f"📌 大额预约门槛：当日累计 {result['reservation_threshold']:,} 金币\n"
        f"📊 今日已取：{result.get('today_withdrawn', 0):,} 金币\n"
    )
    if not result.get("bank_enabled", True):
        message += "\n⚠️ 银行已停止新增存款，仅可取款。\n"

    if pending:
        ready_at = safe_datetime_handler(pending.ready_at)
        expires_at = safe_datetime_handler(pending.expires_at) if pending.expires_at else None
        message += (
            "\n【待确认预约】\n"
            f"🧾 编号：#{pending.reservation_id}\n"
            f"💰 金额：{pending.amount:,} 金币\n"
            f"💸 预计手续费：{pending.fee_amount:,} 金币\n"
            f"⏱️ 可确认时间：{ready_at}\n"
        )
        if expires_at:
            message += f"⌛ 过期时间：{expires_at}\n"
        message += "💡 使用：/钓鱼银行 确认预约"
    else:
        message += "\n暂无待确认预约。"
    return message


def _format_fixed_terms(result):
    if not result.get("success"):
        return result.get("message", "查看定期规则失败")
    message = (
        "【🏦 银行定期】\n"
        f"单笔范围：{result['min_amount']:,} - {result['max_amount']:,} 金币\n"
        f"最多进行中：{result['max_active']} 笔\n"
        f"提前取出：收益清零，本金超过 {result['early_withdraw_penalty_threshold']:,} 金币收 "
        f"{result['early_withdraw_penalty_rate'] * 100:.1f}% 违约金\n\n"
        "可选档位：\n"
    )
    for days, rate in sorted(result["terms"].items()):
        message += f"- {days} 天：{rate * 100:.2f}%\n"
    message += "\n用法：/钓鱼银行 定期存款 金额 天数"
    return message


def _format_fixed_deposits(result):
    if not result.get("success"):
        return result.get("message", "查看定期列表失败")
    deposits = result.get("deposits", [])
    if not deposits:
        return "📄 你还没有定期存款。"
    message = "【📄 我的定期存款】\n"
    for deposit in deposits:
        matures_at = safe_datetime_handler(deposit.matures_at)
        status_text = {
            "active": "进行中",
            "completed": "已领取",
            "cancelled": "已提前取出",
        }.get(deposit.status, deposit.status)
        message += (
            f"\n#{deposit.deposit_id} [{status_text}]\n"
            f"本金：{deposit.principal:,} 金币\n"
            f"周期：{deposit.term_days} 天\n"
            f"收益：{deposit.expected_interest:,} 金币（{deposit.interest_rate * 100:.2f}%）\n"
            f"到期：{matures_at}\n"
        )
    message += "\n领取：/钓鱼银行 定期取出 编号\n提前取出：/钓鱼银行 提前取出 编号"
    if result.get("auto_settle"):
        message += "\n💡 到期未领取的定期会在每日结算时自动入账活期。"
    return message


def _format_transactions(records):
    if not records:
        return "📄 暂无银行流水。"
    message = "【📄 最近银行流水】\n"
    for item in records:
        tx = item["transaction"]
        stamp = safe_datetime_handler(tx.created_at)
        message += f"\n{stamp} {tx.tx_type} {tx.amount:,}"
        if tx.remark:
            message += f"\n  {tx.remark}"
    return message


def _usage():
    return (
        "【🏦 银行帮助】\n"
        "/钓鱼银行 - 查看银行账户\n"
        "/钓鱼银行 存款 金额|全部\n"
        "/钓鱼银行 取款 金额|全部\n"
        "/钓鱼银行 预约取款 金额\n"
        "/钓鱼银行 确认预约\n"
        "/钓鱼银行 取消预约\n"
        "/钓鱼银行 还税 [金额]\n"
        "/钓鱼银行 流水\n"
        "/钓鱼银行 定期\n"
        "/钓鱼银行 定期存款 金额 天数\n"
        "/钓鱼银行 定期列表\n"
        "/钓鱼银行 定期取出 编号\n"
        "/钓鱼银行 提前取出 编号"
    )

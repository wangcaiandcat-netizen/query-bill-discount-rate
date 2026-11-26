"""Apply the configured bank and amount spreads to a DeepFin reference rate."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
import re

from bill_core import ROOT, clean_name, dates_for_bill, load_json, money
from discount_client import valid_rate
from quote_matching import ALIASES, bank_identity

BUSINESS_ALIASES = {
    "广州农商银行": "广州农村商业银行",
    "广州农商行": "广州农村商业银行",
    "上海农商银行": "上海农村商业银行",
    "上海农商行": "上海农村商业银行",
    "北京农商银行": "北京农村商业银行",
    "北京农商行": "北京农村商业银行",
    "深圳农商银行": "深圳农村商业银行",
    "深圳农商行": "深圳农村商业银行",
}


@lru_cache(maxsize=1)
def policy():
    return load_json(ROOT / "references" / "spread-policy.json")


def normalized_name(value):
    name = clean_name(value)
    name = ALIASES.get(name, name)
    name = re.sub(r"股份有限公司|有限责任公司|有限公司", "", name)
    for alias, canonical in BUSINESS_ALIASES.items():
        if matches_member(name, alias):
            name = canonical + name[len(alias):]
            break
    return name


def matches_member(name, member):
    if name == member:
        return True
    if not name.startswith(member):
        return False
    suffix = name[len(member):]
    return bool(re.search(r"(分行|支行|营业部|营业室|分理处)$", suffix)) and not re.search(
        r"银行|国际|财务|公司", suffix
    )


@lru_cache(maxsize=1)
def member_index():
    index = {}
    for level, tier in policy()["tiers"].items():
        for name in tier["members"]:
            index.setdefault(normalized_name(name), level)
    for name in policy()["finance_companies"]:
        index.setdefault(normalized_name(name), "finance_company")
    return index


def matched_institution(value):
    name = normalized_name(value)
    return max(
        ((member, level) for member, level in member_index().items() if matches_member(name, member)),
        key=lambda pair: len(pair[0]), default=(None, None),
    )


def tier_for(bill):
    name = bill.get("acceptor_name")
    code = bill.get("acceptor_code")
    if code is not None and not re.fullmatch(r"[0-9]{12}", str(code)):
        return {"status": "invalid", "message": "请核对承兑行的12位大额行号"}
    resolved = bill.get("acceptor_name_from_code")
    if resolved and code:
        if name and normalized_name(name) != normalized_name(resolved):
            left, _ = matched_institution(name)
            right, _ = matched_institution(resolved)
            if left is None or left != right:
                return {"status": "conflict", "message": "票面承兑行名称与行号对应机构不一致"}
        name = resolved
    _, level = matched_institution(name)
    identity = bank_identity(name)
    if level == "finance_company" or (level is None and identity["market_group"] == "finance_company"):
        return {"status": "finance_company", "message": "财务公司承兑，暂无该类单票点差规则"}
    if level:
        return {"status": "ok", "level": int(level), "assumed": False}

    group = identity["market_group"]
    if group == "unknown":
        group = bill.get("market_group")
    if group == "rural_commercial" and re.search(r"农村合作|农村信用|信用社|信用联社|信用合作", normalized_name(name)):
        group = None
    fallback = {"city_commercial": 4, "head_city": 4, "rural_commercial": 5,
                "private_bank": 6, "foreign_bank": 6}
    if not name and not code:
        if group == "guogu":
            return {"status": "ok", "level": 1, "assumed": False}
        if group == "head_city":
            return {"status": "ok", "level": 2, "assumed": False}
        if not group:
            return {"status": "ok", "level": 1, "assumed": True}
    if group in fallback:
        return {"status": "ok", "level": fallback[group], "assumed": False}
    return {"status": "unknown", "message": "承兑机构待定档，先展示同期限一类行基准"}


def add_months(day, count):
    year, month = divmod(day.year * 12 + day.month - 1 + count, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def resolve_dates(bill, today):
    due = bill.get("due_date")
    months, days = bill.get("remaining_months"), bill.get("remaining_days")
    if due:
        due = date.fromisoformat(due)
    elif months is not None and days is not None:
        raise ValueError("请核对剩余期限，只需提供月数或天数之一")
    elif months is not None:
        if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= 6:
            raise ValueError("剩余月数须为1至6的整数")
        due = add_months(today, months)
    elif days is not None:
        if not isinstance(days, int) or isinstance(days, bool) or days < 1:
            raise ValueError("剩余天数须为正整数，换算后的到期月份须在当月至本月+6月")
        due = today + timedelta(days=days)
    else:
        due = add_months(today, 6)
    month_offset = (due.year - today.year) * 12 + due.month - today.month
    if not 0 <= month_offset <= 6:
        raise ValueError("到期月份须为当月至本月+6月")
    if due < today:
        raise ValueError("到期日不能早于今天")
    dates = dates_for_bill({**bill, "due_date": due.isoformat()}, today.isoformat())
    dates["assumed"] = not bill.get("due_date") and months is None and days is None
    if dates["lifecycle"] != "active" and not (due == today and dates["lifecycle"] == "matured"):
        raise ValueError("票据已到期、结清或已贴现，请核对票据状态")
    return dates


def price_bill(bill, amount_check, dates, baseline, tier):
    if tier["status"] != "ok":
        return {"status": tier["status"], "message": tier["message"]}
    amount = amount_check.get("effective_amount")
    supplied = any(bill.get(key) not in (None, "") for key in ("face_amount", "amount_upper", "sub_bill_range"))
    if amount_check["status"] == "conflict" or (amount is None and supplied):
        return {"status": "needs_amount", "message": "票面金额存在冲突或无法核验，请补充准确金额"}
    amount = Decimal(amount) if amount is not None else None
    if amount is not None and amount <= 0:
        return {"status": "needs_amount", "message": "票面金额须大于0元"}
    bracket_amount = Decimal("1000000") if amount is None else amount
    bracket = next(row for row in policy()["amount_brackets"] if bracket_amount >= row["min_yuan"])
    bank_bp = policy()["tiers"][str(tier["level"])]["spread_bp"]
    amount_bp = bracket["spread_bp"]
    spread = [bank + size for bank, size in zip(bank_bp, amount_bp)]
    rates = [valid_rate(baseline["rate_percent"]) + Decimal(bp) / 100 for bp in spread]
    interest = None
    days = dates["remaining_days"]
    basis = bill.get("day_basis", 360)
    if amount is not None and days and basis in (360, 365):
        interest = [money(amount * rate * Decimal(days) / (Decimal(100) * basis)) for rate in rates]
    return {"status": "estimated", "level": tier["level"], "rate_range_percent": [str(rate) for rate in rates],
            "bank_spread_bp": bank_bp, "amount_spread_bp": amount_bp, "spread_bp": spread,
            "amount_assumed": amount is None, "interest_range_yuan": interest, "day_basis": basis, "days": days}

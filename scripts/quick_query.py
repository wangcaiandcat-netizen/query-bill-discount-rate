"""Resolve ticket inputs and query the 0M-6M DeepFin price API."""
import argparse
from datetime import datetime
from decimal import Decimal
import json
import sys

from bill_core import bill_identity, check_image_input, classify_bill, verify_amount
from discount_client import ApiError, DiscountClient, SHANGHAI
from history import window
from image_batch import prepare_bills
from pricing import price_bill, resolve_dates, tier_for

DISCLAIMER = "利率数据由公开数据获得，仅供参考，不构成投资建议。"


def with_disclaimer(answer):
    """The caller relays this answer verbatim, so the notice rides along at the end."""
    body = (answer or "").strip()
    return f"{body}\n\n**{DISCLAIMER}**" if body else f"**{DISCLAIMER}**"


MANUAL_FIELDS = {
    "bill_type", "acceptor_name", "acceptor_code", "acceptor_name_from_code",
    "market_group", "face_amount", "amount_upper", "sub_bill_range", "amount_role", "due_date",
    "remaining_months", "remaining_days", "actual_due_date", "day_basis", "bill_status",
    "has_discount_endorsement", "discount_date",
}
BILL_FIELDS = MANUAL_FIELDS | {
    "id", "bill_no", "title", "image_type", "document_side", "source_image", "recognition", "manual_fields",
}


def validate_fields(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label}须为JSON对象")
    unknown = value.keys() - allowed
    if "acceptor_level" in unknown:
        raise ValueError(
            "请删除 acceptor_level。提供承兑行名称 acceptor_name，或同时提供大额行号 acceptor_code "
            "和经核验的机构名称 acceptor_name_from_code，脚本会自动分档。"
        )
    if unknown:
        raise ValueError(f"{label}包含不支持的字段，请按 references/schema.md 修正输入")


def percent_range(values):
    lower, upper = [f"{Decimal(v):.4f}".rstrip("0").rstrip(".") + "%" for v in values]
    return lower if lower == upper else f"{lower}–{upper}"


TENOR_LABELS = {"0M": "本月内到期"}


def tenor_label(tenor):
    return TENOR_LABELS.get(tenor, tenor)


def render_bill(row):
    ident = row["id"]
    if row.get("duplicate_of"):
        return f'{ident}：与 {row["duplicate_of"]} 为同一子票。'
    if "baseline" not in row:
        return f'{ident}：{row["message"]}'
    base = row["baseline"]
    tenor = tenor_label(base["tenor"])
    if base["rate_percent"] is None:
        reference = "暂无已入库的一类行直贴基准"
    else:
        collected = datetime.fromisoformat(base["collected_at"]).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
        freshness = "较早采集的" if base["status"] == "stale" else "最新入库的"
        rate = f'{Decimal(base["rate_percent"]).normalize():f}%'
        reference = f"{freshness}一类行直贴基准 {rate}（北京时间 {collected} 采集）"
    body = f"{tenor} {reference}。"
    price = row.get("price")
    if price and price["status"] == "unsupported":
        body = price["message"] + "。以下仅为银票行情参考。" + body
    elif price and price["status"] == "estimated":
        rate_range = percent_range(price["rate_range_percent"])
        body = f'按 {price["level"]} 类行估算，{tenor} 参考贴现年利率约 {rate_range}。'
        if price["interest_range_yuan"]:
            low, high = price["interest_range_yuan"]
            interest = low if low == high else f"{low}–{high}"
            body += f'按 {price["days"]} 天、{price["day_basis"]} 天基数粗算，利息约 {interest} 元。'
        elif price["amount_assumed"]:
            body += "补充实际票额后可估算利息。"
        else:
            body += "计息天数或年基数待核对。"
        low_bp, high_bp = price["spread_bp"]
        spread = str(low_bp) if low_bp == high_bp else f"{low_bp}–{high_bp}"
        body += f"测算采用同期限{reference}，合计加 {spread} 个基点。"
    elif price:
        body += price["message"] + "。"
    elif row.get("message"):
        body += row["message"] + "。"
    if row["assumptions"]:
        body += "采用默认项：" + "、".join(row["assumptions"]) + "。"
    return f"{ident}：{body}"


def query(request, client=None, now=None):
    if not isinstance(request, dict):
        raise ValueError("输入须为JSON对象")
    mode = request.get("mode", "realtime")
    if not isinstance(mode, str) or mode not in {"realtime", "history", "market"}:
        raise ValueError("mode须为realtime或history，兼容历史模式别名market")
    history_mode = mode in {"history", "market"} or "start_at" in request or "end_at" in request
    allowed = {"mode", "start_at", "end_at", "tenor"} if history_mode else {"mode", "bills", "attachments", "recognition"}
    validate_fields(request, allowed, "历史请求" if history_mode else "实时请求")
    if history_mode and request.get("mode") == "realtime":
        raise ValueError("实时模式不能同时指定历史查询区间")
    if history_mode:
        start, end = window(request)
        start_at, end_at = start.isoformat(), end.isoformat()
        tenor = request.get("tenor")
        client = client or DiscountClient()
        return {"mode": "history", "query_window": {"start_at": start_at, "end_at": end_at, "tenor": tenor},
                "items": client.series(start_at, end_at, tenor)}

    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    if "bills" in request or "attachments" in request:
        bills, _, _ = prepare_bills(request)
    else:
        bills = [{"id": "参考查询"}]
    if not bills:
        raise ValueError("bills须包含至少一张票据；只查默认基准可传空对象{}")
    for bill in bills:
        validate_fields(bill, BILL_FIELDS, "票据要素")
        if bill.get("manual_fields") is not None:
            validate_fields(bill["manual_fields"], MANUAL_FIELDS, "手工票面要素")
    results, seen, baselines = [], {}, {}
    for bill in bills:
        ident = bill["id"]
        row = {"id": ident, "assumptions": []}
        results.append(row)
        if bill.get("image_type") in {"bill_back", "non_bill"} or bill.get("document_side") == "back":
            row["message"] = "请提供票据正面或手工要素"
            continue
        checked = check_image_input(bill)
        manual = bill.get("manual_fields") or {}
        if not isinstance(manual, dict) or not manual.keys() <= MANUAL_FIELDS:
            row["message"] = "手工票面要素格式有误，请核对字段"
            continue
        if checked["status"] != "ready":
            bill = {"id": ident, **manual}
            row["assumptions"].append("图片要素未识别，采用已确认手工要素")
        else:
            bill = {**bill, **manual}
        identity = bill_identity(bill)
        if identity and identity in seen:
            row["duplicate_of"] = seen[identity]
            continue
        if identity:
            seen[identity] = ident
        try:
            dates = resolve_dates(bill, now.date())
            row["dates"] = dates
            tier = tier_for(bill)
            kind = classify_bill(bill)
            if kind["status"] == "conflict" or tier["status"] in {"conflict", "invalid"}:
                row["message"] = tier.get("message") or "票据类型和承兑机构信息冲突，请核对"
                continue
            if not kind["bill_type"] and tier["status"] != "finance_company":
                row["assumptions"].append("银票")
            if dates["assumed"]:
                row["assumptions"].append("6M（本月+6月到期档）")
            if tier.get("assumed"):
                row["assumptions"].append("一类行")
            amount = verify_amount(bill)
            if not any(bill.get(k) not in (None, "") for k in ("face_amount", "amount_upper", "sub_bill_range")):
                row["assumptions"].append("100万元及以上金额档")
            due = None if dates["assumed"] else dates["nominal_due_date"]
            month = dates["nominal_due_date"][:7]
            if month not in baselines:
                try:
                    client = client or DiscountClient()
                    baselines[month] = client.latest(due, now)
                except ApiError as exc:
                    baselines[month] = exc
            baseline = baselines[month]
            if isinstance(baseline, ApiError):
                row["error"] = {"code": baseline.code, "message": str(baseline)}
                row["message"] = str(baseline)
                continue
            row["baseline"] = baseline
            if kind["bill_type"] in {"财票", "商票"}:
                row["price"] = {"status": "unsupported", "pricing_allowed": False,
                                "message": f'{kind["bill_type"]}暂无该类单票点差规则，本票贴现利率和利息待专门询价'}
            elif dates["lifecycle"] == "matured":
                row["message"] = "票据当天到期，仅提供行情参考"
            elif baseline["rate_percent"] is not None:
                row["price"] = price_bill(bill, amount, dates, row["baseline"], tier)
        except (ValueError, TypeError, OverflowError) as exc:
            row["message"] = str(exc)
    if baselines and all(isinstance(value, ApiError) for value in baselines.values()):
        raise next(iter(baselines.values()))
    return {"mode": "realtime", "results": results, "answer": with_disclaimer("\n\n".join(map(render_bill, results)))}


def main():
    parser = argparse.ArgumentParser(description="读取JSON票据要素或历史查询区间")
    parser.parse_args()
    sys.stdin.reconfigure(encoding="utf-8-sig")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        output = query(json.loads(sys.stdin.read()))
        print(json.dumps(output, ensure_ascii=False))
    except (ValueError, TypeError, OSError) as exc:
        print(json.dumps({"error": getattr(exc, "code", "INPUT"), "message": str(exc)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()

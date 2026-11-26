"""The only network client: bounded, in-memory DeepFin JSON requests."""
from __future__ import annotations
import json
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

SHANGHAI = timezone(timedelta(hours=8))
DEFAULT_PATH = "/api/v1/external/market/discount-quotes/v2"
DEFAULT_URL = "https://deepfin.vip" + DEFAULT_PATH
TENORS = tuple(f"{months}M" for months in range(0, 7))


class ApiError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def valid_rate(value):
    if isinstance(value, bool):
        raise ValueError("利率不能是布尔值")
    try:
        rate = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("利率无效") from exc
    if not rate.is_finite() or not 0 < rate < 100:
        raise ValueError("年化利率须大于0且小于100%")
    return rate


def aware_time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("时间缺少时区")
    return result


class DiscountClient:
    def __init__(self, url=None, api_key=None, budget_seconds=45):
        self.url = url or os.getenv("DEEPFIN_DISCOUNT_API_URL") or DEFAULT_URL
        parts = urlsplit(self.url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise ApiError("CONFIG", "DeepFin接口地址无效")
        path = parts.path.rstrip("/")
        if path.endswith(("/query", "/realtime", "/history", "/latest")):
            path = path.rsplit("/", 1)[0]
        if path.endswith("/discount-quotes"):
            path += "/v2"
        self.url = urlunsplit((parts.scheme, parts.netloc, path or DEFAULT_PATH, "", ""))
        self.api_key = api_key or os.getenv("DEEPFIN_MARKET_API_KEY")
        self.deadline = time.monotonic() + budget_seconds
        self.opener = build_opener(NoRedirect())

    def post(self, endpoint, payload):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ApiError("TIMEOUT", "DeepFin查询已超过本次时间预算")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        request = Request(self.url + "/" + endpoint, json.dumps(payload).encode("utf-8"), headers, method="POST")
        try:
            with self.opener.open(request, timeout=min(10, remaining)) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ApiError("RESPONSE_LIMIT", "DeepFin单页响应过大，请缩小区间或页大小")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ApiError("CONTRACT", "DeepFin返回格式不是JSON对象")
            return data
        except HTTPError as exc:
            messages = {401:"接口需要有效凭证", 403:"接口拒绝访问", 422:"接口不接受查询参数，请检查接口版本和日期范围",
                        429:"接口查询频率受限", 503:"接口暂不可用"}
            raise ApiError("HTTP_" + str(exc.code), messages.get(exc.code, "接口HTTP错误"+str(exc.code))) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ApiError("UNAVAILABLE", "无法连接DeepFin价格接口或请求超时") from exc
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ApiError("CONTRACT", "DeepFin没有返回可解析的JSON") from exc

    def latest(self, maturity_date=None, now=None):
        data = self.post("realtime", {"maturity_date": maturity_date} if maturity_date else {})
        try:
            if data["tenor"] not in TENORS or data["status"] not in {"fresh", "stale", "no_quote"}:
                raise ValueError("报价状态无效")
            if data["status"] == "no_quote":
                if data["rate_percent"] is not None or data["collected_at"] is not None:
                    raise ValueError("缺报响应字段冲突")
            else:
                valid_rate(data["rate_percent"])
                collected_at = aware_time(data["collected_at"])
                if now and collected_at > now + timedelta(minutes=1):
                    raise ValueError("采集时间在未来")
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiError("CONTRACT", "DeepFin返回的六档基准格式不正确") from exc
        return {key: data[key] for key in ("tenor", "rate_percent", "collected_at", "status")}

    def series(self, start_at, end_at, tenor=None):
        payload = {"start_at": start_at, "end_at": end_at, "page_size": 1000}
        if tenor:
            payload["tenor"] = tenor
        items = []
        previous = 0
        for _ in range(100):
            data = self.post("history", payload)
            if not isinstance(data.get("items"), list) or "next_after_id" not in data:
                raise ApiError("CONTRACT", "DeepFin返回的历史入库价格式不正确")
            try:
                for point in data["items"]:
                    if point["tenor"] not in TENORS:
                        raise ValueError("历史报价期限无效")
                    valid_rate(point["rate_percent"])
                    aware_time(point["round_at"])
                    aware_time(point["collected_at"])
                    items.append({key: point[key] for key in ("tenor", "rate_percent", "round_at", "collected_at")})
            except (KeyError, TypeError, ValueError) as exc:
                raise ApiError("CONTRACT", "DeepFin返回的历史入库价格式不正确") from exc
            cursor = data["next_after_id"]
            if cursor is None:
                return items
            if type(cursor) is not int or cursor <= 0 or cursor == previous:
                raise ApiError("CONTRACT", "历史查询分页标记无效")
            previous = cursor
            payload["after_id"] = cursor
        raise ApiError("LIMIT", "历史报价页数超出查询预算，请缩小日期范围")

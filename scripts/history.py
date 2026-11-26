"""Resolve the caller's explicit time window for published prices."""
from datetime import date, datetime, time, timedelta

from discount_client import SHANGHAI, TENORS, aware_time


def window(request):
    start_at, end_at = request.get("start_at"), request.get("end_at")
    if start_at is None and end_at is None:
        raise ValueError("请提供历史查询的开始和结束日期或一个查询日期")
    if start_at is None or end_at is None:
        day = end_at if start_at is None else start_at
        if not isinstance(day, str) or len(day) != 10:
            raise ValueError("请同时提供带时区的开始和结束时刻，或提供一个查询日期")
        start_at = end_at = day
    if not isinstance(start_at, str) or not isinstance(end_at, str):
        raise ValueError("查询区间须使用日期字符串或带时区的时间字符串")
    if len(start_at) == 10:
        start = datetime.combine(date.fromisoformat(start_at), time.min, SHANGHAI)
    else:
        start = aware_time(start_at)
    if len(end_at) == 10:
        end = datetime.combine(date.fromisoformat(end_at) + timedelta(days=1), time.min, SHANGHAI)
    else:
        end = aware_time(end_at)
    if not timedelta(0) < end - start <= timedelta(days=366):
        raise ValueError("查询区间须正序且不超过366天")
    if request.get("tenor") is not None and request["tenor"] not in TENORS:
        raise ValueError("期限须为0M至6M")
    return start, end

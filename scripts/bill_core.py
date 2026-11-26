"""Preserved bill recognition validation; no price retrieval, OCR or model API."""
from __future__ import annotations
import argparse, copy, hashlib, json, re, sys, unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DISCLAIMER = "仅供了解票据市场行情，不作为实际业务参考或交易依据。所示利率及利息为公开信息模糊匹配后的粗略估算。"
NON_BANK_MESSAGE = "财票和商票暂无可比报价"
BACK_IMAGE_MESSAGE = "这是票据背面，请上传票据正面图片。"
GROUP_LABELS = {"guogu":"国股", "head_city":"大商", "city_commercial":"普通城商", "rural_commercial":"农商", "village_bank":"村镇", "private_bank":"民营", "foreign_bank":"外资", "finance_company":"财司", "enterprise":"企业", "unknown":"待分类"}
DEC = Decimal
CENT = DEC("0.01")

def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def text(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()

def clean_name(value):
    return re.sub(r"\s+", "", text(value)).replace("（","(").replace("）",")")

def number(value):
    if isinstance(value, bool):
        raise ValueError("布尔值不是金额或利率")
    v = DEC(str(value))
    if not v.is_finite():
        raise ValueError("数字不是有限值")
    return v

def money(value):
    return format(number(value).quantize(CENT, rounding=ROUND_HALF_UP), ".2f")

def parse_amount(value):
    s = text(value).replace(",","").replace("人民币","").replace("￥","").replace("¥","").replace("元","").strip()
    if not s: return None
    scale = DEC(1)
    if s.endswith("万"): s, scale = s[:-1], DEC(10000)
    elif s.endswith("亿"): s, scale = s[:-1], DEC(100000000)
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", s): raise ValueError("金额需为明确数值或元/万元/亿元")
    n = number(s) * scale
    if n <= 0: raise ValueError("金额应大于零")
    return n

def chinese_amount(value):
    """Only explicit uppercase RMB amounts; do not infer the unit from magnitude."""
    s = text(value).replace("人民币","").replace("圆","元").replace("正","整").replace(" ","")
    if not s: return None
    chars = {"零":0,"壹":1,"贰":2,"貳":2,"叁":3,"參":3,"肆":4,"伍":5,"陆":6,"陸":6,"柒":7,"捌":8,"玖":9}
    if "元" not in s: raise ValueError("大写金额缺少元")
    whole, fraction = s.split("元",1)
    total, section, digit = 0, 0, 0
    for c in whole:
        if c in chars: digit = chars[c]
        elif c in {"拾","佰","仟"}:
            section += (digit or 1) * {"拾":10,"佰":100,"仟":1000}[c]; digit = 0
        elif c in {"万","亿"}:
            section += digit; digit = 0
            if c == "万": total += section * 10000
            else: total = (total + section) * 100000000
            section = 0
        else: raise ValueError("不能识别的大写金额")
    n = DEC(total + section + digit)
    fraction = fraction.replace("整","")
    if fraction:
        rest = fraction
        for label, factor in (("角", DEC(".1")), ("分", DEC(".01"))):
            m = re.search(r"([零壹贰貳叁參肆伍陆陸柒捌玖])"+label, fraction)
            if m: n += DEC(chars[m.group(1)]) * factor; rest = rest.replace(m.group(0), "")
        if rest.replace("零",""): raise ValueError("大写角分不明确")
    if n <= 0: raise ValueError("金额应大于零")
    return n

def range_amount(value):
    """Inclusive integer serials, 0.01 CNY each. Zero sentinels are not serial 0."""
    if value is None or value == "" or value == []:
        return {"status":"unavailable", "amount":None, "reason":"未提供子票区间"}
    try:
        if isinstance(value, str):
            raw = text(value).replace("，",",")
            if re.sub(r"[\s,~—–\-至]", "", raw) in {"0","00"}:
                return {"status":"unavailable","amount":None,"reason":"子票区间为零占位"}
            segments = re.split(r"[;；\n]+", raw)
            pairs = []
            for segment in segments:
                m = re.fullmatch(r"\s*(\d+)\s*(?:,|—|–|-|~|至)\s*(\d+)\s*", segment)
                if not m: raise ValueError("子票区间格式或数字不完整")
                pairs.append(m.groups())
        elif isinstance(value, dict): pairs = [(value["start"], value["end"])]
        elif isinstance(value, list):
            if len(value)==2 and all(isinstance(v,str) and v.isdigit() for v in value): pairs=[value]
            else: pairs=[(p["start"],p["end"]) if isinstance(p,dict) else p for p in value]
        else: raise ValueError("区间标识应作为文本传入")
        intervals = []
        for a,b in pairs:
            if not isinstance(a,str) or not isinstance(b,str) or not a.isdigit() or not b.isdigit():
                raise ValueError("起止序号必须是完整数字文本")
            lo,hi = int(a),int(b)
            if lo==hi==0 and len(pairs)==1:
                return {"status":"unavailable","amount":None,"reason":"子票区间为零占位"}
            if lo<1 or hi<lo: raise ValueError("起止序号应为正且止号不小于起号")
            intervals.append((lo,hi))
        intervals.sort()
        if any(b[0]<=a[1] for a,b in zip(intervals,intervals[1:])):
            raise ValueError("区间重叠或重复，不累计")
        count = sum(hi-lo+1 for lo,hi in intervals)
        return {"status":"calculated","amount":money(DEC(count)/100),
                "intervals":[[str(a),str(b)] for a,b in intervals], "serial_count":str(count),
                "formula":"(止号 - 起号 + 1) / 100 元"}
    except (ValueError,KeyError,TypeError) as e:
        return {"status":"invalid","amount":None,"reason":str(e)}

def verify_amount(bill):
    candidates, issues = {}, []
    for field, parser in (("face_amount",parse_amount),("amount_upper",chinese_amount)):
        if bill.get(field) not in (None,""):
            try: candidates[field] = money(parser(bill[field]))
            except (ValueError,InvalidOperation) as e: issues.append(field+": "+str(e))
    r = range_amount(bill.get("sub_bill_range"))
    role = bill.get("amount_role","current_bill")
    if r["amount"] and role != "package_total": candidates["range_amount"] = r["amount"]
    if role == "package_total" and r["amount"]:
        # Package total is not comparable to the held sub-interval.
        if candidates.get("face_amount") and number(r["amount"])>number(candidates["face_amount"]):
            issues.append("子票金额大于已识别包总额")
            return {"status":"conflict","effective_amount":None,"candidates":candidates,
                    "range_check":r,"issues":issues}
        return {"status":"derived_sub_bill","effective_amount":r["amount"],"candidates":candidates,
                "range_check":r,"issues":issues+["按当前子票区间计息，未使用原包总额"]}
    vals = set(candidates.values())
    if len(vals)>1: status, effective = "conflict", None
    elif len(vals)==1:
        effective = next(iter(vals))
        status = ("verified_range" if r["amount"] and len(candidates)>1 else
                  "derived_range" if r["amount"] and len(candidates)==1 else
                  "verified_text" if len(candidates)>1 else "single_read")
    else: status, effective = "unavailable", None
    if r["status"]=="invalid": issues.append(r["reason"])
    if status=="conflict": issues.append("金额读数与复算存在冲突；继续类别行情，暂停单票计息")
    if not r["amount"] and status not in ("conflict","unavailable"): issues.append("未完成子票区间复算")
    return {"status":status,"effective_amount":effective,"candidates":candidates,"range_check":r,"issues":issues}

def classify_institution(name):
    from quote_matching import bank_identity
    result=bank_identity(name)
    result["label"]=GROUP_LABELS.get(result["market_group"],result["market_group"])
    return result

def classify_bill(bill):
    inst=classify_institution(bill.get("acceptor_name"))
    explicit=bill.get("bill_type")
    aliases={"银行承兑汇票":"银票","商业承兑汇票":"商票","财务公司承兑汇票":"财票"}
    explicit=aliases.get(explicit,explicit)
    if explicit not in {"银票","商票","财票"}: explicit=None
    inferred=("银票" if inst["is_bank"] else "财票" if inst["market_group"]=="finance_company"
              else "商票" if inst["market_group"]=="enterprise" else None)
    no=text(bill.get("bill_no"))
    prefix=no[0] if re.fullmatch(r"\d{30}",no) and no[0] in "5678" else None
    prefix_type="商票" if prefix in ("6","7") else None
    issues=[]
    if explicit and inferred and explicit != inferred: issues.append("用户/识别票种与承兑主体性质不符")
    if prefix_type and (explicit or inferred) in ("银票","财票"): issues.append("票号前缀与银票/财票识别冲突")
    if prefix in ("5","8") and (explicit or inferred)=="商票": issues.append("票号前缀与商票识别冲突")
    kind=explicit or inferred or prefix_type
    # A bank-style template header alone cannot resolve bank versus finance company.
    if not kind and "商业承兑" in text(bill.get("title")): kind="商票"
    market=inst["market_group"]
    if bill.get("market_group") and inst["market_group"]=="unknown":
        if bill["market_group"] in GROUP_LABELS: market=bill["market_group"]
    if kind=="商票": market="enterprise"
    elif kind=="财票": market="finance_company"
    return {"bill_type":kind,"market_group":market,"market_label":GROUP_LABELS.get(market,"待分类"),
            "institution":inst,"source":"acceptor_and_input","status":"conflict" if issues else "ok" if kind else "needs_bill_type",
            "is_supply_chain":prefix in ("7","8") if prefix else None,"issues":issues}

def today_shanghai():
    return datetime.now(timezone(timedelta(hours=8))).date()

def roll_due(d):
    cal=load_json(ROOT/"references"/"holiday-calendar.json")
    cur=d;statuses=set();roll=0
    for _ in range(40):
        cfg=cal.get("years",{}).get(str(cur.year),{})
        statuses.add(cfg.get("status","provisional_weekend_only"))
        iso=cur.isoformat()
        working=iso in cfg.get("adjusted_workdays",[])
        holiday=any(a<=iso<=b for a,b in cfg.get("holiday_ranges",[]))
        if working or (cur.weekday()<5 and not holiday): break
        cur+=timedelta(days=1);roll+=1
    return cur,{"version":cal["version"],"statuses":sorted(statuses),"roll_days":roll}

def dates_for_bill(bill, as_of):
    warnings=[];nominal=None;actual=None;cal=None;start=None
    try: start=date.fromisoformat(bill.get("discount_date") or as_of)
    except (ValueError,TypeError): warnings.append("贴现日期无法解析，保留类别行情但暂停计息")
    try:
        if bill.get("due_date"):
            nominal=date.fromisoformat(bill["due_date"]);actual,cal=roll_due(nominal)
        if bill.get("actual_due_date"):
            provided=date.fromisoformat(bill["actual_due_date"])
            if actual and provided!=actual: warnings.append("提供的实际到期日与日历推算不同，粗算采用提供日期")
            actual=provided
        days=((actual-start).days if actual else int(bill["remaining_days"]) if bill.get("remaining_days") is not None else None) if start else None
    except (ValueError,TypeError):
        warnings.append("到期日或期限无法解析");days=None
    if cal and "preset" in cal["statuses"]: warnings.append("到期顺延采用预设日历")
    if cal and "provisional_weekend_only" in cal["statuses"]: warnings.append("日历缺少年份，暂按周末顺延")
    if start and start.isoformat()!=as_of: warnings.append("按当前取得行情对指定贴现日期作情景粗算")
    lifecycle="active"
    if "已结清" in text(bill.get("bill_status")): lifecycle="settled"
    elif days is not None and days<=0: lifecycle="matured"
    elif bill.get("has_discount_endorsement"): lifecycle="discount_endorsement_seen"
    return {"discount_date":start.isoformat() if start else None,"requested_discount_date":bill.get("discount_date"),"nominal_due_date":nominal.isoformat() if nominal else None,
            "actual_due_date":actual.isoformat() if actual else None,"remaining_days":days,"calendar":cal,
            "lifecycle":lifecycle,"warnings":warnings}

def check_image_input(bill):
    kind=bill.get("image_type")
    if kind is not None and not isinstance(kind,str):
        return {"status":"image_unverified","message":"图片类型标记无效，尚未完成图片核验；该图片暂不估价。"}
    if kind is not None:
        recognition=bill.get("recognition") or {}
        if (not isinstance(recognition,dict) or recognition.get("status")!="verified"
            or recognition.get("method") not in ("host_multimodal","local_ocr")
            or not isinstance(recognition.get("evidence"),str) or not recognition["evidence"].strip()):
            return {"status":"image_input_unavailable","message":"图片请求缺少有效识别声明，暂不估价。宿主若已实际读到本轮图片，请补齐attachments和recognition后重试；这不代表已确认模型没有视觉能力。未实际读到图片时，请重新传入图片或手工提供票据要素。"}
    messages={
        "non_bill":("not_a_bill","未识别到承兑汇票票面，请上传票据正面图片。"),
        "unreadable":("unreadable_image","图片模糊或关键内容被遮挡，无法识别，请上传清晰的票据图片。"),
        "uncertain":("uncertain_image","暂无法确认是否为承兑汇票，请上传清晰完整的票据正面图片。")}
    if kind in messages:
        status,message=messages[kind]
        return {"status":status,"message":message}
    if bill.get("source_image") and kind not in ("bill_front","bill_back"):
        return {"status":"image_unverified","message":"尚未完成图片类型核验，不能据此查询贴现价格。"}
    if kind in ("bill_front","bill_back"):
        expected="front" if kind=="bill_front" else "back"
        if bill.get("document_side") and bill["document_side"]!=expected:
            return {"status":"uncertain_image","message":"图片正反面标记冲突，需重新核对图片。"}
    if kind=="bill_back" or bill.get("document_side")=="back":
        return {"status":"back_image","message":BACK_IMAGE_MESSAGE}
    return {"status":"ready","message":None}

def bill_identity(bill):
    """Normalize serial notation without inventing an identifier."""
    no=re.sub(r"\s+","",text(bill.get("bill_no")))
    ranges=range_amount(bill.get("sub_bill_range"))
    if not no or ranges["status"]!="calculated":return None
    return (no,tuple(tuple(pair) for pair in ranges["intervals"]))

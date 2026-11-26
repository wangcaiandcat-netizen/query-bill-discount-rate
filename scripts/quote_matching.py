"""Match bank names and classify the accepting institution locally."""
from __future__ import annotations
import calendar, json, re, unicodedata
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def clean(value):
    return re.sub(r"\s+","",unicodedata.normalize("NFKC",str(value or "")))
@lru_cache(maxsize=1)
def policy():
    return json.loads((ROOT/"references"/"market-policy.json").read_text(encoding="utf-8-sig"))
ALIASES={"工行":"中国工商银行","农行":"中国农业银行","中行":"中国银行","建行":"中国建设银行",
 "交行":"交通银行","招行":"招商银行","广发":"广发银行","江苏行":"江苏银行","宁行":"南京银行",
 "波行":"宁波银行","中信":"中信银行","邮储银行":"中国邮政储蓄银行","浦发银行":"上海浦东发展银行"}
# Explicit local market convention, separate from legal institution type.
CITY_BANKS=("长沙银行","中原银行","阜新银行","宁波通商银行","杭州银行","成都银行","重庆银行","苏州银行",
 "齐鲁银行","青岛银行","广州银行","厦门银行","厦门国际银行","郑州银行","晋商银行","贵阳银行",
 "天津银行","渤海银行","恒丰银行","西安银行","甘肃银行","徽商银行","浙商银行","宁夏银行")
def bank_identity(name):
    p=policy();n=clean(name);n=clean(ALIASES.get(n,n))
    base={"canonical_name":None,"market_group":"unknown","is_bank":False,"institution_type":"待识别",
          "local_tier":"无分类","policy_id":p["policy_id"],"policy_version":p["version"]}
    if not n or n in ("国民","农商","银行","城商行"):return base
    # Finance-company or village/rural names may themselves contain a parent bank's name.
    for group in ("finance_company","village_bank","rural_commercial"):
        g=p["groups"][group]
        if any(clean(x) in n for x in g.get("contains",[])) or any(n.endswith(clean(x)) for x in g.get("suffixes",[])):
            base.update(market_group=group,is_bank=g["is_bank"],institution_type=g["institution_type"])
            base["canonical_name"]=re.split(r"股份有限公司|有限责任公司|有限公司",n)[0]
            return base
    # Exact entity prefixes only, longest first; never match a bank embedded in a company name.
    entries=[]
    for e in p["entity_overrides"]:
        for alias in e["contains"]:entries.append((clean(alias),e,e["market_group"]))
    for group in ("policy_bank","state_owned","joint_stock","head_city","private_bank","foreign_bank"):
        e=p["groups"][group]
        for alias in e.get("contains",[]):entries.append((clean(alias),e,group))
    for alias,e,g in sorted(entries,key=lambda a:len(a[0]),reverse=True):
        tail=n[len(alias):] if n.startswith(alias) else None
        if tail is None:continue
        if tail and not (tail.startswith(("股份有限公司","有限责任公司","有限公司","(","国际")) or re.search(r"分行|支行|营业部|营业室|分理处",tail)):
            continue
        # The shorter 中信银行 must not swallow an unknown international entity.
        if tail.startswith("国际") and "国际" not in alias:continue
        group="guogu" if e.get("is_guogu") else g
        official=e["contains"][0] if e.get("entity_id") else alias
        longer=[clean(x) for x in e.get("contains",[]) if clean(x).endswith(alias)]
        if not e.get("entity_id") and longer:official=max(longer,key=len)
        canonical=re.split(r"股份有限公司|有限责任公司|有限公司",clean(official))[0]
        for k,v in ALIASES.items():
            if canonical==k:canonical=v
        base.update(canonical_name=canonical,market_group=group,is_bank=e.get("is_bank",False),
            institution_type=e.get("institution_type"),local_tier=e.get("tier_class","无分类"))
        return base
    if "财务" not in n and "银行" in n:
        canonical=re.split(r"股份有限公司|有限责任公司|有限公司",n)[0]
        m=re.match(r"(.+银行).+(?:分行|支行|营业部|分理处)$",canonical)
        if m:canonical=m[1]
        if re.search(r"银行.*(?:科技|咨询|服务|管理).*公司",n):return base
        base.update(canonical_name=canonical,is_bank=True,institution_type="银行（类别待确认）")
        if canonical in CITY_BANKS or "城市商业银行" in n:
            base.update(market_group="city_commercial",institution_type="普通城市商业银行")
        return base
    if any(x in n for x in ("公司","集团","企业")):
        base.update(market_group="enterprise",institution_type="企业")
    return base

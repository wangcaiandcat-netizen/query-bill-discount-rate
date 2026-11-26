"""Attachment inventory and small read groups; no copying, OCR or model calls."""
from __future__ import annotations
import argparse, copy, json, sys
from datetime import datetime, timezone
from pathlib import Path

def plan_attachments(items, max_images=2, max_encoded_bytes=6*1024*1024):
    if not isinstance(items,list) or not items:raise ValueError("attachments须为非空数组")
    if max_images<1 or max_encoded_bytes<1:raise ValueError("分组上限必须大于零")
    manifest=[];groups=[];group=[];size=0;ids=set()
    for index,item in enumerate(items):
        row={"source_image":item} if isinstance(item,str) else dict(item)
        ident=str(row.get("id") or "image-"+str(index+1))
        if ident in ids:raise ValueError("附件id重复："+ident)
        ids.add(ident)
        path=row.get("source_image")
        length=row.get("size_bytes")
        state="ready"
        if path:
            try:length=Path(path).stat().st_size
            except OSError:state="path_unavailable"
        length=length if isinstance(length,int) and not isinstance(length,bool) and length>=0 else None
        encoded=4*((length+2)//3) if length is not None else None
        record={"id":ident,"source_image":path,"size_bytes":length,"estimated_encoded_bytes":encoded,"status":state}
        if state!="ready":
            record["note"]="本地路径不可读；有真实宿主附件仍可通过附件读取。"
        if encoded and encoded>max_encoded_bytes:
            record["note"]="单图超出建议请求大小；优先宿主读图，必要时仅在内存缩放，不修改原图。"
        manifest.append(record)
        if group and (len(group)>=max_images or encoded is None or size+(encoded or 0)>max_encoded_bytes):
            groups.append(group);group=[];size=0
        group.append(ident);size+=encoded or 0
        if encoded is None or encoded>max_encoded_bytes:
            groups.append(group);group=[];size=0
    if group:groups.append(group)
    return {"query_started_at":datetime.now(timezone.utc).isoformat(),"attachments":manifest,
            "reading_groups":groups,"expected_image_count":len(manifest),
            "note":"只安排分组，不传图、不证明识别成功；全部组完成后合并bills，只扫描一次行情。",
            "limits":{"max_images_per_group":max_images,"max_encoded_bytes":max_encoded_bytes,"kind":"conservative_hint_not_provider_limit"}}

def normalize_recognition(record):
    if not isinstance(record,dict):return record
    record=copy.deepcopy(record)
    if record.get("method") in ("host_multimodal","local_ocr") and isinstance(record.get("evidence"),str) and record["evidence"].strip():
        record.setdefault("status","verified")
    return record

def prepare_bills(request):
    bills=copy.deepcopy(request.get("bills",[]))
    if not isinstance(bills,list) or not all(isinstance(b,dict) for b in bills):
        raise ValueError("bills须为票据对象数组")
    supplied_ids=[str(b["id"]) for b in bills if b.get("id") is not None]
    if len(supplied_ids)!=len(set(supplied_ids)):raise ValueError("bills的id重复，请按附件编号对应")
    shared=normalize_recognition(request.get("recognition"))
    warnings=[]
    for index,b in enumerate(bills):
        b["id"]=str(b.get("id") or "input-"+str(index+1))
        if "recognition" in b:b["recognition"]=normalize_recognition(b["recognition"])
        elif b.get("image_type") is not None and shared is not None:b["recognition"]=copy.deepcopy(shared)
        if b.get("image_type") is not None and isinstance(b.get("recognition"),str):
            warnings.append({"id":b["id"],"message":"识别记录格式错误；请使用recognition:{method:host_multimodal,evidence:实际读图依据}，可放批次顶层。"})
    if len({b["id"] for b in bills})!=len(bills):raise ValueError("自动编号与现有id重复，请逐条提供唯一id")
    attachments=request.get("attachments")
    if attachments is None:
        if not bills:raise ValueError("请提供bills；图片批次同时提供attachments清单")
        return bills,{"status":"not_provided","expected":None,"missing_ids":[]},warnings
    if not isinstance(attachments,list) or not all(isinstance(a,dict) and a.get("id") for a in attachments):
        raise ValueError("attachments须为含唯一id的附件对象数组")
    ids=[str(a["id"]) for a in attachments]
    if len(ids)!=len(set(ids)):raise ValueError("attachments的id重复")
    by_id={b["id"]:b for b in bills};missing=[];ordered=[]
    for a in attachments:
        ident=str(a["id"])
        if ident in by_id:
            b=by_id.pop(ident)
            if a.get("source_image"):b.setdefault("source_image",a["source_image"])
            if "image_type" not in b:b["image_type"]="uncertain";b["recognition"]={"status":"unavailable"}
        else:
            missing.append(ident)
            b={"id":ident,"image_type":"uncertain","recognition":{"status":"unavailable"}}
            if a.get("source_image"):b["source_image"]=a["source_image"]
        ordered.append(b)
    # Explicit manual records may coexist, but unknown image IDs are input mistakes.
    if any(b.get("image_type") is not None or b.get("source_image") for b in by_id.values()):
        raise ValueError("bills包含不在附件清单中的图片id，请使用原附件编号")
    ordered.extend(by_id.values())
    if not ordered:raise ValueError("没有可处理的输入")
    return ordered,{"status":"complete" if not missing else "incomplete","expected":len(ids),"missing_ids":missing},warnings

def main():
    p=argparse.ArgumentParser(description="stdin接attachments路径/对象数组，在内存返回清单和建议读图分组")
    p.add_argument("--max-images",type=int,default=2)
    p.add_argument("--max-encoded-mb",type=float,default=6)
    a=p.parse_args()
    if hasattr(sys.stdin,"reconfigure"):sys.stdin.reconfigure(encoding="utf-8-sig")
    if hasattr(sys.stdout,"reconfigure"):sys.stdout.reconfigure(encoding="utf-8")
    try:
        data=json.loads(sys.stdin.read())
        items=data.get("attachments") if isinstance(data,dict) else data
        print(json.dumps(plan_attachments(items,a.max_images,int(a.max_encoded_mb*1024*1024)),ensure_ascii=False))
    except (ValueError,TypeError,OSError) as e:p.error(str(e))
if __name__=="__main__":main()

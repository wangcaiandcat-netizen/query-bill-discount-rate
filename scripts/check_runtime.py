"""Scoped WorkBuddy image-input diagnostics; configuration is not proof of image delivery."""
from __future__ import annotations
import argparse, json, os, tempfile
from pathlib import Path

def matching_models(data,model_id):
    found=[]
    def visit(value):
        if isinstance(value,list):
            for child in value:visit(child)
        elif isinstance(value,dict):
            if model_id in (value.get("id"),value.get("model"),value.get("modelId")):found.append(value)
            for child in value.values():
                if isinstance(child,(dict,list)):visit(child)
    visit(data)
    return found

def check_model(config_path,model_id):
    result={"model_id":model_id,"config_path":str(config_path),"supports_images":None,
            "image_delivery":"unverified","status":"unknown"}
    try:data=json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    except (OSError,ValueError) as exc:
        result["reason"]="模型配置不可读取："+type(exc).__name__;return result
    found=matching_models(data,model_id)
    flags=[m.get("supportsImages") for m in found if isinstance(m.get("supportsImages"),bool)]
    if flags and all(x is False for x in flags):
        result.update(status="image_input_disabled",supports_images=False,reason="当前配置关闭图片输入；确认实际接口支持图片后可定点开启，再重新读取附件。")
    elif flags and all(x is True for x in flags):
        result.update(status="declared_capable",supports_images=True,reason="配置允许图片输入，仍需实际附件读图验证。")
    elif flags:result["reason"]="同名模型配置冲突，不能推断当前图片能力"
    else:result["reason"]="未找到明确图片能力标记，核对当前会话与宿主读图工具"
    result["can_update_unambiguously"]=len(found)==1
    return result

def set_image_flag(config_path,model_id,enabled,confirmed_capable=False):
    if enabled and not confirmed_capable:raise ValueError("开启前须确认当前实际模型接口支持图片；不能凭模型名称推断")
    path=Path(config_path)
    original=path.read_bytes()
    data=json.loads(original.decode("utf-8-sig"))
    found=matching_models(data,model_id)
    if len(found)!=1:raise ValueError("未找到唯一模型条目，不修改配置")
    model=found[0];previous=model.get("supportsImages")
    if not isinstance(previous,bool):raise ValueError("原图片开关不是明确布尔值，不自动修改")
    if previous==enabled:return {**check_model(path,model_id),"changed":False}
    model["supportsImages"]=enabled
    tmp=None
    try:
        with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=path.parent,prefix=".bill-image-",suffix=".tmp",delete=False) as f:
            tmp=Path(f.name);json.dump(data,f,ensure_ascii=False,indent=2);f.write("\n")
        if path.read_bytes()!=original:raise ValueError("配置在检查后发生变化，取消修改")
        os.replace(tmp,path);tmp=None
    finally:
        if tmp is not None:tmp.unlink(missing_ok=True)
    return {**check_model(path,model_id),"status":"configuration_updated","changed":True,
            "previous_supports_images":previous,"next_step":"重新读取原附件一次；开关已改不代表本轮已收到图片。"}

def main():
    p=argparse.ArgumentParser(description="默认只读检查图片开关；显式修复仅修改指定模型supportsImages")
    p.add_argument("--model",required=True)
    p.add_argument("--model-config",default=str(Path.home()/".workbuddy/models.json"))
    p.add_argument("--set-images",choices=("true","false"),help="仅在已获配置修复授权时使用")
    p.add_argument("--confirmed-capable",action="store_true",help="已确认当前模型服务接口支持图片，开启时必需")
    p.add_argument("--output",help="仅需留档时指定；默认stdout")
    a=p.parse_args()
    if hasattr(__import__("sys").stdout,"reconfigure"):__import__("sys").stdout.reconfigure(encoding="utf-8")
    try:
        result=check_model(a.model_config,a.model) if a.set_images is None else set_image_flag(a.model_config,a.model,a.set_images=="true",a.confirmed_capable)
    except (OSError,ValueError) as e:
        # JSON error details may contain secrets; avoid printing configuration snippets.
        p.error(str(e) if isinstance(e,ValueError) and not isinstance(e,json.JSONDecodeError) else type(e).__name__)
    if a.output:
        dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"output":str(dest.resolve()),"status":result["status"]},ensure_ascii=False))
    else:print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__":main()

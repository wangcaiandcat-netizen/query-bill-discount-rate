# 图片输入、自检与恢复

附件实际可见就直接识别。识别“支持多模态”不靠模型自述：模型/服务接口支持图片、宿主开关允许、当前图片实际送达是三件事。配置检测不能证明第三件事；路径、普通stdout中的base64、工具仅返回保存路径都不算实际读图。

背面看出类型即结束，标bill_back，返回“这是票据背面，请上传票据正面图片。”。不抄票号、背书表，不匹配正面，不把背书信息传播到别的图。模糊金额或裁掉的数字留空；只在关键字段冲突时定点重读一次。

## 看不到时才诊断一次

从当前会话/用户实际选择确定模型id和生效配置，不能猜默认模型，也不能看某个DeepSeek名字就断言它支持或不支持图片。WorkBuddy可先只读检查：

~~~powershell
& $python -B "$skillRoot/scripts/check_runtime.py" --model "当前模型id"
~~~

默认只读~/.workbuddy/models.json；其他已确认配置路径用--model-config传入。Auto路由模型不可见时不修改任意自定义模型。工具只输出id、图片开关和诊断，不打印密钥或整个配置。模型配置冲突/不明确时不自动改。

若开关关闭，且已确认当前实际服务接口支持图片，用户或企业管理员已授权自动修复，可以直接使用固定脚本定点开启，不重复询问：

~~~powershell
& $python -B "$skillRoot/scripts/check_runtime.py" --model "当前模型id" --model-config "已确认生效的models.json" --set-images true --confirmed-capable
~~~

脚本只修改唯一匹配条目的supportsImages，返回原值，原子替换并检测并发改动，不改URL、密钥或其他模型。需要恢复原false值时用--set-images false。没有配置修复授权的普通查价不顺带修改全局配置；简短说明图片输入未开启。

修改后重新读取原附件一次，能读到实际内容才继续；配置更新不等于旧附件已经重新发送。宿主需要重开会话/重新选择模型才能生效时明确说明，不承诺即时恢复。不要另行请求外部模型API验证。

## 有界降级

优先使用宿主真实附件或图像读取工具。可用本地 OCR 时保留原文出处，识别方法写 local_ocr。读图失败时标注无法识别，将用户已确认的票据要素放在该票的 manual_fields，剩余项采用 Skill 默认值。其他可识别图片继续。

每图image_type=正面bill_front/背面bill_back/非票面non_bill/模糊unreadable/不确定uncertain。识别记录可单图或顶层写：
recognition={"method":"host_multimodal","evidence":"本次实际附件/读图工具结果依据"}。
脚本可补齐status=verified，含义只是已实际判读，不是银行验真。没有图像输入用status=unavailable；不编造依据。

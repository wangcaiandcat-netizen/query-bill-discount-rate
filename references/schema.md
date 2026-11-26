# Skill 输入契约

实时请求用 `bills` 数组。每张票可填写 `id`、`bill_type`（银票、财票、商票）、`acceptor_name`、`acceptor_code`（12 位数字字符串）、`acceptor_name_from_code`（已核验的行号对应机构）、`face_amount`（元）、`amount_upper`、`sub_bill_range`、`due_date`（YYYY-MM-DD）、`remaining_months` 或 `remaining_days`。如有名义到期日优先使用该日期。必要时提供实际到期日 `actual_due_date` 或计息基数 `day_basis`（默认 360，可指定 365）。

到期月份须为北京时间当前月（0M）或当前月之后的第1–6个月（1M–6M）。0M 覆盖今天至本月最后一天；1M–6M 包含目标月全部日期，例如2026年11月查询时，0M覆盖11-18至11-30，2026-12-01至12-31都属1M，2027-05-01至05-31都属6M；本月已过的日期和第7个月返回422。仅提供月数或天数时先换算名义到期日，再按其自然月归档。实际到期日用于计息，不改变基准档。

实时顶层字段仅有 `mode`（可省略或为 `realtime`）、`bills`、`attachments`、`recognition`。只查默认基准传 `{}`。例如查询三个月时传 `{"bills":[{"remaining_months":3}]}`，多张票使用同一个 `bills` 数组。脚本遇到未知字段会返回 `INPUT` 并退出；`tickets`、`bill` 和实时 `tenor` 均不属于此契约。

票据对象另支持 `bill_no`、`title`、`image_type`、`document_side`、`source_image`、`recognition`、`manual_fields`、`market_group`、`amount_role`、`bill_status`、`has_discount_endorsement`、`discount_date`，供图片校验、分档、去重和状态识别使用。票据对象及 `manual_fields` 中的未知字段同样返回 `INPUT`。

承兑行档级由脚本根据承兑行名称或已核验的行号对应机构自动识别。旧请求中的 `acceptor_level` 请删除，改为传 `acceptor_name`，或同时传 `acceptor_code` 和已核验的 `acceptor_name_from_code`。

图片请求增加 `attachments`（逐个 `id` 对应）与 `recognition`（真实读图依据，见 [图片输入](image-input.md)）。单票查价示例：

读图失败时，把用户手工确认的字段单独放在该票的 `manual_fields` 对象。脚本会保留这些字段并按默认规则补齐剩余项。

```json
{"bills":[{"id":"票1","bill_type":"银票","acceptor_name":"长沙银行","face_amount":"800000","remaining_days":90}]}
```

历史模式示例，开始和结束日期都包含：

```json
{"mode":"history","start_at":"2026-11-02","end_at":"2026-11-04","tenor":"3M"}
```

省略 `tenor` 查询全部档。只给一个日期时，查询该北京时间自然日。精确时段需要开始和结束两个带时区的时间，包含开始时刻，结束时刻为排他边界。

历史顶层字段为 `mode`、`start_at`、`end_at`、`tenor`。模式为 `history`，兼容旧别名 `market`，提供日期区间也可直接选择历史模式。缺少查询日期或区间时返回 `INPUT`。

历史结果的 `query_window` 记录实际请求的 `start_at`、`end_at` 和 `tenor`。`items` 包含全部逐轮记录，保留 `tenor`、`rate_percent`、`round_at` 和 `collected_at`。区间无数据时返回空数组。

单票结果为 `price.status=unsupported` 时同时返回 `pricing_allowed=false`。此时采用脚本的待询价说明结束单票估价，银票基准仅作为独立行情展示。

实时结果的 `dates` 返回本次解析出的 `nominal_due_date`、`actual_due_date`、`remaining_days` 和日历信息。解释M档使用名义到期日及 `baseline.tenor`；计息天数使用实际到期日，缺少真实票额时不补造利息。

# DeepFin 直贴利率 API（0M–6M）

默认连接生产接口 `https://deepfin.vip/api/v1/external/market/discount-quotes/v2`。使用其他 DeepFin 实例时，通过 `DEEPFIN_DISCOUNT_API_URL` 指定其地址。服务要求授权时，通过 `DEEPFIN_MARKET_API_KEY` 设置具备市场读取权限的密钥。

此接口提供 DeepFin 已入库的直贴参考行情，响应不包含上游采集渠道和内部取价信息。对外说明沿用 DeepFin 行情口径，不推断采集渠道。

0M–6M 为票面到期月份相对北京时间当前月的偏移量。0M 覆盖本月剩余日期（今天至月末），1M–6M 覆盖各目标自然月全部日期（11月查询时1M为12月、6M为次年5月）；超过本月+6月返回422。到期日在本月且已过则返回422。每档使用对应到期月份的已入库参考价，缺报保留为空，不推算补齐。

- `POST /realtime` 接收 `{"maturity_date":"2027-01-15"}`，用 `{}` 查默认 6M。返回最近一次入库的 `tenor`、`rate_percent`、`collected_at`、`status`。利率单位为年化百分比，时间为该笔的实际采集时间。样本距今超过三小时或跨北京时间自然日时标为 `stale`。本月缺报时标为 `no_quote`，利率和采集时间均为空。
- `GET /latest` 返回 0M–6M 各档最近一次入库的最终价，字段同实时接口。
- `POST /history` 按轮次时间筛选，接收带时区的 `start_at`、`end_at`（左闭右开），可选 `tenor`。返回 `items` 和 `next_after_id`。每条记录带期限 `tenor`、利率 `rate_percent`、轮次日期时间 `round_at` 和采集日期时间 `collected_at`。脚本自动取完所有分页并返回逐轮数据。

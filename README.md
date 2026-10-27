# devin-quota

Devin 周额度看板 — opencode 风格的 burn-rate 监控。

## 用法

```bash
python3 quota.py serve        # http://localhost:8377
python3 quota.py poll         # 手动抓一次写入 history.jsonl
```

- `serve` 模式：看板每 60s 自动刷新，数据过期时才打 API（间隔 60s，空闲时自动降到 5min）
- `?refresh=1` 异步触发后台 poll，立即返回当前 state；前端点 Refresh 后 4.5s 自动再拉一次拿新样本
- 数据落盘：`history.jsonl`,一行一个 snapshot
- token 从 `~/.local/share/devin/credentials.toml` 读，org 从 `~/.config/devin/config.json` 读
- launchd 常驻：`com.mark.devin-quota`(RunAtLoad + KeepAlive)，日志 `/tmp/devin-quota-server.log`

## 看板交互

- **Hover**：十字线 + tooltip，显示时刻 / 实测 usage / forecast / even pace 差值
- **拖动框选**放大 X 轴区间，**双击**还原全周视图
- 统计卡：剩余额度 / 距 reset / 燃烧速度 / 本小时消耗 / 今日消耗 / overage 余额 — 全部由 `history.jsonl` 实测样本计算，无回填数据

## 数据源

`GET https://api.devin.ai/{org_id}/billing/quota/usage` — 和 Devin webapp
`app.devin.ai/settings/usage` 页面是同一个接口，只读、无副作用。

返回 `weekly_percentage` / `daily_percentage` / `weekly_reset_at` /
`overage_balance` 等字段。另带 `billing/usage/stats`（计费周期 ACU）和
`billing/usage/limits`（单 session ACU cap)。

## 预测口径

与 opencode / ccusage / ai-session-meter 相同的 week-to-date 平均速率法：

- `weekly_percentage` 是自 reset 起的累计值，所以 `pct / elapsed_hours` 就是
  底层曲线的真实平均斜率 —— 等价于采样斜率在整周窗口上的极限，比用监控
  开始后的一小段样本更准（整数 % 粒度下短窗斜率全是噪声）。`rate_source`
  恒为 `week_avg`;`window_rate_per_hour` 保留已收集样本的斜率作参考。
- **projected exhaustion**: rate 线性外推撞到 100% 的日期；晚于 reset 则本周
  不会耗尽，显示 "On pace for X% at reset"。
- **projected_pct_at_reset**: `pct + rate × (reset - now)`。
- 图上另有 **Even pace** 参考线：窗口内 0%→100% 的匀速对角线。实际线在其上
  = 用超了，其下 = 用得慢；标题行直接显示 `±X% vs even pace`。

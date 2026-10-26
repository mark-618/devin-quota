# devin-quota

Devin 周额度看板 — opencode 风格的 burn-rate 监控。

## 用法

```bash
python3 quota.py serve        # http://localhost:8377
python3 quota.py poll         # 手动抓一次写入 history.jsonl
```

- `serve` 模式：看板每 60s 自动刷新，数据过期时才打 API（间隔 60s）
- 数据落盘：`history.jsonl`,一行一个 snapshot
- token 从 `~/.local/share/devin/credentials.toml` 读，org 从 `~/.config/devin/config.json` 读

## 数据源

`GET https://api.devin.ai/{org_id}/billing/quota/usage` — 和 Devin webapp
`app.devin.ai/settings/usage` 页面是同一个接口，只读、无副作用。

返回 `weekly_percentage` / `daily_percentage` / `weekly_reset_at` /
`overage_balance` 等字段。另带 `billing/usage/stats`（计费周期 ACU）和
`billing/usage/limits`（单 session ACU cap)。

## 预测口径

与开源同类项目(ccusage / claude-monitor / ai-session-meter）一致的两层结构：

- **samples rate**: 本周窗口内历史样本的首尾斜率（%/day)。仅当样本跨度
  ≥1h 且斜率落在 0–150 %/day 的合理区间才采用——短窗口斜率噪声太大。
- **pace rate (fallback)**: `weekly_pct / elapsed_hours_in_week`(week-to-date
  均匀速度），即 claude-meter / ai-session-meter 的标准做法。样本不可用时
  用它，`rate_source` 字段标 `pace` / `samples`。
- **projected exhaustion**: 选中 rate 线性外推撞到 100% 的日期；若晚于
  reset 则本周不会耗尽，看板显示 "On pace for X% at reset"。
- **projected_pct_at_reset**: `pct + rate × (reset - now)`,即 pace projection;
  上界 clamp 在 reset 处，不做超界放大。

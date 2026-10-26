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

- **burn rate (avg)**: 本周窗口内所有样本首尾两点的斜率（%/day)。样本不足或
  跨度 <3 分钟时显示 collecting…
- **pace vs linear**: `weekly_pct / elapsed_fraction_of_week`,即按当前均匀速度
  到 reset 那天会到多少 %,>100% 标红
- **projected exhaustion**: burn rate >0 时线性外推撞到 100% 的日期；若晚于
  reset 则显示 "won't exhaust"

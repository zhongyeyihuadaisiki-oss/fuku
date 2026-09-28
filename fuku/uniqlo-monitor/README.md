# UNIQLO / GU 男装 M/L码折扣监控

- 每小时自动运行（Windows计划任务 `UniqloGUMonitor`，pythonw 后台静默运行，一次约3〜4分钟）
- 扫描 UNIQLO / GU 男装全部带「値下げ」「期間限定価格」标记的商品
- 条件：M或L码有货 + 现价 ≤ 原价×0.7 → 推送到手机（带商品图，点通知直接打开商品页）
- 同一商品同一价格只推一次；卖完后再补货、或者再降价，会重新推送
- 短袖/夏季/内裤袜子等按商品名关键词排除（`EXCLUDE_KEYWORDS`）

## 手机端（只需做一次）

1. 装 App「ntfy」
2. 打开 → 「+」 → Topic 填 `yi-ufm-q7k2m9x4tw` → Subscribe（服务器保持默认 ntfy.sh）
3. 允许通知

## 原价是怎么来的

官网接口只给现价，不给原价。脚本每次运行会扫一遍男装全目录，记下每件商品**没打折时**的价格（`prices.json`），
再加上同商品其他颜色/价格组的正价，取最大值当原价。找不到原价的商品不推送，记为「原价未知」。
运行时间越长，原价库越全（首次运行已学到约1000件）。

## 文件

- `last_run.log` 最近一次运行结果（命中/原价未知/错误）
- `prices.json` 原价库
- `state.json` 已推送记录

## 注意

- 电脑关机/休眠/未登录时不会运行
- 裤子如果是腰围尺码（cm/inch）没有"M"，不在监控范围内
- 改尺码：`TARGET_SIZES`（003=S, 004=M, 005=L）；改折扣门槛：`DISCOUNT_THRESHOLD`

```powershell
schtasks /run /tn "UniqloGUMonitor"      # 立刻手动跑一次
schtasks /delete /tn "UniqloGUMonitor" /f # 停用
```

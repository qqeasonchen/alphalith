# Alphalith 数据源 — 本批次新增

> 2026-06-18 接入：SEC EDGAR / Yahoo 期权 / 东财龙虎榜·解禁·大宗交易
> 全部纯 stdlib，无第三方依赖

---

## 1. SEC EDGAR XBRL — 美股深度财务（`financial_us.py`）

**端点**：`https://data.sec.gov/api/xbrl/companyfacts/CIK<10位>.json`
**覆盖**：503 GAAP 指标（Revenues / NetIncomeLoss / EPS / R&D / OCF / Assets / Equity ...）
**依赖路径**：`Ticker → CIK`（来自 `https://www.sec.gov/files/company_tickers.json`）

### 关键 API

```python
from alphalith.financial_us import fetch_us_snapshot

snap = fetch_us_snapshot("AAPL")
# SecSnapshot(cik='0000320193', revenue_ttm=4.16e11, net_income_ttm=1.12e11,
#             eps_ttm=7.46, rd_ttm=3.7e10, ocf_ttm=1.4e11,
#             total_assets=3.65e11, equity=6.7e10, fiscal_year=2025)
```

### 自动 fallback

`alphalith.financial.load_financials("AAPL")` 在 yfinance 失败时会自动用 SEC 兜底，
成功时也会**叠加**SEC 数据到 `raw_metrics` 的 `sec_*` 字段。

### 踩坑 ⚠️

1. **UA 必须 `公司名 邮箱` 格式** —— 带 `/版本号` 一律 403。当前用：
   `Alphalith Research alphalith@example.com`
2. **TTM 算法**：10-Q 的 OCF / R&D 是 YTD 累计，简单累加会重复计算。
   解法：`上一年报 + 当期 YTD - 去年同期 YTD`
3. **GAAP 字段陷阱**：Apple 实际用 `NetCashProvidedByUsedInOperatingActivities`
   （多 "UsedIn"），不是 `NetCashProvidedByOperatingActivities`

### 实测（FY2025 Apple）

| 指标 | 值 |
|---|---|
| Revenue TTM | $416B |
| Net Income TTM | $112B |
| EPS TTM | $7.46 |
| R&D TTM | $37B |
| OCF TTM | $140B |

---

## 2. Yahoo Finance 期权链（`options.py`）

**端点**：`https://query2.finance.yahoo.com/v7/finance/options/{symbol}`
**覆盖**：全到期日 + calls/puts（IV / OI / Volume / Strike）
**鉴权**：cookie + crumb（`finance.yahoo.com` warm-up → `query1/v1/test/getcrumb`）

### 关键 API

```python
from alphalith.options import fetch_option_chain, option_sentiment

chain = fetch_option_chain("AAPL")
chain.put_call_volume_ratio  # PCR(vol)
chain.put_call_oi_ratio      # PCR(OI)
chain.atm_iv("call")         # 取贴近现价 3 张合约的 IV 均值

sent = option_sentiment("TSLA")  # 一行供情绪层调用
```

### 限流策略

- crumb **磁盘缓存 6h**：`/tmp/alphalith_yahoo_crumb.json`
- 极简 UA `Mozilla/5.0`（完整 Safari UA 反而触发风控）
- query1 / query2 双源轮询

### 当前限制 ⚠️

部分网络（沙箱 / 高频请求 IP）会被 Yahoo 直接 401/429 拒绝 crumb。
解决：在用户本地或独立服务器跑，或接 yfinance 商业代理。

---

## 3. 东财龙虎榜（`dragon.py`）

**端点**：`datacenter-web.eastmoney.com/api/data/v1/get`
**Reports**：
- `RPT_DAILYBILLBOARD_DETAILSNEW` — 每日上榜个股
- `RPT_BILLBOARD_TRADEDETAIL` — 个股席位明细（买卖各前 5）

### 关键 API

```python
from alphalith.dragon import fetch_dragon_list, fetch_dragon_with_seats, summarize_for_agent

# 全市场最近一日 Top
recs = fetch_dragon_list(page_size=20)

# 个股最近一次上榜 + 席位
rec = fetch_dragon_with_seats("603986")
print(summarize_for_agent(rec))
# 龙虎榜[2026-06-18] 兆易创新(603986) 涨跌 +7.33% | 净买入 8.79亿 ...
```

### 字段映射

| API 字段 | DragonRecord 字段 |
|---|---|
| `BILLBOARD_NET_AMT` / `NET_BUY_AMT` | net_buy |
| `BILLBOARD_DEAL_AMT` | turnover |
| `EXPLAIN` | reason（上榜原因） |
| `OPERATEDEPT_NAME` | seat.branch |

### 限流封装（`em.py`）

`em_get()` / `em_table()`：
- 串行节流 1.5s/次（线程锁）
- HTTPError/超时 → 指数退避重试 2 次
- 单 IP 实测 < 60 次/分钟安全

---

## 4. 东财解禁日历（`unlock.py`）

**Report**：`RPT_LIFT_STAGE`
**字段**：`LIFT_DATE` / `NUM` / `LIFT_PROPORTION` / `LIFT_TYPE` / `MARKET_CAP_OF_CIRCULATION`

```python
from alphalith.unlock import fetch_upcoming_unlocks, fetch_stock_unlocks

# 全市场未来 30 天
events = fetch_upcoming_unlocks(days=30)

# 个股未来 180 天
mine = fetch_stock_unlocks("600519", future_days=180)
```

### 风险标签

`UnlockEvent.risk_tag`：自动根据 `占流通比例` 给出 **重大抛压 / 抛压 / 一般**。

---

## 5. 东财大宗交易（`block_trade.py`）

**Report**：`RPT_DATA_BLOCKTRADE`
**字段**：`TRADE_DATE` / `DEAL_AMT` / `PREMIUM_RATIO` / `BUYER_NAME` / `SELLER_NAME`

```python
from alphalith.block_trade import fetch_block_trades, summarize_for_agent

# 茅台最近 30 天
trades = fetch_block_trades(code="600519", days=30)
print(summarize_for_agent(trades, "600519"))
# 大宗交易: 12笔合计4.94亿元 整体折价（减持压力） 折溢价 -3.21% ...
```

### 解读规则

- 折价 > 5% + 卖出方知名营业部 → **减持信号**
- 溢价 + 买入方为机构席位 → **接盘信号**
- 同一买卖双方反复对倒 → 警惕避税或利益输送

---

## Pipeline 注入

### `data.py` 自动叠加

```
fundamental_note  ← SEC 摘要 (US) 或 龙虎榜+解禁+大宗交易 (A 股)
sentiment_note    ← 龙虎榜+大宗交易 资金流信号 (A 股，双通道)
```

### `agents.py` Prompt 强化

- **基本面分析师** 看到 `SEC` 字段 → 解读毛利率/研发强度/现金流
- **情绪分析师** 看到 `[资金流信号]` → 区分机构/游资/解禁/对倒

### 测试用例

```bash
# A 股（兆易创新昨日上榜）
python -c "from alphalith.data import load_market_data; print(load_market_data('603986').sentiment_note)"

# 美股（SEC + yfinance）
python -c "from alphalith.financial import load_financials; f=load_financials('AAPL'); print(f.note)"
```

---

## 兼容性

| 数据源 | A 股 | 港股 | 美股 |
|---|---|---|---|
| SEC EDGAR | — | — | ✅ |
| Yahoo 期权 | — | — | ✅ |
| 东财龙虎榜 | ✅ | — | — |
| 东财解禁 | ✅ | — | — |
| 东财大宗 | ✅ | — | — |

---

🪨 by 广智 — Alphalith

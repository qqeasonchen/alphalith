# Alphalith CLI 使用手册

> 命令行入口 `alphalith` 的所有子命令及完整用法。

---

## 安装与配置

```bash
git clone <repo> alphalith && cd alphalith
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

LLM Key（可选，不接也能跑 StubLLM 兜底）：

```bash
export DEEPSEEK_API_KEY=your-deepseek-key
```

---

## `analyze` — 单标的投研分析

```bash
alphalith analyze 茅台              # 中文名 → A 股
alphalith analyze 600519.SS         # A 股代码
alphalith analyze 0700.HK           # 港股
alphalith analyze NVDA              # 美股
alphalith analyze NVDA --depth deep # 多轮辩论（4 分析师 + 多空 + 风控）
```

参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `symbol` | (必填) | 标的代码 / 中文名 / 英文名 |
| `-d, --depth` | `standard` | `quick` / `standard` / `deep` |
| `--provider` | `sina` | 数据源 |
| `--model` | 系统配置 | 覆盖 LLM 模型 |

---

## `analyze-batch` — 批量分析

```bash
alphalith analyze-batch 600519 0700.HK NVDA --depth quick
```

空格分隔多标的，串行执行，单个失败不阻塞后续。

---

## `backtest` — 策略回测

```bash
# 基础回测（均线策略，90 日数据，5 日持有窗口）
alphalith backtest 600519 --days 90 --horizon 5

# 指定策略
alphalith backtest 600519 --strategy rsi               # 单策略
alphalith backtest 600519 --days 30 --strategy llm     # LLM 决策器

# 输出 HTML 可视化报告
alphalith backtest 600519 --html report.html

# 双策略对比
alphalith backtest 600519 --compare rsi --html cmp.html
alphalith backtest 600519 --compare llm --html cmp.html
```

内置 7 策略：`ma_cross` / `macd` / `rsi` / `bollinger` / `momentum` / `reversal` / `llm`

---

## `dashboard` — 生成 Dashboard HTML

```bash
alphalith dashboard --symbols 600519,0700.HK,NVDA --output dashboard.html
```

生成自包含 HTML 面板，含行情卡片、策略信号矩阵、回测热力图、决策时间线。

---

## `history` — 决策历史

```bash
alphalith history --limit 20               # 最近 20 条
alphalith history --symbol 600519 --limit 10 # 按标的筛选
```

---

## `review` — 审查统计

```bash
alphalith review              # 全局统计
alphalith review --days 30    # 近 30 日
```

输出：买入/卖出/持仓次数 + 胜率 + 平均置信度。

---

## `gui` — 启动 GUI 工作台

```bash
alphalith gui                    # 默认 8888 端口
alphalith gui --port 3000        # 自定义端口
alphalith gui --no-browser       # 仅启动服务不打开浏览器
```

> GUI 功能详见 [README](../README.md#-gui-投研工作台)。

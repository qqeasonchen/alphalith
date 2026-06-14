# 🪨 Alphalith · 慧投

> **项目名来源**: *Alphalith* = **Alpha**（超额收益）+ **Lith**（古希腊语 λίθος，"立石/ bedrock"）→ "封存于立石的 Alpha 决策"，与文末铭文 *Sealed in the Bedrock* 呼应。中文名"慧投"= 慧眼投研。
>
> **The Bedrock of AI-Driven Alpha** · AI 慧眼，洞察先机
>
> 一个轻量、零外部依赖的多智能体 AI 投研引擎，原生支持 **A 股 / 港股 / 美股**。

```bash
pip install -e .
export DEEPSEEK_API_KEY=sk-...
alphalith analyze 茅台
```

15 秒一份带真实行情、真实基本面、真实新闻、真实 LLM 推理的投研报告。

---

## ✨ 核心特性

- **零外部依赖**：仅 Python 3.10+ 标准库即可运行（`dependencies = []`）。`pip install` 几秒搞定。
- **三市场原生支持**：
  - 🇨🇳 A 股：T+1 / 涨跌停 / 印花税 / 100 股每手
  - 🇭🇰 港股：每手字典 / 印花税 0.1% / CCASS 费 / 盘前盘后
  - 🇺🇸 美股：T+1 资金交收 / SEC + FINRA 费 / 1 股起买 / 盘前盘后
- **4 智能体投研委员会**：技术 / 基本面 / 新闻 / 情绪 + 多空辩论 + 风控复核。
- **真实数据闭环**：
  - 行情：新浪 `hq.sinajs.cn`（A/HK/US 全覆盖）
  - 基本面：腾讯 `qt.gtimg.cn`（PE / PB / ROE / 市值）
  - 新闻：东财 `search-api`（5 条/标的实时头条）
- **LLM 智能降级链**：DeepSeek → Qwen → Claude → Ollama → StubLLM（无 key 也能跑）。
- **结构化输出**：JSON 解析 + 正则降级，token 消耗下降 ~3%。
- **Token 透明化**：每次决策报告底部展示真实 in/out token 数，支持账单对账。
- **决策日志**：SQLite 自动落库（`~/.alphalith/journal.db`），可历史回溯与复盘。
- **多策略回测**：均线 / MACD / RSI / 布林带 / 动量突破 / 均值反转 六种决策器，历史 K 线滚动评估。
- **完整风险指标**：胜率、累计收益、最大回撤、夏普、Calmar、Sortino、信息比率、盈亏比、最长连胜/连败。
- **HTML 可视化**：Chart.js 价格图 + 买卖点标注 + 资金曲线对比 + buy & hold 基准。
- **双策略对比**：`--compare` 一键并排对比 + 决策分歧高亮 + 双策略交易明细 + LLM 综合评语。
- **Dashboard 面板**：`alphalith dashboard` 一键生成自包含 HTML，含行情卡片、策略信号矩阵、回测热力图、决策时间线。
- **ADP v1.0 协议**：决策对象遵循"AI 投研决策开放标准"，可直接 webhook / 跨系统传递。

---

## 🚀 30 秒上手

```bash
# 1. 安装
git clone <repo> alphalith && cd alphalith
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. （可选）接 LLM。不接也能跑，走 StubLLM 兜底
export DEEPSEEK_API_KEY=sk-xxxxx

# 3. 三市场任选
alphalith analyze 茅台              # 中文名→A 股
alphalith analyze 600519.SS
alphalith analyze 0700.HK           # 港股
alphalith analyze NVDA              # 美股
alphalith analyze NVDA --depth deep  # 多轮辩论

# 4. 批量分析（命令行多参数，单个失败不阻塞后续）
alphalith analyze-batch 600519 0700.HK NVDA --depth quick

# 5. 历史回测（拉真实日 K 线，滚动模拟简化策略）
alphalith backtest 600519 --days 90 --horizon 5
alphalith backtest 600519 --days 30 --strategy llm   # LLM 决策器
alphalith backtest 600519 --strategy rsi              # MACD/RSI/布林策略
alphalith backtest 600519 --html report.html          # 输出 HTML 可视化
alphalith backtest 600519 --compare rsi --html cmp.html  # 双策略对比
alphalith backtest 600519 --compare llm --html cmp.html   # 技术 vs LLM

# 6. 复盘
alphalith history --limit 20
alphalith review

# 7. Dashboard 面板（生成自包含 HTML）
alphalith dashboard --symbols 600519,0700.HK,NVDA --output dashboard.html
```

---

## 📋 报告示例（茅台真实数据）

```
🪨 Alphalith · 慧投 投研报告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🇨🇳 贵州茅台(600519.SS)  现价 ¥1291.91 (+1.01%)

【4 位分析师】
  🟢 技术分析师      (78%) 现价 1291.91 较昨收 1279.00 上行 +1.01%，
                            距涨停仍有 +8.90% 空间...
  🟢 基本面分析师    (80%) PE(TTM) 14.82 处于历史低位，ROE 19.52% 稳健，
                            高端白酒龙头护城河深厚...
  ⚪ 新闻分析师      (65%) 新闻显示"董事长陈华：暂无拆股计划"，对短期股价影响中性...
  ⚪ 情绪分析师      (65%) 涨跌 +1.01% 与新闻倾向同步，热度温和...

【多空辩论】
  🐂 看多：PE 14.82 处于历史低位，ROE 19.52% 强劲，估值修复空间明确
  🐻 看空：PE 14.82 虽低但 ROE 已显疲态，"暂无拆股计划"缺乏催化剂

【市场规则】
  • T+1：今日买入次日才能卖出
  • 距涨停 +8.90%（涨停价 ¥1406.90）
  • 最小交易单位：100 股（1 手）

🛡 风控：拒绝：建议手数 < 最小交易单位，自动改为 hold
🔧 LLM：deepseek    数据源：sina    深度：standard
💬 调用：6 次    in：2961 tok    out：380 tok    total：3341 tok
```

---

## 🐍 Python API

```python
from alphalith import analyze

d = analyze("茅台")            # 自动走真实行情 + LLM
print(d.action, d.confidence)  # 'hold', 0.86
print(d.to_adp_json())         # ADP v1.0 标准 dict
```

---

## 🔌 LLM 切换

| Provider | 安装 | Env Key | 说明 |
|---|---|---|---|
| DeepSeek | （内置 urllib，零依赖） | `DEEPSEEK_API_KEY` | 推荐，¥0.003/次 |
| Qwen | `pip install -e .[qwen]` | `QWEN_API_KEY` | 阿里通义 |
| Claude | `pip install -e .[anthropic]` | `ANTHROPIC_API_KEY` | 高质量 |
| Ollama | `pip install -e .[ollama]` | `OLLAMA_HOST` | 本地零成本 |
| StubLLM | （内置） | — | 无 key 兜底 |

---

## 🗂️ 目录结构

```
alphalith/
├── core.py        # analyze() 主入口
├── market.py      # 三市场识别 + 中文名解析（茅台→600519.SS）
├── data.py        # 行情/新闻/基本面统一 Provider
├── rules/         # A/HK/US 三市场规则引擎
├── agents.py      # 4 分析师 + 多空辩论
├── llm.py         # 降级链 + token 计数
├── schema.py      # ADP v1.0 Decision dataclass
├── backtest.py    # 回测引擎（均线/LLM 策略 + buy&hold 基准 + 夏普/最大回撤）
├── html_report.py # HTML 可视化报告（Chart.js 价格图/资金曲线/买卖点）
├── journal.py     # SQLite 决策日志
├── report.py      # 中文报告渲染
└── cli.py         # CLI 入口（analyze / analyze-batch / backtest / history / review）
```

---

## 📜 协议与致敬

- **ADP v1.0 协议**：见 `../ADP_PROTOCOL_v1.md`
- **致敬**：受 TradingAgents、TradingAgents-CN、TradingView 启发，但核心代码 100% 自研。详见 `../ATTRIBUTION.md`。
- **License**：MIT

---

## 📈 回测基准对比（均线策略 90 日回测，5 日持有窗口）

| 标的 | 入场 | 胜率 | 累计 | B&H | Alpha | 夏普 | 最大回撤 |
|---|---|---|---|---|---|---|---|
| 茅台 600519 | 36 笔 | 63.9% | +7.90% | -3.73% | **+11.63%** | 0.61 | -26.22% |
| 腾讯 0700.HK | 20 笔 | 80.0% | +5.08% | -16.99% | **+22.07%** | 0.62 | -13.69% |
| NVDA | 19 笔 | 68.4% | +29.14% | +14.91% | **+14.22%** | 3.10 | -11.83% |

> 三市场均跑赢 buy & hold 基准。LLM 决策器（`--strategy llm`）单笔质量更高、夏普更优，但成本随 days 线性增长。

### 策略模板对比（120 日，5 日窗口）

| 标的 | ma_cross | macd | rsi | bollinger | momentum | reversal |
|---|---|---|---|---|---|---|
| 茅台 90d | +7.90% / 36笔 | -9.14% / 14笔 | +10.45% / 6笔 | -23.83% / 25笔 | +11.05% / 15笔 | **+12.44% / 3笔** |
| 腾讯 90d | -70.43% / 35笔 | +7.77% / 12笔 | +8.01% / 7笔 | **+70.82% / 23笔** | -49.32% / 12笔 | +3.84% / 3笔 |
| NVDA 90d | +69.07% / 35笔 | +6.42% / 12笔 | +0.06% / 5笔 | +7.12% / 22笔 | -13.31% / 14笔 | +4.01% / 4笔 |

> 不同市场适配不同策略：A 股 RSI 超卖反弹更有效，港股/美股布林带均值回归更强。
> `reversal` 策略极度精选（3-4 笔/90 日），只在极端 z-score 时出手，盈亏比最高。
> 可用 `--compare` 双策略对比 HTML 直观查看优劣。

---
## 📊 Dashboard 面板

一键生成自包含 HTML Dashboard，无需服务器：

```bash
alphalith dashboard --symbols 600519,0700.HK,NVDA --output dashboard.html
```

面板内容：
- 实时行情卡片（价格 + 涨跌幅 + 成交量）
- 策略信号矩阵（每个标的 × 每种策略的最新动作）
- 回测绩效总览表（6 策略 × 3 市场）
- 收益热力图（Canvas 渲染，绿=盈/红=亏）
- 最近决策时间线（来自 SQLite 日志）

---
## 🖥️ GUI 投研工作台

零外部依赖，单文件 HTML（CSS + JS 内联）+ Python 内置 HTTP 服务，一键启动：

```bash
alphalith gui                    # 默认 8888 端口，自动打开浏览器
alphalith gui --port 3000        # 自定义端口
alphalith gui --no-browser       # 仅启动服务，不打开浏览器
```

### 界面布局

- **侧栏导航**：投研分析 / 策略回测 / 投研面板 / 信号中心 / 历史记录
- **投研分析页**：支持单标的或空格/逗号分隔的多标的；深度可选 Quick/Standard/Deep；勾选"实时辩论"可 SSE 流式观看四分析师报告 + 多空辩论过程；进度条实时显示分析阶段
- **策略回测页**：7 种策略多选（checkbox），并行回测并展示对比表；单策略显示完整 14 项指标（总收益/年化/最大回撤/Sharpe/Sortino/Calmar/信息比率/Alpha/Beta/最大连盈连亏/盈亏比等）+ 收益曲线 + 交易记录
- **投研面板页**：一键生成 Dashboard HTML，支持新窗口预览或下载
- **信号中心 / 历史记录**：保留原有功能

### v0.3.0 新增能力（GUI 已完整覆盖 CLI）

| 功能 | CLI | GUI |
|---|---|---|
| SSE 实时辩论流式展示 | ✅ | ✅ |
| 多标的批量分析 | `analyze-batch` | ✅ 输入框空格分隔 |
| 多策略并行回测 | `backtest --strategy A --strategy B` | ✅ 多选 checkbox |
| 9+ 风险指标展示 | ✅ `--html` | ✅ 14 项指标卡片 |
| 市场规则提示 | ✅ | ✅ 黄色提示卡 |
| ADP JSON 导出 | ✅ `to_adp_json()` | ✅ 一键下载按钮 |
| Token 消耗透明化 | ✅ 报告底部 | ✅ 投资决策卡片内 |
| 完整手续费明细 | ✅ | ✅ 佣金/印花税/过户费/SEC/其他 |
| Dashboard 生成 | ✅ `dashboard` 子命令 | ✅ 投研面板页 |

---

> 🪨 *Sealed in the Bedrock — 决策已封存于立石*

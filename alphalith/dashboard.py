"""
Dashboard — 自包含 HTML 可视化面板。

用法：
    alphalith dashboard --symbols 600519,0700.HK,NVDA --output dashboard.html
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from .market import detect_market
from .data import load_market_data
from .backtest import STRATEGIES, run_backtest, BacktestResult
from .journal import history as journal_history


@dataclass
class DashboardConfig:
    symbols: list[str] = field(default_factory=lambda: ["600519", "0700.HK", "NVDA"])
    backtest_days: int = 90
    backtest_horizon: int = 5


def render_dashboard(cfg: DashboardConfig, journal_path: Optional[str] = None) -> str:
    """生成自包含 HTML Dashboard。

    面板：
    1. 实时行情卡片
    2. 策略信号热力图（每个标的 × 每种策略的最新动作）
    3. 回测绩效总览表
    4. 最近决策时间线（来自 SQLite 日志）
    """
    # 1. 收集实时行情
    quotes: list[dict] = []
    for sym in cfg.symbols:
        try:
            md = load_market_data(sym)
            q = md.quote
            quotes.append({
                "symbol": sym,
                "name": q.name or sym,
                "price": q.price or 0,
                "change_pct": q.change_pct or 0,
                "volume": q.volume or 0,
            })
        except Exception:
            quotes.append({
                "symbol": sym, "name": sym, "price": 0,
                "change_pct": 0, "volume": 0,
            })

    # 2. 每种策略对每个标的的决策快照
    strategy_snapshots: list[dict] = []
    for st_name in STRATEGIES:
        for sym in cfg.symbols:
            try:
                r = run_backtest(sym, days=cfg.backtest_days,
                                 horizon=cfg.backtest_horizon, strategy=st_name)
                snap = {
                    "symbol": sym, "strategy": st_name,
                    "actionable": len(r.actionable),
                    "win_rate": r.win_rate,
                    "total_pnl": r.total_pnl,
                    "sharpe": r.sharpe,
                    "alpha": r.alpha_vs_bh,
                    "latest_action": r.trades[-1].action if r.trades else "hold",
                    "latest_reason": (r.trades[-1].reason or "")[:40] if r.trades else "",
                }
            except Exception:
                snap = {
                    "symbol": sym, "strategy": st_name,
                    "actionable": 0, "win_rate": 0, "total_pnl": 0,
                    "sharpe": 0, "alpha": 0,
                    "latest_action": "hold", "latest_reason": "",
                }
            strategy_snapshots.append(snap)

    # 3. 最近决策日志
    recent_decisions: list[dict] = []
    try:
        rows = journal_history(limit=20)
        for row in rows:
            recent_decisions.append({
                "ts": row.get("ts", ""),
                "symbol": row.get("symbol", ""),
                "action": row.get("action", ""),
                "confidence": row.get("confidence", 0),
                "llm": row.get("llm", ""),
            })
    except Exception:
        pass

    return _DASHBOARD_HTML.replace(
        "__QUOTES__", json.dumps(quotes, ensure_ascii=False)
    ).replace(
        "__SNAPSHOTS__", json.dumps(strategy_snapshots, ensure_ascii=False)
    ).replace(
        "__DECISIONS__", json.dumps(recent_decisions, ensure_ascii=False)
    ).replace(
        "__SYMBOLS__", json.dumps(cfg.symbols, ensure_ascii=False)
    )


_DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>Alphalith Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family: -apple-system,"PingFang SC","Helvetica Neue",sans-serif;
       background:#0b1020; color:#e7ecf5; padding:24px; }
h1 { font-size:22px; margin-bottom:4px; }
.sub { color:#8b95ad; font-size:13px; margin-bottom:20px; }
.grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; margin-bottom:20px; }
.card { background:#0f1428; border:1px solid #1f2742; border-radius:10px; padding:16px; }
.card .name { font-size:13px; color:#8b95ad; }
.card .price { font-size:28px; font-weight:700; margin:6px 0; }
.card .change { font-size:14px; }
.card .volume { font-size:11px; color:#8b95ad; margin-top:4px; }
.up { color:#dc2626; } .down { color:#16a34a; }
.panel { background:#0f1428; border:1px solid #1f2742; border-radius:10px; padding:16px; margin-bottom:18px; }
.panel .title { color:#8b95ad; font-size:13px; margin-bottom:12px; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th,td { padding:6px 8px; border-bottom:1px solid #1f2742; text-align:center; }
th { color:#8b95ad; font-weight:500; background:#0b1020; position:sticky;top:0; }
td:first-child, th:first-child { text-align:left; }
.pos { color:#16a34a; } .neg { color:#dc2626; }
.buy-badge { background:#22c55e22; color:#22c55e; padding:2px 8px; border-radius:3px; font-weight:600; }
.sell-badge { background:#ef444422; color:#ef4444; padding:2px 8px; border-radius:3px; font-weight:600; }
.hold-badge { background:#8b95ad22; color:#8b95ad; padding:2px 8px; border-radius:3px; }
.timeline { font-size:12px; line-height:2; }
.timeline .ts { color:#8b95ad; margin-right:8px; }
.timeline .act { font-weight:600; margin-right:6px; }
</style>
</head>
<body>
<h1>🪨 Alphalith Dashboard</h1>
<div class="sub" id="time"></div>

<!-- 实时行情 -->
<div class="grid2" id="quotes"></div>

<!-- 策略信号矩阵 -->
<div class="panel">
  <div class="title">📊 策略信号矩阵（最近动作）</div>
  <div style="overflow-x:auto;">
    <table id="signalTable">
      <thead id="signalHead"></thead>
      <tbody id="signalBody"></tbody>
    </table>
  </div>
</div>

<!-- 回测绩效总览 -->
<div class="panel">
  <div class="title">📈 回测绩效总览（90 日 / 5 日窗口）</div>
  <div style="overflow-x:auto;">
    <table id="perfTable">
      <thead><tr><th>标的</th><th>策略</th><th>笔数</th><th>胜率</th><th>累计收益</th><th>夏普</th><th>Alpha</th></tr></thead>
      <tbody id="perfBody"></tbody>
    </table>
  </div>
</div>

<!-- 策略对比雷达图 -->
<div class="panel">
  <div class="title">🎯 标的 × 策略收益热力图</div>
  <canvas id="heatmap" height="80"></canvas>
</div>

<!-- 最近决策流 -->
<div class="panel" id="decisionsPanel">
  <div class="title">📋 最近决策流</div>
  <div class="timeline" id="decisions"></div>
</div>

<script>
// --- 数据注入 ---
const QUOTES = __QUOTES__;
const SNAPSHOTS = __SNAPSHOTS__;
const DECISIONS = __DECISIONS__;
const SYMBOLS = __SYMBOLS__;

document.getElementById('time').textContent =
  '数据刷新：' + new Date().toLocaleString('zh-CN');

// --- 1. 行情卡片 ---
const qDiv = document.getElementById('quotes');
QUOTES.forEach(q => {
  const cls = q.change_pct >= 0 ? 'up' : 'down';
  const sign = q.change_pct >= 0 ? '+' : '';
  qDiv.insertAdjacentHTML('beforeend',
    '<div class="card">'
    + '<div class="name">' + q.symbol + ' · ' + q.name + '</div>'
    + '<div class="price">¥' + q.price.toFixed(2) + '</div>'
    + '<div class="change ' + cls + '">' + sign + (q.change_pct*100).toFixed(2) + '%</div>'
    + '<div class="volume">成交量 ' + (q.volume/10000).toFixed(0) + ' 万手</div>'
    + '</div>'
  );
});

// --- 2. 策略信号矩阵 ---
const strategies = [...new Set(SNAPSHOTS.map(s => s.strategy))];
const syms = [...new Set(SNAPSHOTS.map(s => s.symbol))];

// Header
let headHTML = '<tr><th>标的</th>';
strategies.forEach(st => { headHTML += '<th>' + st + '</th>'; });
headHTML += '</tr>';
document.getElementById('signalHead').innerHTML = headHTML;

// Body
let bodyHTML = '';
syms.forEach(sym => {
  bodyHTML += '<tr><td><b>' + sym + '</b></td>';
  strategies.forEach(st => {
    const snap = SNAPSHOTS.find(s => s.symbol === sym && s.strategy === st) || {};
    const act = snap.latest_action || 'hold';
    const badge = act === 'buy' ? '<span class="buy-badge">BUY</span>'
                : act === 'sell' ? '<span class="sell-badge">SELL</span>'
                : '<span class="hold-badge">HOLD</span>';
    bodyHTML += '<td>' + badge + '</td>';
  });
  bodyHTML += '</tr>';
});
document.getElementById('signalBody').innerHTML = bodyHTML;

// --- 3. 回测绩效表 ---
let perfHTML = '';
SNAPSHOTS.forEach(s => {
  const pnlCls = s.total_pnl > 0 ? 'pos' : (s.total_pnl < 0 ? 'neg' : '');
  const alphaCls = s.alpha > 0 ? 'pos' : (s.alpha < 0 ? 'neg' : '');
  perfHTML += '<tr>'
    + '<td>' + s.symbol + '</td>'
    + '<td><b>' + s.strategy + '</b></td>'
    + '<td>' + s.actionable + '</td>'
    + '<td>' + (s.win_rate*100).toFixed(0) + '%</td>'
    + '<td class="' + pnlCls + '">' + (s.total_pnl*100).toFixed(2) + '%</td>'
    + '<td>' + s.sharpe.toFixed(2) + '</td>'
    + '<td class="' + alphaCls + '">' + (s.alpha*100).toFixed(2) + '%</td>'
    + '</tr>';
});
document.getElementById('perfBody').innerHTML = perfHTML;

// --- 4. 收益热力图 (Canvas table substitute) ---
const canvas = document.getElementById('heatmap');
const ctx = canvas.getContext('2d');
const dpr = window.devicePixelRatio || 1;
canvas.width = canvas.offsetWidth * dpr;
canvas.height = 120 * dpr;
canvas.style.height = '120px';
ctx.scale(dpr, dpr);

const padL = 74, padT = 24, cellW = 72, cellH = 26, gap = 2;
const maxPnl = Math.max(...SNAPSHOTS.map(s => Math.abs(s.total_pnl*100)), 1);

ctx.font = '11px -apple-system,"PingFang SC",sans-serif';
// Row labels (symbols)
syms.forEach((sym, ri) => {
  ctx.fillStyle = '#e7ecf5';
  ctx.textAlign = 'right';
  ctx.fillText(sym, padL - 8, padT + ri * (cellH + gap) + cellH * 0.65);
});
// Col labels (strategies)
strategies.forEach((st, ci) => {
  ctx.fillStyle = '#8b95ad';
  ctx.textAlign = 'center';
  ctx.fillText(st, padL + ci * (cellW + gap) + cellW/2, padT - 8);
});
// Cells
syms.forEach((sym, ri) => {
  strategies.forEach((st, ci) => {
    const snap = SNAPSHOTS.find(s => s.symbol === sym && s.strategy === st) || {};
    const pnl = (snap.total_pnl || 0) * 100;
    const x = padL + ci * (cellW + gap);
    const y = padT + ri * (cellH + gap);
    // Color: green for profit, red for loss, intensity by magnitude
    let r, g, b;
    if (pnl >= 0) {
      const t = Math.min(pnl / maxPnl, 1);
      r = Math.round(22 - t * 22);
      g = Math.round(55 + t * 100);
      b = Math.round(34 - t * 34);
    } else {
      const t = Math.min(Math.abs(pnl) / maxPnl, 1);
      r = Math.round(220 + t * 35);
      g = Math.round(38 - t * 38);
      b = Math.round(38 - t * 38);
    }
    ctx.fillStyle = 'rgb(' + r + ',' + g + ',' + b + ')';
    ctx.beginPath();
    ctx.roundRect(x, y, cellW, cellH, 3);
    ctx.fill();
    // Text
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'center';
    ctx.font = 'bold 11px -apple-system,"PingFang SC",sans-serif';
    ctx.fillText(pnl.toFixed(1) + '%', x + cellW/2, y + cellH * 0.7);
  });
});

// --- 5. 最近决策流 ---
const dDiv = document.getElementById('decisions');
if (DECISIONS.length === 0) {
  dDiv.innerHTML = '<span style="color:#8b95ad;">暂无决策记录（运行 alphalith analyze 后自动记录）</span>';
} else {
  DECISIONS.forEach(d => {
    const actCls = d.action === 'buy' ? 'pos' : (d.action === 'sell' ? 'neg' : '');
    dDiv.insertAdjacentHTML('beforeend',
      '<div>'
      + '<span class="ts">' + d.ts + '</span>'
      + '<b>' + d.symbol + '</b> '
      + '<span class="act ' + actCls + '">' + d.action.toUpperCase() + '</span>'
      + '<span style="color:#8b95ad;"> conf:' + (d.confidence||0).toFixed(2) + '</span>'
      + '<span style="color:#8b95ad;margin-left:6px;">via ' + (d.llm||'stub') + '</span>'
      + '</div>'
    );
  });
}
</script>
</body>
</html>"""

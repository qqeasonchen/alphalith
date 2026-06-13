"""HTML 回测报告生成 — 价格 + 资金曲线 + 买卖点 + 交易明细。
独立模块避免 backtest.py 中的 f-string/JS 大括号转义噩梦。
零依赖：仅标准库 + Chart.js CDN。
"""
from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .backtest import BacktestResult


_TPL_HEAD = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>Alphalith 回测 · __SYMBOL__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body { font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
         background:#0b1020; color:#e7ecf5; margin:0; padding:24px; }
  h1 { margin:0 0 4px; font-size:22px; }
  .sub { color:#8b95ad; margin-bottom:18px; font-size:13px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));
           gap:12px; margin-bottom:18px; }
  .card { background:#141a30; border:1px solid #1f2742; border-radius:10px;
          padding:12px 14px; }
  .card .k { color:#8b95ad; font-size:12px; }
  .card .v { font-size:18px; font-weight:600; margin-top:4px; }
  .alpha { color:__ALPHA_COLOR__; }
  .panel { background:#0f1428; border:1px solid #1f2742; border-radius:10px;
           padding:16px; margin-bottom:18px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { padding:6px 8px; border-bottom:1px solid #1f2742; text-align:left; }
  th { color:#8b95ad; font-weight:500; }
  .pos { color:#16a34a; }
  .neg { color:#dc2626; }
</style>
</head>
<body>
  <h1>🪨 Alphalith 回测 · <span id="sym"></span></h1>
  <div class="sub" id="meta"></div>
  <div class="cards" id="cards"></div>
  <div class="panel">
    <div style="margin-bottom:8px; color:#8b95ad; font-size:13px;">📈 价格 + 买卖点（绿▲=买入  红▼=卖出）</div>
    <canvas id="priceChart" height="100"></canvas>
  </div>
  <div class="panel">
    <div style="margin-bottom:8px; color:#8b95ad; font-size:13px;">💰 累计收益对比（策略 vs Buy &amp; Hold）</div>
    <canvas id="equityChart" height="100"></canvas>
  </div>
  <div class="panel">
    <div style="margin-bottom:8px; color:#8b95ad; font-size:13px;">📋 交易明细</div>
    <table id="tradeTable">
      <thead><tr><th>日期</th><th>动作</th><th>入场价</th><th>出场日</th><th>出场价</th><th>PnL</th><th>置信</th><th>理由</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
<script>
const D = __DATA_JSON__;
</script>
"""


_TPL_JS = """<script>
document.getElementById('sym').textContent = D.symbol + ' (' + D.market + ')';
document.getElementById('meta').textContent =
  '策略 ' + D.strategy + '   持有窗口 ' + D.horizon + ' 日   K 线 ' + D.dates.length + ' 根';

const m = D.metrics;
const fmtPct = v => (v*100).toFixed(2) + '%';
const fmtNum = v => v.toFixed(2);
const cards = [
  {k:'胜率', v: fmtPct(m.win_rate)},
  {k:'累计收益', v: fmtPct(m.total_pnl)},
  {k:'Buy & Hold', v: fmtPct(m.buy_hold_return)},
  {k:'Alpha vs B&H', v: fmtPct(m.alpha), cls:'alpha'},
  {k:'最大回撤', v: fmtPct(m.max_drawdown)},
  {k:'Calmar', v: fmtNum(m.calmar)},
  {k:'年化夏普', v: fmtNum(m.sharpe)},
  {k:'Sortino', v: fmtNum(m.sortino)},
  {k:'信息比率', v: fmtNum(m.info_ratio)},
  {k:'最长连胜', v: m.max_win_streak + ' 笔'},
  {k:'最长连败', v: m.max_loss_streak + ' 笔'},
  {k:'盈亏比', v: fmtNum(m.win_loss_ratio)},
  {k:'入场笔数', v: m.n_actionable + ' (买'+m.n_buy+'/卖'+m.n_sell+')'},
];
document.getElementById('cards').innerHTML = cards.map(c =>
  '<div class="card"><div class="k">'+c.k+'</div><div class="v '+(c.cls||'')+'">'+c.v+'</div></div>'
).join('');

new Chart(document.getElementById('priceChart'), {
  type: 'line',
  data: {
    labels: D.dates,
    datasets: [
      { label: '收盘价', data: D.closes,
        borderColor: '#60a5fa', backgroundColor: 'rgba(96,165,250,0.1)',
        tension: 0.2, pointRadius: 0, borderWidth: 2 },
      { type: 'scatter', label: '买入',
        data: D.buys.map(p => ({x:p.x, y:p.y})),
        backgroundColor: '#22c55e', borderColor: '#22c55e',
        pointStyle: 'triangle', radius: 8 },
      { type: 'scatter', label: '卖出',
        data: D.sells.map(p => ({x:p.x, y:p.y})),
        backgroundColor: '#ef4444', borderColor: '#ef4444',
        pointStyle: 'triangle', rotation: 180, radius: 8 },
    ]
  },
  options: {
    responsive: true,
    plugins: { legend: { labels: { color:'#e7ecf5' } } },
    scales: {
      x: { ticks: { color:'#8b95ad', maxTicksLimit: 10 }, grid: { color:'#1f2742' } },
      y: { ticks: { color:'#8b95ad' }, grid: { color:'#1f2742' } },
    }
  }
});

new Chart(document.getElementById('equityChart'), {
  type: 'line',
  data: {
    labels: D.dates,
    datasets: [
      { label: '策略累计收益',
        data: D.eq_curve.map(v => v == null ? null : v*100),
        borderColor: '#fbbf24', backgroundColor:'rgba(251,191,36,0.1)',
        tension: 0.2, pointRadius: 0, borderWidth: 2, spanGaps: true },
      { label: 'Buy & Hold',
        data: D.bh_curve.map(v => v*100),
        borderColor: '#a78bfa', backgroundColor:'rgba(167,139,250,0.1)',
        tension: 0.2, pointRadius: 0, borderWidth: 2 },
    ]
  },
  options: {
    responsive: true,
    plugins: {
      legend: { labels: { color:'#e7ecf5' } },
      tooltip: { callbacks: { label: c => c.dataset.label + ': ' + c.parsed.y.toFixed(2) + '%' } }
    },
    scales: {
      x: { ticks: { color:'#8b95ad', maxTicksLimit: 10 }, grid: { color:'#1f2742' } },
      y: { ticks: { color:'#8b95ad', callback: v => v + '%' }, grid: { color:'#1f2742' } },
    }
  }
});

const tbody = document.querySelector('#tradeTable tbody');
const allTrades = D.buys.map(b => Object.assign({}, b, {action:'buy'}))
  .concat(D.sells.map(s => Object.assign({}, s, {action:'sell'})))
  .sort((a,b) => a.x.localeCompare(b.x));
allTrades.forEach(t => {
  const cls = t.pnl >= 0 ? 'pos' : 'neg';
  const row = '<tr>'
    + '<td>'+t.x+'</td>'
    + '<td>'+(t.action==='buy'?'🟢 buy':'🔴 sell')+'</td>'
    + '<td>'+t.y.toFixed(2)+'</td>'
    + '<td>'+(t.exit_date||'-')+'</td>'
    + '<td>'+(t.exit_price!=null?t.exit_price.toFixed(2):'-')+'</td>'
    + '<td class="'+cls+'">'+(t.pnl*100).toFixed(2)+'%</td>'
    + '<td>'+(t.confidence!=null?t.confidence.toFixed(2):'-')+'</td>'
    + '<td>'+(t.reason||'')+'</td>'
    + '</tr>';
  tbody.insertAdjacentHTML('beforeend', row);
});
</script>
</body>
</html>
"""


def render_html(r: "BacktestResult") -> str:
    """生成单文件 HTML 报告：价格 + 资金曲线 + 基准 + 标注买卖点。"""
    payload = _make_payload(r)

    alpha_color = "#16a34a" if r.alpha_vs_bh >= 0 else "#dc2626"
    head = (_TPL_HEAD
            .replace("__SYMBOL__", r.symbol)
            .replace("__ALPHA_COLOR__", alpha_color)
            .replace("__DATA_JSON__", json.dumps(payload, ensure_ascii=False)))
    return head + _TPL_JS


# ---------- 双策略对比 HTML ----------

_CMP_TPL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>Alphalith 策略对比 · __SYMBOL__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body { font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
         background:#0b1020; color:#e7ecf5; margin:0; padding:24px; }
  h1 { margin:0 0 4px; font-size:22px; }
  .sub { color:#8b95ad; margin-bottom:18px; font-size:13px; }
  .winner { display:inline-block; background:#fbbf24; color:#0b1020; padding:2px 10px;
            border-radius:4px; font-weight:700; font-size:13px; margin-left:8px; }
  .verdict { margin:4px 0 16px; font-size:15px; color:#fbbf24; }
  .cmp-table { width:100%; border-collapse:collapse; margin-bottom:20px; font-size:13px; }
  .cmp-table th, .cmp-table td { padding:8px 10px; border-bottom:1px solid #1f2742;
       text-align:center; }
  .cmp-table th:first-child, .cmp-table td:first-child { text-align:left; color:#8b95ad; }
  .cmp-table th { color:#8b95ad; font-weight:500; background:#0f1428; }
  .cmp-table .win { color:#16a34a; font-weight:600; }
  .cmp-table .lose { color:#dc2626; }
  .pos { color:#16a34a; }
  .neg { color:#dc2626; }
  .panel { background:#0f1428; border:1px solid #1f2742; border-radius:10px;
           padding:16px; margin-bottom:18px; }
  .panel .title { color:#8b95ad; font-size:13px; margin-bottom:10px; }
  .legend { display:flex; gap:16px; flex-wrap:wrap; margin-top:8px; font-size:12px; }
  .legend span { display:inline-flex; align-items:center; gap:4px; }
  .legend .dot { width:10px; height:10px; border-radius:2px; display:inline-block; }
</style>
</head>
<body>
  <h1>🪨 策略对比 · <span id="sym"></span></h1>
  <div class="sub" id="meta"></div>
  <div class="verdict" id="verdict"></div>

  <table class="cmp-table">
    <thead><tr>
      <th>指标</th>
      <th id="hA">策略 A</th>
      <th id="hB">策略 B</th>
    </tr></thead>
    <tbody id="cmpBody"></tbody>
  </table>

  <div class="panel">
    <div class="title">📈 价格 + 买卖点
      <span class="legend">
        <span><span class="dot" style="background:#22c55e"></span> A买入</span>
        <span><span class="dot" style="background:#ef4444"></span> A卖出</span>
        <span><span class="dot" style="background:#06b6d4"></span> B买入</span>
        <span><span class="dot" style="background:#f97316"></span> B卖出</span>
      </span>
    </div>
    <canvas id="priceChart" height="100"></canvas>
  </div>

  <div class="panel">
    <div class="title">💰 累计收益对比（策略 A · 策略 B · Buy &amp; Hold）</div>
    <canvas id="equityChart" height="100"></canvas>
  </div>

  <div class="panel">
    <div class="title">📋 决策分歧（A ≠ B）<span style="color:#8b95ad;font-weight:400;margin-left:8px;">仅当两策略给出不同买卖信号</span></div>
    <div id="divergence" style="font-size:13px; color:#8b95ad;">计算中...</div>
  </div>

  <div style="display:grid; grid-template-columns:1fr 1fr; gap:18px;">
    <div class="panel">
      <div class="title">📋 策略 A 交易明细</div>
      <div style="overflow-x:auto;">
        <table id="tradeTableA" style="font-size:12px;">
          <thead><tr><th>日期</th><th>动作</th><th>入场价</th><th>PnL</th><th>置信</th><th>理由</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <div class="title">📋 策略 B 交易明细</div>
      <div style="overflow-x:auto;">
        <table id="tradeTableB" style="font-size:12px;">
          <thead><tr><th>日期</th><th>动作</th><th>入场价</th><th>PnL</th><th>置信</th><th>理由</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="panel" id="llmReviewPanel" style="display:none;">
    <div class="title">🤖 AI 综合评语</div>
    <div id="llmReview" style="font-size:13px; line-height:1.7; white-space:pre-wrap;"></div>
  </div>

<script>
const A = __DATA_A__;
const B = __DATA_B__;
const fmtPct = v => (v*100).toFixed(2)+'%';
const fmtNum = v => v.toFixed(2);
const winner = (a,b,higherBetter) => higherBetter ? (a>=b?'A':'B') : (a<=b?'A':'B');
const cls = (a,b,wb) => {
  const w = winner(a,b,wb);
  return w==='A'&&a!==b ? 'win' : (w==='B'&&a!==b ? 'lose' : '');
};

document.getElementById('sym').textContent = A.symbol + ' (' + A.market + ')';
document.getElementById('meta').textContent =
  '持有窗口 ' + A.horizon + ' 日   K 线 ' + A.dates.length + ' 根';
document.getElementById('hA').innerHTML = '策略 A <b>'+A.strategy+'</b>';
document.getElementById('hB').innerHTML = '策略 B <b>'+B.strategy+'</b>';

const am = A.metrics, bm = B.metrics;
const aAvg = am.total_pnl / Math.max(am.n_actionable, 1);
const bAvg = bm.total_pnl / Math.max(bm.n_actionable, 1);

// [label, valA, valB, higherBetter, fmt]
const rows = [
  ['入场笔数', am.n_actionable, bm.n_actionable, false, v=>v+''],  
  ['胜率', am.win_rate, bm.win_rate, true, fmtPct],
  ['累计收益', am.total_pnl, bm.total_pnl, true, fmtPct],
  ['Alpha vs B&H', am.alpha, bm.alpha, true, fmtPct],
  ['平均单笔', aAvg, bAvg, true, fmtPct],
  ['盈亏比', am.win_loss_ratio, bm.win_loss_ratio, true, fmtNum],
  ['最大回撤', am.max_drawdown, bm.max_drawdown, false, fmtPct],
  ['Calmar', am.calmar, bm.calmar, true, fmtNum],
  ['年化夏普', am.sharpe, bm.sharpe, true, fmtNum],
  ['Sortino', am.sortino, bm.sortino, true, fmtNum],
  ['信息比率', am.info_ratio, bm.info_ratio, true, fmtNum],
  ['最长连胜', am.max_win_streak, bm.max_win_streak, true, v=>v+' 笔'],
  ['最长连败', am.max_loss_streak, bm.max_loss_streak, false, v=>v+' 笔'],
  ['Buy & Hold', am.buy_hold_return, am.buy_hold_return, true, fmtPct],
];
document.getElementById('cmpBody').innerHTML = rows.map(r => {
  const w = r[3] ? (r[1]>=r[2]?'A':'B') : (r[1]<=r[2]?'A':'B');
  const cA = w==='A'&&r[1]!==r[2]?'win':(w==='B'&&r[1]!==r[2]?'lose':'');
  const cB = w==='B'&&r[1]!==r[2]?'win':(w==='A'&&r[1]!==r[2]?'lose':'');
  return '<tr><td>'+r[0]+'</td><td class="'+cA+'">'+r[4](r[1])+'</td><td class="'+cB+'">'+r[4](r[2])+'</td></tr>';
}).join('');

// Verdict: count wins across all comparable rows
let aWins = 0, bWins = 0;
rows.forEach(r => {
  if (r[1] === r[2]) return;
  if (r[3]) { r[1] > r[2] ? aWins++ : bWins++; }
  else      { r[1] < r[2] ? aWins++ : bWins++; }
});
const vEl = document.getElementById('verdict');
if (aWins > bWins) {
  vEl.innerHTML = '🏆 <b>'+A.strategy+'</b> 在 '+aWins+'/'+(aWins+bWins)+' 项指标胜出，综合更优';
} else if (bWins > aWins) {
  vEl.innerHTML = '🏆 <b>'+B.strategy+'</b> 在 '+bWins+'/'+(aWins+bWins)+' 项指标胜出，综合更优';
} else {
  vEl.innerHTML = '🤝 双方打平，各有千秋';
}

// Price chart with both strategies' markers
new Chart(document.getElementById('priceChart'), {
  type: 'line',
  data: {
    labels: A.dates,
    datasets: [
      { label: '收盘价', data: A.closes,
        borderColor:'#60a5fa', backgroundColor:'rgba(96,165,250,0.1)',
        tension:0.2, pointRadius:0, borderWidth:2, order:3 },
      { type:'scatter', label:'A 买入', data:A.buys.map(p=>({x:p.x,y:p.y})),
        backgroundColor:'#22c55e', borderColor:'#22c55e',
        pointStyle:'triangle', radius:7, order:1 },
      { type:'scatter', label:'A 卖出', data:A.sells.map(p=>({x:p.x,y:p.y})),
        backgroundColor:'#ef4444', borderColor:'#ef4444',
        pointStyle:'triangle', rotation:180, radius:7, order:1 },
      { type:'scatter', label:'B 买入', data:B.buys.map(p=>({x:p.x,y:p.y})),
        backgroundColor:'#06b6d4', borderColor:'#06b6d4',
        pointStyle:'rect', radius:6, order:2 },
      { type:'scatter', label:'B 卖出', data:B.sells.map(p=>({x:p.x,y:p.y})),
        backgroundColor:'#f97316', borderColor:'#f97316',
        pointStyle:'rect', radius:6, order:2 },
    ]
  },
  options: {
    responsive:true,
    plugins:{ legend:{ labels:{ color:'#e7ecf5', usePointStyle:true } } },
    scales:{
      x:{ ticks:{ color:'#8b95ad',maxTicksLimit:10 }, grid:{ color:'#1f2742' } },
      y:{ ticks:{ color:'#8b95ad' }, grid:{ color:'#1f2742' } },
    }
  }
});

// Equity curves: A vs B vs B&H
new Chart(document.getElementById('equityChart'), {
  type: 'line',
  data: {
    labels: A.dates,
    datasets: [
      { label: A.strategy,
        data: A.eq_curve.map(v => v==null?null:v*100),
        borderColor:'#fbbf24', backgroundColor:'rgba(251,191,36,0.08)',
        tension:0.2, pointRadius:0, borderWidth:2, spanGaps:true },
      { label: B.strategy,
        data: B.eq_curve.map(v => v==null?null:v*100),
        borderColor:'#06b6d4', backgroundColor:'rgba(6,182,212,0.08)',
        tension:0.2, pointRadius:0, borderWidth:2, spanGaps:true },
      { label: 'Buy & Hold',
        data: A.bh_curve.map(v => v*100),
        borderColor:'#a78bfa', backgroundColor:'rgba(167,139,250,0.08)',
        tension:0.2, pointRadius:0, borderWidth:2, borderDash:[5,5] },
    ]
  },
  options: {
    responsive:true,
    plugins:{
      legend:{ labels:{ color:'#e7ecf5' } },
      tooltip:{ callbacks:{ label: c => c.dataset.label+': '+c.parsed.y.toFixed(2)+'%' } }
    },
    scales:{
      x:{ ticks:{ color:'#8b95ad',maxTicksLimit:10 }, grid:{ color:'#1f2742' } },
      y:{ ticks:{ color:'#8b95ad',callback:v=>v+'%' }, grid:{ color:'#1f2742' } },
    }
  }
});
"""

_CMP_JS_TAIL = """
// --- 决策分歧 ---
const divEl = document.getElementById('divergence');
const aTrades = A.trades || [];
const bTrades = B.trades || [];
const maxLen = Math.max(aTrades.length, bTrades.length);
const divergeRows = [];
for (let i = 0; i < maxLen; i++) {
  const at = aTrades[i];
  const bt = bTrades[i];
  if (!at || !bt) continue;
  if (at.action === bt.action) continue;
  const aLabel = at.action === 'buy' ? '🟢买' : (at.action === 'sell' ? '🔴卖' : '⚪持');
  const bLabel = bt.action === 'buy' ? '🟢买' : (bt.action === 'sell' ? '🔴卖' : '⚪持');
  const aPnl = at.pnl != null ? (at.pnl * 100).toFixed(2) + '%' : '-';
  const bPnl = bt.pnl != null ? (bt.pnl * 100).toFixed(2) + '%' : '-';
  const aCls = at.pnl > 0 ? 'pos' : (at.pnl < 0 ? 'neg' : '');
  const bCls = bt.pnl > 0 ? 'pos' : (bt.pnl < 0 ? 'neg' : '');
  divergeRows.push(
    '<tr><td>' + at.date + '</td>'
    + '<td>' + aLabel + '</td><td class="' + aCls + '">' + aPnl + '</td>'
    + '<td>' + bLabel + '</td><td class="' + bCls + '">' + bPnl + '</td>'
    + '<td style="font-size:11px;color:#8b95ad;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
    + (at.reason || '') + '</td></tr>'
  );
}
if (divergeRows.length === 0) {
  divEl.innerHTML = '<span style="color:#16a34a;">✅ 两策略在所有决策点一致</span>';
} else {
  const tbl = '<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:4px;">'
    + '<thead><tr><th>日期</th><th>A 决策</th><th>A PnL</th><th>B 决策</th><th>B PnL</th><th style="text-align:left;">A 理由</th></tr></thead>'
    + '<tbody>' + divergeRows.join('') + '</tbody></table>';
  divEl.innerHTML = '<span style="color:#fbbf24;">⚠️ 共 ' + divergeRows.length + ' 次分歧</span>' + tbl;
  const tblEl = divEl.querySelector('table');
  if (tblEl) {
    tblEl.querySelectorAll('th,td').forEach(c => {
      c.style.cssText = 'padding:5px 8px;border-bottom:1px solid #1f2742;text-align:center;';
    });
    tblEl.querySelectorAll('th').forEach(c => {
      c.style.cssText += 'color:#8b95ad;font-weight:500;background:#0f1428;';
    });
  }
}

// --- 交易明细表 ---
function fillTradeTable(tbodyId, trades) {
  const tbody = document.getElementById(tbodyId);
  if (!trades || trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="color:#8b95ad;">无交易记录</td></tr>';
    return;
  }
  trades.forEach(t => {
    const actLabel = t.action === 'buy' ? '🟢 buy' : (t.action === 'sell' ? '🔴 sell' : '⚪ hold');
    const pnlCls = t.pnl > 0 ? 'pos' : (t.pnl < 0 ? 'neg' : '');
    const pnlStr = t.action === 'hold' ? '-' : ((t.pnl * 100).toFixed(2) + '%');
    const row = '<tr>'
      + '<td>' + t.date + '</td>'
      + '<td>' + actLabel + '</td>'
      + '<td>' + (t.price != null ? t.price.toFixed(2) : '-') + '</td>'
      + '<td class="' + pnlCls + '">' + pnlStr + '</td>'
      + '<td>' + (t.confidence != null ? t.confidence.toFixed(2) : '-') + '</td>'
      + '<td style="font-size:11px;color:#8b95ad;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + (t.reason || '') + '</td>'
      + '</tr>';
    tbody.insertAdjacentHTML('beforeend', row);
  });
}
fillTradeTable('tradeTableA', aTrades);
fillTradeTable('tradeTableB', bTrades);

// --- LLM 评语 ---
const llmReview = "__LLM_REVIEW__";
if (llmReview) {
  document.getElementById('llmReviewPanel').style.display = 'block';
  document.getElementById('llmReview').textContent = llmReview;
}
</script>
</body>
</html>
"""


def _make_payload(r: "BacktestResult") -> dict:
    """构建单策略 JSON payload（复用 render_html 逻辑）。"""
    eq_map = dict(r.equity_curve)
    aligned_eq: list[Optional[float]] = []
    cur = 0.0
    has_started = False
    for d in r.bar_dates:
        if d in eq_map:
            cur = eq_map[d]
            has_started = True
        aligned_eq.append(cur if has_started else None)

    def _pt(t):
        return {
            "x": t.date, "y": t.price,
            "exit_date": t.exit_date, "exit_price": t.exit_price,
            "confidence": t.confidence, "reason": t.reason, "pnl": t.pnl_pct,
        }

    # 完整交易列表（含 hold），用于对比分歧
    all_trades = []
    for t in r.trades:
        all_trades.append({
            "date": t.date, "action": t.action, "price": t.price,
            "exit_date": t.exit_date, "exit_price": t.exit_price,
            "pnl": t.pnl_pct, "confidence": t.confidence, "reason": t.reason,
        })

    return {
        "symbol": r.symbol,
        "market": r.market,
        "strategy": r.strategy,
        "horizon": r.horizon,
        "dates": r.bar_dates,
        "closes": r.bar_closes,
        "bh_curve": [v for _, v in r.buy_hold_curve],
        "eq_curve": aligned_eq,
        "buys":  [_pt(t) for t in r.actionable if t.action == "buy"],
        "sells": [_pt(t) for t in r.actionable if t.action == "sell"],
        "trades": all_trades,
        "metrics": {
            "win_rate": r.win_rate,
            "total_pnl": r.total_pnl,
            "buy_hold_return": r.buy_hold_return,
            "alpha": r.alpha_vs_bh,
            "max_drawdown": r.max_drawdown,
            "sharpe": r.sharpe,
            "calmar": r.calmar,
            "sortino": r.sortino,
            "info_ratio": r.info_ratio,
            "max_win_streak": r.max_win_streak,
            "max_loss_streak": r.max_loss_streak,
            "win_loss_ratio": r.win_loss_ratio,
            "n_actionable": len(r.actionable),
            "n_buy": r.n_buy,
            "n_sell": r.n_sell,
            "n_hold": r.n_hold,
        },
    }


def render_compare_html(a: "BacktestResult", b: "BacktestResult", llm_review: str = "") -> str:
    """生成双策略对比 HTML 报告。

    Args:
        a: 策略 A 的回测结果
        b: 策略 B 的回测结果
        llm_review: LLM 对两个策略的对比评语（可选）
    """
    tpl = (_CMP_TPL
           .replace("__SYMBOL__", a.symbol)
           .replace("__DATA_A__", json.dumps(_make_payload(a), ensure_ascii=False))
           .replace("__DATA_B__", json.dumps(_make_payload(b), ensure_ascii=False)))
    # 转义 LLM 评语中的引号和换行
    safe_review = llm_review.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    tpl += _CMP_JS_TAIL.replace("__LLM_REVIEW__", safe_review)
    return tpl

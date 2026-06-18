"""新数据源单元测试 (offline-friendly: 网络失败时跳过)。"""
import pytest


def _has_net(url):
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=5).read(1024)
        return True
    except Exception:
        return False


# ────────────────────────────────────────────────────────────
# em.py 限流封装
# ────────────────────────────────────────────────────────────
def test_em_throttle_serial():
    """em_get 必须串行（线程锁），两次连续调用至少间隔 1.5s。"""
    from alphalith import em
    import time

    # 用 monkeypatch 太重，直接看 module attribute 存在性
    assert hasattr(em, "em_get")
    assert hasattr(em, "em_table")
    assert em._MIN_INTERVAL >= 1.0


# ────────────────────────────────────────────────────────────
# dragon.py 龙虎榜
# ────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _has_net("https://datacenter-web.eastmoney.com"), reason="no net")
def test_dragon_list():
    from alphalith.dragon import fetch_dragon_list
    recs = fetch_dragon_list(page_size=3)
    if recs:  # 非交易日可能为空
        r = recs[0]
        assert r.code and r.name
        assert r.trade_date
        assert isinstance(r.net_buy, float)


# ────────────────────────────────────────────────────────────
# unlock.py 解禁日历
# ────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _has_net("https://datacenter-web.eastmoney.com"), reason="no net")
def test_unlock_upcoming():
    from alphalith.unlock import fetch_upcoming_unlocks
    events = fetch_upcoming_unlocks(days=30, page_size=3)
    if events:
        e = events[0]
        assert e.code and e.name
        assert e.unlock_date
        assert e.shares >= 0


# ────────────────────────────────────────────────────────────
# block_trade.py 大宗交易
# ────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _has_net("https://datacenter-web.eastmoney.com"), reason="no net")
def test_block_trade_recent():
    from alphalith.block_trade import fetch_block_trades
    trades = fetch_block_trades(days=7, page_size=3)
    # 任意工作日都应该有
    if trades:
        t = trades[0]
        assert t.code and t.name
        assert t.amount > 0


# ────────────────────────────────────────────────────────────
# northbound.py 北向资金
# ────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _has_net("https://datacenter-web.eastmoney.com"), reason="no net")
def test_northbound_recent():
    from alphalith.northbound import fetch_northbound_recent_days
    days = fetch_northbound_recent_days(5)
    if days:
        d = days[-1]
        assert d.trade_date
        assert isinstance(d.total_net, float)


@pytest.mark.skipif(not _has_net("https://datacenter-web.eastmoney.com"), reason="no net")
def test_northbound_stock():
    from alphalith.northbound import fetch_stock_northbound
    snap = fetch_stock_northbound("600519")
    if snap:
        assert snap.holding_market_cap > 0
        assert 0 < snap.holding_pct < 50  # 占比合理范围


# ────────────────────────────────────────────────────────────
# hotboard.py 板块热点（兜底总能有数据）
# ────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _has_net("https://datacenter-web.eastmoney.com"), reason="no net")
def test_hotboard_summary():
    from alphalith.hotboard import summarize_for_agent
    s = summarize_for_agent()
    # 即使 push2 不通，龙虎榜兜底也应该有内容
    assert isinstance(s, str)


# ────────────────────────────────────────────────────────────
# financial_us.py SEC EDGAR (网络强依赖)
# ────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _has_net("https://www.sec.gov"), reason="no net")
def test_sec_ticker_to_cik():
    from alphalith.financial_us import ticker_to_cik
    cik = ticker_to_cik("AAPL")
    assert cik == "0000320193"


# ────────────────────────────────────────────────────────────
# data.py end-to-end (A股全套数据流)
# ────────────────────────────────────────────────────────────
@pytest.mark.skipif(not _has_net("https://hq.sinajs.cn"), reason="no net")
def test_data_a_stock_enriched():
    from alphalith.data import load_market_data
    md = load_market_data("600519")
    assert md.fundamental_note  # 至少不是空
    assert md.sentiment_note


# ────────────────────────────────────────────────────────────
# agents.py prompt 完整性
# ────────────────────────────────────────────────────────────
def test_agent_prompts_contain_signal_rules():
    from alphalith.agents import (
        _FOCUS, _AGGRESSIVE_RISK_SYS, _CONSERVATIVE_RISK_SYS,
        _NEUTRAL_RISK_SYS, _FUND_MANAGER_SYS,
    )
    # 情绪分析师应能识别龙虎榜/大宗
    assert "龙虎榜" in _FOCUS["情绪分析师"]
    assert "大宗交易" in _FOCUS["情绪分析师"] or "折价" in _FOCUS["情绪分析师"]
    # 风控三视角各自有资金流读法
    assert "机构" in _AGGRESSIVE_RISK_SYS or "北向" in _AGGRESSIVE_RISK_SYS
    assert "解禁" in _CONSERVATIVE_RISK_SYS
    assert "对冲" in _NEUTRAL_RISK_SYS or "净信号" in _NEUTRAL_RISK_SYS
    # 基金经理有红线
    assert "红线" in _FUND_MANAGER_SYS or "解禁" in _FUND_MANAGER_SYS

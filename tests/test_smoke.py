from alphalith.market import Market, detect_market


def test_a_stock():
    m, s = detect_market("600519")
    assert m == Market.A_STOCK and s == "600519.SS"


def test_chinese_name():
    m, s = detect_market("茅台")
    assert m == Market.A_STOCK and s == "600519.SS"


def test_hk():
    m, s = detect_market("00700")
    assert m == Market.HK_STOCK and s == "0700.HK"


def test_us():
    m, s = detect_market("NVDA")
    assert m == Market.US_STOCK and s == "NASDAQ:NVDA"


def test_pipeline_runs():
    from alphalith import analyze
    d = analyze("茅台", depth="quick")
    assert d.adp_version == "1.0"
    assert d.action in ("buy", "sell", "hold")
    assert 0.0 <= d.confidence <= 1.0
    assert len(d.agent_reports) == 4

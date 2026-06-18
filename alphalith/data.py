"""
Unified data provider — 三市场免费行情路由。

优先级：
  A 股 / 港股 → AkShare（免费、稳定、中文友好）
  美股       → yfinance
  全部失败    → 内置 fallback（保证 demo 始终可跑）

注意：网络不通或包未装时全静默降级，extra 里会标记数据来源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .market import Market, detect_market


@dataclass
class Quote:
    symbol: str
    market: Market
    price: float
    prev_close: float
    change_pct: float
    volume: float
    name: str = ""
    source: str = "fallback"  # akshare | yfinance | fallback


@dataclass
class MarketData:
    quote: Quote
    history_summary: str
    news_headlines: list[str]
    sentiment_note: str
    fundamental_note: str
    sources: dict[str, str] = field(default_factory=dict)
    signal_score: Optional[object] = None  # CompositeSignal（延迟导入）


# ---------- A 股 ----------
def _akshare_a(symbol: str) -> Optional[Quote]:
    try:
        import akshare as ak
    except Exception:
        return None
    try:
        # symbol 形如 600519.SS / 000001.SZ → AkShare 用 6 位代码
        code = symbol.split(".")[0]
        df = ak.stock_zh_a_spot_em()
        row = df[df["代码"] == code]
        if row.empty:
            return None
        r = row.iloc[0]
        price = float(r["最新价"])
        prev = float(r["昨收"])
        return Quote(
            symbol=symbol, market=Market.A_STOCK, price=price, prev_close=prev,
            change_pct=float(r["涨跌幅"]),
            volume=float(r["成交量"]),
            name=str(r["名称"]),
            source="akshare",
        )
    except Exception:
        return None


def _sina_a(symbol: str) -> Optional[Quote]:
    """新浪财经实时行情，零依赖（urllib + 标准库）。
    A 股代码：sh600519 / sz000001
    """
    import urllib.request
    code = symbol.split(".")[0]
    suffix = symbol.split(".")[-1].upper() if "." in symbol else ""
    if suffix == "SS":
        sina_code = "sh" + code
    elif suffix == "SZ":
        sina_code = "sz" + code
    else:
        sina_code = ("sh" if code.startswith(("6", "9")) else "sz") + code
    try:
        url = f"https://hq.sinajs.cn/list={sina_code}"
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
        # 形如：var hq_str_sh600519="贵州茅台,1582.00,1583.00,..."
        body = text.split('"', 2)[1] if '"' in text else ""
        parts = body.split(",")
        if len(parts) < 10:
            return None
        name, _open, prev_close, price, *_ = parts
        if not price or float(price) == 0:
            return None
        price_f = float(price)
        prev_f = float(prev_close)
        vol = float(parts[8]) if len(parts) > 8 else 0.0
        change_pct = (price_f - prev_f) / prev_f * 100 if prev_f else 0.0
        return Quote(
            symbol=symbol, market=Market.A_STOCK, price=price_f, prev_close=prev_f,
            change_pct=change_pct, volume=vol, name=name, source="sina",
        )
    except Exception:
        return None


def _akshare_a_news(code: str) -> list[str]:
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=code)
        return [str(t) for t in df["新闻标题"].head(5).tolist()]
    except Exception:
        return []


# ---------- 港股 ----------
def _akshare_hk(symbol: str) -> Optional[Quote]:
    try:
        import akshare as ak
    except Exception:
        return None
    try:
        code = symbol.split(".")[0].zfill(5)  # 0700 → 00700
        df = ak.stock_hk_spot_em()
        row = df[df["代码"] == code]
        if row.empty:
            return None
        r = row.iloc[0]
        price = float(r["最新价"])
        prev = float(r["昨收"])
        return Quote(
            symbol=symbol, market=Market.HK_STOCK, price=price, prev_close=prev,
            change_pct=float(r["涨跌幅"]),
            volume=float(r["成交量"]),
            name=str(r["名称"]),
            source="akshare",
        )
    except Exception:
        return None


def _sina_hk(symbol: str) -> Optional[Quote]:
    """新浪港股，代码 rt_hk00700"""
    import urllib.request
    code = symbol.split(".")[0].zfill(5)
    try:
        url = f"https://hq.sinajs.cn/list=rt_hk{code}"
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
        body = text.split('"', 2)[1] if '"' in text else ""
        parts = body.split(",")
        # 港股字段：英文名,中文名,开,昨收,最高,最低,最新,涨跌额,涨跌幅,...
        if len(parts) < 11:
            return None
        name = parts[1] or parts[0]
        prev_f = float(parts[3])
        price_f = float(parts[6])
        change_pct = float(parts[8]) if parts[8] else 0.0
        vol = float(parts[12]) if len(parts) > 12 and parts[12] else 0.0
        return Quote(
            symbol=symbol, market=Market.HK_STOCK, price=price_f, prev_close=prev_f,
            change_pct=change_pct, volume=vol, name=name, source="sina",
        )
    except Exception:
        return None


# ---------- 美股 ----------
def _yfinance_us(symbol: str) -> Optional[Quote]:
    try:
        import yfinance as yf
    except Exception:
        return None
    try:
        ticker_str = symbol.split(":")[-1]
        t = yf.Ticker(ticker_str)
        hist = t.history(period="2d")
        if hist.empty:
            return None
        last = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) >= 2 else last
        price = float(last["Close"])
        prev_close = float(prev["Close"])
        change = (price - prev_close) / prev_close * 100 if prev_close else 0.0
        return Quote(
            symbol=symbol, market=Market.US_STOCK, price=price, prev_close=prev_close,
            change_pct=change,
            volume=float(last.get("Volume", 0)),
            name=ticker_str,
            source="yfinance",
        )
    except Exception:
        return None


def _sina_us(symbol: str) -> Optional[Quote]:
    """新浪美股 gb_<ticker小写>"""
    import urllib.request
    ticker = symbol.split(":")[-1].lower()
    try:
        url = f"https://hq.sinajs.cn/list=gb_{ticker}"
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
        body = text.split('"', 2)[1] if '"' in text else ""
        parts = body.split(",")
        # 美股字段：名称,最新价,涨跌幅,...,昨收,...
        if len(parts) < 27:
            return None
        name = parts[0]
        price_f = float(parts[1])
        change_pct = float(parts[2]) if parts[2] else 0.0
        prev_f = float(parts[26]) if parts[26] else price_f / (1 + change_pct/100 or 1)
        vol = float(parts[10]) if len(parts) > 10 and parts[10] else 0.0
        return Quote(
            symbol=symbol, market=Market.US_STOCK, price=price_f, prev_close=prev_f,
            change_pct=change_pct, volume=vol, name=name or ticker.upper(), source="sina",
        )
    except Exception:
        return None


# ---------- Fallback ----------
def _fallback_quote(symbol: str, market: Market) -> Quote:
    base = {Market.A_STOCK: 1587.0, Market.HK_STOCK: 385.0, Market.US_STOCK: 145.0}[market]
    return Quote(
        symbol=symbol, market=market, price=base, prev_close=base * 0.985,
        change_pct=1.5, volume=1_200_000, name=symbol, source="fallback",
    )


# ---------- 路由 ----------
def _fetch_quote(symbol: str, market: Market) -> Quote:
    if market == Market.A_STOCK:
        q = _akshare_a(symbol) or _sina_a(symbol)
    elif market == Market.HK_STOCK:
        q = _akshare_hk(symbol) or _sina_hk(symbol)
    else:  # US
        q = _yfinance_us(symbol) or _sina_us(symbol)
    return q or _fallback_quote(symbol, market)


def _make_history_summary(q: Quote) -> str:
    if q.source == "fallback":
        return (
            f"{q.symbol} 近 20 日均线上行，5/10 日金叉，"
            f"RSI(14)≈58 中性偏多，成交量放大 18%。（占位数据）"
        )
    direction = "上行" if q.change_pct > 0 else ("下行" if q.change_pct < 0 else "持平")
    return (
        f"{q.name}({q.symbol}) 实时价 {q.price:.2f}，较昨收 {q.prev_close:.2f} "
        f"{direction} {q.change_pct:+.2f}%，成交量 {q.volume:,.0f}。"
    )


# ---------- 新闻：东财 HTTP（沙盒可用，无 numpy 依赖） ----------
def _eastmoney_news(code: str, market: Market) -> list[str]:
    """东方财富搜索接口拉个股新闻标题。"""
    import json as _json
    import urllib.request
    import urllib.parse
    # secid: A 股 1.600519 / 0.000001；港股 116.00700；美股 105.NVDA(NASDAQ)/106(NYSE)
    if market == Market.A_STOCK:
        secid = ("1." if code.startswith(("6", "9")) else "0.") + code
    elif market == Market.HK_STOCK:
        secid = "116." + code.zfill(5)
    else:
        secid = "105." + code  # NASDAQ 默认；NYSE 失败时 fallback 占位
    try:
        kw = urllib.parse.quote(code)
        url = (
            f"https://search-api-web.eastmoney.com/search/jsonp"
            f"?cb=cb&param=%7B%22uid%22%3A%22%22%2C"
            f"%22keyword%22%3A%22{kw}%22%2C"
            f"%22type%22%3A%5B%22cmsArticleWebOld%22%5D%2C"
            f"%22client%22%3A%22web%22%2C%22clientVersion%22%3A%22curr%22%2C"
            f"%22param%22%3A%7B%22cmsArticleWebOld%22%3A%7B%22searchScope%22%3A%22default%22%2C"
            f"%22sort%22%3A%22default%22%2C%22pageIndex%22%3A1%2C%22pageSize%22%3A5%7D%7D%7D"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        # 形如 cb({...json...})
        l, r = text.find("("), text.rfind(")")
        if l < 0 or r < 0:
            return []
        data = _json.loads(text[l + 1 : r])
        items = (
            data.get("result", {})
            .get("cmsArticleWebOld", [])
        )
        out = []
        for it in items[:5]:
            t = it.get("title", "")
            # 去掉 <em> 高亮标签
            for tag in ("<em>", "</em>"):
                t = t.replace(tag, "")
            if t:
                out.append(t)
        return out
    except Exception:
        return []


# ---------- 基本面：新浪 PE/PB/总市值 ----------
def _qt_parts(url: str, marker: str) -> list[str]:
    """腾讯财经 qt.gtimg.cn 通用解析。
    text 形如：v_sh600519="1~贵州茅台~600519~..."; 找 marker 之后那段。
    """
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://stockapp.finance.qq.com"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
        if marker not in text:
            return []
        sec = text.split(marker, 1)[1]
        body = sec.split('"', 2)[1] if '"' in sec else ""
        return body.split("~")
    except Exception:
        return []


def _sina_a_fundamental(code: str) -> str:
    """A 股基本面：腾讯财经 qt.gtimg.cn。
    索引：39=ROE%, 44=流通市值(亿), 45=总市值(亿), 46=PB, 52=PE(TTM)
    """
    suffix = "sh" if code.startswith(("6", "9")) else "sz"
    sina_code = suffix + code
    parts = _qt_parts(f"https://qt.gtimg.cn/q={sina_code}", marker=f"v_{sina_code}")
    if len(parts) < 53:
        return ""
    name = parts[1]
    out = [f"{name}({code}) 基本面"]
    pe = parts[52]
    pb = parts[46]
    roe = parts[39]
    mcap = parts[45]
    flow = parts[44]
    if pe and pe not in ("0.00", ""): out.append(f"PE(TTM) {pe}")
    if pb and pb not in ("0.00", ""): out.append(f"PB {pb}")
    if roe and roe not in ("0.00", ""): out.append(f"ROE {roe}%")
    if mcap: out.append(f"总市值 ¥{mcap}亿")
    if flow: out.append(f"流通市值 ¥{flow}亿")
    return "；".join(out) if len(out) > 1 else ""


def _eastmoney_hk_fundamental(code: str) -> str:
    """港股基本面：腾讯 r_hk00700。"""
    code5 = code.zfill(5)
    parts = _qt_parts(f"https://qt.gtimg.cn/q=r_hk{code5}", marker=f"v_r_hk{code5}")
    if len(parts) < 50:
        parts = _qt_parts(f"https://qt.gtimg.cn/q=hk{code5}", marker=f"v_hk{code5}")
    if len(parts) < 40:
        return ""
    name = parts[1]
    out = [f"{name}({code}) 基本面"]
    pe = parts[39] if len(parts) > 39 else ""
    mcap = parts[44] if len(parts) > 44 else ""
    if pe and pe not in ("0.00", ""):
        try:
            float(pe)
            out.append(f"PE {pe}")
        except ValueError:
            pass
    if mcap:
        try:
            float(mcap)
            out.append(f"总市值 HK${mcap}亿")
        except ValueError:
            pass
    return "；".join(out) if len(out) > 1 else ""


def _eastmoney_us_fundamental(ticker: str) -> str:
    """美股基本面：腾讯 us<ticker>.OQ / .N / 不带后缀。"""
    for code in (f"us{ticker}.OQ", f"us{ticker}.N", f"us{ticker}"):
        parts = _qt_parts(f"https://qt.gtimg.cn/q={code}", marker=f"v_{code}")
        if len(parts) < 40:
            continue
        name = parts[1]
        out = [f"{name}({ticker}) 基本面"]
        pe = parts[39] if len(parts) > 39 else ""
        mcap = parts[45] if len(parts) > 45 else ""
        if pe and pe not in ("0.00", ""):
            try:
                float(pe); out.append(f"PE {pe}")
            except ValueError:
                pass
        if mcap:
            try:
                float(mcap); out.append(f"总市值 ${mcap}亿")
            except ValueError:
                pass
        if len(out) > 1:
            return "；".join(out)
    return ""


def load_market_data(symbol_input: str) -> MarketData:
    market, normalized = detect_market(symbol_input)
    quote = _fetch_quote(normalized, market)

    code = normalized.split(".")[0].split(":")[-1]
    ticker = code  # for Reddit/雪球

    # ---------- 新闻 ----------
    news: list[str] = []
    if quote.source != "fallback":
        news = _eastmoney_news(code, market)

    # ---------- 雪球情绪（A/港股） ----------
    xq_headlines: list[str] = []
    xq_sentiment = "无雪球数据"
    if quote.source != "fallback" and market in (Market.A_STOCK, Market.HK_STOCK):
        try:
            from . import xueqiu as _xq
            xq_headlines = _xq.top_headlines(normalized, limit=5)
            xq = _xq.search_sentiment(normalized, name=quote.name)
            xq_sentiment = xq.get("sentiment", "无雪球数据")
        except Exception:
            pass
    # 雪球头条追加到新闻
    for h in xq_headlines:
        if h not in news:
            news.append(h)

    # ---------- Reddit 情绪（美股） ----------
    reddit_sentiment = "无 Reddit 数据"
    reddit_headlines: list[str] = []
    if quote.source != "fallback" and market == Market.US_STOCK:
        try:
            from . import reddit as _rd
            posts = _rd.search_ticker(ticker, limit=10)
            rd = _rd.sentiment_score(posts)
            reddit_sentiment = rd.get("sentiment", "无 Reddit 数据")
            for p in posts[:5]:
                h = f"[r/{p['subreddit']}] {p['title']}"
                reddit_headlines.append(h)
                if h not in news:
                    news.append(h)
        except Exception:
            pass

    if not news:
        news = [
            f"{quote.name or normalized} 暂无实时新闻流，已降级",
        ]

    # ---------- 基本面（优先 financial.py） ----------
    fundamental = ""
    if quote.source != "fallback":
        try:
            from . import financial as _fin
            f = _fin.load_financials(normalized)
            if f.name:
                fundamental = f.note
                # 美股 SEC 增强：把 503 GAAP 关键数加进基本面摘要
                rm = f.raw_metrics or {}
                if rm.get("sec_revenue_ttm"):
                    sec_extra = (
                        f" | SEC: 营收TTM ${rm['sec_revenue_ttm']/1e9:.1f}B"
                        f" 净利TTM ${rm['sec_net_income_ttm']/1e9:.1f}B"
                        f" R&D ${rm['sec_rd_ttm']/1e9:.1f}B"
                        f" OCF ${rm['sec_ocf_ttm']/1e9:.1f}B"
                    )
                    fundamental += sec_extra
        except Exception:
            fundamental = ""

    # ---------- A 股专属增强：龙虎榜 + 解禁 + 大宗交易 + 北向 ----------
    a_share_signals: list[str] = []  # 同时给情绪分析师用
    if market == Market.A_STOCK and quote.source != "fallback":
        try:
            from . import dragon as _dragon
            from . import unlock as _unlock
            from . import block_trade as _bt
            from . import northbound as _nb

            extras: list[str] = []
            try:
                rec = _dragon.fetch_dragon_with_seats(code)
                if rec:
                    extras.append(_dragon.summarize_for_agent(rec))
            except Exception:
                pass
            try:
                events = _unlock.fetch_stock_unlocks(code, future_days=180, history_days=0)
                line = _unlock.summarize_for_agent(events, code)
                if line and "未来无解禁" not in line:
                    extras.append(line)
            except Exception:
                pass
            try:
                trades = _bt.fetch_block_trades(code=code, days=30, page_size=20)
                if trades:
                    extras.append(_bt.summarize_for_agent(trades, code))
            except Exception:
                pass
            # 北向：全市场趋势 + 个股持股
            try:
                mk = _nb.summarize_market_for_agent()
                if mk:
                    extras.append(mk)
                stk = _nb.summarize_stock_for_agent(code)
                if stk:
                    extras.append(stk)
            except Exception:
                pass
            # 板块/概念热点（push2 → 龙虎榜兜底）
            try:
                from . import hotboard as _hb
                hot = _hb.summarize_for_agent()
                if hot:
                    extras.append(hot)
            except Exception:
                pass
            if extras:
                fundamental = (fundamental + "\n\n" + "\n".join(extras)).strip()
                a_share_signals = extras
        except Exception:
            pass

    # Fallback 到旧逻辑
    if not fundamental:
        if quote.source == "fallback":
            fundamental = "估值近三年中位数，ROE 稳健，现金流良好（占位）"
        elif market == Market.A_STOCK:
            fundamental = _sina_a_fundamental(code) or "基本面数据未获取"
        elif market == Market.HK_STOCK:
            fundamental = _eastmoney_hk_fundamental(code) or "基本面数据未获取"
        else:
            fundamental = _eastmoney_us_fundamental(code) or "基本面数据未获取"

    # ---------- 情绪整合 ----------
    parts = []
    if quote.source != "fallback":
        parts.append(f"实时涨跌 {quote.change_pct:+.2f}%")
    if xq_sentiment and xq_sentiment != "无雪球数据":
        parts.append(xq_sentiment)
    if reddit_sentiment and reddit_sentiment != "无 Reddit 数据":
        parts.append(reddit_sentiment)
    if not parts:
        parts.append("社交讨论偏正面（占位）")
    sentiment_note = "；".join(parts)
    if a_share_signals:
        sentiment_note += "\n\n[资金流信号] " + " | ".join(a_share_signals)

    # ---------- 数据源标记 ----------
    sources = {
        "quote": quote.source,
        "news": "eastmoney" if news and "降级" not in news[0] else "fallback",
    }
    if xq_headlines:
        sources["xueqiu"] = f"{len(xq_headlines)} 帖"
    if reddit_headlines:
        sources["reddit"] = f"{len(reddit_headlines)} 帖"

    # ---------- 信号评分（A 股专属）----------
    signal_score = None
    if market == Market.A_STOCK and quote.source != "fallback":
        try:
            from .signal_score import calculate_signal_score

            # 收集信号数据
            dragon_rec = None
            block_trades = []
            nb_summary = ""
            nb_holding_pct = 0.0
            unlock_events = []
            hot_themes = []

            # 龙虎榜
            try:
                from . import dragon as _dragon

                rec = _dragon.fetch_dragon_with_seats(code)
                if rec:
                    dragon_rec = rec
            except Exception:
                pass

            # 大宗交易
            try:
                from . import block_trade as _bt

                block_trades = _bt.fetch_block_trades(
                    code=code, days=30, page_size=20
                )
            except Exception:
                pass

            # 北向资金
            try:
                from . import northbound as _nb

                nb_summary = _nb.summarize_market_for_agent()
                stk_nb = _nb.fetch_stock_northbound(code, days=5)
                if stk_nb:
                    nb_holding_pct = stk_nb[0].holding_ratio or 0.0
            except Exception:
                pass

            # 解禁日历
            try:
                from . import unlock as _unlock

                unlock_events = _unlock.fetch_stock_unlocks(
                    code, future_days=30, history_days=0
                )
            except Exception:
                pass

            # 板块热点
            try:
                from . import hotboard as _hb

                hot_themes = _hb.fetch_hot_themes_from_dragon(50)
            except Exception:
                pass

            # 计算评分
            signal_score = calculate_signal_score(
                symbol=code,
                dragon_rec=dragon_rec,
                block_trades=block_trades,
                nb_summary=nb_summary,
                nb_holding_pct=nb_holding_pct,
                unlock_events=unlock_events,
                hot_themes=hot_themes,
            )
        except Exception:
            pass

    return MarketData(
        quote=quote,
        history_summary=_make_history_summary(quote),
        news_headlines=news,
        sentiment_note=sentiment_note,
        fundamental_note=fundamental,
        sources=sources,
        signal_score=signal_score,
    )


# ---------- 历史 K 线（回测用） ----------
@dataclass
class Bar:
    date: str       # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float


def _qt_kline_code(symbol: str, market: Market) -> str:
    """生成腾讯 K 线接口需要的 code，例如 sh600519 / hk00700 / usNVDA.OQ。"""
    code = symbol.split(".")[0].split(":")[-1]
    if market == Market.A_STOCK:
        return ("sh" if code.startswith(("6", "9")) else "sz") + code
    if market == Market.HK_STOCK:
        return "hk" + code.zfill(5)
    return f"us{code}.OQ"


def load_history(symbol_input: str, days: int = 60) -> list[Bar]:
    """拉日线历史 K 线（腾讯 ifzq.gtimg.cn，公开免鉴权 JSON）。
    沙盒可用——纯 urllib + json，没有 numpy/pandas 依赖。
    """
    import json as _json
    import urllib.request

    market, normalized = detect_market(symbol_input)
    qcode = _qt_kline_code(normalized, market)

    # 腾讯 K 线 API：/appstock/app/fqkline/get?param=sh600519,day,,,60,qfq
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={qcode},day,,,{days},qfq"
    )
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://gu.qq.com"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        data = _json.loads(text)
        node = data.get("data", {}).get(qcode, {})
        # 优先 qfqday（前复权），缺失退回 day
        rows = node.get("qfqday") or node.get("day") or []
        bars: list[Bar] = []
        for r in rows:
            # r 形如 ["2024-01-02","1700.00","1720.00","1690.00","1715.00","1234567",{...}]
            if len(r) < 6:
                continue
            try:
                bars.append(Bar(
                    date=r[0],
                    open=float(r[1]),
                    close=float(r[2]),
                    high=float(r[3]),
                    low=float(r[4]),
                    volume=float(r[5]),
                ))
            except (ValueError, TypeError):
                continue
        return bars
    except Exception:
        return []

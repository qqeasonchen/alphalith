"""
舆情情绪分析 — 多渠道抓取 + LLM 情绪打分。

数据源（6个，按覆盖范围分类）：
  全球（中/英）:
    1. Google News RSS — 100+条，支持中英文关键词搜索
    2. Yahoo Finance RSS — 20+条，按ticker精准匹配
  美股:
    3. Finviz — 90+条，聚合 Bloomberg/Reuters/WSJ 等
    4. StockTwits — 社交媒体实盘讨论，5条
  A股:
    5. 东财公告 API — 公司公告/新闻，8条
    6. 新浪行情 — 实时价格变化上下文

流程：
  1. 根据配置从启用的数据源抓取内容
  2. 去重 + 截断
  3. 调用 LLM 分析情绪：bullish / bearish / neutral + 评分 0-100
  4. 生成舆情摘要
  5. 兜底：关键词情绪分析

零外部依赖，仅用 urllib + LLM。
"""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.parse
import html as _html
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SentimentItem:
    title: str
    sentiment: str = "neutral"  # bullish / bearish / neutral
    score: float = 50.0          # 0-100, >67 bullish, <33 bearish
    source: str = ""             # googlenews / yahoo / finviz / stocktwits / eastmoney / sina


@dataclass
class SentimentReport:
    symbol: str
    name: str = ""
    overall_sentiment: str = "neutral"
    overall_score: float = 50.0
    confidence: float = 0.5
    headlines: list[SentimentItem] = field(default_factory=list)
    summary: str = ""
    data_source: str = ""
    source_stats: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# 数据源元数据
# ═══════════════════════════════════════════════════════════════

SOURCE_META = {
    "googlenews": {
        "name": "Google News",
        "icon": "🌐",
        "color": "#4285F4",
        "desc": "全球新闻聚合，中英文关键词搜索，100+条",
        "coverage": "全球 (中/英)",
    },
    "yahoo": {
        "name": "Yahoo Finance",
        "icon": "📊",
        "color": "#6001D2",
        "desc": "雅虎财经 RSS，按ticker精准匹配，20+条",
        "coverage": "全球",
    },
    "finviz": {
        "name": "Finviz",
        "icon": "📈",
        "color": "#e74c3c",
        "desc": "美股聚合 Bloomberg/Reuters/WSJ 等，90+条",
        "coverage": "美股",
    },
    "stocktwits": {
        "name": "StockTwits",
        "icon": "💬",
        "color": "#1da1f2",
        "desc": "社交媒体实盘讨论，带情绪标签，5条",
        "coverage": "美股",
    },
    "eastmoney": {
        "name": "东方财富",
        "icon": "📰",
        "color": "#e4393c",
        "desc": "公司公告/新闻，A股专精，8条",
        "coverage": "A股",
    },
    "sina": {
        "name": "新浪行情",
        "icon": "📡",
        "color": "#f39c12",
        "desc": "实时价格变化上下文",
        "coverage": "A/HK/US",
    },
    "xueqiu": {
        "name": "雪球",
        "icon": "❄️",
        "color": "#4A90D9",
        "desc": "投资者社区热点讨论与个股分析，20+条",
        "coverage": "A/HK/US",
    },
    "reddit": {
        "name": "Reddit",
        "icon": "🤖",
        "color": "#FF4500",
        "desc": "r/wallstreetbets, r/stocks 等美股社区讨论",
        "coverage": "美股",
    },
}

# 默认全启用
DEFAULT_ENABLED_SOURCES = ["googlenews", "yahoo", "finviz", "stocktwits", "eastmoney", "sina", "xueqiu", "reddit"]


# ═══════════════════════════════════════════════════════════════
# 数据源 1: Google News RSS
# ═══════════════════════════════════════════════════════════════

def _fetch_googlenews(symbol: str, name: str = "", limit: int = 10) -> list[tuple[str, str]]:
    """Google News RSS — 支持中英文搜索。"""
    items: list[tuple[str, str]] = []
    try:
        # 使用标的代码或名称搜索
        query = name if name else symbol
        # 中文市场优先用中文搜索
        is_cn = symbol.isdigit() and len(symbol) == 6
        if is_cn:
            lang = "zh-CN"
            gl = "CN"
            ceid = "CN:zh-Hans"
        else:
            lang = "en-US"
            gl = "US"
            ceid = "US:en"
        q = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={q}+stock&hl={lang}&gl={gl}&ceid={ceid}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        titles = re.findall(r"<item>.*?<title>(.*?)</title>", body, re.DOTALL)
        for t in titles[:limit]:
            clean = re.sub(r"<[^>]+>", "", t).strip()
            clean = _html.unescape(clean)
            # 过滤无关标题
            if len(clean) > 10 and "Google News" not in clean and " - " in clean:
                items.append((clean, "googlenews"))
    except Exception:
        pass
    return items


# ═══════════════════════════════════════════════════════════════
# 数据源 2: Yahoo Finance RSS
# ═══════════════════════════════════════════════════════════════

def _resolve_yahoo_symbol(symbol: str) -> str:
    """将标的代码转换为 Yahoo Finance 格式。"""
    symbol = symbol.upper().strip()
    if symbol.isdigit() and len(symbol) == 6:
        if symbol.startswith("6"):
            return f"{symbol}.SS"  # 上海
        elif symbol.startswith(("0", "3")):
            return f"{symbol}.SZ"  # 深圳
        elif symbol.startswith(("4", "8")):
            return f"{symbol}.BJ"  # 北京
    if ".HK" in symbol:
        code = symbol.replace(".HK", "").zfill(4)
        return f"{code}.HK"
    return symbol


def _fetch_yahoo_rss(symbol: str, limit: int = 10) -> list[tuple[str, str]]:
    """Yahoo Finance RSS。"""
    items: list[tuple[str, str]] = []
    try:
        ysym = _resolve_yahoo_symbol(symbol)
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ysym}&region=US&lang=en-US"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        titles = re.findall(r"<title>(.*?)</title>", body)
        for t in titles[1:limit+1]:  # 跳过 channel title
            clean = _html.unescape(t.strip())
            if len(clean) > 10 and "Yahoo Finance" not in clean:
                items.append((clean, "yahoo"))
    except Exception:
        pass
    return items


# ═══════════════════════════════════════════════════════════════
# 数据源 3: Finviz
# ═══════════════════════════════════════════════════════════════

def _fetch_finviz(symbol: str, limit: int = 12) -> list[tuple[str, str]]:
    """Finviz 新闻聚合 — 仅美股。"""
    items: list[tuple[str, str]] = []
    symbol = symbol.upper().strip()
    # Finviz 仅支持美股代码（纯字母）
    if not symbol.isalpha():
        return items
    try:
        url = f"https://finviz.com/quote.ashx?t={symbol}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # 提取 news 表格中的标题
        rows = re.findall(
            r'<tr[^>]*class="cursor-pointer[^"]*"[^>]*>.*?</tr>',
            html, re.DOTALL
        )
        for row in rows[:limit]:
            texts = re.findall(r'>([^<]{10,150})<', row)
            for t in reversed(texts):
                t = _html.unescape(t.strip())
                if len(t) > 15 and not t.startswith(("http", "//", "{")):
                    items.append((t, "finviz"))
                    break
    except Exception:
        pass
    return items


# ═══════════════════════════════════════════════════════════════
# 数据源 4: StockTwits
# ═══════════════════════════════════════════════════════════════

def _fetch_stocktwits(symbol: str, limit: int = 5) -> list[tuple[str, str]]:
    """StockTwits 社交媒体 — 仅美股。"""
    items: list[tuple[str, str]] = []
    symbol = symbol.upper().strip()
    if not symbol.isalpha():
        return items
    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json?limit={limit}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        for msg in data.get("messages", []):
            body = msg.get("body", "").strip()
            if body and len(body) > 10:
                # 清理 $TICKER 标记
                body = re.sub(r'\$\w+', '', body).strip()
                if len(body) > 10:
                    items.append((body[:120], "stocktwits"))
    except Exception:
        pass
    return items


# ═══════════════════════════════════════════════════════════════
# 数据源 5: 东财公告 API
# ═══════════════════════════════════════════════════════════════

def _parse_market_info(symbol: str) -> tuple[str, str, str]:
    """解析标的代码为 (ann_type, stock_list, market_code)。"""
    symbol = symbol.upper().strip()
    if symbol.isdigit() and len(symbol) == 6:
        if symbol.startswith("6"):
            return ("SHA", symbol, "1")
        elif symbol.startswith(("0", "3")):
            return ("SZA", symbol, "0")
        elif symbol.startswith(("4", "8")):
            return ("BJA", symbol, "0")
    if ".HK" in symbol:
        code = symbol.replace(".HK", "")
        return ("HK", code, "116")
    return ("US", symbol, "105")


def _fetch_eastmoney_announcements(symbol: str, limit: int = 8) -> list[tuple[str, str]]:
    """东财公司公告/新闻。"""
    items: list[tuple[str, str]] = []
    try:
        ann_type, code, _ = _parse_market_info(symbol)
        params = urllib.parse.urlencode({
            "page_size": limit,
            "page_index": 1,
            "ann_type": ann_type,
            "stock_list": code,
            "sr": -1,
        })
        url = f"https://np-anotice-stock.eastmoney.com/api/security/ann?{params}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        ann_list = data.get("data", {}).get("list", [])
        for ann in ann_list[:limit]:
            title = ann.get("title", "").strip()
            title = re.sub(r'\s*\([^)]*:\s*[A-Z]+\)\s*$', '', title)
            if title and len(title) > 4:
                items.append((title, "eastmoney"))
    except Exception:
        pass
    return items


# ═══════════════════════════════════════════════════════════════
# 数据源 6: 新浪行情上下文
# ═══════════════════════════════════════════════════════════════

def _fetch_quote_context(symbol: str) -> list[tuple[str, str]]:
    """从实时行情生成上下文信息。"""
    items: list[tuple[str, str]] = []
    try:
        from .data import load_market_data
        md = load_market_data(symbol)
        q = md.quote
        name = q.name or symbol
        direction = "上涨" if q.change_pct > 0 else "下跌" if q.change_pct < 0 else "持平"
        items.append((
            f"{name} 最新价 {q.price}，{direction} {q.change_pct:+.2f}%，"
            f"今开 {q.open} 最高 {q.high} 最低 {q.low}",
            "sina"
        ))
    except Exception:
        pass
    return items


# ═══════════════════════════════════════════════════════════════
# 自定义数据源抓取
# ═══════════════════════════════════════════════════════════════

def _fetch_custom_source(
    source_id: str, source_cfg: dict, symbol: str, name: str = "", limit: int = 10
) -> list[tuple[str, str]]:
    """抓取自定义数据源内容。

    source_cfg 字段:
        name: 显示名称
        url: 抓取 URL（支持 {symbol} {name} {name_cn} 占位符）
        type: rss | json_api | html_scrape
        item_path: json 路径或正则（type=json_api/html_scrape 时使用）
        field: 从每个 item 取哪个字段（默认 title）
        method: GET | POST
        headers: 额外 HTTP 头 (dict)
    """
    items: list[tuple[str, str]] = []
    try:
        src_type = source_cfg.get("type", "rss")
        url_tmpl = source_cfg.get("url", "")
        if not url_tmpl:
            return items

        # 占位符替换
        url = url_tmpl.replace("{symbol}", symbol)
        url = url.replace("{name}", urllib.parse.quote(name or symbol))
        url = url.replace("{name_cn}", urllib.parse.quote(name or symbol))

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        extra_headers = source_cfg.get("headers", {})
        if isinstance(extra_headers, dict):
            headers.update(extra_headers)

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")

        if src_type == "rss":
            # RSS/XML 格式
            titles = re.findall(r"<item>.*?<title>(.*?)</title>", body, re.DOTALL)
            if not titles:
                titles = re.findall(r"<entry>.*?<title>(.*?)</title>", body, re.DOTALL)
            for t in titles[:limit]:
                clean = re.sub(r"<[^>]+>", "", t).strip()
                clean = _html.unescape(clean)
                if len(clean) > 8:
                    items.append((clean, source_id))

        elif src_type == "json_api":
            # JSON API — 用 item_path 提取
            data = json.loads(body)
            item_path = source_cfg.get("item_path", "").strip()
            field = source_cfg.get("field", "title").strip()
            # 按 . 拆分段路径遍历 JSON
            segments = [s for s in item_path.split(".") if s] if item_path else []
            cursor = data
            for seg in segments:
                if isinstance(cursor, dict):
                    cursor = cursor.get(seg, {})
                elif isinstance(cursor, list) and seg.isdigit():
                    cursor = cursor[int(seg)]
                else:
                    cursor = {}
            # 若 cursor 不是 list，尝试外层 list
            entries = cursor if isinstance(cursor, list) else [cursor]
            for entry in entries[:limit]:
                val = entry.get(field, "") if isinstance(entry, dict) else str(entry)
                val = str(val).strip()
                if len(val) > 4:
                    items.append((val[:200], source_id))

        elif src_type == "html_scrape":
            # HTML 抓取 — 用正则
            pattern = source_cfg.get("item_path", r"<title>(.*?)</title>")
            matches = re.findall(pattern, body, re.DOTALL)
            for m in matches[:limit]:
                clean = re.sub(r"<[^>]+>", "", m).strip()
                clean = _html.unescape(clean)
                if len(clean) > 8:
                    items.append((clean, source_id))

    except Exception:
        pass

    return items


# ═══════════════════════════════════════════════════════════════
# 统一抓取
# ═══════════════════════════════════════════════════════════════

# 源 → 函数映射
_SOURCE_FETCHERS = {
    "googlenews": _fetch_googlenews,
    "yahoo": _fetch_yahoo_rss,
    "finviz": _fetch_finviz,
    "stocktwits": _fetch_stocktwits,
    "eastmoney": _fetch_eastmoney_announcements,
    "sina": _fetch_quote_context,
}


def _load_custom_sources() -> dict:
    """加载配置中的自定义舆情数据源。"""
    try:
        from ..gui import load_config
        cfg = load_config()
        return cfg.get("custom_sentiment_sources", {})
    except Exception:
        return {}


def fetch_all_sources(
    symbol: str,
    name: str = "",
    enabled_sources: list[str] | None = None,
    limit: int = 20,
) -> tuple[list[tuple[str, str]], dict]:
    """从所有可用数据源抓取。返回 (去重列表, 来源统计)。

    Args:
        symbol: 标的代码
        name: 标的名称（用于 Google News 搜索）
        enabled_sources: 启用的数据源列表，None 则全部启用
        limit: 总返回上限
    """
    if enabled_sources is None:
        enabled_sources = DEFAULT_ENABLED_SOURCES

    # 加载自定义源
    custom_sources = _load_custom_sources()

    results: list[tuple[str, str]] = []

    for src_code in enabled_sources:
        # 优先自定义源
        if src_code in custom_sources:
            try:
                items = _fetch_custom_source(
                    src_code, custom_sources[src_code], symbol, name=name, limit=10
                )
                results += items
            except Exception:
                pass
            continue

        fetcher = _SOURCE_FETCHERS.get(src_code)
        if fetcher is None:
            continue
        try:
            if src_code == "googlenews":
                items = fetcher(symbol, name=name, limit=10)
            else:
                items = fetcher(symbol, limit=10)
            results += items
        except Exception:
            pass

    # 来源统计
    stats: dict[str, int] = {}
    for _, src in results:
        stats[src] = stats.get(src, 0) + 1

    # 去重（前 40 字符）
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for title, src in results:
        key = title[:40].strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append((title, src))

    return unique[:limit], stats


def fetch_news(symbol: str, limit: int = 12) -> list[str]:
    """兼容旧接口 — 仅返回标题文本列表。"""
    items, _ = fetch_all_sources(symbol, limit=limit)
    return [title for title, _ in items]


# ═══════════════════════════════════════════════════════════════
# LLM 情绪分析
# ═══════════════════════════════════════════════════════════════

_ANALYZE_PROMPT = """你是金融舆情分析师。根据以下关于 {symbol} 的信息判断市场情绪。

信息列表：
{headlines}

请输出 JSON（不要 markdown）：
{{
  "overall_sentiment": "bullish" | "bearish" | "neutral",
  "overall_score": 0-100 的整数（>67 偏多, <33 偏空, 中间为中性）,
  "confidence": 0-1 的浮点数,
  "summary": "60 字以内的中文舆情摘要",
  "items": [
    {{"title": "信息标题", "sentiment": "bullish|bearish|neutral", "score": 0-100}}
  ]
}}

只输出 JSON，不要任何额外文字。"""


# ═══════════════════════════════════════════════════════════════
# 关键词情绪分析（兜底）
# ═══════════════════════════════════════════════════════════════

_BULLISH_KEYWORDS = [
    "增长", "大涨", "涨停", "突破", "新高", "超预期", "利好", "回购",
    "增持", "分红", "业绩", "盈利", "扩张", "升级", "创新高",
    "上调", "买入", "推荐", "看好", "加仓", "中标", "签约",
    "拆股", "送转", "股东回报", "估值修复", "放量",
    "upgrade", "beat", "outperform", "strong buy", "raised target",
    "record high", "surge", "rally", "breakout", "bullish",
    "positive", "growth", "momentum", "buyback",
]
_BEARISH_KEYWORDS = [
    "下跌", "跌停", "亏损", "暴雷", "减持", "退市", "警告", "处罚",
    "新低", "不及预期", "利空", "调查", "诉讼", "违约", "破产",
    "下调", "卖出", "减仓", "清仓", "违规", "造假",
    "downgrade", "miss", "underperform", "sell", "cut target",
    "plunge", "crash", "bearish", "negative", "decline",
    "layoff", "investigation", "lawsuit", "bankruptcy",
]


def _keyword_sentiment(text: str) -> tuple[str, float]:
    """基于关键词的简单情绪判断。"""
    text_lower = text.lower()
    bull_count = sum(1 for kw in _BULLISH_KEYWORDS if kw in text_lower)
    bear_count = sum(1 for kw in _BEARISH_KEYWORDS if kw in text_lower)
    if bull_count > bear_count:
        return "bullish", min(50 + (bull_count - bear_count) * 10, 95)
    elif bear_count > bull_count:
        return "bearish", max(50 - (bear_count - bull_count) * 10, 5)
    return "neutral", 50


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def analyze(
    symbol: str,
    force_refresh: bool = False,
    enabled_sources: list[str] | None = None,
) -> SentimentReport:
    """获取舆情情绪报告。

    Args:
        symbol: 标的代码
        force_refresh: 忽略缓存
        enabled_sources: 启用的数据源列表，None 则全部启用
    """
    if enabled_sources is None:
        enabled_sources = DEFAULT_ENABLED_SOURCES

    # 1) 获取名称
    name = ""
    try:
        from .data import load_market_data
        md = load_market_data(symbol)
        name = md.quote.name
    except Exception:
        pass

    display_name = name or symbol

    # 2) 多源抓取
    items_with_source, source_stats = fetch_all_sources(
        symbol, name=name, enabled_sources=enabled_sources, limit=30
    )

    if not items_with_source:
        return SentimentReport(
            symbol=symbol, name=name,
            overall_sentiment="neutral", overall_score=50,
            summary=f"未找到 {display_name} 的相关舆情信息",
            data_source="none",
            source_stats={},
        )

    # 3) 尝试 LLM 分析
    try:
        from .llm import get_llm
        llm = get_llm()
        hlist = "\n".join(f"- [{src}] {title}" for title, src in items_with_source)
        prompt = _ANALYZE_PROMPT.format(symbol=display_name, headlines=hlist)
        reply = llm.chat(prompt, system="只输出 JSON，不要 markdown。").strip()

        m = re.search(r'\{.*\}', reply, re.DOTALL)
        if not m:
            raise ValueError("No JSON found in LLM reply")
        data = json.loads(m.group())

        # 构建明细
        analyzed_items = []
        for it in data.get("items", [])[:12]:
            title = it.get("title", "")
            src_match = re.match(r'\[(\w+)\]\s*', title)
            source = src_match.group(1) if src_match else "unknown"
            clean_title = title[src_match.end():] if src_match else title
            analyzed_items.append(SentimentItem(
                title=clean_title,
                sentiment=it.get("sentiment", "neutral"),
                score=float(it.get("score", 50)),
                source=source,
            ))

        src_desc = ", ".join(
            f"{SOURCE_META.get(s, {}).get('icon', '📄')} {SOURCE_META.get(s, {}).get('name', s)} {c}条"
            for s, c in sorted(source_stats.items(), key=lambda x: -x[1])
        )

        return SentimentReport(
            symbol=symbol, name=name,
            overall_sentiment=data.get("overall_sentiment", "neutral"),
            overall_score=float(data.get("overall_score", 50)),
            confidence=float(data.get("confidence", 0.5)),
            headlines=analyzed_items,
            summary=data.get("summary", ""),
            data_source=f"LLM分析 ({src_desc})",
            source_stats=source_stats,
        )
    except Exception:
        # 4) Fallback: 关键词情绪分析
        overall_score = 50
        bull_count = bear_count = 0
        fallback_items = []
        for title, src in items_with_source[:12]:
            sent, score = _keyword_sentiment(title)
            if sent == "bullish":
                bull_count += 1
            elif sent == "bearish":
                bear_count += 1
            fallback_items.append(SentimentItem(
                title=title, sentiment=sent, score=score, source=src,
            ))

        total = bull_count + bear_count
        if total > 0:
            overall_score = 50 + (bull_count - bear_count) / (total or 1) * 40
            overall_score = max(5, min(95, overall_score))

        sentiment = "bullish" if overall_score >= 60 else "bearish" if overall_score <= 40 else "neutral"
        src_desc = ", ".join(
            f"{SOURCE_META.get(s, {}).get('icon', '📄')} {SOURCE_META.get(s, {}).get('name', s)} {c}条"
            for s, c in sorted(source_stats.items(), key=lambda x: -x[1])
        )

        return SentimentReport(
            symbol=symbol, name=name,
            overall_sentiment=sentiment,
            overall_score=round(overall_score, 1),
            confidence=0.3,
            headlines=fallback_items,
            summary=f"关键词情绪分析（{len(items_with_source)}条信息，{len(enabled_sources)}个数据源）: "
                    f"偏多{bull_count}条 偏空{bear_count}条",
            data_source=f"关键词分析 ({src_desc})",
            source_stats=source_stats,
        )

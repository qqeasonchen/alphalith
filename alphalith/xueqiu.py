"""
Xueqiu (雪球) Chinese social sentiment — 零依赖。
数据源: xueqiu.com 公开接口（个股讨论 + 热门帖子）。
"""
from __future__ import annotations

import json as _json
import urllib.request
import urllib.parse
from typing import Optional


def _fetch_xueqiu_status(symbol: str, count: int = 10) -> list[dict]:
    """雪球个股讨论帖。
    https://xueqiu.com/statuses/search.json?count=10&comment=0&symbol=SH600519&source=all&page=1
    """
    code = symbol.split(".")[0].split(":")[-1]
    # 判断市场
    if symbol.upper().endswith((".SS", ".SZ")) or symbol.split(".")[0].isdigit():
        prefix = "SH" if (code.startswith(("6", "9")) and not code.startswith("0")) else "SZ"
        xq_symbol = f"{prefix}{code}"
    elif ".HK" in symbol.upper():
        xq_symbol = f"{code.zfill(5)}"
    else:
        xq_symbol = code.upper()

    url = (
        f"https://xueqiu.com/statuses/search.json"
        f"?count={count}&comment=0&symbol={xq_symbol}&source=all&page=1"
    )
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://xueqiu.com/",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
        posts = []
        for item in data.get("list", [])[:count]:
            text = str(item.get("text", "") or item.get("title", ""))
            # 去 HTML 标签
            import re
            text = re.sub(r"<[^>]+>", "", text)
            posts.append({
                "text": text[:200],
                "reply_count": item.get("reply_count", 0),
                "retweet_count": item.get("retweet_count", 0),
                "like_count": item.get("like_count", 0),
                "created_at": item.get("created_at", 0),
            })
        return posts
    except Exception:
        return []


def _fetch_xueqiu_hot() -> list[dict]:
    """雪球热门讨论（全局热帖）。"""
    url = "https://xueqiu.com/statuses/hot/listV2.json?page=1&last_id=0"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://xueqiu.com/",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
        posts = []
        for item in data.get("list", [])[:15]:
            text = str(item.get("text", "") or item.get("title", ""))
            import re
            text = re.sub(r"<[^>]+>", "", text)
            posts.append({
                "text": text[:200],
                "reply_count": item.get("reply_count", 0),
                "retweet_count": item.get("retweet_count", 0),
                "like_count": item.get("like_count", 0),
                "created_at": item.get("created_at", 0),
            })
        return posts
    except Exception:
        return []


def search_sentiment(symbol: str, name: str = "", limit: int = 10) -> dict:
    """获取雪球情绪数据。"""
    posts = _fetch_xueqiu_status(symbol, count=limit)
    if not posts and name:
        posts = _fetch_xueqiu_status(name, count=limit)

    if not posts:
        return {"score": 0.5, "label": "neutral", "total": 0, "sentiment": "无雪球数据"}

    bullish_words = ["买入", "看多", "利好", "涨", "起飞", "冲", "抄底", "加仓",
                     "翻倍", "主升浪", "突破", "低估", "机会", "龙头", "牛"]
    bearish_words = ["卖出", "看空", "利空", "跌", "崩", "割肉", "减仓", "跑",
                     "腰斩", "风险", "泡沫", "高估", "踩雷", "熊", "套牢"]

    bull_count = 0
    bear_count = 0
    total_likes = 0

    for p in posts:
        t = p["text"]
        b = sum(1 for w in bullish_words if w in t)
        r = sum(1 for w in bearish_words if w in t)
        if b > r:
            bull_count += 1
        elif r > b:
            bear_count += 1
        total_likes += p.get("like_count", 0)

    n = max(len(posts), 1)
    bull_ratio = bull_count / n
    bear_ratio = bear_count / n

    raw = 0.5 + (bull_ratio - bear_ratio) * 0.35
    score = max(0.0, min(1.0, raw))

    if score > 0.6:
        label = "bullish"
        desc = f"雪球看多 ({bull_count}/{n} 帖看多, 共{total_likes}赞)"
    elif score < 0.4:
        label = "bearish"
        desc = f"雪球看空 ({bear_count}/{n} 帖看空, 共{total_likes}赞)"
    else:
        label = "neutral"
        desc = f"雪球中性 ({bull_count}多/{bear_count}空/{n}帖)"

    return {
        "score": score, "label": label, "total": n,
        "total_likes": total_likes, "bull_count": bull_count,
        "bear_count": bear_count,
        "sentiment": desc,
    }


def top_headlines(symbol: str, limit: int = 5) -> list[str]:
    """Top 标题列表（用于新闻快照）。"""
    posts = _fetch_xueqiu_status(symbol, count=limit)
    headlines = []
    for p in posts:
        text = p["text"][:80]
        if text:
            headlines.append(f"[雪球] {text}")
    return headlines[:limit]

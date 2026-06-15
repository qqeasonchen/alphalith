"""
Reddit social sentiment — 零依赖抓取 r/wallstreetbets / r/stocks / r/investing。
使用 old.reddit.com JSON API（无需鉴权，公开子版块）。
"""
from __future__ import annotations

import json as _json
import urllib.request
import time
from typing import Optional


SUBS = ["wallstreetbets", "stocks", "investing"]


def _fetch_subreddit(sub: str, query: str, limit: int = 10) -> list[dict]:
    """抓取子版块帖子。query 可留空获取热门。"""
    url = f"https://old.reddit.com/r/{sub}/search.json?q={query}&sort=relevance&restrict_sr=on&limit={limit}&t=month"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": f"Alphalith/0.3 (research engine; contact@example.com)",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
        posts = []
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            posts.append({
                "title": d.get("title", ""),
                "selftext": (d.get("selftext", "") or "")[:300],
                "score": d.get("score", 0),
                "num_comments": d.get("num_comments", 0),
                "upvote_ratio": d.get("upvote_ratio", 0.5),
                "subreddit": d.get("subreddit", sub),
                "created_utc": d.get("created_utc", 0),
                "permalink": d.get("permalink", ""),
            })
        return posts
    except Exception:
        return []


def _fetch_hot(sub: str, limit: int = 15) -> list[dict]:
    """子版块热门。"""
    url = f"https://old.reddit.com/r/{sub}/hot.json?limit={limit}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": f"Alphalith/0.3 (research engine; contact@example.com)",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
        posts = []
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            if d.get("stickied"):
                continue  # skip pinned
            posts.append({
                "title": d.get("title", ""),
                "selftext": (d.get("selftext", "") or "")[:300],
                "score": d.get("score", 0),
                "num_comments": d.get("num_comments", 0),
                "upvote_ratio": d.get("upvote_ratio", 0.5),
                "subreddit": d.get("subreddit", sub),
                "created_utc": d.get("created_utc", 0),
                "permalink": d.get("permalink", ""),
            })
        return posts
    except Exception:
        return []


def search_ticker(ticker: str, limit: int = 10) -> list[dict]:
    """跨子版块搜索标的。返回合并结果，按分数排序。"""
    all_posts = []
    # 优先搜索
    for sub in SUBS:
        posts = _fetch_subreddit(sub, ticker, limit=limit)
        all_posts += posts
        if len(all_posts) >= limit * 2:
            break
    # 补热门
    if len(all_posts) < limit:
        for sub in SUBS:
            posts = _fetch_hot(sub, limit=limit)
            # 过滤与 ticker 相关性
            ticker_upper = ticker.upper()
            for p in posts:
                tl = (p["title"] + p["selftext"]).upper()
                if ticker_upper in tl:
                    all_posts.append(p)
                elif "YOLO" in tl or "DD" in tl.split() or "GAIN" in tl or "LOSS" in tl:
                    if len(all_posts) < limit * 2:
                        all_posts.append(p)
            if len(all_posts) >= limit * 2:
                break

    # 去重、排序
    seen = set()
    unique = []
    for p in sorted(all_posts, key=lambda x: x["score"], reverse=True):
        key = p["title"][:40].lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique[:limit]


def sentiment_score(posts: list[dict]) -> dict:
    """将 Reddit 帖子列表聚合为情绪分数。"""
    if not posts:
        return {"score": 0.5, "label": "neutral", "total": 0, "sentiment": "无 Reddit 数据"}

    total_score = sum(p["score"] for p in posts)
    total_comments = sum(p["num_comments"] for p in posts)
    avg_upvote = sum(p["upvote_ratio"] for p in posts) / len(posts)

    # 帖子标题情绪检测
    bullish_words = ["bull", "bullish", "moon", "rocket", "pump", "long", "buy", "call",
                     "green", "gain", "profit", "breakout", "upgrade", "beat", "raise"]
    bearish_words = ["bear", "bearish", "crash", "dump", "short", "sell", "put",
                     "red", "loss", "fall", "drop", "risk", "downgrade", "miss", "cut"]

    bull_count = 0
    bear_count = 0
    for p in posts:
        t = p["title"].lower()
        b = sum(1 for w in bullish_words if w in t)
        r = sum(1 for w in bearish_words if w in t)
        if b > r:
            bull_count += 1
        elif r > b:
            bear_count += 1

    # 综合分数
    n = len(posts)
    bull_ratio = bull_count / max(n, 1)
    bear_ratio = bear_count / max(n, 1)
    raw = 0.5 + (bull_ratio - bear_ratio) * 0.3 + (avg_upvote - 0.5) * 0.2
    score = max(0.0, min(1.0, raw))

    if score > 0.6:
        label = "bullish"
        desc = f"Reddit 看多 ({bull_count}/{n} 帖看多, 均赞 {avg_upvote:.0%})"
    elif score < 0.4:
        label = "bearish"
        desc = f"Reddit 看空 ({bear_count}/{n} 帖看空, 均赞 {avg_upvote:.0%})"
    else:
        label = "neutral"
        desc = f"Reddit 中性 ({bull_count}多/{bear_count}空/{n}帖)"

    return {
        "score": score, "label": label, "total": n,
        "total_score": total_score, "total_comments": total_comments,
        "avg_upvote": avg_upvote, "bull_count": bull_count,
        "bear_count": bear_count,
        "sentiment": desc,
    }

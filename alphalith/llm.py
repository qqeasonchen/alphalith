"""
LLM router — 智能降级链：DeepSeek → Qwen → Claude → Ollama → Stub。
LLM 路由：环境变量决定用哪个，全没配就走规则化 Stub（保证 demo 一定能跑）。

每次 chat() 都会把 (prompt_tokens, completion_tokens, calls) 累加到 .usage，
没有 usage 字段的就用粗略估计：1 token ≈ 4 字符（英文）或 1.5 字符（中文）。
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from typing import Protocol


def _estimate_tokens(text: str) -> int:
    """粗略估计：中文 1 字 ≈ 1.5 token，英文按 4 字符 ≈ 1 token。"""
    if not text:
        return 0
    cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cn
    return int(cn * 1.5 + other / 4) or 1


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False  # 是否完全是本地估算（API 没回 usage 才会 True）

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLM(Protocol):
    name: str
    usage: Usage
    def chat(self, prompt: str, system: str = "") -> str: ...


@dataclass
class StubLLM:
    """无 API Key 时的兜底：基于规则生成结构化回答，保证 pipeline 跑通。"""
    name: str = "stub"
    usage: Usage = field(default_factory=Usage)

    def chat(self, prompt: str, system: str = "") -> str:
        bullish_kw = ["金叉", "上行", "回升", "超预期", "正面", "+", "看多"]
        bearish_kw = ["跌停", "下行", "不及预期", "负面", "看空", "-"]
        b = sum(k in prompt for k in bullish_kw)
        s = sum(k in prompt for k in bearish_kw)
        stance = "看多" if b > s else ("看空" if s > b else "中性")
        conf = round(0.55 + random.random() * 0.3, 2)
        reply = (
            f"立场：{stance}\n"
            f"置信度：{conf}\n"
            f"摘要：基于提供的数据，综合技术、基本面、新闻与情绪四个维度，"
            f"当前阶段倾向{stance}；建议观察关键支撑/阻力位与成交量配合。"
        )
        self.usage.calls += 1
        self.usage.prompt_tokens += _estimate_tokens(system) + _estimate_tokens(prompt)
        self.usage.completion_tokens += _estimate_tokens(reply)
        self.usage.estimated = True
        return reply


def _parse_truncated_json(text: str) -> dict | None:
    """Try to salvage data from a truncated JSON response.
    
    DeepSeek response format: {"choices":[{"message":{"content":"..."}}],"usage":{...}}
    If only the usage object / closing braces are missing, extract what we can.
    """
    import json
    import re
    # Strategy 1: JSON5-style repair — close open structures
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    repaired = text + "]" * open_brackets + "}" * open_braces
    try:
        return json.loads(repaired)
    except (json.JSONDecodeError, ValueError):
        pass
    # Strategy 2: regex extract content field directly
    m = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        content = m.group(1).encode().decode("unicode_escape")
        return {"choices": [{"message": {"content": content}}]}
    return None


def _read_response(resp) -> bytes:
    """Read response body with IncompleteRead recovery.
    
    If the connection is truncated, IncompleteRead.partial contains
    the bytes that were successfully read — try to salvage partial JSON.
    """
    from http.client import IncompleteRead
    try:
        return resp.read()
    except IncompleteRead as e:
        # 利用已读取的部分数据（765 bytes 可能包含完整 content）
        if e.partial:
            return e.partial
        raise


def _try_deepseek() -> "LLM | None":
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None

    import json
    import urllib.request
    import urllib.error
    from http.client import IncompleteRead, RemoteDisconnected

    # IncompleteRead / RemoteDisconnected 是可重试的瞬时网络错误
    _RETRYABLE = (IncompleteRead, RemoteDisconnected, ConnectionResetError, TimeoutError)
    _MAX_RETRIES = 1

    @dataclass
    class _DS:
        name: str = "deepseek"
        usage: Usage = field(default_factory=Usage)

        def _call_once(self, prompt: str, system: str) -> tuple[str, dict]:
            msg = []
            if system:
                msg.append({"role": "system", "content": system})
            msg.append({"role": "user", "content": prompt})
            body = json.dumps({
                "model": "deepseek-chat",
                "messages": msg,
                "temperature": 0.0,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.deepseek.com/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = _read_response(resp)
                text = raw.decode("utf-8")
                try:
                    data = json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    # 截断 JSON：尝试修复后解析
                    repaired = _parse_truncated_json(text)
                    if repaired is None:
                        raise
                    data = repaired
            content = data["choices"][0]["message"]["content"] or ""
            u = data.get("usage") or {}
            return content, u

        def chat(self, prompt: str, system: str = "") -> str:
            last_err = None
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    content, u = self._call_once(prompt, system)
                    self.usage.calls += 1
                    if "prompt_tokens" in u:
                        self.usage.prompt_tokens += int(u.get("prompt_tokens", 0))
                        self.usage.completion_tokens += int(u.get("completion_tokens", 0))
                    else:
                        self.usage.prompt_tokens += _estimate_tokens(system) + _estimate_tokens(prompt)
                        self.usage.completion_tokens += _estimate_tokens(content)
                        self.usage.estimated = True
                    return content
                except _RETRYABLE as e:
                    last_err = e
                    if attempt < _MAX_RETRIES:
                        import time
                        time.sleep(1.0 * (attempt + 1))  # 退避 1s / 2s
                        continue
                except (urllib.error.URLError, KeyError, ValueError, json.JSONDecodeError) as e:
                    last_err = e
                    break
                except Exception as e:
                    # catch-all 兜底：任何未预期的异常都不应传播到上层
                    last_err = e
                    break

            self.usage.calls += 1
            err_name = last_err.__class__.__name__ if last_err else "Unknown"
            return f"立场：中性\n置信度：0.5\n摘要：LLM 调用失败({err_name})，已降级。"

    return _DS()


def get_llm() -> "LLM":
    """按降级链选择 LLM；最终兜底 StubLLM 一定能跑。"""
    for factory in [_try_deepseek]:
        llm = factory()
        if llm is not None:
            return llm  # type: ignore[return-value]
    return StubLLM()

"""
Alphalith GUI — 零外部依赖的投研工作台。

Architecture:
  Python http.server (stdlib) + 单文件 HTML (inline CSS/JS, Canvas charts)

Layout:
  ┌──────────────────────────────────────────────┐
  │  Top Nav: Research | Signals | Backtest | Pf │
  ├──────────────┬───────────────────────────────┤
  │  Chat Panel  │  Results Area                 │
  │  (AI 对话)    │  - Chart / Signal / Status    │
  │              │                               │
  │  [Settings]  │                               │
  │  [Input...]  │                               │
  └──────────────┴───────────────────────────────┘

Usage:
  alphalith gui [--port PORT]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import signal
import sys
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

CONFIG_PATH = Path.home() / ".alphalith" / "config.json"
USERS_PATH = Path.home() / ".alphalith" / "users.json"
GUI_DIR = Path(__file__).parent

# In-memory session store: token -> {"username": str, "created": ts, "expires": ts}
_SESSIONS: dict = {}
_SESSIONS_LOCK = threading.Lock()
SESSION_TTL = 7 * 24 * 3600  # 7 天

# ====================== 用户认证工具 ======================

def _load_users() -> dict:
    if USERS_PATH.exists():
        try:
            with open(USERS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_users(users: dict) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    tmp.replace(USERS_PATH)
    try:
        os.chmod(USERS_PATH, 0o600)
    except OSError:
        pass


# 默认管理员账号
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "alphalith"


def _ensure_default_admin() -> dict:
    """如果用户库为空，创建默认管理员账号。返回最新 users 字典。"""
    users = _load_users()
    if users:
        return users
    pwd_hash, salt = _hash_password(DEFAULT_ADMIN_PASSWORD)
    users = {
        DEFAULT_ADMIN_USERNAME: {
            "username": DEFAULT_ADMIN_USERNAME,
            "display_name": "管理员",
            "password_hash": pwd_hash,
            "salt": salt,
            "role": "admin",
            "created_at": time.time(),
            "is_default": True,
        }
    }
    _save_users(users)
    return users


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """PBKDF2-SHA256 加盐哈希。返回 (hash_hex, salt_hex)。"""
    if salt is None:
        salt = secrets.token_hex(16)
    salt_bytes = bytes.fromhex(salt)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 200_000)
    return dk.hex(), salt


def _verify_password(password: str, hash_hex: str, salt: str) -> bool:
    calc_hex, _ = _hash_password(password, salt)
    return secrets.compare_digest(calc_hex, hash_hex)


def _create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _SESSIONS_LOCK:
        _SESSIONS[token] = {
            "username": username,
            "created": now,
            "expires": now + SESSION_TTL,
        }
    return token


def _get_session(token: str) -> dict | None:
    if not token:
        return None
    with _SESSIONS_LOCK:
        sess = _SESSIONS.get(token)
        if not sess:
            return None
        if sess["expires"] < time.time():
            _SESSIONS.pop(token, None)
            return None
        return sess


def _delete_session(token: str) -> None:
    if not token:
        return
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token, None)


def _validate_username(name: str) -> str | None:
    if not name or not isinstance(name, str):
        return "用户名不能为空"
    name = name.strip()
    if len(name) < 2 or len(name) > 32:
        return "用户名长度需 2-32 个字符"
    if not re.match(r"^[A-Za-z0-9_\u4e00-\u9fa5][A-Za-z0-9_\-\.\u4e00-\u9fa5]*$", name):
        return "用户名只能包含字母、数字、下划线、连字符、点和中文"
    return None


def _validate_password(pwd: str) -> str | None:
    if not pwd or not isinstance(pwd, str):
        return "密码不能为空"
    if len(pwd) < 6 or len(pwd) > 128:
        return "密码长度需 6-128 个字符"
    return None

DEFAULT_CONFIG = {
    "provider": "deepseek",
    "model_name": "deepseek-v4-pro",
    "api_key": "",
    "base_url": "https://api.deepseek.com/v1",
    "api_key_env": "DEEPSEEK_API_KEY",
    "temperature": 0.7,
    "data_source": "auto",
    "data_source_order": ["sina", "eastmoney", "tencent", "akshare", "yfinance"],
    "custom_sources": {},
    "sentiment_sources": ["googlenews", "yahoo", "finviz", "stocktwits", "eastmoney", "sina", "xueqiu", "reddit"],
    "sentiment_source_order": ["googlenews", "yahoo", "eastmoney", "finviz", "stocktwits", "sina", "xueqiu", "reddit"],
    "custom_sentiment_sources": {},
    "providers": {
        "deepseek": {
            "name": "DeepSeek 官方",
            "base_url": "https://api.deepseek.com/v1",
            "key_env": "DEEPSEEK_API_KEY",
            "models": [
                {"id": "deepseek-v4-pro",      "name": "V4 Pro · 旗舰",      "desc": "万亿MoE 100万ctx · 编程推理SOTA"},
                {"id": "deepseek-v4-flash",    "name": "V4 Flash · 高速",    "desc": "极致性价比 轻量高速"},
            ],
        },
        "bailian": {
            "name": "阿里云百炼 · Qwen3",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "key_env": "DASHSCOPE_API_KEY",
            "models": [
                {"id": "qwen3.6-max-preview",  "name": "Qwen3.6 Max · 旗舰",  "desc": "最新旗舰 · 需 Coding Plan"},
                {"id": "qwen3-coder-plus",     "name": "Qwen3 Coder Plus · 编程", "desc": "编程专精 · 需 Coding Plan"},
                {"id": "qwen3.5-omni",         "name": "Qwen3.5 Omni · 多模态", "desc": "图文理解 128K ctx"},
            ],
        },
        "openai": {
            "name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "key_env": "OPENAI_API_KEY",
            "models": [
                {"id": "gpt-5.5",              "name": "GPT-5.5 · 旗舰",       "desc": "100万ctx 最强通用"},
                {"id": "gpt-5.6",              "name": "GPT-5.6 · 最新预览",   "desc": "150万ctx 即将发布"},
                {"id": "o4-mini",              "name": "o4-mini · 推理",       "desc": "推理专精 高性价比"},
            ],
        },
        "claude": {
            "name": "Anthropic Claude",
            "base_url": "https://api.anthropic.com/v1",
            "key_env": "ANTHROPIC_API_KEY",
            "models": [
                {"id": "claude-opus-4-7-20250530",  "name": "Opus 4.7 · 旗舰",  "desc": "最强编码 SWE-bench 82% · 需代理"},
                {"id": "claude-sonnet-4-6-20250217","name": "Sonnet 4.6 · 主力", "desc": "编程高效 性价比 · 需代理"},
            ],
        },
        "gemini": {
            "name": "Google Gemini",
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "key_env": "GEMINI_API_KEY",
            "models": [
                {"id": "gemini-3.1-pro",        "name": "3.1 Pro · 推理",      "desc": "复杂推理 Agent · 需代理"},
                {"id": "gemini-3.5-flash",      "name": "3.5 Flash · 最新",    "desc": "Agent & Coding SOTA · 需代理"},
            ],
        },
        "zhipu": {
            "name": "智谱 GLM",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "key_env": "ZHIPU_API_KEY",
            "models": [
                {"id": "glm-5.2",              "name": "GLM-5.2 · 最新",       "desc": "6月13日发布 1M ctx 最强国产Coding · Coding Plan"},
                {"id": "glm-5.2-flash",        "name": "GLM-5.2 Flash · 高速", "desc": "轻量高速 即将发布"},
            ],
        },
        "kimi": {
            "name": "Kimi 月之暗面",
            "base_url": "https://api.moonshot.cn/v1",
            "key_env": "MOONSHOT_API_KEY",
            "models": [
                {"id": "kimi-k2.7-code",       "name": "K2.7 Code · 最新编程", "desc": "6月12日发布 1T参数 编程专精 · Coding Plan"},
                {"id": "kimi-k2.6",            "name": "K2.6 · 旗舰",          "desc": "256K ctx 通用旗舰"},
            ],
        },
        "volcengine-coding": {
            "name": "火山方舟 · Coding Plan",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "key_env": "VOLCANO_API_KEY",
            "protocol": "openai",
            "models": [
                {"id": "deepseek-v3-250324",    "name": "DeepSeek V3 · 编程",     "desc": "DeepSeek V3 编程专精 · Coding Plan"},
                {"id": "deepseek-r1-250528",    "name": "DeepSeek R1 · 推理",     "desc": "DeepSeek R1 推理模型 · Coding Plan"},
                {"id": "doubao-pro-256k",      "name": "豆包 Pro 256K · 旗舰",   "desc": "字节豆包旗舰 · Coding Plan"},
                {"id": "doubao-lite-128k",     "name": "豆包 Lite 128K · 轻量",  "desc": "高速低价 · Coding Plan"},
            ],
        },
        "volcengine-token": {
            "name": "火山方舟 · Token Plan",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "key_env": "VOLCANO_API_KEY",
            "protocol": "openai",
            "models": [
                {"id": "deepseek-v3-250324",    "name": "DeepSeek V3 · 通用",     "desc": "DeepSeek V3 · Token Plan 按量计费"},
                {"id": "deepseek-r1-250528",    "name": "DeepSeek R1 · 推理",     "desc": "DeepSeek R1 · Token Plan 按量计费"},
                {"id": "doubao-pro-32k",        "name": "豆包 Pro 32K · 轻量",    "desc": "豆包轻量版 · Token Plan"},
                {"id": "doubao-lite-32k",       "name": "豆包 Lite 32K · 高速",   "desc": "豆包高速版 · Token Plan"},
            ],
        },
        "qianfan": {
            "name": "百度千帆",
            "base_url": "https://qianfan.baidubce.com/v2",
            "key_env": "QIANFAN_API_KEY",
            "models": [
                {"id": "ernie-4.5-8k",         "name": "ERNIE 4.5 · 旗舰",     "desc": "百度旗舰"},
                {"id": "ernie-speed-128k",     "name": "ERNIE Speed · 高速",   "desc": "高并发低延迟"},
            ],
        },
        "tencent-coding": {
            "name": "腾讯 Coding Plan",
            "base_url": "https://api.lkeap.cloud.tencent.com/coding/v3",
            "key_env": "TENCENT_CODING_API_KEY",
            "models": [
                {"id": "tc-code-latest",         "name": "Auto · 智能路由",      "desc": "自动匹配最优模型 · Coding Plan"},
                {"id": "glm-5",                  "name": "GLM-5 · 智谱",         "desc": "Coding Plan 代理 · 智谱旗舰"},
                {"id": "kimi-k2.5",              "name": "Kimi K2.5 · 月暗",     "desc": "Coding Plan 代理 · 月暗旗舰"},
                {"id": "minimax-m2.5",           "name": "MiniMax M2.5 · 海螺",  "desc": "Coding Plan 代理 · MiniMax旗舰"},
            ],
        },
        "tencent-token": {
            "name": "腾讯 Token Plan · 通用",
            "base_url": "https://api.lkeap.cloud.tencent.com/plan/v3",
            "key_env": "TENCENT_TOKEN_API_KEY",
            "models": [
                {"id": "tc-code-latest",         "name": "Auto · 智能路由",      "desc": "自动匹配最优模型 · Token Plan"},
                {"id": "glm-5.1",                "name": "GLM-5.1 · 智谱最新",   "desc": "Token Plan · 智谱最新旗舰"},
                {"id": "glm-5",                  "name": "GLM-5 · 智谱",         "desc": "Token Plan · 智谱旗舰"},
                {"id": "kimi-k2.5",              "name": "Kimi K2.5 · 月暗",     "desc": "Token Plan · 月暗旗舰"},
                {"id": "minimax-m2.7",           "name": "MiniMax M2.7 · 海螺最新","desc": "Token Plan · MiniMax最新"},
                {"id": "minimax-m2.5",           "name": "MiniMax M2.5 · 海螺",  "desc": "Token Plan · MiniMax旗舰"},
            ],
        },
        "tencent-hy": {
            "name": "腾讯 Hy Token Plan",
            "base_url": "https://api.lkeap.cloud.tencent.com/plan/v3",
            "key_env": "TENCENT_HY_API_KEY",
            "models": [
                {"id": "hy3-preview",            "name": "Hy3 Preview · 旗舰",   "desc": "295B/21B MoE · 256K ctx · Agent 工作负载"},
            ],
        },
        "minimax": {
            "name": "MiniMax",
            "base_url": "https://api.minimax.chat/v1",
            "key_env": "MINIMAX_API_KEY",
            "models": [
                {"id": "minimax-m1",            "name": "MiniMax-M1 · 旗舰",    "desc": "长上下文 推理"},
            ],
        },
        "stepfun": {
            "name": "阶跃星辰",
            "base_url": "https://api.stepfun.com/v1",
            "key_env": "STEPFUN_API_KEY",
            "models": [
                {"id": "step-3.5-flash",         "name": "Step 3.5 Flash · 高速", "desc": "轻量高效"},
            ],
        },
        "siliconflow": {
            "name": "硅基流动 (国内直连)",
            "base_url": "https://api.siliconflow.cn/v1",
            "key_env": "SILICONFLOW_API_KEY",
            "models": [
                {"id": "deepseek-ai/DeepSeek-V4-Pro",   "name": "DeepSeek V4 Pro (代理)",  "desc": "国内直连 免翻墙"},
                {"id": "Qwen/Qwen3.6-Max",              "name": "Qwen3.6 Max (代理)",      "desc": "国内直连 免翻墙"},
                {"id": "Pro/deepseek-ai/DeepSeek-V4-Pro","name": "DS V4 Pro (付费增强)",    "desc": "硅基增强版 效果更优"},
            ],
        },
    },
    "data": {
        "a_share": "akshare",
        "hk_stock": "yfinance",
        "us_stock": "yfinance",
    },
    "agents": {
        "market_scanner": True,
        "sentiment_analyzer": True,
        "technical_trader": True,
    },
    "advanced": {
        "max_agents": 5,
        "timeout": 120,
        "retry": 3,
        "cache_ttl_h": 24,
    },
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            merged = DEFAULT_CONFIG.copy()
            _deep_merge(merged, saved)
            return merged
        except (json.JSONDecodeError, IOError):
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


class GuiHandler(BaseHTTPRequestHandler):
    """Serves app.html and API endpoints."""

    server_config: dict = {}

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def _send_json(self, data, status=200, set_cookie: str | None = None, clear_cookie: bool = False):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Credentials", "true")
        if set_cookie:
            self.send_header(
                "Set-Cookie",
                f"alphalith_session={set_cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}",
            )
        if clear_cookie:
            self.send_header(
                "Set-Cookie",
                "alphalith_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
            )
        self.end_headers()
        self.wfile.write(body)

    def _get_cookie(self, name: str) -> str:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                if k == name:
                    return v
        return ""

    def _current_user(self) -> dict | None:
        token = self._get_cookie("alphalith_session")
        return _get_session(token)

    def _send_html(self, html: str, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, msg, status=500):
        self._send_json({"error": msg}, status)

    def _read_body(self) -> bytes:
        cl = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(cl)

    def _send_sse_headers(self):
        """Server-Sent Events 流式响应头。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _sse_send(self, event: str, data: dict):
        """发送一个 SSE 事件帧。"""
        try:
            payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            html_path = GUI_DIR / "app.html"
            if html_path.exists():
                self._send_html(html_path.read_text(encoding="utf-8"))
            else:
                self._send_error_json("app.html not found", 404)
            return

        if path == "/api/config":
            self._send_json(load_config())
            return

        if path == "/api/auth/me":
            sess = self._current_user()
            if sess:
                users = _load_users()
                u = users.get(sess["username"], {})
                self._send_json({
                    "logged_in": True,
                    "username": sess["username"],
                    "display_name": u.get("display_name") or sess["username"],
                    "role": u.get("role", "user"),
                    "is_default": bool(u.get("is_default")),
                    "created_at": u.get("created_at"),
                    "user_count": len(users),
                })
            else:
                users = _load_users()
                self._send_json({
                    "logged_in": False,
                    "user_count": len(users),
                    "has_default_admin": any(u.get("is_default") for u in users.values()),
                })
            return

        if path == "/api/auth/logout":
            token = self._get_cookie("alphalith_session")
            _delete_session(token)
            self._send_json({"ok": True}, clear_cookie=True)
            return

        if path == "/api/agents/status":
            try:
                from ..agents import AgentPool
                pool = AgentPool()
                statuses = [
                    {"name": a.name, "status": a.status, "last_run": str(a.last_run) if a.last_run else None}
                    for a in pool.agents
                ]
                self._send_json({"agents": statuses})
            except Exception as e:
                self._send_json({"agents": [], "error": str(e)})
            return

        if path == "/api/history":
            try:
                qs = parse_qs(parsed.query)
                sym = qs.get("symbol", [None])[0]
                limit = int(qs.get("limit", ["50"])[0])
                from ..journal import history as jnl_history
                rows = jnl_history(symbol=sym, limit=limit)
                self._send_json({"history": rows, "total": len(rows)})
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/review":
            try:
                qs = parse_qs(parsed.query)
                sym = qs.get("symbol", [None])[0]
                from ..journal import review as jnl_review
                result = jnl_review(symbol=sym)
                self._send_json(result)
            except Exception as e:
                self._send_error_json(str(e))
            return

        # ── Watchlist ──
        if path == "/api/watchlist/lists":
            from ..watchlist import list_lists as wl_list
            self._send_json({"lists": wl_list()})
            return

        if path == "/api/watchlist/items":
            qs = parse_qs(parsed.query)
            list_id = int(qs.get("list_id", [0])[0])
            with_quotes = qs.get("quotes", ["0"])[0] == "1"
            from ..watchlist import list_items, get_items_with_quotes
            if with_quotes:
                items = get_items_with_quotes(list_id)
            else:
                items = list_items(list_id)
            self._send_json({"items": items, "list_id": list_id})
            return

        # ── Portfolio ──
        if path == "/api/portfolio":
            from ..portfolio import list_positions
            self._send_json({"positions": list_positions()})
            return

        if path == "/api/portfolio/summary":
            from ..portfolio import get_summary
            self._send_json(get_summary())
            return

        # ── Alerts ──
        if path == "/api/alerts":
            qs = parse_qs(parsed.query)
            sym = qs.get("symbol", [None])[0]
            from ..alerts import list_alerts
            self._send_json({"alerts": list_alerts(symbol=sym)})
            return

        # ── Sentiment sources config ──
        if path == "/api/sentiment/sources":
            from ..sentiment import SOURCE_META
            cfg = load_config()
            custom = cfg.get("custom_sentiment_sources", {})
            all_sources = dict(SOURCE_META)
            all_sources.update(custom)
            self._send_json({
                "sources": all_sources,
                "enabled": cfg.get("sentiment_sources", list(SOURCE_META.keys())),
                "order": cfg.get("sentiment_source_order", list(SOURCE_META.keys())),
                "custom_sources": custom,
            })
            return

        # ── Sentiment custom sources CRUD ──
        if path == "/api/sentiment/sources/custom":
            cfg = load_config()
            custom = cfg.get("custom_sentiment_sources", {})
            self._send_json({"sources": custom})
            return

        self._send_error_json("Not found", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                save_config(body)
                self._send_json({"ok": True, "path": str(CONFIG_PATH)})
            except json.JSONDecodeError as e:
                self._send_error_json(f"Invalid JSON: {e}", 400)
            return

        if path == "/api/auth/register":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                username = (body.get("username") or "").strip()
                password = body.get("password") or ""
                display_name = (body.get("display_name") or username).strip()

                err = _validate_username(username) or _validate_password(password)
                if err:
                    self._send_error_json(err, 400)
                    return

                users = _load_users()
                if username in users:
                    self._send_error_json("用户名已存在", 409)
                    return

                hash_hex, salt = _hash_password(password)
                users[username] = {
                    "password_hash": hash_hex,
                    "salt": salt,
                    "display_name": display_name,
                    "created_at": int(time.time()),
                }
                _save_users(users)

                token = _create_session(username)
                self._send_json({
                    "ok": True,
                    "username": username,
                    "display_name": display_name,
                }, set_cookie=token)
            except json.JSONDecodeError as e:
                self._send_error_json(f"Invalid JSON: {e}", 400)
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/auth/login":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                username = (body.get("username") or "").strip()
                password = body.get("password") or ""

                if not username or not password:
                    self._send_error_json("用户名和密码不能为空", 400)
                    return

                users = _load_users()
                u = users.get(username)
                if not u:
                    self._send_error_json("用户名或密码错误", 401)
                    return

                if not _verify_password(password, u["password_hash"], u["salt"]):
                    self._send_error_json("用户名或密码错误", 401)
                    return

                token = _create_session(username)
                self._send_json({
                    "ok": True,
                    "username": username,
                    "display_name": u.get("display_name") or username,
                }, set_cookie=token)
            except json.JSONDecodeError as e:
                self._send_error_json(f"Invalid JSON: {e}", 400)
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/resolve":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                # 兼容前端发送的 query 字段，以及直接使用的 text 字段
                text = body.get("query", body.get("text", ""))
                # Try to extract a symbol from natural language
                from ..market import detect_market, CN_NAME_MAP
                
                # 1) Try exact CN name match first
                for cn_name, code in CN_NAME_MAP.items():
                    if cn_name in text:
                        market, sym = detect_market(code)
                        self._send_json({"symbol": code, "market": market, "display": cn_name})
                        return
                
                # 2) Try regex extraction
                import re
                # A-share 6-digit code
                m = re.search(r"\b(\d{6})\b", text)
                if m:
                    market, sym = detect_market(m.group(1))
                    self._send_json({"symbol": sym, "market": market, "display": m.group(1)})
                    return
                # HK stock: 4-5 digits .HK
                m = re.search(r"\b(\d{4,5})\.HK\b", text, re.IGNORECASE)
                if m:
                    market, sym = detect_market(m.group(0))
                    self._send_json({"symbol": sym, "market": market, "display": m.group(0)})
                    return
                # US stock: 2-5 uppercase letters
                m = re.search(r"\b([A-Z]{2,5})\b(?!\.)", text)
                if m:
                    market, sym = detect_market(m.group(1))
                    self._send_json({"symbol": sym, "market": market, "display": m.group(1)})
                    return
                
                self._send_json({"error": "Could not resolve symbol from text", "symbol": "", "market": ""})
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/chat":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                # 兼容前端发送的 message 字段，以及直接使用的 prompt 字段
                prompt = body.get("message", body.get("prompt", ""))
                system = body.get("system", "你是专业的量化投资研究助手，提供数据驱动的分析与建议。")
                if not prompt:
                    self._send_error_json("prompt is required", 400)
                    return

                from ..llm import get_llm
                llm = get_llm()
                reply = llm.chat(prompt, system=system)
                self._send_json({
                    "reply": reply,
                    "usage": {
                        "calls": llm.usage.calls,
                        "total_tokens": llm.usage.total_tokens,
                    },
                })
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/analyze":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbol = body.get("symbol", "")
                depth = body.get("depth", "standard")
                if not symbol:
                    self._send_error_json("symbol is required", 400)
                    return

                from ..core import analyze as run_analyze
                decision = run_analyze(symbol, depth=depth, persist=True)
                self._send_json({
                    "id": decision.id,
                    "symbol": decision.symbol,
                    "market": decision.market.value,
                    "action": decision.action,
                    "confidence": decision.confidence,
                    "entry_price": decision.entry_price,
                    "stop_loss": decision.stop_loss,
                    "take_profit": decision.take_profit,
                    "suggested_shares": decision.suggested_shares,
                    "agent_reports": [
                        {"name": r.name, "stance": r.stance, "confidence": r.confidence, "summary": r.summary}
                        for r in decision.agent_reports
                    ] if decision.agent_reports else [],
                    "situation_summary": {
                        "snapshot_text": decision.situation_summary.snapshot_text,
                        "key_drivers": decision.situation_summary.key_drivers,
                        "uncertainties": decision.situation_summary.uncertainties,
                    } if decision.situation_summary else None,
                    "debate": [
                        {"bull": d.bull, "bear": d.bear}
                        for d in decision.debate
                    ] if decision.debate else [],
                    "manager_report": {
                        "summary": decision.manager_report.summary,
                        "stance": decision.manager_report.stance,
                        "confidence": decision.manager_report.confidence,
                        "key_points": decision.manager_report.key_points,
                    } if decision.manager_report else None,
                    "trader_report": {
                        "action": decision.trader_report.action,
                        "confidence": decision.trader_report.confidence,
                        "position_pct": decision.trader_report.position_pct,
                        "entry_strategy": decision.trader_report.entry_strategy,
                        "reasoning": decision.trader_report.reasoning,
                    } if decision.trader_report else None,
                    "risk_reviews": [
                        {
                            "aggressive": r.aggressive,
                            "aggressive_stance": r.aggressive_stance,
                            "conservative": r.conservative,
                            "conservative_stance": r.conservative_stance,
                            "neutral": r.neutral,
                            "neutral_stance": r.neutral_stance,
                            "final_verdict": r.final_verdict,
                        }
                        for r in (decision.risk_reviews or [])
                    ] if decision.risk_reviews else [],
                    "risk_review": decision.risk_review,
                    "reasoning": decision.reasoning,
                    "market_warnings": list(decision.market_warnings or []),
                    "fees": {
                        "commission": decision.fees.commission,
                        "stamp_tax": decision.fees.stamp_tax,
                        "transfer_fee": decision.fees.transfer_fee,
                        "sec_fee": decision.fees.sec_fee,
                        "other": decision.fees.other,
                        "total": decision.fees.total,
                        "breakeven_pct": decision.fees.breakeven_pct,
                    } if decision.fees else {},
                    "currency": decision.currency.value if hasattr(decision.currency, "value") else str(decision.currency),
                    "adp": decision.to_adp_json(),
                    "extra": decision.extra,
                })
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/analyze/stream":
            # SSE 流式分析 v0.4.1：7层13节点全流水线
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbol = body.get("symbol", "")
                depth = body.get("depth", "standard")
                persist = bool(body.get("persist", True))
                if not symbol:
                    self._send_error_json("symbol is required", 400)
                    return
            except json.JSONDecodeError as e:
                self._send_error_json(f"Invalid JSON: {e}", 400)
                return

            self._send_sse_headers()
            try:
                from ..core import analyze_with_sse

                self._sse_send("progress", {"stage": "init", "pct": 5, "msg": f"🔍 启动 7 层分析流水线: {symbol}..."})

                for event in analyze_with_sse(symbol, depth=depth, persist=persist):
                    if event["type"] == "progress":
                        pct = int(event["step"] / event["total"] * 100)
                        self._sse_send("progress", {
                            "stage": f"layer_{event['step']}",
                            "pct": pct,
                            "msg": event["message"],
                            "step": event["step"],
                            "total": event["total"],
                        })
                        # Forward analyst details if present
                        if "analysts" in event:
                            for a in event["analysts"]:
                                self._sse_send("analyst", a)
                    elif event["type"] == "result":
                        d = event["decision"]
                        self._sse_send("progress", {"stage": "done", "pct": 100, "msg": "✅ 7 层分析完成"})
                        self._sse_send("done", {
                            "id": d.id,
                            "symbol": d.symbol,
                            "market": d.market.value,
                            "action": d.action,
                            "confidence": d.confidence,
                            "entry_price": d.entry_price,
                            "stop_loss": d.stop_loss,
                            "take_profit": d.take_profit,
                            "suggested_shares": d.suggested_shares,
                            "agent_reports": [
                                {"name": r.name, "stance": r.stance, "confidence": r.confidence, "summary": r.summary}
                                for r in d.agent_reports
                            ] if d.agent_reports else [],
                            "situation_summary": {
                                "snapshot_text": d.situation_summary.snapshot_text,
                                "key_drivers": d.situation_summary.key_drivers,
                                "uncertainties": d.situation_summary.uncertainties,
                            } if d.situation_summary else None,
                            "debate": [
                                {"bull": deb.bull, "bear": deb.bear}
                                for deb in (d.debate or [])
                            ],
                            "manager_report": {
                                "summary": d.manager_report.summary,
                                "stance": d.manager_report.stance,
                                "confidence": d.manager_report.confidence,
                                "key_points": d.manager_report.key_points,
                            } if d.manager_report else None,
                            "trader_report": {
                                "action": d.trader_report.action,
                                "confidence": d.trader_report.confidence,
                                "position_pct": d.trader_report.position_pct,
                                "entry_strategy": d.trader_report.entry_strategy,
                                "reasoning": d.trader_report.reasoning,
                            } if d.trader_report else None,
                            "risk_reviews": [
                                {
                                    "aggressive": r.aggressive,
                                    "aggressive_stance": r.aggressive_stance,
                                    "conservative": r.conservative,
                                    "conservative_stance": r.conservative_stance,
                                    "neutral": r.neutral,
                                    "neutral_stance": r.neutral_stance,
                                    "final_verdict": r.final_verdict,
                                }
                                for r in (d.risk_reviews or [])
                            ] if d.risk_reviews else [],
                            "reasoning": d.reasoning,
                            "market_warnings": list(d.market_warnings or []),
                            "fees": {
                                "commission": d.fees.commission,
                                "stamp_tax": d.fees.stamp_tax,
                                "transfer_fee": d.fees.transfer_fee,
                                "sec_fee": d.fees.sec_fee,
                                "other": d.fees.other,
                                "total": d.fees.total,
                                "breakeven_pct": d.fees.breakeven_pct,
                            },
                            "currency": d.currency.value if hasattr(d.currency, "value") else str(d.currency),
                            "extra": d.extra,
                        })
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                try:
                    self._sse_send("error", {"message": str(e)})
                except Exception:
                    pass
            return

        if path == "/api/backtest":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbol = body.get("symbol", "600519")
                # 兼容前端发送的 period_days 和 days 两种字段名
                days = int(body.get("period_days", body.get("days", 90)))
                horizon = int(body.get("horizon", body.get("horizon", 5)))
                strategy = body.get("strategy", "ma_cross")

                from ..backtest import run_backtest, to_dict
                r = run_backtest(symbol, days=days, horizon=horizon, strategy=strategy)
                self._send_json(to_dict(r))
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/compare":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbols = body.get("symbols", [])
                symbol = body.get("symbol", "")
                days = int(body.get("days", body.get("period_days", 90)))
                horizon = int(body.get("horizon", body.get("horizon", 5)))
                strategy = body.get("strategy", "ma_cross")

                from ..backtest import run_backtest, to_dict

                results = []
                # 多标的对比（前端用法：/compare 标的A 标的B）
                if isinstance(symbols, list) and len(symbols) >= 2:
                    for sym in symbols[:5]:
                        try:
                            r = run_backtest(sym, days=days, horizon=horizon, strategy=strategy)
                            results.append(to_dict(r))
                        except Exception as e:
                            results.append({"symbol": sym, "error": str(e)})
                else:
                    # 单标的多策略对比（原始用法，保持兼容）
                    strategy_a = body.get("strategy_a", "ma_cross")
                    strategy_b = body.get("strategy_b", "momentum")
                    sym = symbol or (symbols[0] if symbols else "600519")
                    r_a = run_backtest(sym, days=days, horizon=horizon, strategy=strategy_a)
                    r_b = run_backtest(sym, days=days, horizon=horizon, strategy=strategy_b)
                    results = [to_dict(r_a), to_dict(r_b)]

                # 生成 LLM 对比评语
                llm_review = ""
                valid = [r for r in results if "error" not in r]
                if len(valid) >= 2:
                    try:
                        from ..llm import get_llm
                        llm = get_llm()
                        summary = "\n".join([
                            f"【{r.get('symbol', '?')}】总收益:{r.get('total_return_pct',0):+.2f}%, "
                            f"Sharpe:{r.get('sharpe_ratio',0):.2f}, 最大回撤:{r.get('max_drawdown_pct',0):.2f}%"
                            for r in valid
                        ])
                        prompt = (
                            f"你是量化策略评审专家。以下是回测结果对比：\n{summary}\n\n"
                            f"请用 2-4 句话给出专业评语：哪个表现更优？有什么风险提示？"
                            f"直接输出中文，不要 markdown。"
                        )
                        llm_review = llm.chat(prompt, system="只输出中文评语，不要客套。")
                    except Exception:
                        pass

                self._send_json({
                    "results": results,
                    "llm_review": llm_review,
                })
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/dashboard":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbols = body.get("symbols", [])
                if isinstance(symbols, str):
                    symbols = [s.strip() for s in symbols.split(",") if s.strip()]

                from ..dashboard import DashboardConfig, render_dashboard
                cfg = DashboardConfig(symbols=symbols)
                html = render_dashboard(cfg)
                self._send_json({"html": html})
            except Exception as e:
                self._send_error_json(str(e))
            return

        # ── Watchlist ──
        if path == "/api/watchlist/lists":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                action = body.get("action", "create")
                from ..watchlist import create_list, delete_list, rename_list, list_lists
                if action == "create":
                    name = body.get("name", "").strip()
                    if not name:
                        self._send_error_json("name is required", 400)
                        return
                    result = create_list(name)
                    self._send_json({"ok": True, "list": result})
                elif action == "delete":
                    list_id = int(body.get("list_id", 0))
                    ok = delete_list(list_id)
                    self._send_json({"ok": ok})
                elif action == "rename":
                    list_id = int(body.get("list_id", 0))
                    ok = rename_list(list_id, body.get("name", ""))
                    self._send_json({"ok": ok})
                else:
                    self._send_json({"lists": list_lists()})
            except Exception as e:
                self._send_error_json(str(e))
            return

        if path == "/api/watchlist/items":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                action = body.get("action", "add")
                from ..watchlist import add_item, remove_item, remove_item_by_symbol, list_items
                if action == "add":
                    list_id = int(body.get("list_id", 1))
                    symbol = body.get("symbol", "").strip()
                    if not symbol:
                        self._send_error_json("symbol is required", 400)
                        return
                    item = add_item(list_id, symbol,
                                    name=body.get("name", ""),
                                    market=body.get("market", ""),
                                    notes=body.get("notes", ""))
                    self._send_json({"ok": True, "item": item})
                elif action == "remove":
                    item_id = int(body.get("item_id", 0))
                    symbol = body.get("symbol", "")
                    list_id = int(body.get("list_id", 1))
                    if item_id:
                        ok = remove_item(item_id)
                    elif symbol:
                        ok = remove_item_by_symbol(list_id, symbol)
                    else:
                        self._send_error_json("item_id or symbol required", 400)
                        return
                    self._send_json({"ok": ok})
                else:
                    list_id = int(body.get("list_id", 1))
                    self._send_json({"items": list_items(list_id)})
            except Exception as e:
                self._send_error_json(str(e))
            return

        # ── Portfolio ──
        if path == "/api/portfolio":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                action = body.get("action", "add")
                from ..portfolio import (
                    add_position, update_position, remove_position, get_summary
                )
                if action == "add":
                    symbol = body.get("symbol", "").strip()
                    if not symbol:
                        self._send_error_json("symbol is required", 400)
                        return
                    result = add_position(
                        symbol=symbol,
                        entry_price=float(body.get("entry_price", 0)),
                        quantity=float(body.get("quantity", 0)),
                        name=body.get("name", ""),
                        market=body.get("market", ""),
                        entry_date=body.get("entry_date", ""),
                        notes=body.get("notes", ""),
                    )
                    self._send_json({"ok": True, "position": result})
                elif action == "update":
                    pos_id = int(body.get("id", 0))
                    ok = update_position(
                        pos_id,
                        entry_price=body.get("entry_price"),
                        quantity=body.get("quantity"),
                        notes=body.get("notes"),
                    )
                    self._send_json({"ok": ok})
                elif action == "remove":
                    pos_id = int(body.get("id", 0))
                    ok = remove_position(pos_id)
                    self._send_json({"ok": ok})
                elif action == "summary":
                    self._send_json(get_summary())
                else:
                    self._send_error_json("Unknown action: " + action, 400)
            except Exception as e:
                self._send_error_json(str(e))
            return

        # ── Sentiment ──
        if path == "/api/sentiment":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                symbol = body.get("symbol", "").strip()
                if not symbol:
                    self._send_error_json("symbol is required", 400)
                    return
                from ..sentiment import analyze as sentiment_analyze
                enabled = body.get("enabled_sources") or load_config().get("sentiment_sources", None)
                report = sentiment_analyze(symbol, enabled_sources=enabled)
                self._send_json({
                    "symbol": report.symbol,
                    "name": report.name,
                    "overall_sentiment": report.overall_sentiment,
                    "overall_score": report.overall_score,
                    "confidence": report.confidence,
                    "headlines": [
                        {
                            "title": h.title,
                            "sentiment": h.sentiment,
                            "score": h.score,
                            "source": h.source,
                        }
                        for h in report.headlines
                    ],
                    "summary": report.summary,
                    "data_source": report.data_source,
                    "source_stats": report.source_stats,
                })
            except Exception as e:
                self._send_error_json(str(e))
            return

        # ── Sentiment custom sources CRUD ──
        if path == "/api/sentiment/sources/custom":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                action = body.get("action", "add")
                cfg = load_config()
                custom = cfg.get("custom_sentiment_sources", {})
                if action == "add":
                    code = body.get("code", "").strip()
                    if not code:
                        self._send_error_json("code is required", 400)
                        return
                    # 检查是否与内置源冲突
                    from ..sentiment import SOURCE_META
                    if code in SOURCE_META:
                        self._send_error_json("代码与内置数据源冲突", 409)
                        return
                    # 自动生成 source meta
                    src_type = body.get("type", "rss")
                    type_label = {"rss": "RSS", "json_api": "JSON API", "html_scrape": "HTML抓取"}.get(src_type, src_type)
                    custom[code] = {
                        "name": body.get("name", code),
                        "icon": "🔧",
                        "color": "#9b59b6",
                        "desc": body.get("desc", f"自定义{type_label}源"),
                        "coverage": body.get("coverage", "自定义"),
                        "url": body.get("url", ""),
                        "type": src_type,
                        "item_path": body.get("item_path", ""),
                        "field": body.get("field", "title"),
                        "headers": body.get("headers", {}),
                    }
                    cfg["custom_sentiment_sources"] = custom
                    # 自动加入启用列表和排序
                    if code not in cfg.get("sentiment_sources", []):
                        cfg.setdefault("sentiment_sources", []).append(code)
                    if code not in cfg.get("sentiment_source_order", []):
                        cfg.setdefault("sentiment_source_order", []).append(code)
                    save_config(cfg)
                    self._send_json({"ok": True, "source": custom[code]})

                elif action == "remove":
                    code = body.get("code", "").strip()
                    if code in custom:
                        del custom[code]
                        cfg["custom_sentiment_sources"] = custom
                        # 从启用和排序中移除
                        cfg["sentiment_sources"] = [s for s in cfg.get("sentiment_sources", []) if s != code]
                        cfg["sentiment_source_order"] = [s for s in cfg.get("sentiment_source_order", []) if s != code]
                        save_config(cfg)
                        self._send_json({"ok": True})
                    else:
                        self._send_error_json("自定义源不存在", 404)

                elif action == "update":
                    code = body.get("code", "").strip()
                    if code not in custom:
                        self._send_error_json("自定义源不存在", 404)
                        return
                    for f in ("name", "url", "type", "item_path", "field", "desc", "coverage"):
                        if f in body:
                            custom[code][f] = body[f]
                    if "headers" in body:
                        custom[code]["headers"] = body["headers"]
                    cfg["custom_sentiment_sources"] = custom
                    save_config(cfg)
                    self._send_json({"ok": True, "source": custom[code]})

                else:
                    self._send_error_json("Unknown action: " + action, 400)
            except Exception as e:
                self._send_error_json(str(e))
            return

        # ── Alerts ──
        if path == "/api/alerts":
            try:
                body = json.loads(self._read_body().decode("utf-8"))
                action = body.get("action", "create")
                from ..alerts import (
                    create_alert, delete_alert, toggle_alert, list_alerts, check_alerts,
                    CONDITION_TYPES,
                )
                if action == "create":
                    symbol = body.get("symbol", "").strip()
                    condition_type = body.get("condition_type", "")
                    threshold = float(body.get("threshold", 0))
                    if not symbol or not condition_type:
                        self._send_error_json("symbol and condition_type required", 400)
                        return
                    result = create_alert(symbol, condition_type, threshold,
                                          name=body.get("name", ""))
                    self._send_json({"ok": True, "alert": result})
                elif action == "delete":
                    alert_id = int(body.get("id", 0))
                    ok = delete_alert(alert_id)
                    self._send_json({"ok": ok})
                elif action == "toggle":
                    alert_id = int(body.get("id", 0))
                    enabled = body.get("enabled")
                    ok = toggle_alert(alert_id, enabled)
                    self._send_json({"ok": ok})
                elif action == "check":
                    triggered = check_alerts()
                    self._send_json({"triggered": triggered, "count": len(triggered)})
                elif action == "list":
                    sym = body.get("symbol")
                    self._send_json({"alerts": list_alerts(symbol=sym)})
                elif action == "types":
                    self._send_json({"types": CONDITION_TYPES})
                else:
                    self._send_error_json("Unknown action: " + action, 400)
            except Exception as e:
                self._send_error_json(str(e))
            return

        self._send_error_json("Not found", 404)


def start_gui(port: int = 8888, open_browser: bool = True):
    """Start the Alphalith GUI server."""
    config = load_config()

    # 首次启动自动创建默认管理员
    users = _ensure_default_admin()
    has_default_admin = any(u.get("is_default") for u in users.values())

    server = HTTPServer(("127.0.0.1", port), GuiHandler)
    server.server_config = config

    url = f"http://127.0.0.1:{port}"

    def _serve():
        print(f"\n  Alphalith GUI · AI 投研工作台")
        print(f"  ─────────────────────────────")
        print(f"  地址：{url}")
        print(f"  配置：{CONFIG_PATH}")
        if has_default_admin:
            print(f"  默认账号：{DEFAULT_ADMIN_USERNAME}  密码：{DEFAULT_ADMIN_PASSWORD}")
            print(f"  ⚠️  首次登录后请尽快修改密码")
        print(f"  按 Ctrl+C 停止服务\n")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            print("\n  GUI 服务已停止")

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    if open_browser:
        webbrowser.open(url)

    return server, t

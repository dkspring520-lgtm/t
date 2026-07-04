#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local web dashboard for the A-share T monitor."""

from __future__ import annotations

import json
import hashlib
import hmac
import html as html_lib
import os
import random
import re
import secrets
import subprocess
import sys
import concurrent.futures
import contextvars
import time as time_mod
import urllib.parse
import urllib.request
from datetime import datetime, time, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))
SIM_HISTORY_PATH = BASE_DIR / "simulation_history.jsonl"
SETTINGS_PATH = BASE_DIR / "dashboard_settings.json"
USER_DATA_DIR = BASE_DIR / "user_data"
ADAPTIVE_STRATEGY_PATH = BASE_DIR / "adaptive_strategy.json"
USERS_PATH = BASE_DIR / "commercial_users.json"
SESSIONS_PATH = BASE_DIR / "commercial_sessions.json"
LAST_GEMINI_ERROR = ""
MARKET_CONTEXT_CACHE: dict = {"ts": 0.0, "data": {}}
GEMINI_INTRADAY_CACHE: dict[str, dict] = {}
URGENT_NEWS_CACHE: dict[str, dict] = {}
SESSIONS: dict[str, str] = {}
SESSION_EXPIRES: dict[str, float] = {}
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
REQUEST_EMAIL: contextvars.ContextVar[str] = contextvars.ContextVar("request_email", default="")
LOGIN_FAILURES: dict[str, dict] = {}
MAX_LOGIN_FAILURES = 6
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_LOCK_SECONDS = 15 * 60

TASKS = {
    "start_all": [
        [sys.executable, "seconds_monitor_ctl.py", "start"],
        [sys.executable, "seconds_monitor_ctl.py", "status"],
        [sys.executable, "global_events_push.py"],
    ],
    "signal": [[sys.executable, "dabao_trader_dual.py"]],
    "simulate": [[sys.executable, "simulate_t_random.py", "10", "--cash", "100000", "--per-trade", "20000"]],
    "simulate5": [[sys.executable, "simulate_t_random.py", "10", "--cash", "100000", "--per-trade", "20000", "--days", "5"]],
    "wechat": [[sys.executable, "notify.py", "--self-test"]],
    "cron": [["schtasks", "/Query", "/FO", "LIST"]],
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        email = current_email(self)
        REQUEST_EMAIL.set(email)
        if self._requires_login(path) and not email:
            if path.startswith("/api/"):
                self._send_json({"ok": False, "loggedIn": False, "message": "请先登录后使用。"}, status=401)
            else:
                self._redirect(f"/login?next={urllib.parse.quote(self.path or '/', safe='')}")
            return
        if path in {"/", "/index.html"}:
            self._send_html(HTML)
            return
        if path == "/landing":
            self._send_html(LANDING_HTML)
            return
        if path == "/commercial":
            self._send_html(COMMERCIAL_HTML)
            return
        if path == "/login":
            self._send_html(AUTH_HTML.replace("__MODE__", "login"))
            return
        if path == "/register":
            self._send_html(AUTH_HTML.replace("__MODE__", "register"))
            return
        if path == "/account":
            self._send_html(ACCOUNT_HTML)
            return
        if path == "/research":
            self._send_html(RESEARCH_HTML)
            return
        if path == "/simulation":
            self._send_html(SIMULATION_HTML)
            return
        if path == "/api/status":
            self._send_json({"ok": True, "message": "就绪"})
            return
        if path == "/api/realtime":
            self._send_json(realtime_payload(email))
            return
        if path == "/api/premarket":
            query = parse_qs(parsed_url.query)
            code = (query.get("code") or [""])[0]
            self._send_json(premarket_payload(code, email))
            return
        if path == "/api/watchlist":
            self._send_json(watchlist_payload(email))
            return
        if path == "/api/wechat_messages":
            self._send_json(wechat_messages_payload())
            return
        if path == "/api/screener":
            query = parse_qs(parsed_url.query)
            mode = (query.get("mode") or ["local"])[0]
            self._send_json(screener_payload_v2(mode))
            return
        if path == "/api/single_research":
            query = parse_qs(parsed_url.query)
            code = (query.get("code") or [""])[0]
            mode = (query.get("mode") or ["local"])[0]
            self._send_json(single_research_payload(code, mode))
            return
        if path == "/api/gemini_status":
            self._send_json(gemini_status_payload())
            return
        if path == "/api/gemini_intraday":
            query = parse_qs(parsed_url.query)
            code = (query.get("code") or [""])[0]
            self._send_json(gemini_intraday_payload(code))
            return
        if path == "/api/settings":
            self._send_json(load_dashboard_settings(email))
            return
        if path == "/api/account":
            self._send_json(account_payload(self))
            return
        if path == "/api/simulation_history":
            self._send_json({"ok": True, "history": aggregate_sim_history(), "runs": recent_sim_history(), "latest": latest_sim_result()})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        email = current_email(self)
        REQUEST_EMAIL.set(email)
        if self._requires_login(path) and not email:
            self._send_json({"ok": False, "loggedIn": False, "message": "请先登录后使用。"}, status=401)
            return
        if path.startswith("/api/run/"):
            name = path.rsplit("/", 1)[-1]
            self._send_json(run_task(name, self._read_json()))
            return
        if path == "/api/settings":
            self._send_json(save_dashboard_settings(self._read_json(), email))
            return
        if path == "/api/watchlist":
            self._send_json(save_watchlist_payload(self._read_json(), email))
            return
        if path == "/api/register":
            payload = register_payload(self, self._read_json())
            if payload is not None:
                self._send_json(payload)
            return
        if path == "/api/login":
            payload = login_payload(self, self._read_json())
            if payload is not None:
                self._send_json(payload)
            return
        if path == "/api/logout":
            logout_payload(self)
            return
        self.send_error(404)

    def log_message(self, _format: str, *args: object) -> None:
        return

    def _requires_login(self, path: str) -> bool:
        public_paths = {"/landing", "/login", "/register", "/account", "/api/account", "/api/login", "/api/register", "/api/logout", "/api/status"}
        if path in public_paths:
            return False
        return path in {"/", "/index.html", "/commercial", "/research", "/simulation"} or path.startswith("/api/")

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8", "replace")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def account_strategy_path(email: str | None = None) -> Path:
    return user_data_path(email, "adaptive_strategy.json")


def run_task(name: str, options: dict | None = None) -> dict:
    json_path = BASE_DIR / "last_sim_result.json" if name in {"simulate", "simulate5"} else None
    email = REQUEST_EMAIL.get("")
    if json_path:
        try:
            json_path.unlink()
        except OSError:
            pass
        options = dict(options or {})
        options["json_file"] = str(json_path)
        save_strategy_options(options, email)
    commands = build_commands(name, options or {})
    if not commands:
        return {"ok": False, "summary": "鏈煡浠诲姟", "detail": "", "stats": {}, "stocks": []}

    outputs: list[str] = []
    ok = True
    for cmd in commands:
        code, out = run_cmd(cmd, {"ADAPTIVE_STRATEGY_PATH": str(account_strategy_path(email))})
        outputs.append(out)
        if code != 0:
            ok = False
            break

    raw = "\n".join(part for part in outputs if part.strip())
    stats = parse_sim_stats(raw) if name in {"simulate", "simulate5"} else {}
    stocks = parse_sim_stocks(raw) if name in {"simulate", "simulate5"} else []
    if json_path:
        merge_sim_chart_data(stocks, json_path)
    if name in {"simulate", "simulate5"} and stats:
        stats["review"] = build_sim_review(stocks)
        persist_rolling_cash(options or {}, stats)
        record_sim_history(name, options or {}, stats, stocks)
        update_adaptive_strategy(stocks)
        stats["history"] = aggregate_sim_history()
    summary = summarize(name, raw, ok, stats)
    if name == "start_all":
        raw = clean_start_all_detail(raw)
    return {"ok": ok, "summary": summary, "detail": raw, "stats": stats, "stocks": stocks}


def user_data_path(email: str | None, filename: str) -> Path:
    email = str(email or REQUEST_EMAIL.get("") or "").strip().lower()
    if not email:
        return BASE_DIR / filename
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]
    USER_DATA_DIR.mkdir(exist_ok=True)
    return USER_DATA_DIR / f"{digest}_{filename}"


def load_dashboard_settings(email: str | None = None) -> dict:
    defaults = {"cash": 100000, "trade": 20000, "sample": 10}
    data = read_dashboard_settings_file(email)
    settings = {
        "cash": clamp_int(data.get("cash"), defaults["cash"], 1000, 100000000),
        "trade": clamp_int(data.get("trade"), defaults["trade"], 1000, 100000000),
        "sample": clamp_int(data.get("sample"), defaults["sample"], 1, 30),
    }
    settings.update(public_extra_settings(data))
    settings.update(load_profit_strategy_settings(email))
    settings.update(public_ai_settings(data))
    return {"ok": True, **settings}


def save_dashboard_settings(data: dict, email: str | None = None) -> dict:
    current = read_dashboard_settings_file(email)
    settings = dict(current)
    settings.update({
        "cash": clamp_int(data.get("cash"), 100000, 1000, 100000000),
        "trade": clamp_int(data.get("trade"), 20000, 1000, 100000000),
        "sample": clamp_int(data.get("sample"), 10, 1, 30),
    })
    settings.update(sanitize_extra_settings(data, current))
    settings.update(sanitize_ai_settings(data, current))
    try:
        user_data_path(email, "dashboard_settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        out = {"cash": settings["cash"], "trade": settings["trade"], "sample": settings["sample"]}
        out.update(public_ai_settings(settings))
        return {"ok": False, **out}
    save_strategy_options(data, email)
    out = {"cash": settings["cash"], "trade": settings["trade"], "sample": settings["sample"]}
    out.update(load_profit_strategy_settings(email))
    out.update(public_extra_settings(settings))
    out.update(load_profit_strategy_settings(email))
    out.update(public_ai_settings(settings))
    return {"ok": True, **out}


def sanitize_extra_settings(data: dict, current: dict) -> dict:
    keep = {}
    text_fields = {
        "marketDataApi": 220,
        "newsApi": 220,
        "quoteApi": 220,
        "customStrategy": 2200,
        "strategyMode": 32,
        "maxSignalsPerDay": 8,
        "lowBuyDev": 12,
        "highSellDev": 12,
        "signalCooldown": 8,
    }
    for key, limit in text_fields.items():
        if key in data:
            keep[key] = str(data.get(key) or "").strip()[:limit]
        elif key in current:
            keep[key] = str(current.get(key) or "").strip()[:limit]
    return keep


def public_extra_settings(data: dict) -> dict:
    return {
        "marketDataApi": str(data.get("marketDataApi") or ""),
        "newsApi": str(data.get("newsApi") or ""),
        "quoteApi": str(data.get("quoteApi") or ""),
        "customStrategy": str(data.get("customStrategy") or ""),
        "strategyMode": str(data.get("strategyMode") or "官方默认策略"),
        "maxSignalsPerDay": str(data.get("maxSignalsPerDay") or "2"),
        "lowBuyDev": str(data.get("lowBuyDev") or "-1.20"),
        "highSellDev": str(data.get("highSellDev") or "1.40"),
        "signalCooldown": str(data.get("signalCooldown") or "10"),
    }


def read_dashboard_settings_file(email: str | None = None) -> dict:
    path = user_data_path(email, "dashboard_settings.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def sanitize_ai_settings(data: dict, current: dict) -> dict:
    provider = str(data.get("aiProvider") or current.get("aiProvider") or "Gemini").strip()
    provider_alias = {
        "OpenAI兼容": "OpenAICompatible",
        "OpenAI Compatible": "OpenAICompatible",
        "OpenAI": "OpenAICompatible",
        "OpenAICompatible": "OpenAICompatible",
    }
    provider = provider_alias.get(provider, provider)
    if provider not in {"Gemini", "OpenAICompatible"}:
        provider = "Gemini"
    model_default = "gemini-2.5-flash" if provider == "Gemini" else "gpt-4o-mini"
    ai = {
        "aiProvider": provider,
        "aiModel": str(data.get("aiModel") or current.get("aiModel") or model_default).strip()[:80],
        "aiBase": str(data.get("aiBase") or current.get("aiBase") or "").strip()[:220],
        "aiProxy": str(data.get("aiProxy") or current.get("aiProxy") or "").strip()[:220],
    }
    if data.get("aiClearKey"):
        ai["aiKey"] = ""
    else:
        raw_key = str(data.get("aiKey") or "").strip()
        ai["aiKey"] = raw_key if raw_key and not raw_key.startswith("已配置") else str(current.get("aiKey") or "").strip()
    return ai


def public_ai_settings(data: dict) -> dict:
    key = str(data.get("aiKey") or "").strip()
    fallback_key = ""
    if not key:
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            fallback_key = os.environ.get(name, "").strip()
            if fallback_key:
                break
        if not fallback_key:
            env = load_desktop_env()
            fallback_key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or env.get("API_KEY") or ""
    provider = str(data.get("aiProvider") or "Gemini")
    if provider in {"OpenAI", "OpenAI Compatible", "OpenAI兼容"}:
        provider = "OpenAICompatible"
    return {
        "aiProvider": provider,
        "aiProviderLabel": "OpenAI兼容" if provider == "OpenAICompatible" else "Gemini",
        "aiModel": str(data.get("aiModel") or ("gemini-2.5-flash" if provider == "Gemini" else "gpt-4o-mini")),
        "aiBase": str(data.get("aiBase") or ""),
        "aiProxy": str(data.get("aiProxy") or ""),
        "aiKeyConfigured": bool(key or fallback_key),
        "aiKeyMasked": mask_secret(key) if key else ("已读取本机配置" if fallback_key else ""),
    }


def mask_secret(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "已配置"
    return f"已配置，尾号 {value[-4:]}"


def persist_rolling_cash(options: dict, stats: dict) -> None:
    ending_cash = money_to_float(stats.get("endingCash"))
    if ending_cash <= 0:
        return
    save_dashboard_settings(
        {
            "cash": ending_cash,
            "trade": options.get("trade") or money_to_float(stats.get("trade")),
            "sample": options.get("sample") or 10,
        }
    )


def load_users() -> dict:
    try:
        data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"users": {}}
    except Exception:
        return {"users": {}}


def save_users(data: dict) -> None:
    USERS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def legacy_hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def hash_password(password: str, salt: str, iterations: int = 210000) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${digest.hex()}"


def verify_password(password: str, user: dict) -> bool:
    stored = str(user.get("password") or "")
    salt = str(user.get("salt") or "")
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _scheme, iter_text, digest = stored.split("$", 2)
            expected = hash_password(password, salt, int(iter_text))
            return hmac.compare_digest(stored, expected)
        except Exception:
            return False
    return hmac.compare_digest(stored, legacy_hash_password(password, salt))


def maybe_upgrade_password(password: str, user: dict, store: dict) -> None:
    stored = str(user.get("password") or "")
    if stored.startswith("pbkdf2_sha256$"):
        return
    user["password"] = hash_password(password, str(user.get("salt") or ""))
    save_users(store)


def login_throttle_key(handler: Handler, email: str) -> str:
    forwarded = str(handler.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    ip = forwarded or (handler.client_address[0] if handler.client_address else "unknown")
    return f"{ip}|{email}"


def login_block_seconds(key: str) -> int:
    item = LOGIN_FAILURES.get(key)
    if not item:
        return 0
    now = time_mod.time()
    if item.get("lockUntil", 0) > now:
        return int(item["lockUntil"] - now)
    if now - item.get("firstAt", now) > LOGIN_WINDOW_SECONDS:
        LOGIN_FAILURES.pop(key, None)
    return 0


def record_login_failure(key: str) -> None:
    now = time_mod.time()
    item = LOGIN_FAILURES.get(key)
    if not item or now - item.get("firstAt", now) > LOGIN_WINDOW_SECONDS:
        item = {"firstAt": now, "count": 0, "lockUntil": 0}
    item["count"] = int(item.get("count", 0)) + 1
    if item["count"] >= MAX_LOGIN_FAILURES:
        item["lockUntil"] = now + LOGIN_LOCK_SECONDS
    LOGIN_FAILURES[key] = item


def clear_login_failures(key: str) -> None:
    LOGIN_FAILURES.pop(key, None)


def load_sessions() -> None:
    if SESSIONS:
        return
    try:
        data = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    now = time_mod.time()
    changed = False
    for token, item in (data.get("sessions") or {}).items():
        email = str(item.get("email") or "").strip().lower()
        expires = float(item.get("expires") or 0)
        if re.fullmatch(r"[a-f0-9]{48}", str(token)) and email and expires > now:
            SESSIONS[str(token)] = email
            SESSION_EXPIRES[str(token)] = expires
        else:
            changed = True
    if changed:
        save_sessions()


def save_sessions() -> None:
    now = time_mod.time()
    payload = {"sessions": {}}
    for token, email in list(SESSIONS.items()):
        expires = float(SESSION_EXPIRES.get(token) or 0)
        if expires <= now:
            SESSIONS.pop(token, None)
            SESSION_EXPIRES.pop(token, None)
            continue
        payload["sessions"][token] = {"email": email, "expires": expires}
    try:
        SESSIONS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def create_session(email: str) -> str:
    token = secrets.token_hex(24)
    SESSIONS[token] = email
    SESSION_EXPIRES[token] = time_mod.time() + SESSION_TTL_SECONDS
    save_sessions()
    return token


def remove_session(token: str) -> None:
    SESSIONS.pop(token, None)
    SESSION_EXPIRES.pop(token, None)
    save_sessions()


def current_email(handler: Handler) -> str:
    cookie = handler.headers.get("Cookie", "")
    m = re.search(r"session=([a-f0-9]+)", cookie)
    if not m:
        return ""
    load_sessions()
    token = m.group(1)
    email = SESSIONS.get(token, "")
    if not email:
        return ""
    if SESSION_EXPIRES.get(token, 0) <= time_mod.time():
        remove_session(token)
        return ""
    return email


def set_session_cookie(handler: Handler, token: str) -> None:
    handler.send_header("Set-Cookie", f"session={token}; Path=/; Max-Age={SESSION_TTL_SECONDS}; HttpOnly; SameSite=Lax")


def register_payload(handler: Handler, data: dict) -> dict | None:
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    nickname = str(data.get("nickname") or "").strip() or email.split("@")[0]
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"ok": False, "message": "请输入正确邮箱。"}
    if len(password) < 6:
        return {"ok": False, "message": "密码至少6位。"}
    store = load_users()
    users = store.setdefault("users", {})
    if email in users:
        return {"ok": False, "message": "该邮箱已注册，请直接登录。"}
    salt = secrets.token_hex(12)
    users[email] = {
        "email": email,
        "nickname": nickname,
        "salt": salt,
        "password": hash_password(password, salt),
        "plan": "体验版",
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "watchLimit": 1,
        "aiReviewLimit": 5,
    }
    save_users(store)
    token = create_session(email)
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    set_session_cookie(handler, token)
    payload = json.dumps({"ok": True, "message": "注册成功。", "account": public_user(users[email])}, ensure_ascii=False).encode("utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)
    return None


def login_payload(handler: Handler, data: dict) -> dict | None:
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    throttle_key = login_throttle_key(handler, email)
    blocked = login_block_seconds(throttle_key)
    if blocked > 0:
        return {"ok": False, "message": f"登录尝试过多，请 {max(1, blocked // 60)} 分钟后再试。"}
    store = load_users()
    users = store.get("users", {})
    user = users.get(email)
    if not user or not verify_password(password, user):
        record_login_failure(throttle_key)
        return {"ok": False, "message": "邮箱或密码不正确。"}
    clear_login_failures(throttle_key)
    maybe_upgrade_password(password, user, store)
    token = create_session(email)
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    set_session_cookie(handler, token)
    payload = json.dumps({"ok": True, "message": "登录成功。", "account": public_user(user)}, ensure_ascii=False).encode("utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)
    return None


def logout_payload(handler: Handler) -> None:
    cookie = handler.headers.get("Cookie", "")
    m = re.search(r"session=([a-f0-9]+)", cookie)
    if m:
        remove_session(m.group(1))
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Set-Cookie", "session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
    payload = json.dumps({"ok": True, "message": "已退出。"}, ensure_ascii=False).encode("utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)
    return None


def account_payload(handler: Handler) -> dict:
    email = current_email(handler)
    if not email:
        return {"ok": False, "loggedIn": False, "message": "未登录。"}
    user = load_users().get("users", {}).get(email)
    if not user:
        return {"ok": False, "loggedIn": False, "message": "账号不存在。"}
    return {"ok": True, "loggedIn": True, "account": public_user(user)}


def public_user(user: dict) -> dict:
    return {
        "email": user.get("email"),
        "nickname": user.get("nickname"),
        "plan": user.get("plan", "体验版"),
        "createdAt": user.get("createdAt"),
        "watchLimit": user.get("watchLimit", 1),
        "aiReviewLimit": user.get("aiReviewLimit", 5),
    }


def dashboard_watchlist(email: str | None = None):
    try:
        from monitor_config import DEFAULT_WATCHLIST, parse_watchlist_text
    except Exception:
        return []
    data = read_dashboard_settings_file(email)
    text = str(data.get("watchlistText") or "").strip()
    stocks = parse_watchlist_text(text) if text else []
    return stocks or list(DEFAULT_WATCHLIST)


def dashboard_watchlist_text(stocks=None, email: str | None = None) -> str:
    try:
        from monitor_config import watchlist_text
        return watchlist_text(stocks or dashboard_watchlist(email))
    except Exception:
        return ""


def realtime_payload(email: str | None = None) -> dict:
    try:
        import stock_t_signal as signal_mod
        from stock_t_signal import analyze_observation, fetch_minutes, fetch_quote, _format_time, _vwap
    except Exception as exc:
        return {"ok": False, "stocks": [], "error": str(exc)}

    rows = []
    signal_mod.ADAPTIVE_STRATEGY_PATH = account_strategy_path(email)
    market_context = cached_market_context()
    for stock in dashboard_watchlist(email):
        try:
            quote = fetch_quote(stock.symbol)
            minutes = fetch_minutes(stock.symbol)
            avg = _vwap(quote, minutes) if quote else None
            signals = analyze_observation(stock)
            dev = (quote.price - avg) / avg * 100.0 if quote and avg else 0.0
            signal = signals[0] if signals else None
            signal_action = signal.action if signal else ""
            is_buy_signal = any(word in signal_action for word in ("低吸", "买入"))
            is_sell_signal = any(word in signal_action for word in ("高抛", "卖出"))
            is_open = market_is_open()
            rapid_news = rapid_news_payload(stock.name, stock.code, minutes, quote)
            agents = monitor_agents_payload(stock.name, quote, minutes, avg, dev, signal, is_open, market_context, gemini_cached_advice(stock.code))
            if rapid_news.get("active"):
                agents.insert(2, rapid_news_agent(rapid_news))
            display_signal, display_reason = news_adjusted_signal(signal.action if signal else ("观察" if is_open else "休市中"), signal.reason if signal else ("暂无高质量买卖点" if is_open else "当前休市，仅显示最近分时数据"), rapid_news)
            rows.append(
                {
                    "name": stock.name,
                    "code": stock.code,
                    "time": _format_time(quote.time_raw, minutes) if quote else "--:--",
                    "price": quote.price if quote else 0,
                    "change": quote.change_pct if quote else 0,
                    "avg": avg or 0,
                    "dev": dev,
                    "signal": display_signal,
                    "reason": display_reason,
                    "marketStatus": "交易中" if is_open else "休市中",
                    "prices": [{"time": m.time, "price": m.price} for m in minutes],
                    "buyTime": signal.time if signal and is_buy_signal else "",
                    "sellTime": signal.time if signal and is_sell_signal else "",
                    "smartMoney": smart_money_payload(quote, minutes, avg, dev),
                    "rapidNews": rapid_news,
                    "agents": agents,
                }
            )
        except Exception as exc:
            rows.append({"name": stock.name, "code": stock.code, "time": "--:--", "price": 0, "change": 0, "avg": 0, "dev": 0, "signal": "异常", "reason": str(exc)})
    return {"ok": True, "stocks": rows}


PREMARKET_FUTURES = [
    ("黄金", "GC=F", "$/oz", 1.8),
    ("白银", "SI=F", "$/oz", 0.8),
    ("铜", "HG=F", "$/lb", 2.1),
    ("WTI原油", "CL=F", "$/bbl", 0.5),
    ("布油", "BZ=F", "$/bbl", 0.4),
    ("美元指数", "DX-Y.NYB", "", -1.6),
    ("离岸人民币", "CNH=X", "", -0.6),
]


def premarket_payload(code: str = "", email: str | None = None) -> dict:
    rows = []
    market_context = cached_market_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
        futures = [pool.submit(fetch_yahoo_market, name, symbol, unit, weight) for name, symbol, unit, weight in PREMARKET_FUTURES]
        for future in concurrent.futures.as_completed(futures, timeout=12):
            item = future.result()
            if item:
                rows.append(item)
    order = {name: i for i, (name, *_rest) in enumerate(PREMARKET_FUTURES)}
    rows.sort(key=lambda row: order.get(row["name"], 99))
    target = premarket_target_stock(code, email)
    bias = premarket_stock_bias(rows, target)
    data = {"ok": True, "updatedAt": datetime.now().strftime("%m-%d %H:%M"), "rows": rows, "target": bias, "zijin": bias}
    MARKET_CONTEXT_CACHE["ts"] = datetime.now().timestamp()
    MARKET_CONTEXT_CACHE["data"] = data
    return data


def premarket_target_stock(code: str = "", email: str | None = None) -> dict:
    if code:
        try:
            from monitor_config import parse_stock_token
            stock = parse_stock_token(code)
            if stock:
                return {"name": stock.name, "code": stock.code, "symbol": stock.symbol}
        except Exception:
            pass
    try:
        stock = (dashboard_watchlist(email) or [None])[0]
        if stock:
            return {"name": stock.name, "code": stock.code, "symbol": stock.symbol}
    except Exception:
        pass
    return {"name": "紫金矿业", "code": "601899", "symbol": "sh601899"}


def cached_market_context() -> dict:
    cached = MARKET_CONTEXT_CACHE.get("data") or {}
    if isinstance(cached, dict):
        return cached
    return {}


def rapid_news_payload(name: str, code: str, minutes: list, quote) -> dict:
    prices = [float(getattr(m, "price", 0) or 0) for m in minutes if float(getattr(m, "price", 0) or 0) > 0]
    if len(prices) < 6:
        return {"active": False}
    price = float(getattr(quote, "price", 0) or prices[-1] or 0)
    if price <= 0:
        return {"active": False}
    change5 = (price - prices[-6]) / prices[-6] * 100.0 if prices[-6] else 0.0
    change10 = (price - prices[-11]) / prices[-11] * 100.0 if len(prices) >= 11 and prices[-11] else change5
    if abs(change5) < 1.1 and abs(change10) < 1.8:
        return {"active": False, "change5": change5, "change10": change10}
    direction = "快速拉升" if change5 > 0 or change10 > 0 else "快速下跌"
    news = fetch_urgent_stock_news(name, code, direction)
    return {
        "active": True,
        "direction": direction,
        "change5": round(change5, 2),
        "change10": round(change10, 2),
        **news,
    }


def fetch_urgent_stock_news(name: str, code: str, direction: str) -> dict:
    cache_key = f"{code}:{direction}"
    now_ts = datetime.now().timestamp()
    cached = URGENT_NEWS_CACHE.get(cache_key)
    if cached and now_ts - float(cached.get("ts") or 0) < 600:
        return dict(cached.get("data") or {})
    query = f"{name} {code} 公告 利好 利空 业绩 减持 增持 事故"
    titles: list[str] = []
    for url in urgent_news_urls(query):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            text = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "replace")
            for raw in re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", text, flags=re.I | re.S):
                title = html_lib.unescape((raw[0] or raw[1] or "").strip())
                title = re.sub(r"\s+", " ", title)
                if title and "Google" not in title and "Bing" not in title:
                    titles.append(title)
            if titles:
                break
        except Exception:
            continue
    result = classify_urgent_news(titles, direction)
    URGENT_NEWS_CACHE[cache_key] = {"ts": now_ts, "data": result}
    return result


def urgent_news_urls(query: str) -> list[str]:
    q = urllib.parse.quote(query)
    return [
        f"https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        f"https://www.bing.com/news/search?q={q}&format=rss&setlang=zh-CN&cc=CN",
    ]


def classify_urgent_news(titles: list[str], direction: str) -> dict:
    bull_words = ("利好", "增持", "回购", "中标", "获批", "并购", "预增", "增长", "新高", "资源", "金价", "铜价")
    bear_words = ("利空", "减持", "处罚", "立案", "亏损", "下滑", "事故", "停产", "问询", "监管", "解禁", "债务")
    for title in titles[:8]:
        if any(word in title for word in bear_words):
            return {"bias": "偏利空", "headline": shorten_sentence(title, 46), "checked": True}
        if any(word in title for word in bull_words):
            return {"bias": "偏利好", "headline": shorten_sentence(title, 46), "checked": True}
    headline = shorten_sentence(titles[0], 46) if titles else ""
    return {"bias": "未发现突发消息", "headline": headline, "checked": bool(titles)}


def rapid_news_agent(news: dict) -> str:
    direction = news.get("direction") or "快速异动"
    change5 = float(news.get("change5") or 0)
    bias = news.get("bias") or "未发现突发消息"
    headline = news.get("headline") or "暂无可用标题"
    if direction == "快速拉升":
        action = "有利好也不追高，等回踩黄线；无利好按资金脉冲处理"
    else:
        action = "有利空先降级观察；无利空等止跌拐头和黄线确认"
    return f"消息员：{direction}，近5分钟{change5:+.2f}%；新闻核验：{bias}；{headline}；{action}"


def news_adjusted_signal(action: str, reason: str, news: dict) -> tuple[str, str]:
    if not news.get("active"):
        return action, reason
    direction = str(news.get("direction") or "快速异动")
    bias = str(news.get("bias") or "未发现突发消息")
    headline = str(news.get("headline") or "暂无可用标题")
    prefix = f"异动核验：{direction}，{bias}，{headline}。"
    buy_like = any(word in action for word in ("低吸", "买入"))
    sell_like = any(word in action for word in ("高抛", "卖出", "高位"))
    if direction == "快速下跌" and bias == "偏利空" and buy_like:
        return "消息观察", prefix + "低吸信号降级，等止跌拐头和黄线重新站稳。"
    if direction == "快速下跌" and buy_like:
        return action, prefix + "未见明确利空，但仍需二次止跌确认；" + reason
    if direction == "快速拉升" and sell_like and bias == "偏利好":
        return action, prefix + "利好配合上涨，高抛只做减仓，不做重仓反向；" + reason
    if direction == "快速拉升" and not sell_like:
        return "脉冲观察", prefix + "先不追涨，等回踩黄线或放量滞涨再判断。"
    return action, prefix + reason


def fetch_yahoo_market(name: str, symbol: str, unit: str, weight: float) -> dict | None:
    try:
        encoded = urllib.parse.quote(symbol, safe="")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=2d&interval=5m"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace"))
        result = (data.get("chart", {}).get("result") or [None])[0] or {}
        meta = result.get("meta", {})
        price = float(meta.get("regularMarketPrice") or 0)
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
        change = (price - prev) / prev * 100 if price and prev else 0.0
        ts = int(meta.get("regularMarketTime") or 0)
        quote_time = datetime.fromtimestamp(ts, timezone(timedelta(hours=8))).strftime("%m-%d %H:%M") if ts else ""
        return {"name": name, "symbol": symbol, "price": price, "change": change, "unit": unit, "weight": weight, "time": quote_time, "source": "Yahoo 5分钟快照"}
    except Exception:
        return None


def zijin_premarket_bias(rows: list[dict]) -> dict:
    return premarket_stock_bias(rows, {"name": "紫金矿业", "code": "601899", "symbol": "sh601899"})


def premarket_stock_bias(rows: list[dict], target: dict) -> dict:
    stock_name = str(target.get("name") or "目标股票")
    code = str(target.get("code") or "")
    category = premarket_stock_category(stock_name, code)
    by_name = {row["name"]: row for row in rows}
    score = 50.0
    reasons: list[str] = []
    weights = premarket_factor_weights(category)
    for name in ("黄金", "白银", "铜", "WTI原油", "布油", "美元指数", "离岸人民币"):
        row = by_name.get(name)
        if not row:
            continue
        change = float(row.get("change") or 0)
        weight = float(weights.get(name, 0))
        if not weight:
            continue
        score += change * weight * 5
        if abs(change) >= (0.25 if name in {"美元指数", "离岸人民币"} else 0.5):
            direction = "利好" if change * weight > 0 else "压制"
            reasons.append(f"{name}{change:+.2f}%，{direction}{category}情绪")
    score = max(0, min(100, score))
    if score >= 68:
        signal = "偏多"
        action = f"{stock_name}开盘可重点观察，等黄线承接后再做T"
    elif score <= 42:
        signal = "偏空"
        action = f"{stock_name}开盘先防守，低于黄线不急着接"
    else:
        signal = "观望"
        action = "外盘方向不强，盘中按黄线和量能确认"
    if not reasons:
        reasons = ["外盘快照不足，先按盘中黄线和成交量判断"]
    return {"name": stock_name, "code": code, "category": category, "score": round(score, 1), "signal": signal, "action": action, "reasons": reasons[:4]}


def premarket_stock_category(name: str, code: str) -> str:
    text = f"{name}{code}"
    if any(word in text for word in ("紫金", "钼", "铜", "铝", "金", "矿", "有色", "神华", "煤", "能源", "石油")):
        return "资源周期"
    if any(word in text for word in ("隆基", "新能源", "光伏", "电池", "锂", "储能")):
        return "新能源"
    if any(word in text for word in ("电驱", "汽车", "机器人", "智能", "制造", "电机")):
        return "高端制造"
    if any(word in text for word in ("中兴", "新易盛", "通信", "电子", "芯", "数据", "软件")):
        return "科技成长"
    return "综合观察"


def premarket_factor_weights(category: str) -> dict:
    if category == "资源周期":
        return {"黄金": 1.8, "白银": 0.7, "铜": 2.1, "WTI原油": 0.4, "布油": 0.3, "美元指数": -1.5, "离岸人民币": -0.5}
    if category == "新能源":
        return {"铜": 0.8, "WTI原油": -0.3, "布油": -0.2, "美元指数": -0.7, "离岸人民币": -0.4}
    if category == "高端制造":
        return {"铜": 0.6, "WTI原油": -0.2, "美元指数": -0.6, "离岸人民币": -0.5}
    if category == "科技成长":
        return {"铜": 0.3, "美元指数": -0.8, "离岸人民币": -0.5}
    return {"铜": 0.4, "WTI原油": 0.2, "美元指数": -0.6, "离岸人民币": -0.3}


def smart_money_payload(quote, minutes: list, avg: float | None, dev: float) -> dict:
    """Phase 1 smart-money approximation from ordinary minute bars.

    This is intentionally conservative because we do not have full tick/order-book data.
    It estimates absorption, program push, and distribution from price response and volume.
    """
    if not quote or not minutes or len(minutes) < 8:
        return {"text": "主力行为：数据不足，普通行情模式。", "confidence": 20}
    prices = [m.price for m in minutes if getattr(m, "price", 0) > 0]
    vols = _bar_volume_deltas(minutes)
    if len(prices) < 8 or len(vols) < 8:
        return {"text": "主力行为：数据不足，普通行情模式。", "confidence": 20}
    recent = prices[-8:]
    recent_vol = vols[-8:]
    prev_vol = vols[-16:-8] if len(vols) >= 16 else vols[:-8]
    vol_base = sum(prev_vol) / len(prev_vol) if prev_vol else max(sum(vols) / len(vols), 1)
    vol_ratio = min(max((sum(recent_vol) / len(recent_vol)) / max(vol_base, 1), 0), 5)
    drift = (recent[-1] - recent[0]) / recent[0] * 100 if recent[0] else 0
    higher_low = min(recent[-4:]) >= min(recent[:4])
    failed_high = max(recent[-4:]) <= max(recent[:4]) * 1.002
    above_vwap = bool(avg and quote.price >= avg)

    accumulation = 30 + (22 if higher_low else 0) + (18 if dev < 0 and quote.price > min(recent) else 0) + int(vol_ratio * 5)
    program_push = 25 + (24 if drift > 0.35 else 0) + (14 if above_vwap else 0) + int(vol_ratio * 6)
    distribution = 25 + (22 if quote.change_pct > 1 and failed_high else 0) + (18 if dev > 0.8 and vol_ratio > 1.1 else 0)
    accumulation = max(0, min(100, accumulation))
    program_push = max(0, min(100, program_push))
    distribution = max(0, min(100, distribution))
    scores = {"吸筹承接": accumulation, "程序化推升": program_push, "高位派发": distribution}
    label, score = max(scores.items(), key=lambda item: item[1])
    confidence = max(35, min(78, score - 8))
    bias = "偏多" if label in {"吸筹承接", "程序化推升"} else "风险"
    return {
        "text": f"主力行为：疑似{label} {score}%｜方向{bias}｜可信度{confidence}%（普通分时降级判断）",
        "confidence": confidence,
        "accumulation": accumulation,
        "programPush": program_push,
        "distribution": distribution,
    }


def monitor_agents_payload(name: str, quote, minutes: list, avg: float | None, dev: float, signal, is_open: bool, market_context: dict | None = None, gemini_advice: dict | None = None) -> list[str]:
    if not is_open:
        ai_text = ""
        if gemini_advice:
            macro, path, action = compact_ai_advice(gemini_advice)
            ai_text = f"；Gemini参考：{macro}，路径{path}，{action}"
        return [
            "技术员：休市中，仅展示最近分时",
            "资金员：等待开盘后重新计算量价",
            "外盘员：金银铜油美元仅作次日参考" + ai_text,
            "风控员：休市不触发买卖提醒",
            "决策员：观察，盘前/盘后以大方向预案为主",
        ]
    price = float(getattr(quote, "price", 0) or 0)
    change = float(getattr(quote, "change_pct", 0) or 0)
    action = str(getattr(signal, "action", "") or "观察")
    reason = str(getattr(signal, "reason", "") or "暂无高质量买卖点")
    prices = [float(getattr(m, "price", 0) or 0) for m in minutes if float(getattr(m, "price", 0) or 0) > 0]
    day_high = max(prices) if prices else price
    day_low = min(prices) if prices else price
    lift = (price - day_low) / day_low * 100.0 if day_low > 0 else 0.0
    fade = (day_high - price) / day_high * 100.0 if day_high > 0 else 0.0
    swing = (day_high - day_low) / day_low * 100.0 if day_low > 0 else 0.0
    tech = "趋势偏强" if change > 0.8 else "趋势偏弱" if change < -0.8 else "震荡观察"
    if avg and price:
        pos = "黄线上方" if price >= avg else "黄线下方"
        tech += f"，{pos}{dev:+.2f}%，日内{day_low:.2f}-{day_high:.2f}，振幅{swing:.2f}%"
    money = "分时数据不足，先观察量价"
    vol_detail = ""
    if minutes and len(minutes) >= 8:
        vols = _bar_volume_deltas(minutes)
        recent_vol = sum(vols[-5:]) / max(1, len(vols[-5:]))
        base_vol = sum(vols[:-5]) / max(1, len(vols[:-5])) if len(vols) > 5 else recent_vol
        ratio = recent_vol / max(base_vol, 1)
        recent_prices = prices[-6:] if len(prices) >= 6 else prices
        recent_change = (recent_prices[-1] - recent_prices[0]) / recent_prices[0] * 100.0 if len(recent_prices) >= 2 and recent_prices[0] else 0.0
        vol_detail = f"近5分钟量比{ratio:.2f}x，价格{recent_change:+.2f}%"
        if ratio >= 1.4:
            money = f"成交放大，{vol_detail}，若放量滞涨要防高位派发"
        elif ratio >= 0.8:
            money = f"量能正常，{vol_detail}，看能否站稳黄线"
        else:
            money = f"量能收缩，{vol_detail}，谨防无量冲高或假突破"
    futures = market_link_payload(name, market_context)
    ai_macro = ""
    ai_path = ""
    ai_action = ""
    if gemini_advice:
        ai_macro, ai_path, ai_action = compact_ai_advice(gemini_advice)
        if ai_macro or ai_path:
            futures += f"；Gemini大方向：{ai_macro or '观察'}，路径：{ai_path or '待确认'}"
    tradable = any(word in action for word in ("低吸", "高抛", "买入", "卖出"))
    if "卖" in action or "高抛" in action:
        risk = f"卖出信号后不追高，若跌回黄线/计划价再接；当前距日高回落{fade:.2f}%"
    elif "买" in action or "低吸" in action:
        risk = f"买入信号先看止跌，不把做T变补仓；当前距日低反弹{lift:.2f}%"
    else:
        risk = "未达强信号，避免频繁提醒"
    decision = f"{action}｜{reason}"
    if ai_action:
        decision += f"｜AI意见：{ai_action[:80]}"
    if tradable:
        decision += "｜提醒已按同方向限频，防止刷屏"
    return [
        f"技术员：{tech}",
        f"资金员：{money}",
        f"外盘员：{futures}",
        f"风控员：{risk}",
        f"决策员：{decision}",
    ]


def compact_ai_advice(gemini_advice: dict) -> tuple[str, str, str]:
    analysis = gemini_advice.get("analysis") or {}
    macro = str(analysis.get("macroView") or "观察").strip()
    path_value = analysis.get("pathForecast") or "待确认"
    if isinstance(path_value, dict):
        path = str(path_value.get("mostLikely") or path_value.get("主路径") or next(iter(path_value.values()), "待确认"))
    else:
        path = str(path_value)
    action = str(analysis.get("action") or "等待价格验证").strip()
    return shorten_sentence(macro, 42), shorten_sentence(path, 46), shorten_sentence(action, 36)


def shorten_sentence(text: str, limit: int) -> str:
    text = re.sub(r"\s+", "", str(text or "")).strip("；。 ")
    return text if len(text) <= limit else text[:limit] + "..."


def market_link_payload(name: str, market_context: dict | None) -> str:
    rows = (market_context or {}).get("rows") or []
    by_name = {row.get("name"): row for row in rows if isinstance(row, dict)}
    parts = []
    for key in ("黄金", "铜", "美元指数", "WTI原油"):
        row = by_name.get(key)
        if not row:
            continue
        change = float(row.get("change") or 0)
        parts.append(f"{key}{change:+.2f}%")
    bias = ((market_context or {}).get("zijin") or {}).get("signal") or "观望"
    if "紫金" in name or "矿" in name or "钼" in name:
        if parts:
            return f"{'，'.join(parts)}；资源股外盘倾向{bias}，盘中仍以黄线和量能确认"
        return "外盘快照暂缺，资源股先按金铜和美元方向人工复核"
    if parts:
        return f"大宗/美元背景：{'，'.join(parts)}，对非资源股只作风险偏好参考"
    return "外盘快照暂缺，先看个股分时量价"


def gemini_intraday_payload(code: str) -> dict:
    key = load_gemini_key()
    if not key:
        return {"ok": False, "message": "未读取到 Gemini Key，无法进行盘中AI研判。"}
    try:
        from monitor_config import load_watchlist, parse_stock_token
        from stock_t_signal import analyze_observation, fetch_minutes, fetch_quote, _format_time, _vwap
    except Exception as exc:
        return {"ok": False, "message": f"监控模块读取失败：{exc}"}

    parsed_stock = parse_stock_token(code)
    symbol = parsed_stock.symbol if parsed_stock else ""
    raw_code = parsed_stock.code if parsed_stock else str(code).strip()[-6:]
    watchlist = load_watchlist()
    stock = next((item for item in watchlist if item.symbol == symbol or item.code == raw_code), None)
    if stock is None:
        stock = parsed_stock
    if stock is None:
        prefix = "sh" if raw_code.startswith(("5", "6")) else "sz"
        symbol = prefix + raw_code
        stock = type("StockLike", (), {"name": stock_name_local(symbol[-6:]), "code": symbol[-6:], "symbol": symbol})()
    try:
        quote = fetch_quote(stock.symbol)
        minutes = fetch_minutes(stock.symbol)
        if not quote:
            return {"ok": False, "message": "暂时没有读取到实时行情。"}
        avg = _vwap(quote, minutes)
        signals = analyze_observation(stock)
        signal = signals[0] if signals else None
        prices = [float(m.price) for m in minutes if float(m.price) > 0]
        day_high = max(prices) if prices else float(quote.high or quote.price)
        day_low = min(prices) if prices else float(quote.low or quote.price)
        dev = (quote.price - avg) / avg * 100 if avg else 0.0
        swing = (day_high - day_low) / day_low * 100 if day_low else 0.0
        vols = _bar_volume_deltas(minutes)
        vol_ratio = 0.0
        if len(vols) >= 8:
            recent = sum(vols[-5:]) / max(1, len(vols[-5:]))
            base = sum(vols[:-5]) / max(1, len(vols[:-5])) if len(vols) > 5 else recent
            vol_ratio = recent / max(base, 1)
        market_context = cached_market_context()
        market_text = market_link_payload(stock.name, market_context)
        local_agents = monitor_agents_payload(stock.name, quote, minutes, avg, dev, signal, market_is_open(), market_context)
        compact = {
            "股票": f"{stock.name}{stock.code}",
            "时间": _format_time(quote.time_raw, minutes),
            "现价": round(float(quote.price), 3),
            "昨收": round(float(getattr(quote, "pre_close", 0) or 0), 3),
            "涨跌幅": round(float(quote.change_pct), 2),
            "黄线均价": round(float(avg or 0), 3),
            "黄线偏离": round(dev, 2),
            "日内低点": round(day_low, 3),
            "日内高点": round(day_high, 3),
            "日内振幅": round(swing, 2),
            "近5分钟量比": round(vol_ratio, 2),
            "本地信号": getattr(signal, "action", "观察") if signal else "观察",
            "本地原因": getattr(signal, "reason", "暂无高质量买卖点") if signal else "暂无高质量买卖点",
            "外盘": market_text,
            "本地多角色": local_agents,
        }
        prompt = (
            "你是A股盘前、盘中和盘后方向研判助手，先判断全天大方向和最可能路径，再判断是否适合做T。"
            "重点围绕市场风险偏好、资源品方向、黄金/铜/美元/原油、分时黄线/VWAP、量价、日内高低点。"
            "必须判断今天更像：高开低走、低开高走、冲高回落、探底回升、震荡洗盘、趋势推进、无方向。"
            "如果本地信号不是观察，必须集中讨论这个买卖点是否最优：是否太早、是否追高/接刀、是否需要等更好价格。"
            "原则：上涨到高位后，优先等回踩/反抽不过再卖；下探到低位后，优先等回踩不破/二次拐头再买；只有急转直下或急速V反才允许快速执行。"
            "请严格输出JSON，字段为 macroView, pathForecast, keyPrices, pointReview, trend, action, buyPlan, sellPlan, invalidation, agents。"
            "pathForecast要给最可能路径和备选路径；keyPrices要给支撑、压力、黄线、低吸价、高抛价。"
            "pointReview要明确写：最优/可做但不最优/放弃，并给一句原因。"
            "agents数组正好5行，分别以技术员、资金员、外盘员、风控员、决策员开头。"
            "macroView要说明大方向偏多/偏空/震荡和原因；要求中文、简洁、给具体价格；不要承诺收益；如果证据不足就说观察。数据："
            + json.dumps(compact, ensure_ascii=False)
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.15,
                "maxOutputTokens": 900,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        ai_config = load_ai_config()
        base = (ai_config.get("base") or load_desktop_env().get("GEMINI_API_BASE") or os.environ.get("GEMINI_API_BASE") or "https://generativelanguage.googleapis.com").rstrip("/")
        model = ai_config.get("model") or load_desktop_env().get("GEMINI_MODEL") or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
        req = urllib.request.Request(
            f"{base}/v1beta/models/{model}:generateContent?key={key}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        data = json.loads(gemini_open(req, timeout=9).read().decode("utf-8", "replace"))
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = parse_json_object(text)
        result = {"ok": True, "stock": compact, "analysis": parsed, "model": model, "message": "Gemini盘中研判完成。", "cachedAt": datetime.now().strftime("%H:%M")}
        GEMINI_INTRADAY_CACHE[stock.code] = {"ts": datetime.now().timestamp(), **result}
        return result
    except Exception as exc:
        return {"ok": False, "message": f"Gemini盘中研判失败：{type(exc).__name__}: {str(exc)[:260]}"}


def gemini_cached_advice(code: str, max_age_seconds: int = 30 * 60) -> dict | None:
    item = GEMINI_INTRADAY_CACHE.get(str(code)[-6:])
    if not item:
        return None
    if datetime.now().timestamp() - float(item.get("ts") or 0) > max_age_seconds:
        return None
    return item


def stock_name_local(code: str) -> str:
    return {
        "601899": "紫金矿业",
        "601012": "隆基绿能",
        "600580": "卧龙电驱",
        "603993": "洛阳钼业",
        "000063": "中兴通讯",
        "300502": "新易盛",
    }.get(str(code)[-6:], str(code)[-6:])


def _bar_volume_deltas(minutes: list) -> list[float]:
    values = [float(getattr(bar, "volume_lot", 0) or 0) for bar in minutes]
    if not values:
        return []
    decreases = sum(1 for prev, cur in zip(values, values[1:]) if cur < prev)
    if decreases >= max(2, len(values) // 5):
        return [max(v, 0.0) for v in values]
    out: list[float] = []
    last = 0.0
    for value in values:
        delta = max(value - last, 0.0)
        if not out and delta == 0 and value > 0:
            delta = value
        out.append(delta)
        last = max(last, value)
    return out


def watchlist_payload(email: str | None = None) -> dict:
    try:
        from monitor_config import stock_to_dict

        stocks = dashboard_watchlist(email)
        return {"ok": True, "stocks": [stock_to_dict(s) for s in stocks], "text": dashboard_watchlist_text(stocks, email)}
    except Exception as exc:
        return {"ok": False, "stocks": [], "text": "", "error": str(exc)}


def save_watchlist_payload(data: dict, email: str | None = None) -> dict:
    try:
        from monitor_config import parse_watchlist_text, stock_to_dict

        stocks = parse_watchlist_text(str(data.get("text") or ""))
        if not stocks:
            stocks = dashboard_watchlist(email)
        text = dashboard_watchlist_text(stocks, email)
        current = read_dashboard_settings_file(email)
        current["watchlistText"] = text
        user_data_path(email, "dashboard_settings.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "stocks": [stock_to_dict(s) for s in stocks], "text": text}
    except Exception as exc:
        return {"ok": False, "stocks": [], "text": "", "error": str(exc)}


def market_is_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    current = now.time()
    return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)


def wechat_messages_payload() -> dict:
    log_path = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "logs" / "gateway.log"
    messages = []
    if not log_path.exists():
        return {"ok": False, "messages": [], "error": "未找到微信日志"}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
    except Exception as exc:
        return {"ok": False, "messages": [], "error": str(exc)}
    pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*platform=weixin.*msg='(.*?)'")
    for line in lines:
        m = pattern.search(line)
        if m:
            messages.append({"time": m.group(1)[5:16], "text": m.group(2)})
    return {"ok": True, "messages": messages[-8:]}


def screener_payload() -> dict:
    """Legacy local screener kept for compatibility; /api/screener uses v2."""
    try:
        from simulate_t_random import fallback_stock_pool
    except Exception as exc:
        return {"ok": False, "stocks": [], "error": str(exc)}

    rows = []
    for stock in fallback_stock_pool()[:30]:
        quote = fast_quote(stock.symbol)
        change = float(quote.get("change") or 0.0)
        amount = float(quote.get("amount") or 0.0)
        category = stock_category(stock.name)
        score = category["base"] + (2 if change >= 2 else 1 if change >= 0.5 else -1 if change <= -2.5 else 0)
        score += 2 if amount >= 800000 else 1 if amount >= 200000 else 0
        rows.append({
            "name": stock.name,
            "code": stock.code,
            "category": category["name"],
            "price": float(quote.get("price") or 0.0),
            "change": change,
            "score": max(score, 0),
            "reasons": [category["reason"], "本地多因子初筛"],
            "agents": ["技术员：等待放量确认", "风控员：控制仓位", "决策员：本地规则初筛"],
        })
    rows.sort(key=lambda item: (-item["score"], item["category"], item["code"]))
    return {"ok": True, "aiEnabled": False, "aiStatus": "local", "aiMessage": "本地多因子规则已生成。", "stocks": rows[:18]}


def fast_quote(symbol: str) -> dict:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        raw = opener.open(f"http://qt.gtimg.cn/q={symbol}", timeout=1.4).read()
        txt = raw.decode("gb18030", "replace").strip()
        if "~" not in txt:
            return {}
        s = txt.split("~")
        return {
            "name": s[1],
            "price": float(s[3]),
            "change": float(s[32]),
            "amount": float(s[37]) if len(s) > 37 and s[37] else 0.0,
        }
    except Exception:
        return {}


def screener_payload_v2(mode: str = "review") -> dict:
    review_mode = mode in {"review", "uzi", "agents", "local"}
    use_ai = mode in {"gemini", "ai"}
    rows = local_screener_rows(aggressive=False, review=True)
    ai_configured = ai_research_configured()
    ai_enabled = apply_ai_agents_fast(rows[:10]) if use_ai and ai_configured else False
    if ai_enabled:
        ai_status = "enabled"
        ai_message = "AI选股已完成：已生成候选股的多Agent摘要。"
    elif use_ai and ai_configured:
        ai_status = "timeout_or_error"
        ai_message = f"AI本次超时或返回异常，已切回评审选股。原因：{LAST_GEMINI_ERROR or '返回内容不可解析或超时'}"
    elif use_ai:
        ai_status = "not_configured"
        ai_message = "未配置AI Key或第三方中转地址，当前使用评审选股。"
    else:
        ai_status = "review_panel" if review_mode else "review_panel"
        ai_message = "评审选股已完成：TradingAgents + UZI评审 + Kronos路径因子共同筛选10只。"
    for row in rows:
        row["agents"] = normalize_local_agents(row.get("agents", []))
    return {
        "ok": True,
        "mode": "ai" if use_ai else "review",
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "aiEnabled": ai_enabled,
        "aiStatus": ai_status,
        "aiMessage": ai_message,
        "geminiConfigured": ai_configured,
        "aiConfigured": ai_configured,
        "geminiError": LAST_GEMINI_ERROR,
        "stocks": rows[:10],
    }


def single_research_payload(code: str, mode: str = "review") -> dict:
    try:
        from monitor_config import parse_stock_token
    except Exception as exc:
        return {"ok": False, "message": f"单股研究初始化失败：{exc}", "stock": None}
    stock = parse_stock_token(code)
    if not stock:
        return {"ok": False, "message": "请输入6位股票代码，例如 600580。", "stock": None}
    use_ai = mode in {"gemini", "ai"}
    row = research_row_for_stock(stock, aggressive=False, single=True)
    message = f"{row['name']} {row['code']} 单股研究已完成。"
    if use_ai:
        if ai_research_configured() and apply_ai_agents_fast([row]):
            message = f"{row['name']} {row['code']} AI研究已完成。"
        elif ai_research_configured():
            message = f"{row['name']} {row['code']} AI暂未返回，已使用评审研究。"
        else:
            message = f"{row['name']} {row['code']} 未配置AI，已使用评审研究。"
    return {
        "ok": True,
        "message": message,
        "updatedAt": datetime.now().strftime("%m-%d %H:%M"),
        "stock": row,
    }


def research_row_for_stock(stock, aggressive: bool = False, single: bool = False) -> dict:
    quote = fast_quote(stock.symbol)
    name = str(quote.get("name") or stock.name)
    change = float(quote.get("change") or 0.0)
    amount = float(quote.get("amount") or 0.0)
    category = stock_category(f"{name}{stock.code}")
    daily_profile = daily_stock_analysis_profile(name, category["name"], change, amount)
    trend_score = 2 if change >= 2 else 1 if change >= 0.5 else -1 if change <= -2.5 else 0
    money_score = money_activity_score(amount)
    risk_penalty = 1 if abs(change) >= 6 else 0
    medium = medium_term_profile(name, category["name"], change, amount, daily_profile)
    growth = aggressive_growth_profile(stock.code, name, category["name"], change, amount) if aggressive else {"score": 0, "label": "评审", "logic": "多Agent评审筛选", "risk": "按趋势和成交量确认"}
    score = max(category["base"] + trend_score + money_score + daily_profile["score"] + medium["score"] + growth["score"] - risk_penalty, 0)
    risk = "波动偏大，轻仓观察" if risk_penalty else "等待量价确认"
    tech = "强势跟踪" if change >= 1 else "等待放量" if change >= -2.5 else "先等止跌"
    tier = research_tier(score, change, amount, category["name"], medium["score"])
    forecast = three_month_forecast(medium["score"], score, change, amount, category["name"], aggressive=aggressive, growth_score=growth["score"])
    action = "观察"
    if score >= 11 and medium["score"] >= 7:
        action = "重点跟踪"
    elif score >= 8:
        action = "加入候选"
    elif change <= -3:
        action = "等止跌确认"
    if single:
        tier["decision"] = f"{action}｜{tier['useCase']}｜未来1-3个月看{forecast['basis']}，必须用价格和成交量确认"
    return {
        "name": name,
        "code": stock.code,
        "symbol": stock.symbol,
        "category": category["name"],
        "categoryReason": category["reason"],
        "price": float(quote.get("price") or 0.0),
        "change": change,
        "amountWan": amount,
        "amountText": format_amount_wan(amount),
        "score": score,
        "reasons": [
            category["reason"],
            f"中期逻辑：{medium['logic']}",
            f"未来催化：{medium['catalyst']}",
            f"日线分析：{daily_profile['daily']}",
            f"新闻情绪：{daily_profile['news']}",
            f"资金观察：{daily_profile['money']}",
            "单股研究：融合技术面、行业催化、资金活跃度、风险控制和3个月情景预测",
        ],
        "agents": research_agents(
            tech,
            name,
            stock.code,
            category,
            change,
            amount,
            daily_profile,
            medium,
            forecast,
            risk,
            growth["risk"] if aggressive else medium["risk"],
            tier["decision"],
        ),
        "dailyAnalysis": daily_profile,
        "mediumAnalysis": medium,
        "mediumScore": medium["score"],
        "forecast": forecast,
        "growthAnalysis": growth,
        "screenMode": "AI选股" if aggressive else "评审研究",
        "horizon": medium["horizon"],
        "tier": tier["tier"],
        "useCase": tier["useCase"],
        "decision": tier["decision"],
    }


def local_screener_rows(aggressive: bool = False, review: bool = False) -> list[dict]:
    try:
        from simulate_t_random import build_random_pool, fallback_stock_pool
    except Exception:
        return []
    try:
        pool = build_random_pool()
    except Exception:
        pool = fallback_stock_pool()
    if len(pool) > 180:
        priority_codes = {"601899", "601012", "000063", "300502", "002050", "600519", "601088", "600030", "601318"}
        priority = [] if aggressive else [stock for stock in pool if stock.code in priority_codes]
        rest = [stock for stock in pool if stock.code not in priority_codes]
        sample_size = 220 if aggressive else 130
        pool = priority + random.sample(rest, min(sample_size, len(rest)))
    else:
        random.shuffle(pool)

    def one(stock):
        return research_row_for_stock(stock, aggressive=aggressive)

    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool_exec:
        rows = list(pool_exec.map(one, pool))
    if review:
        rows = [apply_selection_review_panel(row) for row in rows]
        rows.sort(key=lambda item: (-item.get("reviewScore", 0), -item.get("mediumScore", 0), -item["score"], item["category"], item["code"]))
    elif aggressive:
        rows.sort(key=lambda item: (-item.get("growthAnalysis", {}).get("score", 0), -item["score"], item["category"], item["code"]))
    else:
        rows.sort(key=lambda item: (-item.get("mediumScore", 0), -item["score"], item["category"], item["code"]))
    return diversify_screener_rows(rows, limit=10, aggressive=aggressive or review)


def research_agents(
    tech: str,
    name: str,
    code: str,
    category: dict,
    change: float,
    amount: float,
    daily_profile: dict,
    medium: dict,
    forecast: dict,
    risk: str,
    risk_detail: str,
    decision: str,
) -> list[str]:
    cat = category["name"]
    if change >= 2.5:
        tech_detail = f"短线偏强但不追高，等回踩黄线或放量突破确认，当前涨跌{change:+.2f}%"
    elif change >= 0.3:
        tech_detail = f"温和走强，适合观察是否沿均线抬升，当前涨跌{change:+.2f}%"
    elif change <= -2.5:
        tech_detail = f"弱势回落，先等止跌K线和量能收缩，当前涨跌{change:+.2f}%"
    else:
        tech_detail = f"震荡区间，方向未选出，当前涨跌{change:+.2f}%"

    news_map = {
        "资源周期": "重点跟踪金价、铜价、美元、矿山并购和海外扰动",
        "AI科技": "重点跟踪算力订单、光模块、服务器链和海外科技股反馈",
        "新能源": "重点跟踪产业链价格、装机、储能和政策边际变化",
        "消费医药": "重点跟踪业绩兑现、集采政策、创新药/医疗需求和估值修复",
        "金融蓝筹": "重点跟踪成交额、政策预期、指数方向和利率环境",
    }
    basic_map = {
        "资源周期": "周期弹性强，核心变量是商品价格和产能兑现",
        "AI科技": "弹性来自景气预期，必须防估值和拥挤交易风险",
        "新能源": "行业分化明显，只选量价改善和业绩修复方向",
        "消费医药": "更看确定性、估值修复和中期业绩兑现",
        "金融蓝筹": "偏指数配置属性，适合趋势确认后跟随",
    }
    money_text = money_activity_label(amount)
    if amount >= 50000:
        money_detail = f"{money_text}，可以纳入重点观察，但避免急拉追入"
    elif amount >= 10000:
        money_detail = f"{money_text}，流动性够观察，等待连续性"
    else:
        money_detail = f"{money_text}，资金不够主动，信号需要降权"
    return [
        f"技术员：{tech}；{tech_detail}；{daily_profile['daily']}",
        f"新闻员：{news_map.get(cat, '重点跟踪公告、行业景气和政策变化')}；{daily_profile['news']}",
        f"基本面员：{basic_map.get(cat, category['reason'])}；未来1-3个月看{forecast['basis']}",
        f"资金员：{money_detail}",
        f"风控员：{risk}；{risk_detail}",
        f"决策员：{decision}",
    ]


def apply_selection_review_panel(row: dict) -> dict:
    change = float(row.get("change") or 0)
    amount = float(row.get("amountWan") or 0)
    medium_score = int(row.get("mediumScore") or 0)
    growth_score = int((row.get("growthAnalysis") or {}).get("score") or 0)
    base_score = int(row.get("score") or 0)
    category = str(row.get("category") or "综合观察")
    volatility_ok = 1 if 0.2 <= abs(change) <= 6.5 else 0
    trend_ok = 2 if change >= 0.8 else 1 if change >= -1.5 else 0
    uzi_score = max(0, min(10, medium_score + growth_score + trend_ok + volatility_ok - (2 if abs(change) >= 7 else 0)))
    trading_score = max(0, min(10, base_score // 2 + (2 if category in {"资源周期", "AI科技", "新能源", "高端制造"} else 0)))
    kronos_score = max(0, min(10, 5 + trend_ok + volatility_ok + (1 if -2.5 <= change <= 4.5 else -1)))
    review_score = round(uzi_score * 0.38 + trading_score * 0.34 + kronos_score * 0.28, 2)
    row["reviewScore"] = review_score
    row["screenMode"] = "评审团选股"
    row["tier"] = "评审通过" if review_score >= 7.2 else "观察候选" if review_score >= 5.8 else "暂缓"
    row["useCase"] = "1-3个月候选，盘中用黄线确认"
    row["decision"] = f"评审分 {review_score}/10｜{row['tier']}｜不追高，等价格和成交量确认"
    row["agents"] = [
        f"TradingAgents：行业{category}，中期分{medium_score}/10，先看催化和趋势共振",
        f"UZI评审：商业/基本面/风险综合 {uzi_score}/10，结论：{row['tier']}",
        f"Kronos路径员：选股阶段做波动适配 {kronos_score}/10，盘中再用分钟线确认路径",
        f"资金员：成交额{format_amount_wan(amount)}，当前涨跌{change:+.2f}%，避免追高，关注放量突破或回踩承接",
        f"风控员：候选不等于买入，必须经过盘中黄线/VWAP二次确认",
        f"决策员：{row['decision']}",
    ]
    row.setdefault("reasons", []).insert(0, "评审团选股：TradingAgents负责行业和资金，UZI负责深度评审，Kronos负责路径适配")
    return row


def diversify_screener_rows(rows: list[dict], limit: int = 10, aggressive: bool = False) -> list[dict]:
    """Return a compact cross-industry shortlist instead of many similar names."""
    selected: list[dict] = []
    used_codes: set[str] = set()
    category_counts: dict[str, int] = {}
    max_per_category = 3
    categories = sorted({str(row.get("category") or "综合观察") for row in rows})
    if aggressive:
        rows = [row for row in rows if row.get("growthAnalysis", {}).get("score", 0) >= 2] or rows
    for category in categories:
        best = next((row for row in rows if row.get("category") == category and row.get("code") not in used_codes), None)
        if best:
            selected.append(best)
            used_codes.add(str(best.get("code")))
            category_counts[category] = category_counts.get(category, 0) + 1
            if len(selected) >= limit:
                return selected
    for row in rows:
        code = str(row.get("code"))
        category = str(row.get("category") or "综合观察")
        if code in used_codes:
            continue
        if category_counts.get(category, 0) >= max_per_category:
            continue
        selected.append(row)
        used_codes.add(code)
        category_counts[category] = category_counts.get(category, 0) + 1
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for row in rows:
            code = str(row.get("code"))
            if code in used_codes:
                continue
            selected.append(row)
            used_codes.add(code)
            if len(selected) >= limit:
                break
    selected.sort(key=lambda item: (-item.get("mediumScore", 0), -item["score"], item["category"], item["code"]))
    return selected


def research_tier(score: int, change: float, amount: float, category: str, medium_score: int = 0) -> dict:
    if medium_score >= 7 and score >= 10:
        tier = "中期核心"
    elif score >= 9 and amount >= 50000:
        tier = "重点跟踪"
    elif score >= 7:
        tier = "观察池"
    elif score <= 3:
        tier = "暂时剔除"
    else:
        tier = "等待确认"
    if medium_score >= 7:
        use_case = "1-3月潜力"
    elif category in {"资源周期", "AI科技", "新能源"} and score >= 7:
        use_case = "适合波段跟踪"
    elif amount >= 10000 and 0.3 <= abs(change) <= 4.5:
        use_case = "适合做T观察"
    elif abs(change) >= 6:
        use_case = "只看不追"
    else:
        use_case = "等待放量"
    decision = f"{tier}｜{use_case}｜未来几个月看行业催化、趋势和资金持续性"
    return {"tier": tier, "useCase": use_case, "decision": decision}


def medium_term_profile(name: str, category: str, change: float, amount: float, daily_profile: dict) -> dict:
    """Score 1-3 month potential. This is a local pre-research score, not a promise."""
    score = 0
    horizon = "1-3个月观察"
    if category == "资源周期":
        score += 4
        logic = "资源价格、黄金铜周期和海外供给扰动可能带来中期弹性"
        catalyst = "金价、铜价、美元走弱、矿山扩产或并购进展"
        risk = "大宗商品回落、美元走强或海外矿山扰动"
    elif category == "AI科技":
        score += 4
        logic = "AI算力和通信链条仍是高弹性方向，适合跟踪订单和业绩兑现"
        catalyst = "算力订单、光模块需求、海外科技链景气"
        risk = "估值过高、业绩不及预期或板块拥挤交易"
    elif category == "新能源":
        score += 3
        logic = "新能源处在修复和分化阶段，只选有量价改善的标的"
        catalyst = "产业链价格企稳、装机改善、政策刺激"
        risk = "产能过剩、价格战和反弹无量"
    elif category == "金融蓝筹":
        score += 2
        logic = "金融权重更适合指数修复和低波动配置"
        catalyst = "政策预期、成交额放大、指数走强"
        risk = "指数缩量或宏观预期走弱"
    elif category == "消费医药":
        score += 2
        logic = "消费医药更看业绩确定性和估值修复"
        catalyst = "业绩改善、估值修复、消费或医药政策边际改善"
        risk = "需求恢复慢或业绩低于预期"
    else:
        score += 1
        logic = "综合类标的需要更强的行业消息和资金确认"
        catalyst = "公告、行业景气、资金持续流入"
        risk = "题材不清晰，容易轮动失败"

    if amount >= 50000:
        score += 2
    elif amount >= 10000:
        score += 1
    if 0.3 <= change <= 4.5:
        score += 2
    elif -2.5 <= change < 0.3:
        score += 1
    elif abs(change) >= 6:
        score -= 2
    if "偏强" in str(daily_profile.get("daily", "")) or "转强" in str(daily_profile.get("daily", "")):
        score += 1
    score = max(0, min(10, score))
    return {"score": score, "horizon": horizon, "logic": logic, "catalyst": catalyst, "risk": risk}


def aggressive_growth_profile(code: str, name: str, category: str, change: float, amount: float) -> dict:
    score = 0
    logic = "题材弹性观察"
    risk = "弹性较高，必须控制仓位"
    leader_codes = {"601899", "601012", "600519", "601088", "601318", "600036", "600030", "000858", "000333"}
    if code.startswith(("30", "68")):
        score += 3
        logic = "创业板/科创板弹性更高，适合激进池观察"
    elif code.startswith(("00", "60")):
        score += 1
    if code in leader_codes:
        score -= 2
        risk = "偏龙头稳健，不是最高弹性方向"
    if 1.2 <= change <= 5.5:
        score += 3
        logic = "价格启动但未明显过热"
    elif 0.2 <= change < 1.2:
        score += 1
        logic = "低位温和启动，等待放量"
    elif change > 7:
        score -= 2
        risk = "短线过热，谨防追高回落"
    elif change < -4:
        score -= 1
        risk = "弱势回撤，需先看止跌"
    if 100000 <= amount <= 900000:
        score += 2
    elif amount > 900000:
        score += 1
    if category in {"AI科技", "新能源", "资源周期"}:
        score += 1
    return {"score": max(0, min(10, score)), "label": "激进成长", "logic": logic, "risk": risk}


def three_month_forecast(medium_score: int, score: int, change: float, amount: float, category: str, aggressive: bool = False, growth_score: int = 0) -> dict:
    """Local 3-month scenario label. It is a research estimate, not a guaranteed return."""
    if aggressive and growth_score >= 7 and score >= 10:
        label = "高弹性"
        expected = "12%-28%"
        confidence = "中"
    elif aggressive and growth_score >= 5:
        label = "激进偏强"
        expected = "8%-20%"
        confidence = "中低"
    elif medium_score >= 8 and score >= 11 and amount >= 200000:
        label = "偏强"
        expected = "8%-18%"
        confidence = "中高"
    elif medium_score >= 7 and score >= 9:
        label = "温和偏强"
        expected = "5%-12%"
        confidence = "中"
    elif medium_score >= 5:
        label = "观察修复"
        expected = "0%-8%"
        confidence = "中低"
    else:
        label = "谨慎观察"
        expected = "-5%-5%"
        confidence = "低"
    if abs(change) >= 6:
        label = "过热观察"
        confidence = "低"
        expected = "波动较大"
    basis = "行业催化+趋势资金"
    if category == "资源周期":
        basis = "金属价格+资源扩张"
    elif category == "AI科技":
        basis = "AI算力需求+订单兑现"
    elif category == "新能源":
        basis = "产业链修复+政策催化"
    elif category == "金融蓝筹":
        basis = "指数修复+成交额"
    elif category == "消费医药":
        basis = "估值修复+业绩改善"
    return {"label": label, "expected": expected, "confidence": confidence, "basis": basis}


def money_activity_score(amount_wan: float) -> int:
    amount = float(amount_wan or 0)
    if amount >= 50000:
        return 2
    if amount >= 10000:
        return 1
    return 0


def money_activity_label(amount_wan: float) -> str:
    amount = float(amount_wan or 0)
    if amount >= 50000:
        return f"成交活跃，成交额{format_amount_wan(amount)}"
    if amount >= 10000:
        return f"成交正常，成交额{format_amount_wan(amount)}"
    if amount > 0:
        return f"成交偏弱，成交额{format_amount_wan(amount)}"
    return "成交额暂未获取，资金项降权"


def format_amount_wan(amount_wan: float) -> str:
    amount = float(amount_wan or 0)
    if amount >= 10000:
        return f"{amount / 10000:.2f}亿"
    if amount > 0:
        return f"{amount:.0f}万"
    return "--"


def daily_stock_analysis_profile(name: str, category: str, change: float, amount: float) -> dict:
    """A light local adapter inspired by daily_stock_analysis style reports.

    It keeps the research page fast and usable without depending on the external
    project's runtime. Gemini can still enhance the wording when available.
    """
    amount_level = "活跃" if amount >= 50000 else "正常" if amount >= 10000 else "偏弱"
    score = 0
    if category in {"资源周期", "AI科技", "新能源"}:
        score += 1
    if amount >= 50000:
        score += 2
    elif amount >= 10000:
        score += 1
    if change >= 2:
        daily = "日线动量偏强，适合加入强势跟踪"
        score += 2
    elif change >= 0.5:
        daily = "日线有转强迹象，等待放量确认"
        score += 1
    elif change <= -3:
        daily = "短线回撤较深，先看止跌结构"
        score -= 1
    else:
        daily = "日线趋势中性，等待方向选择"

    if category == "资源周期":
        news = "重点看黄金、铜、美元、全球资源并购和地缘风险"
        risk = "资源价格回落或美元走强时降低仓位"
    elif category == "AI科技":
        news = "重点看算力、光模块、通信订单和海外科技链消息"
        risk = "高位放量滞涨时防止情绪退潮"
    elif category == "新能源":
        news = "重点看产业链价格、装机数据和政策催化"
        risk = "反弹无量时避免左侧追涨"
    elif category == "金融蓝筹":
        news = "重点看指数共振、成交额和政策预期"
        risk = "指数走弱时只做防守观察"
    else:
        news = "重点看公告、行业消息和资金是否持续放大"
        risk = "题材不清晰时降低权重"

    money = f"成交额{format_amount_wan(amount)}，级别{amount_level}，需要继续观察连续性"
    if amount >= 50000 and change >= 0:
        money = "成交活跃且价格未弱，资金承接较好"
    elif amount >= 50000 and change < 0:
        money = "放量下跌，需确认是否为洗盘而非派发"
    return {"score": max(score, -1), "daily": daily, "news": news, "money": money, "risk": risk}


def normalize_local_agents(agents: list) -> list[str]:
    out = []
    for text in agents:
        cleaned = str(text)
        if "模型未配置" in cleaned or "Gemini" in cleaned:
            cleaned = "决策员：本地多因子初筛，等待量价确认"
        out.append(cleaned)
    return out


def gemini_status_payload() -> dict:
    ai_config = load_ai_config()
    if ai_config.get("provider") == "OpenAICompatible":
        return openai_compatible_status_payload(ai_config)
    key = load_gemini_key()
    if not key:
        return {"ok": False, "configured": False, "message": "未读取到 Gemini Key。请在设置里填写 Key，或在桌面 1.env 写入 GEMINI_API_KEY。"}
    try:
        payload = {
            "contents": [{"parts": [{"text": "只回复：正常"}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 32, "thinkingConfig": {"thinkingBudget": 0}},
        }
        ai_config = load_ai_config()
        base = (ai_config.get("base") or "https://generativelanguage.googleapis.com").rstrip("/")
        model = ai_config.get("model") or load_desktop_env().get("GEMINI_MODEL") or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
        req = urllib.request.Request(
            f"{base}/v1beta/models/{model}:generateContent?key={key}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        data = json.loads(gemini_open(req, timeout=10).read().decode("utf-8", "replace"))
        text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return {"ok": True, "configured": True, "model": model, "message": "Gemini Key 可用。", "sample": text[:80]}
    except Exception as exc:
        return {"ok": False, "configured": True, "message": f"Gemini 请求失败：{type(exc).__name__}: {str(exc)[:300]}"}


def openai_compatible_status_payload(ai_config: dict) -> dict:
    key = str(ai_config.get("key") or "").strip()
    base = normalize_openai_base(str(ai_config.get("base") or "").strip())
    model = str(ai_config.get("model") or "gpt-4o-mini").strip()
    if not base:
        return {"ok": False, "configured": bool(key), "message": "请填写第三方中转 API 地址，例如 https://你的域名/v1。"}
    if not key:
        return {"ok": False, "configured": False, "message": "请填写第三方中转 API Key。"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复：正常"}],
        "temperature": 0,
        "max_tokens": 32,
    }
    try:
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        data = json.loads(gemini_open(req, timeout=10).read().decode("utf-8", "replace"))
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "configured": True, "model": model, "message": "第三方AI中转可用。", "sample": str(text)[:80]}
    except Exception as exc:
        return {"ok": False, "configured": True, "message": f"第三方AI中转请求失败：{type(exc).__name__}: {str(exc)[:300]}"}


def normalize_openai_base(base: str) -> str:
    base = str(base or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/v1") else f"{base}/v1"


def stock_category(name: str) -> dict:
    overrides = {
        "688356": {"name": "消费医药", "base": 3, "reason": "医药高分子材料与医疗应用相关"},
    }
    for code, item in overrides.items():
        if code in name:
            return item
    if any(x in name for x in ["恒瑞", "迈瑞", "药明", "键凯", "制药", "医药", "医疗", "生物", "药", "医", "疫苗", "诊断", "器械"]):
        return {"name": "消费医药", "base": 3, "reason": "医药、医疗器械或生命科学相关"}
    if any(x in name for x in ["中际", "新易盛", "中兴", "工业富联", "中科曙光", "浪潮信息", "科技", "电子", "智能", "软件", "信息", "通信", "芯", "半导", "数据", "网络", "光电"]):
        return {"name": "AI科技", "base": 5, "reason": "AI算力/通信链条弹性较高"}
    if any(x in name for x in ["紫金", "神火", "神华", "中国铝业", "洛阳钼业", "卫星化学", "矿", "黄金", "铜", "铝", "钼", "煤", "钢", "稀土", "石化", "化学"]):
        return {"name": "资源周期", "base": 4, "reason": "黄金、铜、能源或化工周期相关"}
    if any(x in name for x in ["隆基", "宁德", "比亚迪", "赛力斯", "江淮", "阳光电源", "能源", "电气", "电池", "锂", "光伏", "风电", "储能", "新能源"]):
        return {"name": "新能源", "base": 4, "reason": "新能源与汽车产业链"}
    if any(x in name for x in ["招商", "中信", "平安", "东方财富", "天风", "银行", "证券", "保险"]):
        return {"name": "金融蓝筹", "base": 3, "reason": "金融权重，适合趋势确认"}
    if any(x in name for x in ["茅台", "五粮液", "酒", "食品", "消费"]):
        return {"name": "消费医药", "base": 3, "reason": "消费医药核心资产"}
    return {"name": "综合观察", "base": 2, "reason": "基础股票池观察标的"}


def apply_gemini_agents(rows: list[dict]) -> bool:
    return apply_ai_agents_fast(rows)


def ai_research_configured() -> bool:
    ai = load_ai_config()
    if ai.get("provider") == "OpenAICompatible":
        return bool(ai.get("key") and ai.get("base"))
    return bool(load_gemini_key())


def apply_ai_agents_fast(rows: list[dict]) -> bool:
    ai = load_ai_config()
    if ai.get("provider") == "OpenAICompatible":
        return apply_openai_compatible_agents_fast(rows)
    return apply_gemini_agents_fast(rows)


def apply_openai_compatible_agents_fast(rows: list[dict]) -> bool:
    global LAST_GEMINI_ERROR
    LAST_GEMINI_ERROR = ""
    ai = load_ai_config()
    key = str(ai.get("key") or "").strip()
    base = normalize_openai_base(str(ai.get("base") or "").strip())
    if not key or not base or not rows:
        LAST_GEMINI_ERROR = "未配置第三方AI中转地址或Key"
        return False
    compact = [
        {
            "name": row.get("name"),
            "code": row.get("code"),
            "category": row.get("category"),
            "price": row.get("price"),
            "change": row.get("change"),
            "score": row.get("score"),
            "reasons": row.get("reasons", []),
        }
        for row in rows
    ]
    prompt = (
        "你是A股多Agent选股评审团，用技术员、新闻员、基本面员、资金员、风控员、决策员视角。"
        "只返回JSON数组，每项包含code和agents数组。agents三到五行，必须中文、具体、不要模板化。候选股："
        + json.dumps(compact, ensure_ascii=False)
    )
    payload = {
        "model": str(ai.get("model") or "gpt-4o-mini"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 900,
    }
    try:
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        data = json.loads(gemini_open(req, timeout=8).read().decode("utf-8", "replace"))
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = parse_json_array(text)
    except Exception as exc:
        LAST_GEMINI_ERROR = f"{type(exc).__name__}: {str(exc)[:300]}"
        return False
    by_code = {str(item.get("code")): item for item in parsed if isinstance(item, dict)}
    updated = 0
    for row in rows:
        item = by_code.get(str(row.get("code")))
        agents = item.get("agents") if item else None
        if isinstance(agents, list) and len(agents) >= 2:
            row["agents"] = [str(value)[:160] for value in agents[:5]]
            updated += 1
    if updated <= 0:
        LAST_GEMINI_ERROR = "AI返回内容没有匹配到 code/agents JSON"
    return updated > 0


def apply_gemini_agents_fast(rows: list[dict]) -> bool:
    global LAST_GEMINI_ERROR
    LAST_GEMINI_ERROR = ""
    key = load_gemini_key()
    if not key or not rows:
        LAST_GEMINI_ERROR = "未读取到 Key 或候选股为空"
        return False
    compact = [
        {
            "name": row.get("name"),
            "code": row.get("code"),
            "category": row.get("category"),
            "price": row.get("price"),
            "change": row.get("change"),
            "score": row.get("score"),
            "reasons": row.get("reasons", []),
        }
        for row in rows
    ]
    prompt = (
        "你是A股研究员，用技术员、资金员、风控员、决策员视角做简短分析。"
        "只返回JSON数组，每项包含code和agents数组。agents正好三行，必须中文。候选股："
        + json.dumps(compact, ensure_ascii=False)
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 700,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    ai_config = load_ai_config()
    base = (ai_config.get("base") or load_desktop_env().get("GEMINI_API_BASE") or os.environ.get("GEMINI_API_BASE") or "https://generativelanguage.googleapis.com").rstrip("/")
    model = ai_config.get("model") or load_desktop_env().get("GEMINI_MODEL") or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    req = urllib.request.Request(
        f"{base}/v1beta/models/{model}:generateContent?key={key}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        data = json.loads(gemini_open(req, timeout=8).read().decode("utf-8", "replace"))
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = parse_json_array(text)
    except Exception as exc:
        LAST_GEMINI_ERROR = f"{type(exc).__name__}: {str(exc)[:300]}"
        return False
    by_code = {str(item.get("code")): item for item in parsed if isinstance(item, dict)}
    updated = 0
    for row in rows:
        item = by_code.get(str(row.get("code")))
        agents = item.get("agents") if item else None
        if isinstance(agents, list) and len(agents) >= 2:
            row["agents"] = [str(value)[:120] for value in agents[:3]]
            updated += 1
    if updated <= 0:
        LAST_GEMINI_ERROR = "Gemini 返回内容没有匹配到 code/agents JSON"
    return updated > 0


def parse_json_array(text: str) -> list:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.I | re.M).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []
    except Exception:
        pass
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return []
    data = json.loads(m.group(0))
    return data if isinstance(data, list) else []


def parse_json_object(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.I | re.M).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"raw": text[:1200]}
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {"raw": text[:1200]}
    data = json.loads(m.group(0))
    return data if isinstance(data, dict) else {"raw": text[:1200]}


def load_gemini_key() -> str:
    ai = load_ai_config()
    if ai.get("provider") == "Gemini" and ai.get("key"):
        return str(ai["key"])
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    env = load_desktop_env()
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "API_KEY"):
        value = env.get(name, "").strip()
        if value:
            return value
    return ""


def load_ai_config() -> dict:
    settings = read_dashboard_settings_file()
    provider = str(settings.get("aiProvider") or "Gemini").strip()
    if provider in {"OpenAI", "OpenAI Compatible", "OpenAI兼容"}:
        provider = "OpenAICompatible"
    key = str(settings.get("aiKey") or "").strip()
    model = str(settings.get("aiModel") or ("gemini-2.5-flash" if provider == "Gemini" else "gpt-4o-mini")).strip()
    base = str(settings.get("aiBase") or "").strip()
    proxy = str(settings.get("aiProxy") or "").strip()
    return {"provider": provider, "key": key, "model": model, "base": base, "proxy": proxy}


def configure_gemini_network() -> None:
    env = {**load_desktop_env()}
    ai = load_ai_config()
    if ai.get("base"):
        env["GEMINI_API_BASE"] = str(ai["base"])
    for name in ("GEMINI_API_BASE",):
        value = env.get(name, "").strip()
        if value:
            os.environ[name] = value


def gemini_open(req: urllib.request.Request, timeout: int):
    env = load_desktop_env()
    ai = load_ai_config()
    proxy = ai.get("proxy") or env.get("HTTPS_PROXY") or env.get("HTTP_PROXY") or env.get("ALL_PROXY")
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(req, timeout=timeout)


def load_desktop_env() -> dict[str, str]:
    path = Path.home() / "Desktop" / "1.env"
    env: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().strip('"').strip("'")
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip().upper()] = v.strip().strip('"').strip("'")
            elif line.startswith("AIza"):
                env["GEMINI_API_KEY"] = line
    except Exception:
        pass
    return env


def build_commands(name: str, options: dict) -> list[list[str]] | None:
    if name in {"simulate", "simulate5"}:
        sample = clamp_int(options.get("sample"), 10, 1, 30)
        cash = clamp_float(options.get("cash"), 100000.0, 1000.0, 100000000.0)
        per_trade = clamp_float(options.get("trade"), max(cash / max(sample, 1), 1000.0), 1000.0, cash)
        max_trades = min(3, sample)
        cmd = [
            sys.executable,
            "simulate_t_random.py",
            str(sample),
            "--cash",
            str(int(cash)),
            "--per-trade",
            str(int(per_trade)),
            "--max-trades",
            str(max_trades),
        ]
        json_file = str(options.get("json_file") or "")
        if json_file:
            cmd.extend(["--json-file", json_file])
        stocks = str(options.get("stocks") or "").strip()
        if stocks:
            cmd.extend(["--stocks", stocks])
        if name == "simulate5":
            days = clamp_int(options.get("days"), 5, 1, 60)
            cmd.extend(["--days", str(days)])
        return [cmd]
    return TASKS.get(name)


PROFIT_STRATEGY_FIELDS = {
    "vwap_take_profit_pct": (0.25, 0.10, 1.00),
    "normal_take_profit_pct": (0.60, 0.20, 1.50),
    "late_take_profit_pct": (0.45, 0.15, 1.20),
}


def load_profit_strategy_settings(email: str | None = None) -> dict:
    try:
        data = json.loads(account_strategy_path(email).read_text(encoding="utf-8"))
    except Exception:
        data = {}
    out = {
        key: clamp_float(data.get(key), default, low, high)
        for key, (default, low, high) in PROFIT_STRATEGY_FIELDS.items()
    }
    out.update({
        "maxSignalsPerDay": str(clamp_int(data.get("max_signals_per_day"), 2, 1, 6)),
        "lowBuyDev": str(clamp_float(data.get("buy_min_dev"), -1.20, -3.00, -0.50)),
        "highSellDev": str(clamp_float(data.get("sell_min_dev"), 1.40, 0.50, 3.00)),
        "signalCooldown": str(clamp_int(data.get("signal_cooldown_minutes"), 10, 1, 120)),
    })
    return out


def save_strategy_options(options: dict, email: str | None = None) -> None:
    incoming = {
        key: clamp_float(options.get(key), default, low, high)
        for key, (default, low, high) in PROFIT_STRATEGY_FIELDS.items()
        if key in options
    }
    if "lowBuyDev" in options:
        incoming["buy_min_dev"] = clamp_float(options.get("lowBuyDev"), -1.20, -3.00, -0.50)
        incoming["buy_max_dev"] = min(incoming["buy_min_dev"] - 0.55, -1.60)
    if "highSellDev" in options:
        incoming["sell_min_dev"] = clamp_float(options.get("highSellDev"), 1.40, 0.50, 3.00)
        incoming["sell_max_dev"] = max(incoming["sell_min_dev"] + 0.55, 1.80)
    if "maxSignalsPerDay" in options:
        incoming["max_signals_per_day"] = clamp_int(options.get("maxSignalsPerDay"), 2, 1, 6)
    if "signalCooldown" in options:
        incoming["signal_cooldown_minutes"] = clamp_int(options.get("signalCooldown"), 10, 1, 120)
    if "strategyMode" in options:
        incoming["strategy_mode"] = str(options.get("strategyMode") or "官方默认策略")[:32]
    if "customStrategy" in options:
        incoming["custom_strategy_note"] = str(options.get("customStrategy") or "")[:2200]
    if not incoming:
        return
    try:
        current = json.loads(account_strategy_path(email).read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
    except Exception:
        current = {}
    current.update(incoming)
    current.setdefault("last_notes", ["用户自定义低利润目标，优先提高单笔兑现率"])
    current["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        account_strategy_path(email).write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def clamp_float(value: object, default: float, low: float, high: float) -> float:
    try:
        number = float(str(value).replace(",", "").strip())
    except Exception:
        number = default
    return max(low, min(high, number))


def clamp_int(value: object, default: int, low: int, high: int) -> int:
    try:
        number = int(float(str(value).replace(",", "").strip()))
    except Exception:
        number = default
    return max(low, min(high, number))


def run_cmd(cmd: list[str], extra_env: dict | None = None) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items() if v is not None})
    env.pop("HERMES_SEND_TARGET", None)
    env["DISABLE_HERMES"] = "1"
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=90,
            startupinfo=hidden_startupinfo(),
            creationflags=hidden_creationflags(),
        )
        out = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part)
        return proc.returncode, out
    except Exception as exc:
        return 1, f"执行失败：{exc}"


def clean_start_all_detail(raw: str) -> str:
    parts = []
    if "秒级监控" in raw or "seconds_monitor" in raw:
        parts.append("秒级监控：正常")
    if "Scheduled Jobs" in raw or "任务" in raw:
        parts.append("定时任务：已检查")
    if "推送" in raw or "微信" in raw:
        parts.append("微信推送：已检查")
    return "\n".join(parts or ["一键启动已执行"])


def summarize(name: str, raw: str, ok: bool, stats: dict) -> str:
    if name in {"simulate", "simulate5"}:
        if stats:
            history = stats.get("history") or {}
            extra = ""
            if history:
                extra = f"；累计{history.get('trades', 0)}笔，总胜率{history.get('winRate', '--')}，总盈亏{history.get('pnl', '--')}"
            return f"模拟完成：触发 {stats.get('trigger', '--')}，胜率 {stats.get('win', '--')}，盈亏 {stats.get('pnl', '--')}{extra}"
        return raw.strip() or "模拟完成，但暂时没有返回结果。"
    if name == "start_all":
        return "一键启动完成。秒级监控、微信链路和定时任务已检查。"
    if name == "wechat":
        return "微信链路正在运行。" if ok else "微信链路未运行。"
    if name == "cron":
        return "定时任务已检查。"
    if name == "signal":
        return raw.strip() or "当前暂无明确买卖点。"
    return raw.strip() or "完成。"


def parse_sim_stats(raw: str) -> dict:
    stats = {"cash": "100,000元", "trade": "20,000元", "trigger": "--", "win": "--", "pnl": "--", "return": "--", "endingCash": "--"}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("资金"):
            m = re.search(r"资金\s+([^\s]+)\s+单笔\s+([^\s]+)", line)
            if m:
                stats["cash"] = m.group(1)
                stats["trade"] = m.group(2)
        elif line.startswith("触发"):
            m = re.search(r"触发\s+([^\s]+)\s+胜率\s+([^\s]+)", line)
            if m:
                stats["trigger"] = m.group(1)
                stats["win"] = m.group(2)
        elif line.startswith("模拟盈亏"):
            m = re.search(r"模拟盈亏\s+([^\s]+)\s+资金收益\s+([^\s]+)", line)
            if m:
                stats["pnl"] = m.group(1)
                stats["return"] = m.group(2)
        elif line.startswith("滚动资金"):
            m = re.search(r"滚动资金\s+([^\s]+)", line)
            if m:
                stats["endingCash"] = m.group(1)
    return stats


def record_sim_history(name: str, options: dict, stats: dict, stocks: list[dict]) -> None:
    trigger = parse_trigger(stats.get("trigger", "--"))
    wins = sum(1 for row in stocks if float(row.get("pnl") or 0) > 0)
    record = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task": name,
        "cash": stats.get("cash"),
        "trade": stats.get("trade"),
        "endingCash": stats.get("endingCash"),
        "triggered": trigger[0],
        "total": trigger[1],
        "wins": wins,
        "pnl": money_to_float(stats.get("pnl")),
        "review": stats.get("review") or {},
        "stocks": [
            {
                k: row.get(k)
                for k in (
                    "name",
                    "code",
                    "action",
                    "pnl",
                    "pnlText",
                    "rate",
                    "money",
                    "detail",
                    "reason",
                    "failureType",
                    "suggestion",
                    "prices",
                    "buyTime",
                    "sellTime",
                    "tradeAmount",
                    "shares",
                )
            }
            for row in stocks
        ],
    }
    try:
        with SIM_HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


def update_adaptive_strategy(stocks: list[dict]) -> None:
    defaults = {
        "buy_min_dev": -1.3,
        "buy_max_dev": -2.8,
        "buy_rebound": 0.6,
        "buy_confirm": 6,
        "sell_min_dev": 1.5,
        "sell_max_dev": 2.8,
        "sell_fade": 1.4,
        "sell_confirm": 7,
        "hold_exit_minutes": 25,
        "min_take_profit_minutes": 25,
        "min_stop_minutes": 15,
        "fast_take_profit_pct": 1.8,
        "emergency_stop_pct": -2.2,
        "vwap_reclaim_pct": 0.5,
        "vwap_exit_buffer_pct": 0.20,
        "vwap_take_profit_pct": 0.25,
        "normal_take_profit_pct": 0.60,
        "late_take_profit_pct": 0.45,
        "opening_enabled": 1,
        "opening_drop_pct": -1.8,
        "opening_reclaim_pct": 0.45,
        "opening_spike_pct": 1.6,
        "opening_fade_pct": 0.38,
        "swing_enabled": 1,
        "swing_min_space_pct": 0.85,
        "swing_buy_fade_pct": 0.75,
        "swing_sell_lift_pct": 0.85,
        "reverse_t_enabled": 1,
        "trade_end_hm": "14:00",
        "trade_end_minutes": 840,
        "opening_reverse_strict": 0,
        "min_trade_quality": 11,
        "second_confirm_enabled": 1,
        "observe_dev": 0.8,
        "strict_min_score": 8,
        "strict_day_range": 2.0,
        "version": 8,
    }
    try:
        current = json.loads(ADAPTIVE_STRATEGY_PATH.read_text(encoding="utf-8"))
    except Exception:
        current = {}
    strategy = {**defaults, **{k: current.get(k, v) for k, v in defaults.items()}}
    traded = [row for row in stocks if str(row.get("action") or "") != "未触发"]
    losses = [row for row in traded if float(row.get("pnl") or 0) <= 0]
    low_early = sum(1 for row in losses if row.get("failureType") == "低吸过早")
    reverse_early = sum(1 for row in losses if row.get("failureType") == "反T过早")
    notes = []
    if traded and low_early / max(len(traded), 1) >= 0.25:
        notes.append("低吸过早偏多：建议只做突破最近5分钟小压力后的正T，不自动加严参数")
    if traded and reverse_early / max(len(traded), 1) >= 0.25:
        notes.append("反T过早偏多：保留反T，但只允许回落确认后的强信号")
    strategy["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    strategy["last_notes"] = notes or ["本轮无需调参，继续累计样本"]
    try:
        ADAPTIVE_STRATEGY_PATH.write_text(json.dumps(strategy, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def aggregate_sim_history() -> dict:
    trades = wins = runs = no_trigger = 0
    pnl = 0.0
    failures: dict[str, int] = {}
    try:
        lines = SIM_HISTORY_PATH.read_text(encoding="utf-8").splitlines()[-500:]
    except Exception:
        lines = []
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        runs += 1
        triggered = int(item.get("triggered") or 0)
        total = int(item.get("total") or 0)
        trades += triggered
        wins += int(item.get("wins") or 0)
        pnl += float(item.get("pnl") or 0)
        no_trigger += max(total - triggered, 0)
        for stock in item.get("stocks") or []:
            kind = stock.get("failureType")
            if kind and kind != "盈利样本":
                failures[kind] = failures.get(kind, 0) + 1
    win_rate = wins / trades * 100 if trades else 0.0
    top_failures = sorted(failures.items(), key=lambda x: x[1], reverse=True)[:3]
    return {"runs": runs, "trades": trades, "winRate": f"{win_rate:.1f}%", "pnl": f"{pnl:+,.2f}元", "noTrigger": no_trigger, "failures": [{"type": k, "count": v} for k, v in top_failures]}


def recent_sim_history(limit: int = 12) -> list[dict]:
    try:
        lines = SIM_HISTORY_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception:
        return []
    rows: list[dict] = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except Exception:
            continue
        rows.append({"time": item.get("time"), "task": item.get("task"), "cash": item.get("cash"), "trade": item.get("trade"), "endingCash": item.get("endingCash"), "triggered": item.get("triggered"), "total": item.get("total"), "wins": item.get("wins"), "pnl": item.get("pnl"), "review": item.get("review") or {}, "stocks": item.get("stocks") or []})
    return rows


def latest_sim_result() -> dict:
    runs = recent_sim_history(1)
    if not runs:
        return {}
    item = runs[0]
    stats = {"cash": item.get("cash") or "--", "trade": item.get("trade") or "--", "endingCash": item.get("endingCash") or item.get("cash") or "--", "trigger": f"{item.get('triggered', 0)}/{item.get('total', 0)}", "win": f"{(float(item.get('wins') or 0) / float(item.get('triggered') or 1) * 100):.1f}%" if item.get("triggered") else "--", "pnl": f"{float(item.get('pnl') or 0):+,.2f}元", "return": "--", "review": item.get("review") or {}, "history": aggregate_sim_history()}
    return {"stats": stats, "stocks": item.get("stocks") or [], "time": item.get("time")}


def build_sim_review(stocks: list[dict]) -> dict:
    traded = [row for row in stocks if str(row.get("action") or "") != "未触发"]
    losses = [row for row in traded if float(row.get("pnl") or 0) <= 0]
    buckets: dict[str, int] = {}
    focus: list[dict] = []
    for row in stocks:
        kind, suggestion = classify_sim_row(row)
        row["failureType"] = kind
        row["suggestion"] = suggestion
        if kind != "盈利样本":
            buckets[kind] = buckets.get(kind, 0) + 1
        if row in losses and len(focus) < 4:
            focus.append({"name": row.get("name"), "code": row.get("code"), "action": row.get("action"), "pnlText": row.get("pnlText"), "type": kind, "suggestion": suggestion})
    win_rate = (len(traded) - len(losses)) / len(traded) * 100 if traded else 0.0
    if not traded:
        headline = "本轮没有触发交易，先扩大样本，不把低波动股票算作策略失败。"
    elif losses:
        main = max(buckets.items(), key=lambda x: x[1])[0] if buckets else "亏损样本不足"
        headline = f"本轮触发 {len(traded)} 笔，胜率 {win_rate:.1f}%。主要问题：{main}。"
    else:
        headline = f"本轮触发 {len(traded)} 笔全部盈利，继续累计样本。"
    return {"headline": headline, "failures": buckets, "focus": focus, "suggestions": review_suggestions(buckets, traded, losses)}


def classify_sim_row(row: dict) -> tuple[str, str]:
    action = str(row.get("action") or "")
    detail = str(row.get("detail") or row.get("reason") or "")
    pnl = float(row.get("pnl") or 0)
    if action == "未触发":
        return "未触发", "只记录观察；按振幅、成交量和VWAP偏离分层，不把低波动股票强行纳入交易。"
    if pnl > 0:
        return "盈利样本", "保留当前触发条件，继续统计量价结构。"
    if "反T" in action and "止损" in detail:
        return "反T过早", "反T必须等待冲高回落和量价背离，不追正在加速的上涨。"
    if "低吸" in action or "正T" in action:
        return "低吸过早", "低于VWAP后必须出现止跌拐头，再买。"
    if "超时" in detail:
        return "持仓超时", "缩短等待时间，弱反弹及时退出。"
    return "亏损样本", "降低单笔仓位，等待更多确认。"


def review_suggestions(buckets: dict, traded: list, losses: list) -> list[str]:
    suggestions = []
    if buckets.get("低吸过早", 0):
        suggestions.append("正T低吸增加二次确认：低于VWAP后必须出现止跌拐头，再买。")
    if buckets.get("反T过早", 0):
        suggestions.append("反T高抛增加滞涨确认：冲高后放量不涨或回落，再卖。")
    if not traded:
        suggestions.append("本轮无触发，先扩大随机股票池和测试天数，不直接放宽实时提醒。")
    if not suggestions:
        suggestions.append("本轮参数稳定，继续累计样本。")
    return suggestions


def parse_trigger(text: object) -> tuple[int, int]:
    m = re.search(r"(\d+)\s*/\s*(\d+)", str(text))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def money_to_float(text: object) -> float:
    try:
        cleaned = str(text).replace(",", "").replace("元", "").strip()
        return float(re.sub(r"[^0-9.+-]", "", cleaned) or 0)
    except Exception:
        return 0.0


def parse_sim_stocks(raw: str) -> list[dict]:
    rows: list[dict] = []
    pattern = re.compile(r"^\s*(.+?)\((\d{6})\)\s+(.+)$")
    for line in raw.splitlines():
        line = line.strip()
        m = pattern.match(line)
        if not m:
            continue
        name, code, rest = m.groups()
        pnl_match = re.search(r"([+-]\d+(?:\.\d+)?)%", rest)
        yuan_match = re.search(r"([+-][\d,.]+)元", rest)
        if rest.startswith("未触发"):
            action = "未触发"
        else:
            action = rest.split()[0] if rest.split() else "未触发"
        pnl = float(pnl_match.group(1)) if pnl_match else 0.0
        rate = "100%" if pnl_match and pnl > 0 else ("0%" if pnl_match else "--")
        money = f"{money_to_float(yuan_match.group(1)):+,.2f}元" if yuan_match else "--"
        rows.append({"name": name, "code": code, "action": action, "rate": rate, "pnl": pnl, "pnlText": f"{pnl:+.2f}%" if pnl_match else "--", "money": money, "detail": rest, "prices": [], "buyTime": "", "sellTime": ""})
    return rows


def merge_sim_chart_data(rows: list[dict], path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    by_code = {str(item.get("code")): item for item in payload if isinstance(item, dict)}
    for row in rows:
        item = by_code.get(str(row.get("code")))
        if not item:
            continue
        row["prices"] = item.get("prices", [])
        row["buyTime"] = item.get("buyTime", "--:--")
        row["sellTime"] = item.get("sellTime", "--:--")
        row["reason"] = item.get("reason") or row.get("detail", "")
        row["tradeAmount"] = item.get("tradeAmount")
        row["shares"] = item.get("shares")


def get_user_env(name: str) -> str:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"[Environment]::GetEnvironmentVariable('{name}','User')"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=hidden_startupinfo(),
            creationflags=hidden_creationflags(),
            timeout=5,
        )
        return proc.stdout.strip()
    except Exception:
        return ""


def hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def hidden_creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


LANDING_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>T神器 - A股做T盘中信号系统</title>
<style>
:root{--ink:#17202a;--muted:#667481;--line:#e8eef2;--red:#eb5b68;--green:#2fb878;--gold:#d7a032;--blue:#2563eb}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font:14px/1.65 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:#f7fbfb}
a,button{font:inherit}a{text-decoration:none;color:inherit}.page{min-height:100vh;background:radial-gradient(circle at 82% 0,#83f1ee 0,transparent 34%),linear-gradient(135deg,#7e969c,#f7fbfb 42%,#fff)}.nav{position:sticky;top:0;z-index:10;height:64px;display:flex;align-items:center;justify-content:space-between;width:min(1180px,calc(100vw - 32px));margin:auto}.brand{display:flex;align-items:center;gap:10px;font-weight:950;font-size:18px}.mark{width:34px;height:34px;border-radius:11px;background:#17202a;color:#fff;display:grid;place-items:center}.nav-actions{display:flex;gap:10px}.btn{height:38px;border:1px solid var(--line);border-radius:11px;background:#fff;padding:0 16px;display:inline-flex;align-items:center;font-weight:850;box-shadow:0 10px 24px rgba(20,38,48,.08)}.btn.primary{background:#17202a;color:#fff;border-color:#17202a}.hero{width:min(1180px,calc(100vw - 32px));margin:0 auto;padding:72px 0 44px;display:grid;grid-template-columns:1.02fr .98fr;gap:34px;align-items:center}.eyebrow{display:inline-flex;border:1px solid rgba(23,32,42,.12);background:rgba(255,255,255,.74);border-radius:999px;padding:6px 11px;font-weight:850;color:#40505e}.hero h1{font-size:56px;line-height:1.04;margin:18px 0 16px;letter-spacing:0}.lead{font-size:18px;color:#52616f;margin:0 0 26px;max-width:560px}.hero-actions{display:flex;gap:12px;flex-wrap:wrap}.proof{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:28px;max-width:560px}.proof div{background:rgba(255,255,255,.72);border:1px solid rgba(255,255,255,.78);border-radius:14px;padding:12px}.proof b{display:block;font-size:20px}.product{background:rgba(255,255,255,.88);border:1px solid rgba(255,255,255,.86);border-radius:28px;padding:18px;box-shadow:0 32px 90px rgba(24,48,56,.18)}.screen{border:1px solid var(--line);border-radius:20px;background:#fff;overflow:hidden}.screen-top{height:44px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:8px;padding:0 14px;font-weight:950}.dot{width:9px;height:9px;border-radius:50%;background:#d4dce2}.dash{padding:16px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.metric{border:1px solid var(--line);border-radius:14px;padding:12px;background:#fbfdfd}.metric span{display:block;color:var(--muted);font-size:12px}.metric b{font-size:20px}.row{display:grid;grid-template-columns:126px 92px 1fr 92px;gap:12px;align-items:center;border-bottom:1px solid #eef2f4;padding:15px 2px}.row:last-child{border-bottom:0}.name{font-weight:950}.code{color:var(--muted);margin-left:4px}.pill{border-radius:999px;padding:5px 9px;font-weight:900;background:#f1f5f7;color:#52616f;display:inline-flex}.pill.buy{background:#fff0f1;color:var(--red)}.pill.hold{background:#eef7ff;color:var(--blue)}.chart{height:42px}.pos{color:var(--red)}.neg{color:var(--green)}.band{width:min(1180px,calc(100vw - 32px));margin:0 auto;padding:34px 0}.section-title{font-size:28px;margin:0 0 8px}.section-sub{color:var(--muted);margin:0 0 20px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:0 16px 42px rgba(31,46,56,.07)}.card h3{margin:0 0 8px}.card p{color:#52616f;margin:0}.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.step{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px}.step b{display:block;font-size:22px;margin-bottom:8px}.table{background:#fff;border:1px solid var(--line);border-radius:18px;overflow:hidden}.table-row{display:grid;grid-template-columns:160px 1fr 1fr;gap:14px;padding:14px 18px;border-bottom:1px solid #eef2f4}.table-row:last-child{border-bottom:0}.table-row strong{font-weight:950}.pricing{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}.price{background:#fff;border:1px solid var(--line);border-radius:20px;padding:20px}.price.featured{border-color:#17202a;box-shadow:0 20px 55px rgba(23,32,42,.13)}.money{font-size:32px;font-weight:950;margin:8px 0}.list{padding:0;margin:12px 0 0;list-style:none}.list li{padding:6px 0;color:#52616f}.cta{margin:28px auto 0;width:min(1180px,calc(100vw - 32px));background:#17202a;color:#fff;border-radius:26px;padding:30px;display:flex;align-items:center;justify-content:space-between;gap:16px}.cta h2{margin:0;font-size:30px}.cta p{margin:4px 0 0;color:#d7dee3}.footer{width:min(1180px,calc(100vw - 32px));margin:0 auto;padding:28px 0 40px;color:#6d7883}.warn{font-size:12px;color:#7b8792;margin-top:12px}@media(max-width:900px){.hero{grid-template-columns:1fr;padding-top:36px}.hero h1{font-size:40px}.proof,.cards,.pricing,.metrics,.steps{grid-template-columns:1fr}.row,.table-row{grid-template-columns:1fr}.cta{display:block}.nav{position:static}.nav-actions{display:none}}
</style>
</head>
<body>
<div class="page">
  <nav class="nav"><a class="brand" href="/landing"><span class="mark">T</span><span>T神器</span></a><div class="nav-actions"><a class="btn" href="#features">功能</a><a class="btn" href="/commercial">功能中心</a><a class="btn" href="/account">账号</a><a class="btn" href="#pricing">价格</a><a class="btn primary" href="/">进入控制台</a></div></nav>
  <section class="hero">
    <div>
      <span class="eyebrow">面向A股日内做T的实时信号雷达</span>
      <h1>做T神器：盘中买卖点与策略控制台</h1>
      <p class="lead">把分时黄线、VWAP偏离、量能结构、板块联动和AI复核合成一个清晰的盘中判断：低吸、高抛、接回、止损，以及可执行的价格带。</p>
      <div class="hero-actions"><a class="btn primary" href="/">打开本地控制台</a><a class="btn" href="/commercial">打开功能中心</a><a class="btn" href="#pricing">查看商业化方案</a></div>
      <div class="proof"><div><b>秒级监控</b><span>盘中刷新</span></div><div><b>黄线战法</b><span>VWAP核心</span></div><div><b>微信提醒</b><span>强信号推送</span></div></div>
      <p class="warn">提示：本系统用于策略研究和风险提醒，不构成投资建议。</p>
    </div>
    <div class="product">
      <div class="screen">
        <div class="screen-top"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span>T神器控制台</span></div>
        <div class="dash">
          <div class="metrics"><div class="metric"><span>盘前分</span><b>82</b></div><div class="metric"><span>黄金</span><b class="pos">+0.55%</b></div><div class="metric"><span>铜</span><b class="pos">+2.63%</b></div><div class="metric"><span>美元</span><b>+0.09%</b></div></div>
          <div class="row"><div><span class="name">紫金矿业</span><span class="code">601899</span></div><span class="pill buy">低吸观察</span><svg class="chart" viewBox="0 0 220 42"><polyline points="0,28 22,30 44,26 66,31 88,21 110,18 132,20 154,14 176,17 198,12 220,13" fill="none" stroke="#eb5b68" stroke-width="3" stroke-linecap="round"/></svg><b class="pos">偏多</b></div>
          <div class="row"><div><span class="name">隆基绿能</span><span class="code">601012</span></div><span class="pill hold">观察</span><svg class="chart" viewBox="0 0 220 42"><polyline points="0,18 22,16 44,22 66,20 88,25 110,24 132,28 154,27 176,30 198,29 220,32" fill="none" stroke="#2fb878" stroke-width="3" stroke-linecap="round"/></svg><b class="neg">偏弱</b></div>
        </div>
      </div>
    </div>
  </section>
</div>
<section id="features" class="band">
  <h2 class="section-title">从工具到产品</h2>
  <p class="section-sub">商业化版本重点不是花哨页面，而是让用户每天开盘前和盘中知道该看什么。</p>
  <div class="cards">
    <div class="card"><h3>盘前市场风向</h3><p>跟踪黄金、白银、铜、原油、美元、离岸人民币和板块消息，形成重点股票盘前偏多/偏空评分。</p></div>
    <div class="card"><h3>盘中主力雷达</h3><p>围绕黄线均线、VWAP偏离、量能结构和趋势动量，识别吸筹、拉升、派发与假突破。</p></div>
    <div class="card"><h3>强信号提醒</h3><p>只在高质量买卖点出现时提醒，输出价格带、止损位和接回位，避免无意义刷屏。</p></div>
  </div>
</section>
<section class="band">
  <h2 class="section-title">适合的用户</h2>
  <div class="cards">
    <div class="card"><h3>持仓做T用户</h3><p>重点关注自选股票、行业板块和外盘方向，开盘前先判断高开低走或低开修复概率。</p></div>
    <div class="card"><h3>日内做T用户</h3><p>需要黄线附近的低吸、高抛、接回信号，而不是简单涨跌提醒。</p></div>
    <div class="card"><h3>策略研究用户</h3><p>通过模拟测试和失败复盘，持续优化触发条件和股票池。</p></div>
  </div>
</section>
<section class="band">
  <h2 class="section-title">商业版功能规划</h2>
  <p class="section-sub">核心卖点不是“预测涨跌”，而是把盘前路径、盘中买卖点和风控纪律产品化。</p>
  <div class="cards">
    <div class="card"><h3>自定义做T逻辑</h3><p>用户可选择黄线战法、开盘急跌急拉、二次确认、反T回补等模块，保存成个人策略。</p></div>
    <div class="card"><h3>AI集中复核</h3><p>出现候选买卖点后，AI讨论是否最优、是否追高接刀、价格带是否合理，再决定是否强提醒。</p></div>
    <div class="card"><h3>复盘训练库</h3><p>记录每次模拟和实盘提醒结果，统计低吸过早、反T过早、尾盘失效等问题，持续修正规则。</p></div>
  </div>
</section>
<section id="pricing" class="band">
  <h2 class="section-title">商业化定价</h2>
  <p class="section-sub">价格简单，功能直接：先用起来，再决定是否长期使用。</p>
  <div class="pricing">
    <div class="price"><h3>体验版</h3><div class="money">免费试用</div><ul class="list"><li>基础单股监控</li><li>盘前方向预览</li><li>模拟测试体验</li><li>策略研究预览</li></ul></div>
    <div class="price featured"><h3>月卡</h3><div class="money">¥9.9/月</div><ul class="list"><li>多股实时监控</li><li>正反T价格带提醒</li><li>模拟测试与复盘</li><li>选股研究与AI复核</li></ul></div>
    <div class="price"><h3>永久版</h3><div class="money">¥99</div><ul class="list"><li>包含月卡核心功能</li><li>永久使用当前版本</li><li>策略模板持续更新</li><li>优先体验新增功能</li></ul></div>
  </div>
</section>
<section class="cta"><div><h2>让做T从感觉变成纪律。</h2><p>盘前看方向，盘中等价格带，收盘复盘策略。</p></div><div class="hero-actions"><a class="btn primary" href="/">进入控制台</a><a class="btn" href="/register">注册体验账号</a></div></section>
<footer class="footer">T神器 · A股做T盘中信号系统 · 策略研究工具，不承诺收益。</footer>
</body>
</html>"""


AUTH_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>账号 - T神器</title>
<style>
:root{--ink:#17202a;--muted:#6b7785;--line:#e8eef2;--blue:#2563eb;--green:#2fb878;--bg:#f5faf9}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.6 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 88% 0,#7cefed,transparent 30%),linear-gradient(135deg,#81989f,#f7fbfb 42%,#fff);display:flex;align-items:center;justify-content:center;padding:18px}
a,button,input{font:inherit}a{text-decoration:none;color:inherit}.card{width:min(980px,100%);display:grid;grid-template-columns:1.05fr 420px;gap:18px;background:rgba(255,255,255,.92);border:1px solid rgba(255,255,255,.9);border-radius:28px;box-shadow:0 28px 80px rgba(25,45,50,.20);padding:26px}.hero{padding:18px}.brand{font-size:18px;font-weight:950;margin-bottom:44px}.hero h1{font-size:42px;line-height:1.08;margin:0 0 12px}.hero p{margin:0;color:#52616f}.points{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:28px}.point{background:#fff;border:1px solid var(--line);border-radius:16px;padding:13px}.point b{display:block;font-size:18px}.point span{color:#6b7785;font-size:12px}.form{background:#fff;border:1px solid var(--line);border-radius:22px;padding:22px;box-shadow:0 14px 38px rgba(25,45,50,.09)}.form h2{margin:0 0 6px;font-size:24px}.sub{color:var(--muted);margin-bottom:18px}.field{margin-bottom:12px}.field label{display:block;font-size:12px;color:#6b7785;font-weight:900;margin-bottom:6px}.field input{width:100%;height:42px;border:1px solid var(--line);border-radius:12px;padding:0 12px;font-weight:850}.field input:focus{outline:2px solid rgba(37,99,235,.14);border-color:#9ec0ff}.btns{display:grid;gap:9px;margin-top:14px}button{height:42px;border:1px solid var(--line);border-radius:12px;background:#fff;padding:0 14px;font-weight:950;cursor:pointer}button.primary{background:#17202a;color:#fff;border-color:#17202a}.msg{min-height:22px;margin-top:12px;color:#52616f}.msg.bad{color:#dc3545}.links{display:flex;justify-content:space-between;margin-top:16px;color:#52616f}.links a{color:#2563eb;font-weight:900}@media(max-width:850px){.card{grid-template-columns:1fr}.hero h1{font-size:32px}.points{grid-template-columns:1fr}}
</style>
</head>
<body>
<main class="card">
  <section class="hero">
    <div class="brand">T神器 · 商业内测账号</div>
    <h1>把策略、提醒和复盘绑定到你的账号。</h1>
    <p>当前是本地内测版：账号会保存在本机，用于模拟会员权限、策略草稿和后续商业化流程。</p>
    <div class="points">
      <div class="point"><b>体验版</b><span>注册后可试用基础能力</span></div>
      <div class="point"><b>月卡</b><span>9.9元/月，多股监控</span></div>
      <div class="point"><b>永久版</b><span>99元，长期使用</span></div>
      <div class="point"><b>云端可迁移</b><span>后续接数据库和支付</span></div>
    </div>
  </section>
  <section class="form">
    <h2 id="title">登录</h2>
    <div id="desc" class="sub">登录后进入会员中心。</div>
    <div id="nicknameBox" class="field" hidden><label>昵称</label><input id="nickname" placeholder="例如：紫金观察员"></div>
    <div class="field"><label>邮箱</label><input id="email" placeholder="you@example.com" autocomplete="email"></div>
    <div class="field"><label>密码</label><input id="password" type="password" placeholder="至少 6 位" autocomplete="current-password"></div>
    <div class="btns"><button class="primary" onclick="submitAuth()" id="submitBtn">登录</button><button onclick="location.href='/'">返回控制台</button></div>
    <div id="msg" class="msg"></div>
    <div class="links"><a id="switchLink" href="/register">没有账号？注册</a><a href="/account">会员中心</a></div>
  </section>
</main>
<script>
const mode='__MODE__';
const isRegister=mode==='register';
document.getElementById('title').textContent=isRegister?'注册体验账号':'登录';
const params=new URLSearchParams(location.search);
const next=params.get('next')||'/';
document.getElementById('desc').textContent=isRegister?'注册后自动登录，默认进入体验版账号。':'登录后进入你的操作台。';
document.getElementById('nicknameBox').hidden=!isRegister;
document.getElementById('submitBtn').textContent=isRegister?'注册并登录':'登录';
document.getElementById('switchLink').textContent=isRegister?'已有账号？登录':'没有账号？注册';
document.getElementById('switchLink').href=(isRegister?'/login':'/register')+(next?('?next='+encodeURIComponent(next)):'');
async function submitAuth(){
  const msg=document.getElementById('msg');msg.textContent='正在处理...';msg.className='msg';
  const payload={email:email.value.trim(),password:password.value,nickname:nickname.value.trim()};
  try{
    const res=await fetch(isRegister?'/api/register':'/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await res.json();
    if(!data.ok){msg.textContent=data.message||'操作失败';msg.className='msg bad';return}
    msg.textContent=data.message||'成功';
    setTimeout(()=>location.href=next,350);
  }catch(e){msg.textContent='网络或本地服务异常';msg.className='msg bad'}
}
</script>
</body>
</html>"""


ACCOUNT_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>会员中心 - T神器</title>
<style>
:root{--ink:#17202a;--muted:#6b7785;--line:#e8eef2;--blue:#2563eb;--green:#2fb878;--bg:#f5faf9}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.6 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 88% 0,#7cefed,transparent 30%),linear-gradient(135deg,#81989f,#f7fbfb 42%,#fff);padding:24px}a,button{font:inherit;text-decoration:none;color:inherit}button{height:38px;border:1px solid var(--line);border-radius:12px;background:#fff;padding:0 14px;font-weight:950;cursor:pointer;box-shadow:0 8px 20px rgba(20,38,48,.06)}button.primary{background:#17202a;color:#fff;border-color:#17202a}.shell{width:min(1180px,100%);margin:auto}.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}.brand{font-size:24px;font-weight:950}.sub{color:var(--muted)}.actions{display:flex;gap:9px;flex-wrap:wrap}.panel{background:rgba(255,255,255,.93);border:1px solid rgba(255,255,255,.9);border-radius:24px;box-shadow:0 28px 80px rgba(25,45,50,.18);padding:22px}.grid{display:grid;grid-template-columns:1.15fr .85fr;gap:14px;margin-top:14px}.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px}.card h2,.card h3{margin:0 0 10px}.kv{display:grid;grid-template-columns:150px 1fr;border-top:1px solid #eef2f4}.kv div{padding:12px 0;border-bottom:1px solid #eef2f4}.kv div:nth-child(odd){color:#6b7785;font-weight:900}.plan{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.price{border:1px solid var(--line);border-radius:16px;padding:15px;background:#fff}.price.active{border-color:#9ee4bd;background:#f2fff7}.price b{font-size:20px}.price ul{margin:10px 0 0;padding-left:18px;color:#52616f}.empty{text-align:center;padding:50px;color:#52616f}@media(max-width:850px){.grid,.plan{grid-template-columns:1fr}.top{display:block}.actions{margin-top:10px}}
</style>
</head>
<body>
<main class="shell">
  <div class="top">
    <div><div class="brand">会员中心</div><div class="sub">本地内测账号，后续可升级为云端商业版。</div></div>
    <div class="actions"><a href="/"><button>控制台</button></a><a href="/commercial"><button>功能中心</button></a><a href="/landing"><button>商业页</button></a></div>
  </div>
  <section id="root" class="panel"><div class="empty">正在读取账号状态...</div></section>
</main>
<script>
async function loadAccount(){
  const root=document.getElementById('root');
  const data=await fetch('/api/account').then(r=>r.json()).catch(()=>({ok:false}));
  if(!data.loggedIn){
    root.innerHTML=`<div class="empty"><h2>还没有登录</h2><p>先注册一个体验账号，就可以保存商业功能草稿和会员权限。</p><p><a href="/register"><button class="primary">注册体验账号</button></a> <a href="/login"><button>登录</button></a></p></div>`;
    return;
  }
  const a=data.account||{};
  root.innerHTML=`
    <div class="card"><h2>${a.nickname||'用户'}，欢迎回来</h2><div class="sub">当前套餐：${a.plan||'体验版'}。这是本地商业化原型，正式上线后会接支付、数据库和云端权限。</div></div>
    <div class="grid">
      <div class="card"><h3>账号信息</h3><div class="kv"><div>邮箱</div><div>${a.email||'--'}</div><div>昵称</div><div>${a.nickname||'--'}</div><div>套餐</div><div>${a.plan||'体验版'}</div><div>注册时间</div><div>${a.createdAt||'--'}</div><div>监控额度</div><div>${a.watchLimit||1} 只股票</div><div>AI复核额度</div><div>${a.aiReviewLimit||5} 次/日</div></div></div>
      <div class="card"><h3>快捷入口</h3><p><a href="/"><button class="primary">进入控制台</button></a></p><p><a href="/commercial"><button>配置商业功能</button></a></p><p><a href="/simulation"><button>打开模拟测试</button></a></p><p><button onclick="logout()">退出登录</button></p></div>
    </div>
    <div class="card" style="margin-top:14px"><h3>套餐规划</h3><div class="plan"><div class="price active"><b>体验版</b><p>免费试用</p><ul><li>基础单股监控</li><li>盘前方向预览</li><li>模拟测试体验</li></ul></div><div class="price"><b>月卡</b><p>¥9.9 / 月</p><ul><li>多股实时监控</li><li>模拟测试与复盘</li><li>AI买卖点复核</li></ul></div><div class="price"><b>永久版</b><p>¥99</p><ul><li>核心功能长期使用</li><li>策略模板更新</li><li>优先体验新增功能</li></ul></div></div></div>`;
}
async function logout(){await fetch('/api/logout',{method:'POST'});location.href='/login'}
loadAccount();
</script>
</body>
</html>"""


COMMERCIAL_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>商业功能中心 - T神器</title>
<style>
:root{--ink:#17202a;--muted:#6b7785;--line:#e8eef2;--red:#eb5b68;--green:#2fb878;--blue:#2563eb;--bg:#f5faf9}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.6 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 88% 0,#7cefed,transparent 30%),linear-gradient(135deg,#81989f,#f7fbfb 42%,#fff)}
a,button{font:inherit}a{text-decoration:none;color:inherit}button{height:36px;border:1px solid var(--line);border-radius:11px;background:#fff;padding:0 14px;font-weight:900;cursor:pointer;box-shadow:0 8px 20px rgba(20,38,48,.06)}button.primary{background:#17202a;color:#fff;border-color:#17202a}
.shell{width:min(1320px,calc(100vw - 28px));margin:18px auto 32px}.top{height:68px;display:flex;align-items:center;justify-content:space-between}.brand{font-size:22px;font-weight:950}.sub{color:var(--muted)}.actions{display:flex;gap:9px;align-items:center}.hero{background:rgba(255,255,255,.9);border:1px solid rgba(255,255,255,.86);border-radius:24px;padding:24px;box-shadow:0 24px 70px rgba(25,45,50,.14);display:grid;grid-template-columns:1fr 380px;gap:18px}.hero h1{font-size:38px;line-height:1.1;margin:0 0 10px}.hero p{color:#52616f;margin:0}.status{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.stat{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px}.stat span{display:block;color:var(--muted);font-size:12px}.stat b{font-size:24px}.grid{display:grid;grid-template-columns:310px 1fr;gap:14px;margin-top:14px}.panel{background:rgba(255,255,255,.94);border:1px solid rgba(255,255,255,.86);border-radius:20px;box-shadow:0 14px 38px rgba(25,45,50,.10);overflow:hidden}.head{height:48px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 16px;font-weight:950}.body{padding:14px}.tabs{display:grid;gap:8px}.tab{width:100%;justify-content:space-between;box-shadow:none;background:#f8fbfb}.tab.active{background:#17202a;color:#fff}.form{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.field{background:#f8fbfb;border:1px solid #eef2f4;border-radius:14px;padding:12px}.field label{display:block;color:var(--muted);font-size:12px;font-weight:900}.field input,.field select{width:100%;height:34px;margin-top:6px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:0 10px;font-weight:850}.toggle{display:flex;gap:8px;align-items:center;margin-top:8px;color:#52616f}.preview{margin-top:14px;border:1px solid var(--line);border-radius:16px;background:#fff;overflow:hidden}.preview-row{display:grid;grid-template-columns:150px 1fr;gap:12px;padding:12px 14px;border-bottom:1px solid #eef2f4}.preview-row:last-child{border-bottom:0}.tag{display:inline-flex;border-radius:999px;padding:4px 8px;background:#eef7ff;color:#2563eb;font-weight:900;margin:0 6px 6px 0}.flow{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px}.step{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px}.step b{display:block;font-size:22px}.step p{margin:6px 0 0;color:#52616f}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px}.card h3{margin:0 0 8px}.card p{margin:0;color:#52616f}.price-line{font-size:26px;font-weight:950;margin:4px 0 8px}.todo{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.todo div{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px}.todo b{display:block;margin-bottom:6px}.warn{font-size:12px;color:#73808a;margin-top:10px}@media(max-width:900px){.hero,.grid,.form,.flow,.cards,.todo{grid-template-columns:1fr}.top{height:auto;display:block}.actions{margin-top:10px;flex-wrap:wrap}}
</style>
</head>
<body>
<main class="shell">
  <div class="top">
    <div><div class="brand">商业功能中心</div><div class="sub">先把核心功能做成可配置，再接账号、支付和云端部署。</div></div>
    <div class="actions"><a href="/landing"><button>商业页</button></a><a href="/account"><button>账号</button></a><a href="/"><button class="primary">控制台</button></a></div>
  </div>
  <section class="hero">
    <div><h1>把做T策略产品化</h1><p>商业版的核心不是承诺收益，而是让用户能配置自己的做T逻辑、接收高质量提醒、复盘失败原因，并用AI复核买卖点是否真的值得执行。</p></div>
    <div class="status">
      <div class="stat"><span>当前阶段</span><b>内测前</b></div>
      <div class="stat"><span>核心模块</span><b>4 个</b></div>
      <div class="stat"><span>建议服务器</span><b>2核4G</b></div>
      <div class="stat"><span>收费准备</span><b>60%</b></div>
    </div>
  </section>
  <section class="grid">
    <div class="panel">
      <div class="head">商业模块</div>
      <div class="body tabs">
        <button class="tab active" onclick="showModule('strategy',this)">自定义策略</button>
        <button class="tab" onclick="showModule('ai',this)">AI集中复核</button>
        <button class="tab" onclick="showModule('alert',this)">提醒规则</button>
        <button class="tab" onclick="showModule('member',this)">会员能力</button>
      </div>
    </div>
    <div class="panel">
      <div class="head"><span id="moduleTitle">自定义策略</span><button onclick="saveDraft()">保存本地草稿</button></div>
      <div class="body">
        <div id="moduleBody"></div>
        <div class="preview">
          <div class="preview-row"><b>当前组合</b><div id="summaryTags"></div></div>
          <div class="preview-row"><b>用户看到的结果</b><div id="userPreview"></div></div>
          <div class="preview-row"><b>后端需要接入</b><div id="backendPreview"></div></div>
        </div>
      </div>
    </div>
  </section>
  <section class="panel" style="margin-top:14px">
    <div class="head">商业化主流程</div>
    <div class="body">
      <div class="flow">
        <div class="step"><b>1</b><p>用户选择股票池和策略模板。</p></div>
        <div class="step"><b>2</b><p>盘前生成大方向和关键价格带。</p></div>
        <div class="step"><b>3</b><p>盘中候选点先经过本地规则，再交给AI复核。</p></div>
        <div class="step"><b>4</b><p>只把强信号推送给用户，并自动沉淀复盘样本。</p></div>
      </div>
    </div>
  </section>
  <section class="panel" style="margin-top:14px">
    <div class="head">收费方案</div>
    <div class="body">
      <div class="cards">
        <div class="card"><h3>体验版</h3><div class="price-line">免费试用</div><p>基础单股监控、盘前方向预览、模拟测试体验。</p></div>
        <div class="card"><h3>月卡</h3><div class="price-line">¥9.9 / 月</div><p>多股监控、买卖点提醒、模拟复盘、选股研究、AI复核。</p></div>
        <div class="card"><h3>永久版</h3><div class="price-line">¥99</div><p>核心功能长期使用，策略模板更新，优先体验新增功能。</p></div>
      </div>
      <p class="warn">对外文案只写“辅助决策、纪律管理、研究参考”，不要承诺收益。</p>
    </div>
  </section>
</main>
<script>
const state={module:'strategy',strategySource:'使用官方默认策略',risk:'稳健',maxDaily:'1',buyDev:'-1.30',sellDev:'1.50',takeProfit:'0.75',aiReview:true,secondConfirm:true,pathGate:true,alert:'强提醒',plan:'专业版'};
const modules={
strategy:{title:'自定义策略',html:`<div class="form"><div class="field"><label>策略来源</label><select id="strategySource" onchange="setVal('strategySource',this.value)"><option>使用官方默认策略</option><option>复制默认后自定义</option><option>使用控制台设置</option></select></div><div class="field"><label>策略风格</label><select id="risk" onchange="setVal('risk',this.value)"><option>稳健</option><option>平衡</option><option>激进</option></select></div><div class="field"><label>每日最多交易</label><input id="maxDaily" type="number" min="0" max="5" value="1" oninput="setVal('maxDaily',this.value)"></div><div class="field"><label>正T低吸阈值</label><input id="buyDev" type="number" step="0.05" value="-1.30" oninput="setVal('buyDev',this.value)"></div><div class="field"><label>反T高抛阈值</label><input id="sellDev" type="number" step="0.05" value="1.50" oninput="setVal('sellDev',this.value)"></div><div class="field"><label>止盈目标%</label><input id="takeProfit" type="number" step="0.05" value="0.75" oninput="setVal('takeProfit',this.value)"></div><div class="field"><label>二次确认</label><div class="toggle"><input id="secondConfirm" type="checkbox" checked onchange="setBool('secondConfirm',this.checked)">低点回踩不破/高点反抽不过</div></div><div class="field"><label>路径预判</label><div class="toggle"><input id="pathGate" type="checkbox" checked onchange="setBool('pathGate',this.checked)">先判断低开修复/冲高回落/单边弱</div></div></div><p class="warn">当前正式生效入口在控制台“设置”：低吸阈值、高抛阈值、提醒次数、冷却和策略说明会同步给模拟测试与实时监控。</p>`},
ai:{title:'AI集中复核',html:`<div class="form"><div class="field"><label>AI复核</label><div class="toggle"><input id="aiReview" type="checkbox" checked onchange="setBool('aiReview',this.checked)">买卖点出现后先让AI讨论是否最优</div></div><div class="field"><label>复核重点</label><select><option>是否追高/接刀</option><option>大方向是否一致</option><option>价格带是否合理</option></select></div><div class="field"><label>输出格式</label><select><option>一句话结论 + 价格</option><option>五角色短评</option><option>详细复盘</option></select></div><div class="field"><label>模型策略</label><select><option>Gemini优先，本地兜底</option><option>本地规则优先</option><option>人工确认</option></select></div></div>`},
alert:{title:'提醒规则',html:`<div class="form"><div class="field"><label>提醒强度</label><select id="alert" onchange="setVal('alert',this.value)"><option>强提醒</option><option>普通提醒</option><option>只记录不提醒</option></select></div><div class="field"><label>单股每日提醒</label><select><option>买卖各1次</option><option>最多2次</option><option>不限但限频</option></select></div><div class="field"><label>推送渠道</label><select><option>微信ClawBot</option><option>浏览器声音</option><option>短信/邮件预留</option></select></div><div class="field"><label>提醒内容</label><select><option>股票名 + 价格带 + 原因</option><option>简约一句话</option><option>详细角色分析</option></select></div></div>`},
member:{title:'会员能力',html:`<div class="cards"><div class="card"><h3>体验版</h3><p>免费试用，基础单股监控、盘前风向、模拟体验。</p></div><div class="card"><h3>月卡</h3><p>¥9.9/月，多股监控、强提醒、AI买卖点复核。</p></div><div class="card"><h3>永久版</h3><p>¥99，核心功能长期使用，策略模板持续更新。</p></div></div><p class="warn">所有页面只做研究辅助，不承诺收益。</p>`}
};
function showModule(name,btn){state.module=name;document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));btn.classList.add('active');render()}
function setVal(k,v){state[k]=v;renderSummary()}
function setBool(k,v){state[k]=v;renderSummary()}
function render(){const m=modules[state.module];document.getElementById('moduleTitle').textContent=m.title;document.getElementById('moduleBody').innerHTML=m.html;renderSummary()}
function renderSummary(){document.getElementById('summaryTags').innerHTML=[state.strategySource||'官方默认策略',state.risk,`每日最多${state.maxDaily}笔`,state.secondConfirm?'二次确认':'允许快速拐头',state.pathGate?'路径预判':'不做路径闸门',state.aiReview?'AI复核':'本地规则',state.alert,state.plan].map(x=>`<span class="tag">${x}</span>`).join('');document.getElementById('userPreview').textContent=`用户将看到：官方默认策略或个人策略、盘前方向、候选买卖点、建议价格带、止盈止损和失效条件。`;document.getElementById('backendPreview').textContent=`需要接入：用户策略配置表、默认策略模板、参数校验、模拟验证、版本回滚、提醒记录和复盘训练库。`}
function saveDraft(){localStorage.setItem('commercialDraft',JSON.stringify(state));alert('已保存本地草稿')}
try{Object.assign(state,JSON.parse(localStorage.getItem('commercialDraft')||'{}'))}catch(e){}
render()
</script>
</body>
</html>"""


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>T神器</title>
<style>
:root{--bg:#eef6f6;--panel:#ffffff;--ink:#20252b;--muted:#7b8792;--line:#e9eef1;--green:#35b978;--red:#ec5f6b;--blue:#2563eb;--yellow:#d99a1b}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;font:13px/1.5 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 90% 0,#79eee9 0,transparent 36%),linear-gradient(135deg,#79939b,#eaf5f5 42%,#f7fbfb);overflow:hidden}
button,input{font:inherit}button{height:34px;border:1px solid var(--line);border-radius:11px;background:linear-gradient(180deg,#fff,#f9fbfb);padding:0 13px;font-weight:850;cursor:pointer;box-shadow:0 8px 18px rgba(31,46,56,.06);transition:transform .12s ease,box-shadow .12s ease,border-color .12s ease}button:hover{transform:translateY(-1px);box-shadow:0 12px 24px rgba(31,46,56,.10);border-color:#dbe5ea}button.primary{background:#20252b;color:#fff;border-color:#20252b}button:disabled{opacity:.6;cursor:wait}
.shell{min-height:100vh;padding:18px;display:flex;align-items:center;justify-content:center}.panel{position:relative;width:min(1560px,calc(100vw - 28px));height:min(920px,calc(100vh - 28px));background:rgba(255,255,255,.94);border:1px solid rgba(255,255,255,.85);border-radius:22px;box-shadow:0 28px 80px rgba(25,45,50,.20);padding:18px;display:grid;grid-template-rows:auto auto auto minmax(0,1fr);gap:12px;overflow:hidden}.top{display:flex;align-items:center;justify-content:space-between}.title{font-size:22px;font-weight:950}.sub{color:var(--muted);font-size:12px}.top-actions{display:flex;gap:8px;align-items:center}.settings-panel,.ai-panel{position:absolute;right:18px;top:62px;z-index:10;width:min(430px,calc(100vw - 44px));background:rgba(255,255,255,.98);border:1px solid var(--line);border-radius:16px;box-shadow:0 22px 60px rgba(31,46,56,.18);padding:12px}.settings-panel[hidden],.ai-panel[hidden]{display:none}.settings-head,.ai-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-weight:950}.settings-group{border-top:1px solid var(--line);padding-top:10px;margin-top:10px}.settings-title{font-size:11px;color:var(--muted);font-weight:950;margin:0 0 8px}.settings-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px}.settings-actions button{box-shadow:none}.settings-note{font-size:11px;color:#64727f;line-height:1.6}.ai-config-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px}.ai-config-grid label{font-size:11px;color:#64727f;font-weight:900}.ai-config-grid label.wide{grid-column:1/-1}.ai-config-grid input,.ai-config-grid select{width:100%;height:34px;margin-top:4px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:0 10px;color:#20252b;font-weight:800}.settings-messages{max-height:170px;overflow:auto;border-top:1px solid var(--line);padding-top:10px;color:#4f5b66;line-height:1.65}.ai-panel{top:112px;width:min(520px,calc(100vw - 44px));max-height:68vh;overflow:auto}.ai-body{font-size:12px;line-height:1.75;color:#39444d}.ai-body b{display:block;margin:8px 0 3px}.ai-chip{display:inline-flex;margin:0 5px 5px 0;border-radius:999px;background:#eef7ff;color:#2563eb;padding:4px 8px;font-weight:900}
.actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.field{position:relative}.field span{position:absolute;top:1px;left:10px;font-size:9px;color:var(--muted);font-weight:800}.field input{height:34px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:12px 10px 0;width:116px}.stock-manager{display:flex;align-items:center;gap:8px;flex:1 1 520px;min-width:420px;padding:7px 9px;border:1px solid #dfe7eb;border-radius:17px;background:linear-gradient(180deg,#fff,#f8fbfb);box-shadow:0 12px 28px rgba(31,46,56,.07)}.stock-manager-title{font-size:11px;color:#64727f;font-weight:950;white-space:nowrap}.stock-manager input{height:30px;border:1px solid #e3e9ed;border-radius:10px;background:#fff;padding:0 10px;font-weight:800;color:#20252b;width:190px}.watch-tags{display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-width:180px;flex:1}.tag{display:inline-flex;align-items:center;gap:6px;border-radius:999px;background:linear-gradient(180deg,#edf6ff,#e8f2ff);color:#2563eb;padding:5px 9px;font-weight:850;box-shadow:inset 0 0 0 1px rgba(37,99,235,.05);cursor:pointer}.tag.active{background:#20252b;color:#fff}.tag button{height:18px;width:18px;border:0;border-radius:50%;padding:0;background:#dcecff;color:#2563eb;box-shadow:none;line-height:18px}.tag.active button{background:rgba(255,255,255,.18);color:#fff}.bar{height:3px;background:linear-gradient(90deg,#4cc9f0,#35b978);border-radius:99px;opacity:0}.bar.on{opacity:1;animation:pulse .9s infinite alternate}@keyframes pulse{from{filter:brightness(.8)}to{filter:brightness(1.2)}}
.premarket{display:grid;grid-template-columns:230px minmax(0,1fr) 330px;gap:10px;align-items:stretch}.pm-card{background:rgba(255,255,255,.94);border:1px solid var(--line);border-radius:17px;padding:10px 12px;box-shadow:0 12px 28px rgba(31,46,56,.06)}.pm-title{font-size:12px;color:var(--muted);font-weight:900}.pm-score{font-size:26px;font-weight:950;letter-spacing:.2px}.pm-signal{display:inline-flex;border-radius:999px;padding:4px 9px;font-weight:950;background:#eef7ff;color:#2563eb}.pm-signal.bull{background:#fff0f1;color:var(--red)}.pm-signal.bear{background:#ebfff2;color:var(--green)}.pm-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(86px,1fr));gap:8px}.pm-item{background:linear-gradient(180deg,#fbfdfd,#f6faf9);border:1px solid #eef2f4;border-radius:13px;padding:8px 9px}.pm-item b{display:block;font-size:12px}.pm-item span{font-size:12px;font-weight:950}.pm-reasons{font-size:12px;color:#52606c;line-height:1.7}
.live{min-height:0;overflow:auto;overflow-x:hidden;background:#fff;border:1px solid var(--line);border-radius:18px;box-shadow:0 14px 34px rgba(31,46,56,.08)}.monitor-table{min-width:0;width:100%}.monitor-head,.monitor-row{display:grid;grid-template-columns:178px 92px minmax(210px,.9fr) 78px 106px 146px minmax(210px,1fr) 74px;gap:10px;align-items:center}.monitor-head{position:sticky;top:0;z-index:2;height:40px;padding:0 14px;background:linear-gradient(180deg,#fbfdfd,#f6faf9);border-bottom:1px solid var(--line);color:#7b8792;font-size:11px;font-weight:950}.monitor-row{min-height:92px;padding:8px 14px;border-bottom:1px solid #edf1f3}.monitor-row:hover{background:#fbfdfd}.monitor-row:last-child{border-bottom:0}.monitor-row.strong-signal{background:linear-gradient(90deg,rgba(236,95,107,.08),rgba(255,255,255,0));box-shadow:inset 4px 0 0 rgba(236,95,107,.75)}.rank-dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:9px;background:#aab4bd}.rank-dot.up{background:var(--red)}.rank-dot.down{background:var(--green)}.live-name{font-size:15px;font-weight:950}.live-code,.live-time{color:var(--muted);margin-left:5px}.live-price{font-size:23px;font-weight:950}.live-pos{color:var(--red)}.live-neg{color:var(--green)}.live-chart{width:100%;height:72px;display:block}.kv{font-size:12px;color:var(--muted);line-height:1.6}.kv b{display:block;color:var(--ink);font-size:15px}.signal-pill{display:inline-flex;border-radius:999px;padding:5px 9px;background:#eef3f6;color:#52606c;font-weight:900}.signal-pill.hot{background:#fff0f1;color:#ec5f6b}.live-signal{font-size:12px;color:#4f5b66;line-height:1.55;margin-top:4px}.money-line{border-radius:13px;background:linear-gradient(180deg,#f8fbfb,#f3f7f8);padding:8px 10px;color:#52606c;font-size:12px;line-height:1.48;max-height:72px;overflow:hidden}.signal-toasts{position:absolute;right:18px;top:116px;z-index:30;display:flex;flex-direction:column;gap:10px;width:min(390px,calc(100vw - 44px));pointer-events:none}.signal-toast{pointer-events:auto;color:#fff;border:1px solid rgba(255,255,255,.18);border-radius:16px;box-shadow:0 20px 55px rgba(15,25,35,.26);padding:12px 14px;animation:toastIn .18s ease-out}.signal-toast.buy{background:linear-gradient(135deg,#e5484d,#7f1d1d)}.signal-toast.sell{background:linear-gradient(135deg,#16a34a,#14532d)}.signal-toast .toast-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:7px}.signal-toast .toast-name{font-weight:950;font-size:15px}.signal-toast .toast-signal{border-radius:999px;background:rgba(255,255,255,.18);padding:3px 8px;font-size:12px;font-weight:900;white-space:nowrap}.signal-toast .toast-price{color:rgba(255,255,255,.82);font-size:12px}.signal-toast .toast-reason{color:#fff;font-size:12px;line-height:1.55;margin-bottom:7px}.signal-toast .toast-agents{color:rgba(255,255,255,.86);font-size:12px;line-height:1.6}.signal-toast button{height:24px;padding:0 7px;border:0;border-radius:8px;background:rgba(255,255,255,.16);color:#fff;box-shadow:none}.signal-toast.fade{opacity:0;transform:translateY(-6px);transition:.25s}.op{display:flex;gap:6px;flex-wrap:wrap}.op button{height:28px;padding:0 9px;border-radius:8px;color:#2563eb;background:#eef7ff;border-color:#dbeafe;box-shadow:none}.op button.ai{color:#fff;background:#20252b;border-color:#20252b}.empty-live{padding:42px;text-align:center;color:var(--muted);font-weight:850}@keyframes toastIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}
.bottom{display:none}.box{background:#fff;border:1px solid var(--line);border-radius:16px;overflow:hidden;display:flex;flex-direction:column;min-width:0;min-height:0}.head{height:42px;padding:0 14px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);font-weight:900}.badge{font-size:11px;color:#35a66d;background:#eafff1;border-radius:999px;padding:4px 9px}.content{padding:12px 14px;overflow:auto;line-height:1.75}.empty{color:var(--muted)}pre{margin:0;padding:12px 14px;flex:1;overflow:auto;white-space:pre-wrap;word-break:break-word;font:12px/1.65 "Microsoft YaHei UI",Consolas,monospace}.msg{margin:0 0 8px}.msg time{color:var(--muted);margin-right:8px}.wechat-focus{outline:2px solid #8fe8b1;box-shadow:0 0 0 5px rgba(69,201,129,.14)}
@media(max-width:1050px){html,body{overflow:auto}.shell{padding:10px}.panel{width:calc(100vw - 20px);height:auto;min-height:calc(100vh - 20px)}.metrics{grid-template-columns:repeat(2,1fr)}.bottom{grid-template-columns:1fr}.premarket{grid-template-columns:1fr}.live{overflow:auto}.monitor-table{min-width:980px}.monitor-head,.monitor-row{grid-template-columns:160px 86px 210px 72px 96px 132px 190px 64px}}
</style>
</head>
<body>
<div class="shell"><main class="panel">
  <div id="signalToasts" class="signal-toasts"></div>
  <div class="top">
    <div><div class="title">T神器控制台</div><div class="sub">多股票实时监控</div></div>
    <div class="top-actions"><button id="settingsBtn" onclick="toggleSettings()">设置</button><div id="status" class="sub">就绪</div></div>
  </div>
  <div id="settingsPanel" class="settings-panel" hidden>
    <div class="settings-head"><span>系统设置</span><button onclick="toggleSettings(false)">关闭</button></div>
    <div class="settings-group" style="border-top:0;margin-top:0;padding-top:0">
      <div class="settings-title">页面入口</div>
      <div class="settings-actions">
        <button onclick="location.href='/simulation'">模拟测试</button>
        <button onclick="location.href='/research'">选股研究</button>
        <button onclick="location.href='/account'">账号中心</button>
        <button onclick="location.href='/landing'">商业页面</button>
        <button onclick="location.href='/commercial'">功能中心</button>
        <button onclick="clearAll()">清空提示</button>
      </div>
    </div>
    <div class="settings-group">
      <div class="settings-title">AI模型配置</div>
      <div class="ai-config-grid">
        <label>服务商<select id="aiProvider"><option value="Gemini">Gemini</option><option value="OpenAICompatible">OpenAI兼容</option></select></label>
        <label>模型<input id="aiModel" placeholder="Gemini填 gemini-2.5-flash；中转填模型名" /></label>
        <label class="wide">第三方AI中转地址<input id="aiBase" placeholder="Gemini可留空；中转填 https://域名/v1" /></label>
        <label class="wide">代理地址<input id="aiProxy" placeholder="如 http://127.0.0.1:10808，可留空" /></label>
        <label class="wide">API Key<input id="aiKey" type="password" placeholder="未配置" autocomplete="off" /></label>
      </div>
      <div class="settings-actions">
        <button onclick="saveAiSettings()">保存AI配置</button>
        <button onclick="checkAiFromSettings()">检测AI</button>
        <button onclick="clearAiKey()">清除Key</button>
        <button onclick="location.href='/research'">去AI选股</button>
      </div>
      <div id="aiConfigStatus" class="settings-note">AI Key 和第三方中转地址只保存在本机配置，界面不会明文回显 Key。</div>
    </div>
    <div class="settings-group">
      <div class="settings-title">第三方API接口</div>
      <div class="ai-config-grid">
        <label class="wide">行情接口<input id="marketDataApi" placeholder="可填 MiniQMT / Level2 / 自建行情API地址" /></label>
        <label class="wide">新闻接口<input id="newsApi" placeholder="可填新闻API、RSS聚合或热点接口地址" /></label>
        <label class="wide">备用报价接口<input id="quoteApi" placeholder="可留空，当前默认使用本地腾讯/东方财富数据" /></label>
      </div>
      <div class="settings-note">未配置时继续使用本地默认数据源；配置后后续可接入更稳定的商业行情。</div>
    </div>
    <div class="settings-group">
      <div class="settings-title">自定义做T策略</div>
      <div class="ai-config-grid">
        <label>策略模式<select id="strategyMode"><option>官方默认策略</option><option>自定义策略</option><option>AI复核优先</option></select></label>
        <label>单股每日提醒<input id="maxSignalsPerDay" type="number" min="1" max="6" placeholder="2" /></label>
        <label>低吸偏离%<input id="lowBuyDev" type="number" step="0.05" placeholder="-1.20" /></label>
        <label>高抛偏离%<input id="highSellDev" type="number" step="0.05" placeholder="1.40" /></label>
        <label>提醒冷却分钟<input id="signalCooldown" type="number" min="1" max="120" placeholder="10" /></label>
        <label class="wide">策略说明<input id="customStrategy" placeholder="例如：围绕黄线，低开急跌只等右侧拐头；冲高无量先反T。" /></label>
      </div>
      <div class="settings-actions">
        <button onclick="saveAiSettings()">保存全部设置</button>
        <button onclick="location.href='/simulation'">去模拟验证</button>
      </div>
      <div class="settings-note">自定义策略会同步给模拟测试和监控参数，正式买卖提醒仍以量价确认和风控为准。</div>
    </div>
  </div>
  <div id="aiPanel" class="ai-panel" hidden>
    <div class="ai-head"><span>Gemini盘中研判</span><button onclick="toggleAi(false)">关闭</button></div>
    <div id="aiBody" class="ai-body">点击股票行右侧“AI”，生成大方向和做T计划。</div>
  </div>
  <section><div class="actions">
    <div class="stock-manager">
      <span class="stock-manager-title">多股监控</span>
      <div id="watchTags" class="watch-tags"></div>
      <input id="stockCodeInput" placeholder="输入股票代码，如 601899" title="如 601899 或 sh601899" />
      <button onclick="addWatchStock()">添加</button>
      <input id="watchInput" type="hidden" value="sh601899,sh601012" />
    </div>
  </div><div id="loading" class="bar"></div></section>
  <section id="premarket" class="premarket">
    <div class="pm-card"><div class="pm-title" id="pmTargetTitle">目标股开盘前风向</div><div class="pm-score">--</div><span class="pm-signal">读取中</span></div>
    <div class="pm-card"><div class="pm-title">期货/外盘快照 <span class="sub">延迟参考</span></div><div id="pmList" class="pm-list"></div></div>
    <div class="pm-card"><div class="pm-title">盘前结论</div><div id="pmReason" class="pm-reasons">正在读取黄金、铜、原油、美元。</div></div>
  </section>
  <section id="live" class="live"></section>
  <section class="bottom">
    <div class="box"><div class="head"><span>监控摘要</span><span id="count" class="badge">独立模拟</span></div><div id="rows" class="content empty">模拟测试已移到独立页面，点击上方“模拟测试”查看曲线、买卖点、复盘和历史缓存。</div><div id="review" class="content"><b>策略复盘</b>模拟后自动显示失败原因和下一轮优化方向。</div></div>
    <div class="box"><div class="head"><span>运行日志</span><span class="badge">中文输出</span></div><pre id="out">已就绪。</pre></div>
  </section>
</main></div>
<script>
const $=id=>document.getElementById(id);
const labels={signal:'多股信号',simulate:'随机股票当日做T模拟',simulate5:'随机股票近5天测试'};
let tradeManual=false,audioCtx=null,lastAlertKeys=new Map(),toastKeys=new Map(),settingsTimer=null;
window.addEventListener('DOMContentLoaded',async()=>{document.addEventListener('click',initAudio,{once:true});loadSettings();await loadWatchlist();loadPremarket();loadRealtime();setInterval(loadRealtime,10000);setInterval(loadPremarket,60000);});
function toggleSettings(force){const el=$('settingsPanel');if(!el)return;const show=typeof force==='boolean'?force:el.hasAttribute('hidden');if(show)el.removeAttribute('hidden');else el.setAttribute('hidden','')}
function toggleAi(force){const el=$('aiPanel');if(!el)return;const show=typeof force==='boolean'?force:el.hasAttribute('hidden');if(show)el.removeAttribute('hidden');else el.setAttribute('hidden','')}
function setBusy(on){document.querySelectorAll('button').forEach(b=>b.disabled=on)}
let mainOptions={cash:100000,trade:20000,sample:10};
let signalPrefs={maxSignalsPerDay:2,signalCooldown:10};
function simOptions(){return mainOptions}
async function run(name){setBusy(true);$('status').textContent='运行中';$('loading').classList.add('on');append('正在：'+(labels[name]||name));try{const res=await fetch('/api/run/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(simOptions())});const data=await res.json();append(data.summary||'完成。');$('status').textContent=data.ok?'完成':'失败'}catch(e){append('执行失败：'+e.message);$('status').textContent='失败'}$('loading').classList.remove('on');setBusy(false)}
function syncTradeAmount(){}
function syncInputCards(){const o=simOptions();if($('cash'))$('cash').textContent=formatYuan(o.cash);if($('trade'))$('trade').textContent=formatYuan(o.trade)}
async function loadSettings(){try{const s=await (await fetch('/api/settings',{cache:'no-store'})).json();if(s.ok){mainOptions={cash:Number(s.cash||100000),trade:Number(s.trade||20000),sample:Number(s.sample||10)};fillAiSettings(s)}}catch(e){}syncInputCards()}
function saveSettingsDebounced(){clearTimeout(settingsTimer);settingsTimer=setTimeout(saveSettings,450)}
function saveSettings(){fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(simOptions())}).catch(()=>{})}
function fillAiSettings(s){signalPrefs.maxSignalsPerDay=Number(s.maxSignalsPerDay||2);signalPrefs.signalCooldown=Number(s.signalCooldown||10);if($('aiProvider'))$('aiProvider').value=({OpenAI:'OpenAICompatible','OpenAI兼容':'OpenAICompatible',OpenAICompatible:'OpenAICompatible'}[s.aiProvider]||(s.aiProvider||'Gemini'));if($('aiModel'))$('aiModel').value=s.aiModel||'gemini-2.5-flash';if($('aiBase'))$('aiBase').value=s.aiBase||'';if($('aiProxy'))$('aiProxy').value=s.aiProxy||'';if($('aiKey'))$('aiKey').placeholder=s.aiKeyConfigured?(s.aiKeyMasked||'已配置'):'未配置';['marketDataApi','newsApi','quoteApi','customStrategy','strategyMode','maxSignalsPerDay','lowBuyDev','highSellDev','signalCooldown'].forEach(k=>{if($(k))$(k).value=s[k]||''});if($('aiConfigStatus'))$('aiConfigStatus').textContent=s.aiKeyConfigured?'AI Key 已配置。需要更换时直接输入新 Key 并保存。':'未配置 AI Key，本地规则仍可使用。'}
function aiOptions(extra={}){return {...simOptions(),aiProvider:$('aiProvider')?.value||'Gemini',aiModel:$('aiModel')?.value||'gemini-2.5-flash',aiBase:$('aiBase')?.value||'',aiProxy:$('aiProxy')?.value||'',aiKey:$('aiKey')?.value||'',marketDataApi:$('marketDataApi')?.value||'',newsApi:$('newsApi')?.value||'',quoteApi:$('quoteApi')?.value||'',customStrategy:$('customStrategy')?.value||'',strategyMode:$('strategyMode')?.value||'官方默认策略',maxSignalsPerDay:$('maxSignalsPerDay')?.value||'2',lowBuyDev:$('lowBuyDev')?.value||'-1.20',highSellDev:$('highSellDev')?.value||'1.40',signalCooldown:$('signalCooldown')?.value||'10',...extra}}
async function saveAiSettings(){const msg=$('aiConfigStatus');if(msg)msg.textContent='正在保存AI配置...';try{const data=await (await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(aiOptions())})).json();if(data.ok){if($('aiKey'))$('aiKey').value='';fillAiSettings(data);append('AI配置已保存。')}else throw new Error('保存失败')}catch(e){if(msg)msg.textContent='保存失败：'+(e.message||e)}}
async function clearAiKey(){const msg=$('aiConfigStatus');if(msg)msg.textContent='正在清除Key...';try{const data=await (await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(aiOptions({aiClearKey:true,aiKey:''}))})).json();if($('aiKey'))$('aiKey').value='';fillAiSettings(data);append('AI Key 已清除。')}catch(e){if(msg)msg.textContent='清除失败：'+(e.message||e)}}
async function checkAiFromSettings(){await saveAiSettings();const msg=$('aiConfigStatus');if(msg)msg.textContent='正在检测AI...';try{const ctrl=new AbortController();const timer=setTimeout(()=>ctrl.abort(),10000);const data=await (await fetch('/api/gemini_status',{cache:'no-store',signal:ctrl.signal})).json();clearTimeout(timer);if(msg)msg.textContent=(data.ok?'检测通过：':'检测失败：')+(data.message||'无返回');append((data.ok?'AI检测通过：':'AI检测失败：')+(data.message||''))}catch(e){if(msg)msg.textContent='检测超时或网络不可达。'}}
let watchStocks=[],premarketTargetCode='';
async function loadWatchlist(){try{const data=await (await fetch('/api/watchlist',{cache:'no-store'})).json();if(data.ok){watchStocks=data.stocks||[];$('watchInput').value=data.text||'';renderWatchTags()}}catch(e){}}
async function saveWatchlist(){const text=watchStocks.map(s=>s.symbol).join(',');try{const data=await (await fetch('/api/watchlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})})).json();if(data.ok){watchStocks=data.stocks||[];$('watchInput').value=data.text;renderWatchTags();append('已保存监控股票：'+watchStocks.map(s=>s.name+s.code).join('、'));loadPremarket();loadRealtime()}else append('保存失败：'+(data.error||'未知原因'))}catch(e){append('保存失败：'+e.message)}}
function renderWatchTags(){const el=$('watchTags');if(!el)return;if(!premarketTargetCode&&watchStocks[0])premarketTargetCode=watchStocks[0].code;el.innerHTML=(watchStocks||[]).map((s,i)=>`<span class="tag ${premarketTargetCode===s.code?'active':''}" onclick="selectPremarket('${escapeHtml(s.code)}')" title="点击切换盘前风向">${escapeHtml(s.name)} ${escapeHtml(s.code)}<button onclick="event.stopPropagation();removeWatchStock(${i})" title="移除">×</button></span>`).join('')||'<span class="sub">请添加要监控的股票</span>'}
function selectPremarket(code){premarketTargetCode=code;renderWatchTags();loadPremarket()}
function addWatchStock(){const raw=($('stockCodeInput')?.value||'').trim();const token=normalizeStockToken(raw);if(!token){append('请输入股票代码，例如 601899。');return}const exists=watchStocks.some(s=>s.symbol===token);if(!exists){watchStocks.push({name:stockName(token),code:token.slice(2),symbol:token});saveWatchlist()}if($('stockCodeInput'))$('stockCodeInput').value=''}
function removeWatchStock(index){watchStocks.splice(index,1);saveWatchlist()}
function normalizeStockToken(raw){let v=String(raw||'').trim().toLowerCase();const m=v.match(/(sh|sz)?(\d{6})/);if(!m)return '';const code=m[2];const prefix=(m[1]||(code.startsWith('6')||code.startsWith('5')?'sh':'sz'));return prefix+code}
function stockName(symbol){const code=symbol.slice(2);return ({'601899':'紫金矿业','601012':'隆基绿能','000063':'中兴通讯','600519':'贵州茅台','300502':'新易盛','002050':'三花智控','600580':'卧龙电驱'})[code]||code}

function formatYuan(n){return Number(n||0).toLocaleString('zh-CN',{maximumFractionDigits:0})+'元'}function parseYuan(s){return Number(String(s||'').replace(/[^\d.-]/g,''))||0}
function updateStats(s,persist=true){if(!Object.keys(s).length)return;if($('cash'))$('cash').textContent=s.endingCash||s.cash||'--';if($('trade'))$('trade').textContent=s.trade||'--';if($('trigger'))$('trigger').textContent=s.trigger||'--';if($('win'))$('win').textContent=s.win||'--';if($('pnl')){$('pnl').textContent=s.pnl||'--';$('pnl').className='v '+((s.pnl||'').startsWith('-')?'neg':'pos')}if($('ret'))$('ret').textContent=s.return||'--';const ending=parseYuan(s.endingCash);if(persist&&ending>0){mainOptions.cash=ending;saveSettings()}}
async function restoreLatestSim(){try{const data=await (await fetch('/api/simulation_history',{cache:'no-store'})).json();const latest=data.latest||{};if(latest.stats){updateStats(latest.stats,false);renderReview((latest.stats||{}).review||{},(latest.stats||{}).history||{});append('已恢复最近一次模拟：'+(latest.time||''))}}catch(e){}}
function renderReview(review,history){const parts=[];if(review.headline)parts.push(`<b>本轮复盘</b><div>${escapeHtml(review.headline)}</div>`);if((review.suggestions||[]).length)parts.push('<b>下一轮优化</b><ul>'+review.suggestions.map(x=>`<li>${escapeHtml(x)}</li>`).join('')+'</ul>');if(history&&history.runs){const fails=(history.failures||[]).map(x=>`${escapeHtml(x.type)} ${x.count}次`).join('；')||'暂无高频问题';parts.push(`<b>累计统计</b><div>${history.runs} 次模拟，${history.trades} 笔交易，总胜率 ${history.winRate}，总盈亏 ${history.pnl}。问题：${fails}</div>`)}$('review').innerHTML=parts.join('')||'<b>策略复盘</b>模拟后自动显示失败原因和下一轮优化方向。'}
async function loadRealtime(){try{const data=await (await fetch('/api/realtime',{cache:'no-store'})).json();renderRealtime(data.stocks||[])}catch(e){$('live').innerHTML='<div class="empty-live">实时监控暂不可用：'+escapeHtml(e.message)+'</div>'}}
async function loadPremarket(){try{const q=premarketTargetCode?'?code='+encodeURIComponent(premarketTargetCode):'';const data=await (await fetch('/api/premarket'+q,{cache:'no-store'})).json();renderPremarket(data)}catch(e){$('pmReason').textContent='外盘读取失败：'+e.message}}
function renderPremarket(data){const z=data.target||data.zijin||{},rows=data.rows||[];const signal=z.signal||'观望';const cls=signal==='偏多'?'bull':signal==='偏空'?'bear':'';if($('pmTargetTitle'))$('pmTargetTitle').textContent=(z.name||'目标股')+'开盘前风向';document.querySelector('.pm-score').textContent=(z.score??'--')+'分';const pill=document.querySelector('.pm-signal');pill.textContent=signal+'｜'+(z.category||'综合')+'｜刷新 '+(data.updatedAt||'--');pill.className='pm-signal '+cls;$('pmList').innerHTML=rows.length?rows.map(r=>{const ch=Number(r.change||0),c=ch>=0?'live-pos':'live-neg';const price=Number(r.price||0);return `<div class="pm-item"><b>${escapeHtml(r.name)}</b><span class="${c}">${price>=100?price.toFixed(1):price.toFixed(2)} ${ch>=0?'+':''}${ch.toFixed(2)}%</span><div class="sub">${escapeHtml(r.time||'时间未知')}</div></div>`}).join(''):'<div class="muted">暂无外盘数据</div>';$('pmReason').innerHTML=`<b>${escapeHtml(z.action||'等待盘中确认')}</b><br>${(z.reasons||[]).map(escapeHtml).join('<br>')}<br><span class="sub">外盘为 Yahoo 5分钟快照，按当前主监控股票降权计算，只作为盘前方向。</span>`}
function renderRealtime(rows){if(!rows.length){$('live').innerHTML='<div class="empty-live">暂无监控股票，请先添加代码。</div>';return}$('live').innerHTML=`<div class="monitor-table"><div class="monitor-head"><span>股票</span><span>现价</span><span>日内曲线</span><span>涨跌</span><span>均价/偏离</span><span>买卖点</span><span>多角色结论</span><span>操作</span></div>${rows.map(r=>{maybeBeep(r);showSignalToast(r);const change=Number(r.change||0);const cls=change>=0?'live-pos':'live-neg';const tradable=/低吸|高抛|买入|卖出/.test(r.signal||'');const closed=r.marketStatus==='休市中';const signal=closed?'休市中':(r.signal||'观察');const dot=change>=0?'up':'down';const agents=(Array.isArray(r.agents)&&r.agents.length?r.agents:[(r.smartMoney&&r.smartMoney.text)||'主力行为：普通行情模式，仅供辅助参考']).map(escapeHtml).join('<br>');return `<div class="monitor-row ${tradable?'strong-signal':''}"><div><span class="rank-dot ${dot}"></span><span class="live-name">${escapeHtml(r.name)}</span><span class="live-code">${escapeHtml(r.code)}</span><div class="kv">更新时间 ${escapeHtml(r.time||'--:--')}</div></div><div class="live-price ${cls}">${Number(r.price||0).toFixed(2)}</div><div>${liveSpark(r)}</div><div class="${cls}" style="font-weight:950">${change.toFixed(2)}%</div><div class="kv"><b>${Number(r.avg||0).toFixed(2)}</b>偏离 ${Number(r.dev||0).toFixed(2)}%</div><div><span class="signal-pill ${tradable?'hot':''}">${escapeHtml(signal)}</span><div class="live-signal">${escapeHtml(r.reason||'暂无高质量买卖点')}</div></div><div class="money-line">${agents}</div><div class="op"><button class="ai" onclick="aiIntraday('${escapeHtml(r.code)}')">AI</button><button onclick="focusStock('${escapeHtml(r.code)}')">详情</button></div></div>`}).join('')}</div>`}
function alertSide(sig){if(/低吸|买入/.test(sig||''))return 'buy';if(/高抛|卖出/.test(sig||''))return 'sell';return ''}
function showSignalToast(r){const sig=r.signal||'',side=alertSide(sig);if(!side)return;const today=new Date().toISOString().slice(0,10);const dailyKey=`toast:${today}:${r.code}:${side}`;if(Number(localStorage.getItem(dailyKey)||0)>=Math.max(1,signalPrefs.maxSignalsPerDay||2))return;const key=[r.code,side].join(':');const now=Date.now();if(toastKeys.has(key)&&now-toastKeys.get(key)<Math.max(1,signalPrefs.signalCooldown||10)*60000)return;toastKeys.set(key,now);localStorage.setItem(dailyKey,String(Number(localStorage.getItem(dailyKey)||0)+1));const box=$('signalToasts');if(!box)return;const isBuy=side==='buy';const agents=(Array.isArray(r.agents)&&r.agents.length?r.agents:[]).slice(0,4).map(escapeHtml).join('<br>');const el=document.createElement('div');el.className='signal-toast '+(isBuy?'buy':'sell');el.innerHTML=`<div class="toast-top"><div><div class="toast-name">${escapeHtml(r.name)} ${escapeHtml(r.code)}</div><div class="toast-price">${escapeHtml(r.time||'--:--')}｜现价 ${Number(r.price||0).toFixed(2)}｜偏离 ${Number(r.dev||0).toFixed(2)}%</div></div><span class="toast-signal">${isBuy?'买入':'卖出'}｜${escapeHtml(sig)}</span><button title="关闭">×</button></div><div class="toast-reason">${escapeHtml(r.reason||'等待量价确认')}</div><div class="toast-agents">${agents||'多角色结论生成中'}</div>`;box.prepend(el);const close=()=>{el.classList.add('fade');setTimeout(()=>el.remove(),260)};el.querySelector('button').onclick=close;setTimeout(close,60000);[...box.children].slice(2).forEach(x=>x.remove())}
function focusStock(code){location.href='/research?code='+encodeURIComponent(code)}
async function aiIntraday(code){toggleAi(true);const body=$('aiBody');body.innerHTML='<span class="ai-chip">Gemini</span><span class="ai-chip">大方向</span><span class="ai-chip">路径预判</span><span class="ai-chip">买卖点复核</span><br>正在集中讨论当前买卖点...';try{const ctrl=new AbortController();const timer=setTimeout(()=>ctrl.abort(),11000);const data=await (await fetch('/api/gemini_intraday?code='+encodeURIComponent(code),{cache:'no-store',signal:ctrl.signal})).json();clearTimeout(timer);if(!data.ok){body.textContent=data.message||'Gemini 暂无返回';return}const a=data.analysis||{},s=data.stock||{};const agents=Array.isArray(a.agents)?a.agents:[];const key=formatKeyPrices(a.keyPrices);const path=formatPathForecast(a.pathForecast);body.innerHTML=`<span class="ai-chip">${escapeHtml(s.股票||code)}</span><span class="ai-chip">现价 ${escapeHtml(s.现价??'--')}</span><span class="ai-chip">昨收 ${escapeHtml(s.昨收??'--')}</span><span class="ai-chip">黄线偏离 ${escapeHtml(s.黄线偏离??'--')}%</span><b>大方向</b>${escapeHtml(a.macroView||a.trend||'观察')}<b>今日路径预判</b>${escapeHtml(path)}<b>关键价位</b>${escapeHtml(key)}<b>买卖点复核</b>${escapeHtml(a.pointReview||'当前未形成最优买卖点，先按观察处理')}<b>趋势判断</b>${escapeHtml(a.trend||'证据不足，先观察')}<b>操作计划</b>${escapeHtml(a.action||'不强行交易')}<br>买入：${escapeHtml(a.buyPlan||'等待低位确认')}<br>卖出：${escapeHtml(a.sellPlan||'等待高位确认')}<b>失效条件</b>${escapeHtml(a.invalidation||'跌破/突破关键价后重新评估')}<b>多角色研判</b>${agents.map(escapeHtml).join('<br>')||'暂无多角色返回'}<p class="sub">模型：${escapeHtml(data.model||'Gemini')}。该观点30分钟内会同步给监控观察员参考。</p>`;loadRealtime()}catch(e){body.textContent='Gemini 分析超时或网络不可达：'+(e.message||e)}}
function formatPathForecast(v){if(!v)return '证据不足，暂按震荡处理';if(typeof v==='string')return v;const map={mostLikely:'主路径',alternative:'备选路径',bullish:'偏多路径',bearish:'偏空路径',neutral:'震荡路径'};return Object.entries(v).map(([k,val])=>`${map[k]||k}：${val}`).join('｜')}
function formatKeyPrices(v){if(!v)return '等待确认';if(typeof v==='string')return v;const map={support:'支撑位',resistance:'压力位',vwap:'黄线均价',buyLow:'低吸区',sellHigh:'高抛区',stopLoss:'止损位',takeProfit:'止盈位'};return Object.entries(v).map(([k,val])=>`${map[k]||k}：${val}`).join('｜')}
function liveSpark(row){const series=(row.prices||[]).filter(x=>Number(x.price)>0);const c=Number(row.change)>=0?'#ec5f6b':'#35b978';if(series.length<2)return '<svg class="live-chart" viewBox="0 0 300 100"><line x1="8" y1="50" x2="292" y2="50" stroke="#d7dde2" stroke-width="2"/></svg>';const step=Math.max(1,Math.floor(series.length/90));const points=series.filter((_,i)=>i%step===0);const values=points.map(x=>Number(x.price));const min=Math.min(...values),max=Math.max(...values),span=Math.max(max-min,.01);const xy=points.map((p,i)=>({x:8+(i/(points.length-1))*284,y:86-((Number(p.price)-min)/span)*74,time:p.time}));const pts=xy.map(p=>`${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');const norm=t=>String(t||'').replace(':','');const mark=(time,label,color,dy)=>{if(!time||time==='--:--')return '';const target=norm(time);let idx=xy.findIndex(p=>norm(p.time)>=target);if(idx<0)idx=xy.length-1;const p=xy[idx],y=Math.max(10,Math.min(96,p.y+dy));return `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="5" fill="${color}"/><text x="${p.x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" font-size="10" font-weight="900" fill="${color}">${label}</text>`};return `<svg class="live-chart" viewBox="0 0 300 100"><line x1="8" y1="86" x2="292" y2="86" stroke="#eef1f3"/><line x1="8" y1="12" x2="292" y2="12" stroke="#eef1f3"/><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/>${mark(row.buyTime,'B','#2563eb',-8)}${mark(row.sellTime,'S','#ec5f6b',14)}</svg>`}
function initAudio(){try{audioCtx=new (window.AudioContext||window.webkitAudioContext)()}catch(e){}}
function maybeBeep(r){const sig=r.signal||'',side=alertSide(sig);if(!side||sig.includes('休市')||sig.includes('异常'))return;const today=new Date().toISOString().slice(0,10);const dayKey=`alert:${today}:${r.code}:${side}`;const count=Number(localStorage.getItem(dayKey)||0);if(count>=1)return;const key=[r.code,side].join(':');const now=Date.now();if(lastAlertKeys.has(key)&&now-lastAlertKeys.get(key)<600000)return;lastAlertKeys.set(key,now);localStorage.setItem(dayKey,String(count+1));if(audioCtx)[760,1040,760].forEach((f,i)=>setTimeout(()=>tone(f,160),i*210));speakBrowser(`${r.name}，现价${Number(r.price||0).toFixed(2)}，${sig}，${r.reason||''}`)}
function tone(freq,duration){try{const osc=audioCtx.createOscillator(),gain=audioCtx.createGain();osc.frequency.value=freq;gain.gain.value=.075;osc.connect(gain);gain.connect(audioCtx.destination);osc.start();setTimeout(()=>osc.stop(),duration)}catch(e){}}
function speakBrowser(text){try{if(!('speechSynthesis'in window))return;const u=new SpeechSynthesisUtterance(text);u.lang='zh-CN';u.rate=1.05;u.volume=.9;window.speechSynthesis.cancel();window.speechSynthesis.speak(u)}catch(e){}}
function append(t){const o=$('out');o.textContent=(o.textContent==='已就绪。'?'':o.textContent+'\n\n')+t;o.scrollTop=o.scrollHeight}
function clearAll(){$('out').textContent='已清空。';$('status').textContent='就绪'}
function escapeHtml(s){return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
</script>
</body>
</html>"""
SIMULATION_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>A股模拟测试</title>
<style>
:root{--ink:#20252b;--muted:#7b8792;--line:#e9eef1;--green:#35b978;--red:#ec5f6b;--yellow:#d99a1b}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:13px/1.55 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 88% 0,#7cefed,transparent 34%),linear-gradient(135deg,#78939b,#f2fbfb 42%,#f7fbfb)}
button,input{font:inherit}button{height:36px;border:1px solid var(--line);border-radius:11px;background:#fff;padding:0 15px;font-weight:850;cursor:pointer;box-shadow:0 8px 18px rgba(31,46,56,.06)}button.primary{background:#20252b;color:#fff;border-color:#20252b}button:disabled{opacity:.6;cursor:wait}
.page{min-height:100vh;padding:14px;display:grid;grid-template-rows:auto auto auto auto minmax(0,1fr);gap:9px}.top,.controls,.panel,.metric,.progress{background:rgba(255,255,255,.94);border:1px solid rgba(255,255,255,.85);border-radius:16px;box-shadow:0 14px 38px rgba(25,45,50,.12)}.top{height:58px;display:flex;align-items:center;justify-content:space-between;padding:0 16px}.title{font-size:20px;font-weight:950}.sub,.muted{color:var(--muted)}.controls{padding:9px 11px;display:flex;flex-wrap:wrap;gap:8px;align-items:end}.field span{display:block;font-size:10px;color:var(--muted);font-weight:850}.field input{width:132px;height:32px;border:1px solid var(--line);border-radius:10px;padding:0 10px}.field.wide input{width:220px}.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.metric{padding:10px 13px}.metric .k{font-size:10px;color:var(--muted);font-weight:850}.metric .v{font-size:19px;font-weight:950}.win{color:var(--yellow)}.pos{color:var(--green)!important}.neg{color:var(--red)!important}.layout{display:grid;grid-template-rows:minmax(0,1fr) auto;gap:9px;min-height:0}.panel{overflow:hidden;display:flex;flex-direction:column;min-height:0}.head{height:38px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 14px;font-weight:950}.badge{font-size:11px;color:#35a66d;background:#eafff1;border-radius:999px;padding:4px 9px}.rows{flex:1;overflow:auto;padding:0}.sim-table{min-width:1120px}.sim-head,.sim-row{display:grid;grid-template-columns:190px 110px minmax(320px,1fr) 90px 130px minmax(240px,.75fr);gap:12px;align-items:center}.sim-head{position:sticky;top:0;z-index:2;height:34px;padding:0 14px;background:#f8fbfb;border-bottom:1px solid var(--line);color:#7b8792;font-size:11px;font-weight:950}.sim-row{min-height:68px;border-bottom:1px solid #f1f3f4;padding:7px 14px}.sim-row:last-child{border-bottom:0}.stock{font-weight:950;font-size:14px}.code{color:var(--muted);margin-left:6px}.reason{color:#56616b;font-size:12px;line-height:1.45}.status{font-weight:900}.chart{width:100%;height:52px;display:block}.empty{padding:40px 12px;text-align:center;color:var(--muted)}.side{display:grid;grid-template-columns:1fr 1fr;gap:9px;min-height:138px;max-height:178px}.content{padding:10px 14px;overflow:auto;line-height:1.58;font-size:12px}.content b{display:block;margin:4px 0}.content ul{margin:4px 0 0;padding-left:18px}.run-item{border-bottom:1px solid #f2f4f5;padding:6px 0}.bar{height:3px;background:linear-gradient(90deg,#4cc9f0,#35b978);border-radius:99px;opacity:0}.bar.on{opacity:1;animation:pulse .9s infinite alternate}.progress{display:none;padding:8px 10px}.progress.on{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.step{border:1px solid #eef2f4;border-radius:11px;padding:7px 8px;background:#f9fbfb}.step b{display:block;font-size:12px}.step span{font-size:11px;color:var(--muted)}.step.active{border-color:#bdebd2;background:#effff5}.step.done b{color:var(--green)}@keyframes pulse{from{filter:brightness(.8)}to{filter:brightness(1.2)}}
@media(max-width:1050px){.metrics{grid-template-columns:repeat(2,1fr)}.sim-table{min-width:980px}.side{grid-template-columns:1fr;max-height:none}}
</style>
</head>
<body>
<div class="page">
  <div class="top"><div><div class="title">模拟测试</div><div class="sub">随机股票、日内曲线、买卖点、失败复盘、历史缓存</div></div><div><button onclick="location.href='/'">返回监控</button> <button onclick="location.href='/research'">选股研究</button></div></div>
  <div class="controls">
    <label class="field"><span>模拟资金</span><input id="cashInput" type="number" min="1000" step="10000" value="100000" oninput="syncTradeAmount()" /></label>
    <label class="field"><span>单笔金额</span><input id="tradeInput" type="number" min="1000" step="1000" value="20000" oninput="tradeManual=true;syncCards()" /></label>
    <label class="field"><span>测试股数</span><input id="sampleInput" type="number" min="1" max="30" step="1" value="10" oninput="syncCards()" /></label>
    <label class="field wide"><span>自定义股票</span><input id="stocksInput" placeholder="如 601899,601012,600580" oninput="stocksManual=true;syncCards()" /></label>
    <button onclick="syncWatchlistStocks(true)">同步监控股票</button>
    <label class="field"><span>黄线止盈%</span><input id="vwapProfitInput" type="number" min="0.10" max="1.00" step="0.05" value="0.25" oninput="syncCards()" /></label>
    <label class="field"><span>普通止盈%</span><input id="normalProfitInput" type="number" min="0.20" max="1.50" step="0.05" value="0.60" oninput="syncCards()" /></label>
    <label class="field"><span>尾盘目标%</span><input id="lateProfitInput" type="number" min="0.15" max="1.20" step="0.05" value="0.45" oninput="syncCards()" /></label>
    <button class="primary" onclick="runSim('simulate')">当日模拟</button>
    <button onclick="runSim('simulate5')">近5天测试</button>
    <button onclick="loadHistory()">刷新历史</button>
    <button onclick="clearView()">清空</button>
    <div id="status" class="sub">就绪</div>
  </div>
  <div id="loading" class="bar"></div>
  <div id="progress" class="progress">
    <div class="step" data-step="0"><b>准备样本</b><span>读取股票池</span></div>
    <div class="step" data-step="1"><b>抓取分时</b><span>并发读取行情</span></div>
    <div class="step" data-step="2"><b>筛选信号</b><span>VWAP与右侧确认</span></div>
    <div class="step" data-step="3"><b>模拟成交</b><span>一日一笔优先级</span></div>
    <div class="step" data-step="4"><b>生成复盘</b><span>统计胜率与问题</span></div>
  </div>
  <section class="metrics">
    <div class="metric"><div class="k">总资金</div><div id="cash" class="v">100,000元</div></div>
    <div class="metric"><div class="k">单笔金额</div><div id="trade" class="v">20,000元</div></div>
    <div class="metric"><div class="k">触发次数</div><div id="trigger" class="v">--</div></div>
    <div class="metric"><div class="k">胜率</div><div id="win" class="v win">--</div></div>
    <div class="metric"><div class="k">模拟盈亏</div><div id="pnl" class="v">--</div></div>
    <div class="metric"><div class="k">资金收益</div><div id="ret" class="v">--</div></div>
  </section>
  <section class="layout">
    <main class="panel"><div class="head"><span>日内曲线与买卖点</span><span id="count" class="badge">等待运行</span></div><div id="rows" class="rows empty">点击“当日模拟”后显示曲线、B/S买卖点、盈亏和失败原因。</div></main>
    <aside class="side"><div class="panel"><div class="head">策略复盘</div><div id="review" class="content">模拟后自动讨论失败位置和下一轮优化方向。</div></div><div class="panel"><div class="head">历史缓存</div><div id="history" class="content">正在读取历史记录。</div></div></aside>
  </section>
</div>
<script>
const $=id=>document.getElementById(id);let tradeManual=false,stocksManual=false,settingsTimer=null;
window.addEventListener('DOMContentLoaded',()=>{loadSettings();syncWatchlistStocks(false);loadHistory();restoreLatestSim();});
function pct(id,fallback){const n=Number($(id).value||fallback);return Math.max(0.05,Math.min(2,n))}
function options(){return {cash:Number($('cashInput').value||100000),trade:Number($('tradeInput').value||20000),sample:Number($('sampleInput').value||10),stocks:($('stocksInput')?.value||'').trim(),vwap_take_profit_pct:pct('vwapProfitInput',0.25),normal_take_profit_pct:pct('normalProfitInput',0.6),late_take_profit_pct:pct('lateProfitInput',0.45)}}
function setBusy(on){document.querySelectorAll('button').forEach(b=>b.disabled=on)}
async function runSim(name){setBusy(true);$('status').textContent='运行中';$('loading').classList.add('on');startProgress();saveSettings();try{const res=await fetch('/api/run/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(options())});markProgress(3);const data=await res.json();markProgress(4);updateStats(data.stats||{});renderRows(data.stocks||[]);renderReview((data.stats||{}).review||{},(data.stats||{}).history||{});$('status').textContent=data.summary||'完成';await loadHistory(false);finishProgress()}catch(e){$('status').textContent='失败：'+e.message;stopProgress()}$('loading').classList.remove('on');setBusy(false)}
async function syncWatchlistStocks(force){try{const data=await (await fetch('/api/watchlist',{cache:'no-store'})).json();const stocks=(data.stocks||[]).map(s=>s.code||String(s.symbol||'').slice(2)).filter(Boolean);if(!stocks.length)return;if(force||(!$('stocksInput').value.trim()&&!stocksManual)){$('stocksInput').value=stocks.join(',');$('sampleInput').value=Math.min(Math.max(stocks.length,1),30);stocksManual=false;syncCards();$('status').textContent='已同步监控股票：'+stocks.join('、')}}catch(e){if(force)$('status').textContent='同步监控股票失败：'+(e.message||e)}}
let progressTimer=null,progressStep=0;
function startProgress(){progressStep=0;$('progress').classList.add('on');markProgress(0);clearInterval(progressTimer);progressTimer=setInterval(()=>{progressStep=Math.min(progressStep+1,3);markProgress(progressStep)},1800)}
function markProgress(step){progressStep=Math.max(progressStep,step);document.querySelectorAll('#progress .step').forEach((el,i)=>{el.classList.toggle('done',i<progressStep);el.classList.toggle('active',i===progressStep)})}
function finishProgress(){clearInterval(progressTimer);markProgress(5);setTimeout(()=>$('progress').classList.remove('on'),1200)}
function stopProgress(){clearInterval(progressTimer);document.querySelectorAll('#progress .step').forEach(el=>el.classList.remove('active'))}
function syncTradeAmount(){if(!tradeManual){const cash=Number($('cashInput').value||100000);$('tradeInput').value=Math.max(1000,Math.floor(cash*.2/1000)*1000)}syncCards()}
function syncCards(){const o=options();$('cash').textContent=formatYuan(o.cash);$('trade').textContent=formatYuan(o.trade);clearTimeout(settingsTimer);settingsTimer=setTimeout(saveSettings,450)}
async function loadSettings(){try{const s=await (await fetch('/api/settings',{cache:'no-store'})).json();if(s.ok){$('cashInput').value=s.cash;$('tradeInput').value=s.trade;$('sampleInput').value=s.sample;$('vwapProfitInput').value=s.vwap_take_profit_pct??0.25;$('normalProfitInput').value=s.normal_take_profit_pct??0.6;$('lateProfitInput').value=s.late_take_profit_pct??0.45}}catch(e){}syncCards()}
function saveSettings(){fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(options())}).catch(()=>{})}
function formatYuan(n){return Number(n||0).toLocaleString('zh-CN',{maximumFractionDigits:0})+'元'}function parseYuan(s){return Number(String(s||'').replace(/[^\d.-]/g,''))||0}
function updateStats(s,persist=true){if(!Object.keys(s).length)return;$('cash').textContent=s.endingCash||s.cash||'--';$('trade').textContent=s.trade||'--';$('trigger').textContent=s.trigger||'--';$('win').textContent=s.win||'--';$('pnl').textContent=s.pnl||'--';$('ret').textContent=s.return||'--';$('pnl').className='v '+((s.pnl||'').startsWith('-')?'neg':'pos');const ending=parseYuan(s.endingCash);if(persist&&ending>0){$('cashInput').value=ending.toFixed(2);saveSettings()}}
function renderRows(rows){if(!rows.length){$('rows').className='rows empty';$('rows').textContent='暂无股票明细。';$('count').textContent='0 只';return}$('rows').className='rows';$('count').textContent=rows.length+' 只';$('rows').innerHTML=`<div class="sim-table"><div class="sim-head"><span>股票</span><span>状态</span><span>曲线与买卖点</span><span>胜率</span><span>盈亏金额</span><span>原因</span></div>${rows.map(r=>{const pos=Number(r.pnl||0)>0,cls=pos?'pos':'neg';const win=Number(r.pnl||0)>0?'100%':(r.action&&r.action!=='未触发'?'0%':'--');return `<div class="sim-row"><div><span class="stock">${esc(r.name)}</span><span class="code">${esc(r.code)}</span></div><div class="status">${esc(r.action||'--')}</div><div>${chart(r)}</div><div class="${cls}">${win}</div><div><span class="${cls}" style="font-weight:950">${esc(r.pnlText||'--')}</span><div class="muted">${esc(r.money||'--')}</div></div><div class="reason">${esc(r.reason||r.detail||'')}</div></div>`}).join('')}</div>`}
function renderReview(review,history){const parts=[];if(review.headline)parts.push(`<b>本轮复盘</b><div>${esc(review.headline)}</div>`);if((review.focus||[]).length)parts.push('<ul>'+review.focus.map(x=>`<li>${esc(x.name)} ${esc(x.code)}：${esc(x.type)}，${esc(x.suggestion)}</li>`).join('')+'</ul>');if((review.suggestions||[]).length)parts.push('<b>下一轮优化</b><ul>'+review.suggestions.map(x=>`<li>${esc(x)}</li>`).join('')+'</ul>');if(history&&history.runs){const fails=(history.failures||[]).map(x=>`${esc(x.type)} ${x.count}次`).join('；')||'暂无高频问题';const noTrig=history.noTrigger?`，未触发 ${history.noTrigger} 次`:'';parts.push(`<b>累计统计</b><div>${history.runs} 次模拟，${history.trades} 笔交易，总胜率 ${history.winRate}，总盈亏 ${history.pnl}${noTrig}。问题：${fails}</div>`)}$('review').innerHTML=parts.join('')||'模拟后自动讨论失败位置和下一轮优化方向。'}
async function loadHistory(showStatus=true){try{const data=await (await fetch('/api/simulation_history',{cache:'no-store'})).json();const h=data.history||{},runs=data.runs||[];$('history').innerHTML=`<b>总统计</b><div>${h.runs||0} 次模拟，${h.trades||0} 笔交易，总胜率 ${h.winRate||'--'}，总盈亏 ${h.pnl||'--'}</div>`+'<b>最近记录</b>'+(runs.length?runs.map(r=>`<div class="run-item"><b>${esc(r.time||'--')}</b><div class="muted">触发 ${r.triggered||0}/${r.total||0}，盈利 ${r.wins||0}，盈亏 ${Number(r.pnl||0).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}元</div><div class="muted">${esc((r.review||{}).headline||'')}</div></div>`).join(''):'<div class="muted">暂无历史。</div>');if(showStatus)$('status').textContent='历史已读取'}catch(e){$('history').textContent='历史读取失败：'+e.message}}
async function restoreLatestSim(){try{const data=await (await fetch('/api/simulation_history',{cache:'no-store'})).json();const latest=data.latest||{};if(latest.stats){updateStats(latest.stats,false);renderRows(latest.stocks||[]);renderReview((latest.stats||{}).review||{},(latest.stats||{}).history||{});$('status').textContent='已恢复最近一次模拟'}}catch(e){}}
function chart(row){const series=(row.prices||[]).filter(x=>Number(x.price)>0),c=Number(row.pnl||0)>=0?'#35b978':'#ec5f6b';if(series.length<2)return '<svg class="chart" viewBox="0 0 420 86"><line x1="10" y1="43" x2="410" y2="43" stroke="#d7dde2" stroke-width="2"/></svg>';const step=Math.max(1,Math.floor(series.length/95)),points=series.filter((_,i)=>i%step===0),values=points.map(x=>Number(x.price));const min=Math.min(...values),max=Math.max(...values),span=Math.max(max-min,.01);const xy=points.map((p,i)=>({x:10+(i/(points.length-1))*400,y:74-((Number(p.price)-min)/span)*62,time:p.time}));const pts=xy.map(p=>`${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');const mark=(time,label,color,dy)=>{if(!time||time==='--:--')return '';let idx=xy.findIndex(p=>String(p.time)>=String(time).replace(':',''));if(idx<0)idx=xy.length-1;const p=xy[idx],y=Math.max(10,Math.min(82,p.y+dy));return `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="5" fill="${color}"/><text x="${p.x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" font-size="10" font-weight="900" fill="${color}">${label}</text>`};return `<svg class="chart" viewBox="0 0 420 86"><line x1="10" y1="74" x2="410" y2="74" stroke="#eef1f3"/><line x1="10" y1="12" x2="410" y2="12" stroke="#eef1f3"/><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>${mark(row.buyTime,'B','#2563eb',-9)}${mark(row.sellTime,'S','#ec5f6b',14)}</svg>`}
function clearView(){renderRows([]);renderReview({},{});$('status').textContent='已清空'}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
</script>
</body>
</html>"""
RESEARCH_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>A股选股研究</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.55 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:#20252b;background:radial-gradient(circle at 88% 0,#7cefed,transparent 34%),linear-gradient(135deg,#78939b,#f2fbfb 42%,#f7fbfb)}button,a.btn{height:36px;border:1px solid #e9eef1;border-radius:11px;background:#fff;color:#20252b;text-decoration:none;padding:0 15px;font-weight:850;cursor:pointer;box-shadow:0 8px 18px rgba(31,46,56,.06);display:inline-flex;align-items:center}button.primary{background:#20252b;color:#fff;border-color:#20252b}button:disabled{opacity:.6;cursor:wait}input,select{height:36px;border:1px solid #e9eef1;border-radius:11px;background:#fff;padding:0 12px;font:inherit;font-weight:800}.shell{width:min(1680px,calc(100vw - 28px));min-height:calc(100vh - 36px);margin:18px auto;padding:18px;border-radius:22px;background:rgba(255,255,255,.94);box-shadow:0 24px 70px rgba(25,45,50,.18);display:grid;grid-template-rows:auto auto auto 1fr auto;gap:14px}.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.title{font-size:23px;font-weight:950}.sub,.muted{color:#7b8792}.actions,.single-bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.actions{justify-content:flex-end}.single-bar{padding:10px 12px;background:#fff;border:1px solid #e9eef1;border-radius:16px;box-shadow:0 10px 30px rgba(31,46,56,.06)}.single-bar input{width:170px}.status{min-height:24px;color:#58636d;font-weight:750}.grid{display:grid;grid-template-columns:300px 1fr;gap:14px;min-height:0}.panel{background:#fff;border:1px solid #e9eef1;border-radius:16px;overflow:hidden;box-shadow:0 10px 30px rgba(31,46,56,.06)}.head{height:46px;padding:0 16px;border-bottom:1px solid #e9eef1;display:flex;align-items:center;justify-content:space-between;font-weight:950}.body{padding:12px;overflow:auto}.cat{width:100%;justify-content:space-between;margin:5px 0;box-shadow:none;background:#f6f8f9}.cat.active{background:#20252b;color:#fff}.badge{border-radius:999px;background:#eafff1;color:#35a66d;padding:4px 9px;font-size:12px;font-weight:850}table{width:100%;border-collapse:collapse}th,td{padding:12px 14px;border-bottom:1px solid #edf0f2;text-align:left;vertical-align:top}th{font-size:12px;color:#7b8792}.stock{font-weight:950}.code{color:#7b8792;margin-left:7px}.score{font-weight:950;color:#d99a1b}.up{color:#ec5f6b}.down{color:#35b978}.tag{display:inline-flex;border-radius:999px;background:#f0f4f5;padding:4px 8px;color:#58636d;font-size:12px;font-weight:750}.agents{line-height:1.75;color:#303942}.detail{padding:16px}.detail h3{margin:0 0 8px}.empty{text-align:center;padding:35px;color:#7b8792;font-weight:800}@media(max-width:900px){.grid{grid-template-columns:1fr}.top{display:block}.actions{justify-content:flex-start;margin-top:12px}th,td{padding:10px 8px;font-size:12px}}
</style>
</head>
<body>
<main class="shell">
  <div class="top"><div><div class="title">A股选股研究</div><div class="sub">多Agent评审 + AI选股，输出10只跨行业候选</div></div><div class="actions"><a class="btn" href="/">返回监控</a><button class="primary" id="reviewBtn" onclick="loadData('review')">评审选股</button><button id="geminiBtn" onclick="loadData('gemini')">AI选股</button><button id="checkBtn" onclick="checkAi()">检测AI</button></div></div>
  <div class="status" id="status">准备加载本地选股...</div>
  <section class="single-bar">
    <b>单股研究</b>
    <input id="singleInput" placeholder="输入代码，如 600580" onkeydown="if(event.key==='Enter')researchSingle()" />
    <select id="singleMode"><option value="review">评审研究</option><option value="gemini">AI研究</option></select>
    <button id="singleBtn" onclick="researchSingle()">开始研究</button>
    <span class="muted">输出技术、消息、基本面、资金、风控和3个月预测</span>
  </section>
  <section class="grid"><aside class="panel"><div class="head">分类池 <span class="badge" id="count">0只</span></div><div class="body" id="categories"></div></aside><section class="panel"><div class="head">候选股票</div><div style="overflow:auto;max-height:62vh"><table><thead><tr><th>股票</th><th>分类</th><th>层级</th><th>3个月预测</th><th>中期分</th><th>价格</th><th>涨跌</th><th>多Agent结论</th></tr></thead><tbody id="rows"><tr><td colspan="8" class="empty">加载中...</td></tr></tbody></table></div></section></section>
  <section class="panel detail" id="detail" style="display:none"></section>
</main>
<script>
const $=id=>document.getElementById(id);let allRows=[],activeCategory='全部';
function esc(v){return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]))}
function setBusy(on){['reviewBtn','geminiBtn','checkBtn','singleBtn'].forEach(id=>{const el=$(id);if(el)el.disabled=on})}
function normalizeList(data){if(Array.isArray(data))return data;if(Array.isArray(data.stocks))return data.stocks;if(Array.isArray(data.rows))return data.rows;return[]}
function clsChange(v){const n=Number(v);return n>0?'up':n<0?'down':''}
function renderCategories(){const map=new Map();allRows.forEach(r=>map.set(r.category||'综合观察',(map.get(r.category||'综合观察')||0)+1));const cats=[['全部',allRows.length],...Array.from(map.entries())];$('categories').innerHTML=cats.map(([name,count])=>`<button class="cat ${name===activeCategory?'active':''}" data-cat="${esc(name)}"><span>${esc(name)}</span><small>${count}只</small></button>`).join('');document.querySelectorAll('.cat').forEach(btn=>btn.addEventListener('click',()=>{activeCategory=btn.dataset.cat||'全部';renderCategories();renderRows()}))}
function currentRows(){return activeCategory==='全部'?allRows:allRows.filter(r=>(r.category||'综合观察')===activeCategory)}
function renderRows(){const rows=currentRows();$('count').textContent=rows.length+'只';if(!rows.length){$('rows').innerHTML='<tr><td colspan="8" class="empty">暂无候选</td></tr>';return}$('rows').innerHTML=rows.map((r,idx)=>{const agents=Array.isArray(r.agents)&&r.agents.length?r.agents:['技术员：本地规则初筛','资金员：等待量价确认','风控员：控制仓位'];const medium=r.mediumAnalysis||{},forecast=r.forecast||{};return `<tr onclick="showDetail(${idx})" style="cursor:pointer"><td><span class="stock">${esc(r.name)}</span><span class="code">${esc(r.code)}</span></td><td><span class="tag">${esc(r.category||'综合观察')}</span><div class="muted">${esc(r.categoryReason||r.reason||'')}</div></td><td><span class="tag">${esc(r.tier||'等待确认')}</span></td><td><span class="tag">${esc(forecast.label||'观察')}</span><div class="muted">${esc(forecast.expected||'--')}｜置信${esc(forecast.confidence||'--')}</div></td><td class="score">${esc(r.mediumScore??'--')}/10</td><td>${esc(r.price??'--')}</td><td class="${clsChange(r.change)}">${esc(r.change??'--')}%</td><td><div class="agents">${agents.slice(0,4).map(esc).join('<br>')}</div><div class="muted">催化：${esc(medium.catalyst||'等待消息确认')}</div></td></tr>`}).join('')}
function showDetail(idx){const r=currentRows()[idx];if(!r)return;const agents=Array.isArray(r.agents)?r.agents:[];const reasons=Array.isArray(r.reasons)?r.reasons:[];const daily=r.dailyAnalysis||{},medium=r.mediumAnalysis||{},forecast=r.forecast||{};$('detail').style.display='block';$('detail').innerHTML=`<h3>${esc(r.name)} <span class="code">${esc(r.code)}</span></h3><p><span class="tag">${esc(r.category||'综合观察')}</span> <span class="tag">${esc(r.tier||'等待确认')}</span> <span class="tag">${esc(r.useCase||'等待放量')}</span> <span class="tag">3个月 ${esc(forecast.label||'观察')}</span> <span class="tag">弹性 ${esc(forecast.expected||'--')}</span> <span class="tag">中期分 ${esc(r.mediumScore??'--')}/10</span></p><p class="muted">${esc(r.decision||r.categoryReason||'本地多因子观察')}</p><div class="agents">${agents.map(esc).join('<br>')}</div><p><b>未来1-3个月预测</b><br>结论：${esc(forecast.label||'观察')}｜预估弹性：${esc(forecast.expected||'--')}｜置信度：${esc(forecast.confidence||'--')}<br>依据：${esc(forecast.basis||'行业催化+趋势资金')}</p><p><b>未来1-3个月逻辑</b><br>逻辑：${esc(medium.logic||'等待更多数据')}<br>催化：${esc(medium.catalyst||'等待消息确认')}<br>风险：${esc(medium.risk||'控制仓位')}<br>周期：${esc(r.horizon||'1-3个月观察')}</p><p><b>全方面调研框架</b><br>日线：${esc(daily.daily||'等待更多数据')}<br>新闻：${esc(daily.news||'等待新闻确认')}<br>资金：${esc(daily.money||'等待成交确认')}<br>结论：${esc(r.decision||'等待价格、成交量和消息共振')}</p><p class="muted">${reasons.map(x=>'· '+esc(x)).join('<br>')}</p>`}
function renderSingleDetail(r){const agents=Array.isArray(r.agents)?r.agents:[];const reasons=Array.isArray(r.reasons)?r.reasons:[];const daily=r.dailyAnalysis||{},medium=r.mediumAnalysis||{},forecast=r.forecast||{};$('detail').style.display='block';$('detail').innerHTML=`<h3>单股研究：${esc(r.name)} <span class="code">${esc(r.code)}</span></h3><p><span class="tag">${esc(r.category||'综合观察')}</span> <span class="tag">${esc(r.tier||'等待确认')}</span> <span class="tag">${esc(r.useCase||'等待放量')}</span> <span class="tag">价格 ${esc(r.price??'--')}</span> <span class="tag">涨跌 ${esc(r.change??'--')}%</span> <span class="tag">评分 ${esc(r.score??'--')}</span></p><p><b>多Agent结论</b></p><div class="agents">${agents.map(esc).join('<br>')}</div><p><b>3个月预测</b><br>结论：${esc(forecast.label||'观察')}｜预估弹性：${esc(forecast.expected||'--')}｜置信度：${esc(forecast.confidence||'--')}｜依据：${esc(forecast.basis||'行业催化+趋势资金')}</p><p><b>研究拆解</b><br>技术：${esc(daily.daily||'等待更多数据')}<br>消息：${esc(daily.news||'等待新闻确认')}<br>基本面/行业：${esc(medium.logic||'等待更多数据')}<br>资金：${esc(daily.money||'等待成交确认')}<br>风控：${esc(medium.risk||daily.risk||'控制仓位')}</p><p class="muted">${reasons.map(x=>'· '+esc(x)).join('<br>')}</p>`;$('detail').scrollIntoView({behavior:'smooth',block:'start'})}
async function researchSingle(){const code=($('singleInput').value||'').trim();if(!code){$('status').textContent='请输入股票代码，例如 600580。';return}setBusy(true);$('status').textContent='正在进行单股多因子研究...';try{const mode=$('singleMode').value||'local';const res=await fetch('/api/single_research?code='+encodeURIComponent(code)+'&mode='+encodeURIComponent(mode),{cache:'no-store'});const data=await res.json();if(!data.ok)throw new Error(data.message||'单股研究失败');$('status').textContent=(data.message||'单股研究完成')+(data.updatedAt?'｜更新时间 '+esc(data.updatedAt):'');renderSingleDetail(data.stock)}catch(e){$('status').textContent='单股研究失败：'+(e.message||e)}finally{setBusy(false)}}
async function loadData(mode){setBusy(true);const loadingText=mode==='gemini'?'正在请求 AI 选股，超时会自动切回评审选股...':'正在让TradingAgents、UZI评审、Kronos路径因子共同选股...';$('status').textContent=loadingText;$('rows').innerHTML='<tr><td colspan="8" class="empty">加载中...</td></tr>';try{const ctrl=new AbortController();const timer=setTimeout(()=>ctrl.abort(),mode==='gemini'?8500:9000);const res=await fetch('/api/screener?mode='+encodeURIComponent(mode),{cache:'no-store',signal:ctrl.signal});clearTimeout(timer);const data=await res.json();allRows=normalizeList(data);activeCategory='全部';const stamp=data.updatedAt?`｜更新时间 ${esc(data.updatedAt)}`:'';$('status').textContent=(data.aiMessage||data.message||(mode==='gemini'?'AI选股已完成。':'评审团选股已完成。'))+stamp;renderCategories();renderRows()}catch(e){$('status').textContent=mode==='gemini'?'AI 暂时无响应，已切换本地选股。':'本地选股请求失败，请刷新页面。';if(mode==='gemini')return loadData('review');$('rows').innerHTML=`<tr><td colspan="8" class="empty">${esc(e.message||e)}</td></tr>`}finally{setBusy(false)}}
async function checkAi(){setBusy(true);$('status').textContent='正在检测 AI...';try{const ctrl=new AbortController();const timer=setTimeout(()=>ctrl.abort(),7000);const data=await(await fetch('/api/gemini_status',{cache:'no-store',signal:ctrl.signal})).json();clearTimeout(timer);$('status').textContent=(data.ok?'AI 可用：':'AI 异常：')+(data.message||'无返回')}catch(e){$('status').textContent='AI 检测超时或网络不可达，本地选股仍可使用。'}finally{setBusy(false)}}
document.addEventListener('DOMContentLoaded',()=>{const code=new URLSearchParams(location.search).get('code');if(code){$('singleInput').value=code;researchSingle()}else{loadData('review')}});
</script>
</body>
</html>"""
def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"http://{HOST}:{PORT}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


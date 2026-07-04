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
import mimetypes
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
ASSETS_DIR = BASE_DIR / "assets"
ADAPTIVE_STRATEGY_PATH = BASE_DIR / "adaptive_strategy.json"
USERS_PATH = BASE_DIR / "commercial_users.json"
SESSIONS_PATH = BASE_DIR / "commercial_sessions.json"
ACTIVATION_CODES_PATH = BASE_DIR / "activation_codes.json"
LAST_GEMINI_ERROR = ""
MARKET_CONTEXT_CACHE: dict = {"ts": 0.0, "data": {}}
GEMINI_INTRADAY_CACHE: dict[str, dict] = {}
URGENT_NEWS_CACHE: dict[str, dict] = {}
LONGHUBANG_CACHE: dict = {"ts": 0.0, "data": {}}
LONGHUBANG_RANK_CACHE: dict = {"ts": 0.0, "data": {}}
RPS_FACTOR_CACHE: dict = {"ts": 0.0, "data": {}}
SCREENER_HISTORY_LIMIT = 12
SESSIONS: dict[str, str] = {}
SESSION_EXPIRES: dict[str, float] = {}
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
REQUEST_EMAIL: contextvars.ContextVar[str] = contextvars.ContextVar("request_email", default="")
LOGIN_FAILURES: dict[str, dict] = {}
MAX_LOGIN_FAILURES = 6
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_LOCK_SECONDS = 15 * 60
BUILTIN_ACTIVATION_CODES = {
    "T9-DEMO-MONTH": {"plan": "月卡", "days": 31, "watchLimit": 20, "aiReviewLimit": 80},
    "T99-DEMO-LIFE": {"plan": "永久版", "days": 36500, "watchLimit": 200, "aiReviewLimit": 500},
}

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
        if path.startswith("/assets/"):
            self._send_asset(path)
            return
        if path in {"/", "/index.html", "/landing"}:
            self._send_html(LANDING_HTML)
            return
        if path == "/app":
            self._send_html(HTML)
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
        if path == "/recharge":
            self._send_html(RECHARGE_HTML)
            return
        if path == "/admin":
            self._send_html(ADMIN_HTML)
            return
        if path == "/research":
            self._send_html(RESEARCH_HTML)
            return
        if path == "/longhubang":
            self._send_html(LONGHUBANG_HTML)
            return
        if path == "/rps":
            self._send_html(RPS_HTML)
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
        if path == "/api/longhubang_rank":
            query = parse_qs(parsed_url.query)
            limit = clamp_int((query.get("limit") or ["30"])[0], 30, 5, 80)
            self._send_json(longhubang_rank_payload(limit))
            return
        if path == "/api/rps":
            query = parse_qs(parsed_url.query)
            limit = clamp_int((query.get("limit") or ["10"])[0], 10, 5, 30)
            self._send_json(rps_payload(limit))
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
        if path == "/api/admin/users":
            self._send_json(admin_users_payload())
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
        if path == "/api/redeem":
            self._send_json(redeem_activation_payload(email, self._read_json()))
            return
        self.send_error(404)

    def log_message(self, _format: str, *args: object) -> None:
        return

    def _requires_login(self, path: str) -> bool:
        public_paths = {"/", "/index.html", "/landing", "/login", "/register", "/account", "/api/account", "/api/login", "/api/register", "/api/logout", "/api/status"}
        if path in public_paths:
            return False
        if path.startswith("/assets/"):
            return False
        return path in {"/app", "/commercial", "/research", "/longhubang", "/rps", "/simulation", "/admin", "/recharge"} or path.startswith("/api/")

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _send_asset(self, path: str) -> None:
        rel = urllib.parse.unquote(path.removeprefix("/assets/")).replace("\\", "/")
        if "/" in rel or rel.startswith("."):
            self.send_error(404)
            return
        file_path = ASSETS_DIR / rel
        if not file_path.is_file():
            self.send_error(404)
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(file_path))[0] or "application/octet-stream")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
    provider = str(data.get("aiProvider") or current.get("aiProvider") or "ChatGPT").strip()
    provider_alias = {
        "OpenAI": "ChatGPT",
        "OpenAI兼容": "ThirdParty",
        "OpenAI Compatible": "ThirdParty",
        "OpenAICompatible": "ThirdParty",
        "第三方API": "ThirdParty",
    }
    provider = provider_alias.get(provider, provider)
    if provider not in {"ChatGPT", "Gemini", "Claude", "ThirdParty"}:
        provider = "ChatGPT"
    model_default = {"ChatGPT": "gpt-4o-mini", "Gemini": "gemini-2.5-flash", "Claude": "claude-sonnet-4", "ThirdParty": "gpt-4o-mini"}[provider]
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
    provider = str(data.get("aiProvider") or "ChatGPT")
    provider = {"OpenAI": "ChatGPT", "OpenAI Compatible": "ThirdParty", "OpenAI兼容": "ThirdParty", "OpenAICompatible": "ThirdParty", "第三方API": "ThirdParty"}.get(provider, provider)
    if provider not in {"ChatGPT", "Gemini", "Claude", "ThirdParty"}:
        provider = "ChatGPT"
    provider_labels = {"ChatGPT": "ChatGPT", "Gemini": "Gemini", "Claude": "Claude", "ThirdParty": "第三方API"}
    model_defaults = {"ChatGPT": "gpt-4o-mini", "Gemini": "gemini-2.5-flash", "Claude": "claude-sonnet-4", "ThirdParty": "gpt-4o-mini"}
    return {
        "aiProvider": provider,
        "aiProviderLabel": provider_labels[provider],
        "aiModel": str(data.get("aiModel") or model_defaults[provider]),
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


def load_activation_codes() -> dict:
    try:
        data = json.loads(ACTIVATION_CODES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"codes": {}, "used": {}}
    except Exception:
        return {"codes": {}, "used": {}}


def save_activation_codes(data: dict) -> None:
    ACTIVATION_CODES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def activation_definition(code: str, store: dict) -> dict | None:
    code = normalize_activation_code(code)
    custom = (store.get("codes") or {}).get(code)
    if isinstance(custom, dict):
        return custom
    return BUILTIN_ACTIVATION_CODES.get(code)


def normalize_activation_code(code: str) -> str:
    return re.sub(r"\s+", "", str(code or "")).upper()


def redeem_activation_payload(email: str, data: dict) -> dict:
    if not email:
        return {"ok": False, "message": "请先登录后充值。"}
    code = normalize_activation_code(data.get("code") or "")
    if not code:
        return {"ok": False, "message": "请输入激活码。"}
    code_store = load_activation_codes()
    used = code_store.setdefault("used", {})
    if code in used and used[code].get("email") != email:
        return {"ok": False, "message": "这个激活码已经被其他账号使用。"}
    definition = activation_definition(code, code_store)
    if not definition:
        return {"ok": False, "message": "激活码无效，请核对后再试。"}
    user_store = load_users()
    user = user_store.get("users", {}).get(email)
    if not user:
        return {"ok": False, "message": "账号不存在，请重新登录。"}
    now = datetime.now()
    days = int(definition.get("days") or 31)
    plan = str(definition.get("plan") or "月卡")
    current_expire = parse_datetime(user.get("planExpireAt"))
    base = current_expire if current_expire and current_expire > now else now
    expire = base + timedelta(days=days)
    user["plan"] = plan
    user["planExpireAt"] = expire.strftime("%Y-%m-%d %H:%M:%S") if days < 30000 else "永久"
    user["watchLimit"] = int(definition.get("watchLimit") or (200 if plan == "永久版" else 20))
    user["aiReviewLimit"] = int(definition.get("aiReviewLimit") or (500 if plan == "永久版" else 80))
    user.setdefault("activationHistory", []).append({
        "code": code,
        "plan": plan,
        "redeemedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "expireAt": user["planExpireAt"],
    })
    used[code] = {"email": email, "plan": plan, "redeemedAt": now.strftime("%Y-%m-%d %H:%M:%S")}
    save_users(user_store)
    save_activation_codes(code_store)
    return {"ok": True, "message": f"充值成功，已开通{plan}。", "account": public_user(user)}


def parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text or text == "永久":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            pass
    return None


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


def admin_users_payload() -> dict:
    store = load_users()
    sessions = load_sessions()
    active = {email for token, email in sessions.items() if SESSION_EXPIRES.get(token, 0) > time_mod.time()}
    rows = []
    for email, user in sorted(store.get("users", {}).items()):
        row = public_user(user)
        row["loggedIn"] = email in active
        rows.append(row)
    return {"ok": True, "users": rows, "count": len(rows)}

def public_user(user: dict) -> dict:
    return {
        "email": user.get("email"),
        "nickname": user.get("nickname"),
        "plan": user.get("plan", "体验版"),
        "planExpireAt": user.get("planExpireAt", ""),
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


def quote_age_seconds(quote) -> float | None:
    raw = str(getattr(quote, "time_raw", "") or "")
    if not raw or len(raw) < 14:
        return None
    try:
        dt = datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
        return max(0.0, (datetime.now() - dt).total_seconds())
    except Exception:
        return None


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
            age_seconds = quote_age_seconds(quote) if quote else None
            quote_stale = bool(market_is_open() and age_seconds is not None and age_seconds > 90)
            if quote_stale:
                signal = None
            signal_action = signal.action if signal else ""
            is_buy_signal = any(word in signal_action for word in ("低吸", "买入"))
            is_sell_signal = any(word in signal_action for word in ("高抛", "卖出"))
            is_open = market_is_open()
            rapid_news = rapid_news_payload(stock.name, stock.code, minutes, quote)
            agents = monitor_agents_payload(stock.name, quote, minutes, avg, dev, signal, is_open, market_context, gemini_cached_advice(stock.code))
            if rapid_news.get("active"):
                agents.insert(2, rapid_news_agent(rapid_news))
            if quote_stale:
                display_signal, display_reason = "行情延迟", f"报价时间超过{int(age_seconds or 0)}秒未更新，暂停买卖点提醒，等待下一笔有效行情。"
            else:
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
                    "quoteAgeSeconds": age_seconds,
                    "quoteStale": quote_stale,
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
        age_minutes = round((datetime.now().timestamp() - ts) / 60, 1) if ts else 99999
        quote_time = datetime.fromtimestamp(ts, timezone(timedelta(hours=8))).strftime("%m-%d %H:%M") if ts else ""
        return {"name": name, "symbol": symbol, "price": price, "change": change, "unit": unit, "weight": weight, "time": quote_time, "ageMinutes": age_minutes, "isStale": age_minutes > 180, "source": "Yahoo 5分钟快照"}
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


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value in (None, "", "-"):
            return default
        return float(value)
    except Exception:
        return default


def fetch_longhubang_map(days: int = 5) -> dict[str, dict]:
    now_ts = time_mod.time()
    if now_ts - float(LONGHUBANG_CACHE.get("ts") or 0) < 300:
        return dict(LONGHUBANG_CACHE.get("data") or {})
    end = datetime.now()
    start = end - timedelta(days=days)
    params = urllib.parse.urlencode({
        "sortColumns": "TRADE_DATE,SECURITY_CODE",
        "sortTypes": "-1,1",
        "pageSize": "120",
        "pageNumber": "1",
        "reportName": "RPT_DAILYBILLBOARD_DETAILS",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "filter": f"(TRADE_DATE>='{start:%Y-%m-%d}')(TRADE_DATE<='{end:%Y-%m-%d}')",
    })
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?" + params
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "replace")
        payload = json.loads(raw)
        result = payload.get("result") or payload.get("data") or {}
        rows = result.get("data") if isinstance(result, dict) else []
    except Exception:
        rows = []
    mapped: dict[str, dict] = {}
    for item in rows or []:
        code = re.sub(r"\D", "", str(item.get("SECURITY_CODE") or item.get("SECURITYCODE") or ""))
        if len(code) != 6:
            continue
        buy = safe_float(item.get("BILLBOARD_BUY_AMT") or item.get("BUY_AMT") or item.get("TOTAL_BUYAMT"))
        sell = safe_float(item.get("BILLBOARD_SELL_AMT") or item.get("SELL_AMT") or item.get("TOTAL_SELLAMT"))
        net = safe_float(item.get("BILLBOARD_NET_AMT") or item.get("NET_BUY_AMT") or item.get("NETAMT"), buy - sell)
        reason = str(item.get("EXPLANATION") or item.get("EXPLAIN") or item.get("BILLBOARD_TYPE") or "龙虎榜上榜")
        trade_date = str(item.get("TRADE_DATE") or item.get("TDATE") or "")[:10]
        current = mapped.setdefault(code, {
            "onList": True,
            "name": str(item.get("SECURITY_NAME_ABBR") or item.get("SECURITY_NAME") or item.get("SNAME") or code),
            "score": 0,
            "reason": "",
            "netBuy": 0.0,
            "buy": 0.0,
            "sell": 0.0,
            "date": trade_date,
            "reasons": [],
        })
        current["netBuy"] += net
        current["buy"] += buy
        current["sell"] += sell
        if trade_date and trade_date > str(current.get("date") or ""):
            current["date"] = trade_date
        if reason and reason not in current["reasons"]:
            current["reasons"].append(reason)
    for info in mapped.values():
        info["reason"] = "；".join(info.pop("reasons", [])[:2]) or "龙虎榜上榜"
        score = 35
        score += min(30, max(-20, float(info.get("netBuy") or 0) / 100000000 * 15))
        if float(info.get("buy") or 0) > float(info.get("sell") or 0):
            score += 15
        if "机构" in str(info.get("reason") or ""):
            score += 8
        if "游资" in str(info.get("reason") or ""):
            score += 4
        info["score"] = round(max(0, min(100, score)), 1)
        info["netBuyText"] = format_lhb_amount(float(info.get("netBuy") or 0))
    LONGHUBANG_CACHE["ts"] = now_ts
    LONGHUBANG_CACHE["data"] = mapped
    return dict(mapped)


def format_lhb_amount(value: float) -> str:
    sign = "+" if value > 0 else ""
    if abs(value) >= 100000000:
        return f"{sign}{value / 100000000:.2f}亿"
    if abs(value) >= 10000:
        return f"{sign}{value / 10000:.1f}万"
    return f"{sign}{value:.0f}"


def longhubang_for_code(code: str) -> dict:
    info = fetch_longhubang_map().get(str(code))
    if info:
        return info
    return {"onList": False, "score": 0, "reason": "近5日未上龙虎榜", "netBuy": 0, "netBuyText": "--", "date": ""}


def datacenter_rows(report: str, *, filter_text: str = "", sort_columns: str = "", sort_types: str = "", page_size: int = 100) -> list[dict]:
    params = {
        "reportName": report,
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "pageSize": str(page_size),
        "pageNumber": "1",
    }
    if filter_text:
        params["filter"] = filter_text
    if sort_columns:
        params["sortColumns"] = sort_columns
    if sort_types:
        params["sortTypes"] = sort_types
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace")
        payload = json.loads(raw)
        result = payload.get("result") or payload.get("data") or {}
        rows = result.get("data") if isinstance(result, dict) else []
        return rows or []
    except Exception:
        return []


def longhubang_rank_payload(limit: int = 30) -> dict:
    now_ts = time_mod.time()
    cache = LONGHUBANG_RANK_CACHE.get("data") or {}
    if cache and int(cache.get("_limit") or 0) >= limit and now_ts - float(LONGHUBANG_RANK_CACHE.get("ts") or 0) < 180:
        return cache

    latest_rows = datacenter_rows(
        "RPT_DAILYBILLBOARD_DETAILS",
        sort_columns="TRADE_DATE,BILLBOARD_NET_AMT",
        sort_types="-1,-1",
        page_size=120,
    )
    if not latest_rows:
        return {"ok": False, "message": "龙虎榜数据暂不可用", "updatedAt": datetime.now().strftime("%m-%d %H:%M"), "rows": []}

    trade_date = str(latest_rows[0].get("TRADE_DATE") or "")[:10]
    detail_rows = datacenter_rows(
        "RPT_DAILYBILLBOARD_DETAILS",
        filter_text=f"(TRADE_DATE='{trade_date}')",
        sort_columns="BILLBOARD_NET_AMT",
        sort_types="-1",
        page_size=300,
    )
    dept_rows = datacenter_rows(
        "RPT_OPERATEDEPT_TRADE_DETAILSNEW",
        filter_text=f"(TRADE_DATE='{trade_date}')",
        sort_columns="TRADE_DATE,SECURITY_CODE,NET_AMT",
        sort_types="-1,1,-1",
        page_size=800,
    )
    org_rows = datacenter_rows(
        "RPT_ORGANIZATION_TRADE_DETAILSNEW",
        filter_text=f"(TRADE_DATE='{trade_date}')",
        sort_columns="NET_BUY_AMT",
        sort_types="-1",
        page_size=300,
    )

    by_code: dict[str, dict] = {}
    for item in detail_rows:
        code = re.sub(r"\D", "", str(item.get("SECURITY_CODE") or ""))
        if len(code) != 6:
            continue
        row = by_code.setdefault(code, {
            "code": code,
            "name": str(item.get("SECURITY_NAME_ABBR") or code),
            "date": trade_date,
            "price": safe_float(item.get("CLOSE_PRICE")),
            "change": safe_float(item.get("CHANGE_RATE")),
            "reason": "",
            "reasons": [],
            "buy": 0.0,
            "sell": 0.0,
            "net": 0.0,
            "deal": 0.0,
            "turnover": safe_float(item.get("TURNOVERRATE")),
            "buySeats": item.get("BUY_SEAT_NEW") or item.get("BUY_SEAT") or "",
            "sellSeats": item.get("SELL_SEAT_NEW") or item.get("SELL_SEAT") or "",
            "departments": [],
            "organizations": [],
        })
        row["buy"] += safe_float(item.get("BILLBOARD_BUY_AMT"))
        row["sell"] += safe_float(item.get("BILLBOARD_SELL_AMT"))
        row["net"] += safe_float(item.get("BILLBOARD_NET_AMT"))
        row["deal"] += safe_float(item.get("BILLBOARD_DEAL_AMT"))
        reason = str(item.get("EXPLANATION") or item.get("EXPLAIN") or "")
        if reason and reason not in row["reasons"]:
            row["reasons"].append(reason)

    for row in by_code.values():
        row["reason"] = "；".join(row.pop("reasons", [])[:2]) or "龙虎榜上榜"

    for item in dept_rows:
        code = re.sub(r"\D", "", str(item.get("SECURITY_CODE") or ""))
        row = by_code.get(code)
        if not row:
            continue
        buy = safe_float(item.get("ACT_BUY"))
        sell = safe_float(item.get("ACT_SELL"))
        net = safe_float(item.get("NET_AMT"), buy - sell)
        row["departments"].append({
            "name": str(item.get("OPERATEDEPT_NAME") or item.get("ORG_NAME_ABBR") or "未知营业部"),
            "shortName": str(item.get("ORG_NAME_ABBR") or item.get("OPERATEDEPT_NAME") or "未知营业部"),
            "type": classify_seat_name(str(item.get("OPERATEDEPT_NAME") or item.get("ORG_NAME_ABBR") or "")),
            "buy": buy,
            "sell": sell,
            "net": net,
            "buyText": format_lhb_amount(buy),
            "sellText": format_lhb_amount(sell),
            "netText": format_lhb_amount(net),
        })

    for item in org_rows:
        code = re.sub(r"\D", "", str(item.get("SECURITY_CODE") or ""))
        row = by_code.get(code)
        if not row:
            continue
        buy = safe_float(item.get("BUY_AMT"))
        sell = safe_float(item.get("SELL_AMT"))
        net = safe_float(item.get("NET_BUY_AMT"), buy - sell)
        row["organizations"].append({
            "name": "机构专用",
            "buyTimes": int(safe_float(item.get("BUY_TIMES"))),
            "sellTimes": int(safe_float(item.get("SELL_TIMES"))),
            "buy": buy,
            "sell": sell,
            "net": net,
            "buyText": format_lhb_amount(buy),
            "sellText": format_lhb_amount(sell),
            "netText": format_lhb_amount(net),
        })

    rows = list(by_code.values())
    for row in rows:
        row["buyText"] = format_lhb_amount(row["buy"])
        row["sellText"] = format_lhb_amount(row["sell"])
        row["netText"] = format_lhb_amount(row["net"])
        row["dealText"] = format_lhb_amount(row["deal"])
        row["departments"].sort(key=lambda x: abs(float(x.get("net") or 0)), reverse=True)
        row["buyTop"] = sorted(row["departments"], key=lambda x: float(x.get("buy") or 0), reverse=True)[:5]
        row["sellTop"] = sorted(row["departments"], key=lambda x: float(x.get("sell") or 0), reverse=True)[:5]
    rows.sort(key=lambda x: (float(x.get("net") or 0), float(x.get("buy") or 0)), reverse=True)

    payload = {
        "ok": True,
        "message": f"龙虎榜排名已更新：{trade_date}，共{len(rows)}只上榜股票。",
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tradeDate": trade_date,
        "_limit": limit,
        "rows": rows[:limit],
    }
    LONGHUBANG_RANK_CACHE["ts"] = now_ts
    LONGHUBANG_RANK_CACHE["data"] = payload
    return payload


def classify_seat_name(name: str) -> str:
    if "机构专用" in name:
        return "机构"
    if "沪股通" in name or "深股通" in name or "港股通" in name:
        return "北向/通道"
    if "营业部" in name or "证券" in name or "分公司" in name or "有限责任公司" in name or "股份有限公司" in name:
        return "营业部/游资席位"
    return "其他席位"


def rps_payload(limit: int = 10) -> dict:
    """Local RPS-style mainline scanner.

    This is a fast intraday relative-strength radar. It ranks the current
    sample by live change and liquidity first; true 20/60/120-day RPS can be
    added once a stable daily-history vendor is connected.
    """
    try:
        from simulate_t_random import build_random_pool, fallback_stock_pool
    except Exception as exc:
        return {"ok": False, "message": f"RPS初始化失败：{exc}", "rows": [], "themes": []}
    try:
        pool = build_random_pool()
    except Exception:
        pool = fallback_stock_pool()
    if not pool:
        return {"ok": False, "message": "股票池为空，暂时无法计算RPS。", "rows": [], "themes": []}

    priority_codes = {"601899", "601012", "300502", "002050", "000063", "600580", "603993", "601088"}
    priority = [stock for stock in pool if stock.code in priority_codes]
    rest = [stock for stock in pool if stock.code not in priority_codes]
    random.shuffle(rest)
    scan_pool = (priority + rest)[:360]

    def quote_row(stock):
        quote = fast_quote(stock.symbol)
        price = float(quote.get("price") or 0.0)
        change = float(quote.get("change") or 0.0)
        amount = float(quote.get("amount") or 0.0)
        name = str(quote.get("name") or stock.name)
        if price <= 0:
            return None
        cat = stock_category(f"{name}{stock.code}")
        return {
            "name": name,
            "code": stock.code,
            "symbol": stock.symbol,
            "price": price,
            "change": change,
            "amountWan": amount,
            "amountText": format_amount_wan(amount),
            "category": cat["name"],
            "categoryReason": cat["reason"],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool_exec:
        rows = [row for row in pool_exec.map(quote_row, scan_pool) if row]
    if len(rows) < 8:
        return {"ok": False, "message": "行情样本不足，稍后刷新。", "rows": [], "themes": []}

    changes = sorted(float(row["change"]) for row in rows)
    amounts = sorted(float(row["amountWan"]) for row in rows)

    def rank_pct(values: list[float], value: float) -> float:
        if len(values) <= 1:
            return 50.0
        below = sum(1 for item in values if item <= value)
        return below / len(values) * 100

    external = rps_external_factors()
    category_stats: dict[str, dict] = {}
    for row in rows:
        cat = row["category"]
        stat = category_stats.setdefault(cat, {"count": 0, "positive": 0, "change": 0.0, "amount": 0.0})
        stat["count"] += 1
        stat["positive"] += 1 if float(row["change"]) > 0 else 0
        stat["change"] += float(row["change"])
        stat["amount"] += float(row["amountWan"])
    themes = []
    for cat, stat in category_stats.items():
        heat = (stat["positive"] / max(stat["count"], 1)) * 45 + max(-5, min(8, stat["change"] / max(stat["count"], 1))) * 5 + min(stat["amount"] / 800000, 1) * 15
        raw_heat = round(max(0, min(100, heat)), 1)
        adjusted = rps_theme_adjusted_heat(cat, raw_heat, external)
        themes.append({
            "name": cat,
            "count": stat["count"],
            "positive": stat["positive"],
            "avgChange": round(stat["change"] / max(stat["count"], 1), 2),
            "amountWan": round(stat["amount"], 2),
            "amountText": format_amount_wan(stat["amount"]),
            "rawHeat": raw_heat,
            "heat": adjusted["heat"],
            "externalBias": adjusted["bias"],
            "externalReason": adjusted["reason"],
        })
    themes.sort(key=lambda item: (-item["heat"], -item["positive"], item["name"]))
    theme_heat = {item["name"]: item["heat"] for item in themes}

    for row in rows:
        change_rank = rank_pct(changes, float(row["change"]))
        amount_rank = rank_pct(amounts, float(row["amountWan"]))
        heat = float(theme_heat.get(row["category"], 50.0))
        rps = change_rank * 0.58 + amount_rank * 0.24 + heat * 0.18
        row["rps"] = round(max(0, min(100, rps)), 1)
        row["changeRank"] = round(change_rank, 1)
        row["amountRank"] = round(amount_rank, 1)
        row["themeHeat"] = round(heat, 1)
        row["signal"] = "主线强势" if row["rps"] >= 82 and row["change"] > 0 else "主线观察" if row["rps"] >= 68 else "等待确认"
        row["agents"] = [
            f"技术员：RPS {row['rps']}，相对强度分位{row['changeRank']}，先看是否沿均线保持强势",
            f"资金员：成交额{row['amountText']}，活跃度分位{row['amountRank']}，放量持续才算主线",
            f"主线员：{row['category']}主题热度{row['themeHeat']}，{rps_theme_reason(row['category'], external)}",
            f"风控员：RPS只负责找强，不等于买点；买入仍需盘中黄线/VWAP确认",
            f"决策员：{row['signal']}｜适合加入主线观察池，等回踩承接或放量突破",
        ]
        row["reason"] = f"{row['category']}热度{row['themeHeat']}，涨幅分位{row['changeRank']}，成交活跃分位{row['amountRank']}"

    rows.sort(key=lambda item: (-item["rps"], -item["themeHeat"], -item["amountWan"], item["code"]))
    selected = diversify_rps_rows(rows, limit=limit)
    yearly_paths = yearly_mainline_paths(themes, rows, external)
    fund_flow = market_fund_flow_payload(themes, rows)
    rank_matrix = rps_rank_matrix_payload(yearly_paths, themes, external)
    return {
        "ok": True,
        "message": f"RPS主线扫描完成：从{len(rows)}只样本中选出{len(selected)}只。",
        "updatedAt": datetime.now().strftime("%m-%d %H:%M"),
        "rows": selected,
        "themes": themes[:8],
        "paths": yearly_paths,
        "fundFlow": fund_flow,
        "rankMatrix": rank_matrix,
        "externalFactors": external,
        "market": {
            "sample": len(rows),
            "up": sum(1 for row in rows if float(row.get("change") or 0) > 0),
            "down": sum(1 for row in rows if float(row.get("change") or 0) < 0),
            "flat": sum(1 for row in rows if float(row.get("change") or 0) == 0),
            "leader": themes[0]["name"] if themes else "--",
            "leaderHeat": themes[0]["heat"] if themes else 0,
        },
    }


def rps_external_factors() -> dict:
    """Small real-time sanity check for RPS themes.

    The sector scanner uses ordinary stock quotes, while resource themes are
    heavily affected by gold/copper/oil/USD. This prevents a local seasonal
    path from ranking gold first when the live external factors are weak.
    """
    now_ts = datetime.now().timestamp()
    cached = RPS_FACTOR_CACHE.get("data") or {}
    if cached and now_ts - float(RPS_FACTOR_CACHE.get("ts") or 0) < 120:
        return cached

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
        futures = [pool.submit(fetch_yahoo_market, name, symbol, unit, weight) for name, symbol, unit, weight in PREMARKET_FUTURES]
        for future in concurrent.futures.as_completed(futures, timeout=10):
            item = future.result()
            if item:
                rows.append(item)
    factors = {str(row.get("name")): row for row in rows}
    stale_names = [name for name, row in factors.items() if row.get("isStale")]

    def live_change(name: str) -> float:
        row = factors.get(name) or {}
        if not row or row.get("isStale"):
            return 0.0
        return float(row.get("change") or 0)

    gold_raw = float(factors.get("黄金", {}).get("change") or 0)
    copper_raw = float(factors.get("铜", {}).get("change") or 0)
    dollar_raw = float(factors.get("美元指数", {}).get("change") or 0)
    gold = live_change("黄金")
    copper = live_change("铜")
    oil = (live_change("WTI原油") + live_change("布油")) / 2
    dollar = live_change("美元指数")
    resource_score = gold * 18 + copper * 22 + oil * 5 - dollar * 8
    data = {
        "updatedAt": datetime.now().strftime("%m-%d %H:%M"),
        "rows": rows,
        "gold": round(gold, 2),
        "goldRaw": round(gold_raw, 2),
        "copper": round(copper, 2),
        "copperRaw": round(copper_raw, 2),
        "oil": round(oil, 2),
        "dollar": round(dollar, 2),
        "dollarRaw": round(dollar_raw, 2),
        "resourceScore": round(resource_score, 2),
        "resourceWeak": gold < 0 and copper <= 0,
        "hasFreshFactors": any(not row.get("isStale") for row in rows),
        "staleNames": stale_names,
        "source": "Yahoo 5分钟快照（超过3小时只展示，不参与排名）" if stale_names else "Yahoo 5分钟快照",
    }
    RPS_FACTOR_CACHE["ts"] = now_ts
    RPS_FACTOR_CACHE["data"] = data
    return data


def rps_theme_reason(category: str, external: dict) -> str:
    if not external.get("hasFreshFactors"):
        return "外盘快照已过期，当前只按A股样本强弱排序"
    if category == "资源周期":
        return f"外盘校验：黄金{external.get('gold', 0):+.2f}%、铜{external.get('copper', 0):+.2f}%、美元{external.get('dollar', 0):+.2f}%"
    if category in {"AI科技", "新能源", "高端制造"}:
        return f"外盘校验：美元{external.get('dollar', 0):+.2f}%、铜{external.get('copper', 0):+.2f}%"
    return "关注板块内是否多股共振"


def rps_theme_adjusted_heat(category: str, heat: float, external: dict) -> dict:
    if not external.get("hasFreshFactors"):
        return {"heat": round(max(0, min(100, heat)), 1), "bias": 0.0, "reason": "外盘快照过期，未参与排名"}
    bias = 0.0
    reason = "实时样本强弱"
    gold = float(external.get("gold") or 0)
    copper = float(external.get("copper") or 0)
    oil = float(external.get("oil") or 0)
    dollar = float(external.get("dollar") or 0)
    if category == "资源周期":
        bias = gold * 3.6 + copper * 4.2 + oil * 1.2 - dollar * 2.0
        reason = f"黄金{gold:+.2f}%、铜{copper:+.2f}%、油{oil:+.2f}%、美元{dollar:+.2f}%"
        if gold < 0 and copper <= 0:
            return {"heat": round(min(max(0, heat + bias), 62), 1), "bias": round(bias, 1), "reason": reason + "，资源线降温"}
    elif category == "新能源":
        bias = copper * 1.4 - oil * 0.8 - dollar * 1.2
        reason = f"铜{copper:+.2f}%、油{oil:+.2f}%、美元{dollar:+.2f}%"
    elif category in {"AI科技", "高端制造"}:
        bias = -dollar * 1.6 + copper * 0.5
        reason = f"美元{dollar:+.2f}%、铜{copper:+.2f}%"
    return {"heat": round(max(0, min(100, heat + bias)), 1), "bias": round(bias, 1), "reason": reason}


def rps_rank_matrix_payload(paths: list[dict], themes: list[dict], external: dict | None = None) -> dict:
    """Create a compact date-by-rank matrix for mainline discovery.

    It mirrors the common RPS mainline table: columns are recent checkpoints,
    rows are ranks, and cells are sectors. Current implementation uses local
    path scores with deterministic rotation; real daily RPS can later replace
    this without changing the UI shape.
    """
    external = external or {}
    base_names = [str(item.get("name") or "") for item in paths if item.get("name")]
    extras = ["半导体", "通信设备", "机器人", "创新药", "电力", "银行", "证券", "军工", "低空经济", "消费电子", "煤炭", "航运", "数据中心", "有色金属"]
    if external.get("hasFreshFactors") and not external.get("resourceWeak"):
        extras.extend(["黄金", "铜矿"])
    names = []
    for name in base_names + extras:
        short = name.split("/")[0].replace("黄金铜油", "黄金").replace("AI科技", "算力")
        if short and short not in names:
            names.append(short)
    names = names[:20]
    today = datetime.now()
    dates = []
    d = today
    while len(dates) < 9:
        if d.weekday() < 5:
            dates.append(d.strftime("%m/%d"))
        d -= timedelta(days=1)
    dates.reverse()
    heat_map = {str(item.get("name") or ""): float(item.get("heat") or 0) for item in themes}
    path_score = {str(item.get("name") or "").split("/")[0].replace("黄金铜油", "黄金").replace("AI科技", "算力"): float(item.get("score") or 0) for item in paths}
    columns = []
    for col_idx, date_text in enumerate(dates):
        scored = []
        for idx, name in enumerate(names):
            seed = sum(ord(ch) for ch in (name + date_text))
            base = path_score.get(name, 42 + (seed % 18))
            if name in {"黄金", "铜矿", "有色金属"} and external.get("resourceWeak"):
                base = min(base, 48)
            heat_bonus = max(0, heat_map.get(name, 0) - 50) * 0.16
            drift = ((seed + col_idx * 7) % 19) - 9
            momentum = col_idx * (0.45 if idx < 5 else 0.15)
            scored.append({"name": name, "score": round(base + heat_bonus + drift + momentum, 2)})
        scored.sort(key=lambda item: (-item["score"], item["name"]))
        columns.append({"date": date_text, "items": [item["name"] for item in scored[:20]]})
    return {"dates": dates, "columns": columns, "rankCount": min(20, len(names)), "mode": "实时样本+外盘校验"}


def market_fund_flow_payload(themes: list[dict], rows: list[dict]) -> dict:
    """Build an intraday-style sector fund-flow chart from local samples.

    This is an estimated flow view based on turnover, change direction, and
    theme breadth. It is intentionally labelled as an estimate until real
    minute-level sector fund-flow data is connected.
    """
    by_theme: dict[str, list[dict]] = {}
    for row in rows:
        by_theme.setdefault(str(row.get("category") or "综合观察"), []).append(row)
    theme_heat = {str(item.get("name") or ""): float(item.get("heat") or 0) for item in themes}
    palette = ["#e5484d", "#7c3aed", "#f59e0b", "#2563eb", "#16a34a", "#0ea5e9", "#64748b", "#db2777"]
    series = []
    bars = []
    times = ["09:30", "10:00", "10:30", "11:00", "13:00", "13:30", "14:00", "14:30", "15:00"]
    for idx, (theme, items) in enumerate(sorted(by_theme.items(), key=lambda kv: -theme_heat.get(kv[0], 0))[:8]):
        amount = sum(float(item.get("amountWan") or 0) for item in items)
        weighted_change = sum(float(item.get("change") or 0) * max(float(item.get("amountWan") or 0), 1) for item in items) / max(amount, 1)
        breadth = sum(1 for item in items if float(item.get("change") or 0) > 0) / max(len(items), 1)
        net = amount * (weighted_change / 100) * (0.62 + breadth * 0.76)
        trend = []
        drift = net / max(len(times), 1)
        for step, _time in enumerate(times):
            wave = ((step % 3) - 1) * abs(net) * 0.045
            open_bias = abs(net) * 0.12 if step == 1 and net > 0 else (-abs(net) * 0.10 if step == 1 else 0)
            trend.append(round(drift * (step + 1) + wave + open_bias, 2))
        series.append({
            "name": theme,
            "color": palette[idx % len(palette)],
            "netWan": round(net, 2),
            "netText": format_amount_wan(net),
            "amountText": format_amount_wan(amount),
            "breadth": round(breadth * 100, 1),
            "points": [{"time": t, "value": v} for t, v in zip(times, trend)],
        })
        bars.append({
            "name": theme,
            "netWan": round(net, 2),
            "netText": format_amount_wan(net),
            "inflow": net > 0,
            "breadth": round(breadth * 100, 1),
        })
    bars.sort(key=lambda item: -abs(float(item.get("netWan") or 0)))
    total = sum(float(item.get("netWan") or 0) for item in bars)
    return {
        "title": "大盘/板块资金走势",
        "mode": "本地估算",
        "times": times,
        "series": series,
        "bars": bars,
        "summary": f"估算净流{'入' if total >= 0 else '出'} {format_amount_wan(total)}，用于观察板块强弱和资金轮动，不等同真实L2资金流。",
    }


def yearly_mainline_paths(themes: list[dict], rows: list[dict], external: dict | None = None) -> list[dict]:
    """Infer this year's mainline rotation map from local theme tags.

    The free quote source currently provides live snapshots, not full-year
    daily money-flow history. This returns an explainable mainline map using
    A-share seasonal anchors plus today's RPS/turnover heat; it can be replaced
    by true monthly RPS curves once a daily-history vendor is connected.
    """
    now = datetime.now()
    external = external or {}
    theme_heat = {str(item.get("name") or ""): float(item.get("heat") or 0) for item in themes}
    theme_counts: dict[str, int] = {}
    theme_amounts: dict[str, float] = {}
    for row in rows:
        cat = str(row.get("category") or "综合观察")
        theme_counts[cat] = theme_counts.get(cat, 0) + 1
        theme_amounts[cat] = theme_amounts.get(cat, 0.0) + float(row.get("amountWan") or 0)

    anchors = [
        ("AI科技", "AI科技/算力通信", "1-3月", "算力投资、通信链、AI应用扩散", "看成交持续、订单兑现和板块内多股共振"),
        ("资源周期", "黄金铜油/资源周期", "2-6月", "美元、金铜油价格、地缘扰动、矿业并购", "紫金矿业重点看金价、铜价、美元和成交量"),
        ("新能源", "新能源/电力设备", "3-7月", "价格企稳、装机需求、政策预期和出口链", "先看超跌修复，再看业绩和放量突破"),
        ("高端制造", "机器人/高端制造", "4-8月", "设备更新、机器人、军工制造、国产替代", "适合找趋势回踩，不追单日脉冲"),
        ("消费医药", "消费医药/创新药", "5-9月", "估值修复、业绩兑现、创新药和医疗需求", "更看确定性和政策风险，少追高波动"),
        ("金融地产", "金融地产/高股息", "全年防守", "分红、防守资金、政策托底和指数稳定器", "用来判断大盘风险偏好，不一定适合高频做T"),
    ]
    current_month = max(1, min(12, now.month))
    month_names = ["一月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月", "十一月", "十二月"][:current_month]
    paths = []
    for idx, (key, name, months, drivers, watch) in enumerate(anchors):
        heat = theme_heat.get(key, 0.0)
        amount = theme_amounts.get(key, 0.0)
        coverage = theme_counts.get(key, 0)
        anchor_bonus = max(0, 22 - abs(current_month - min(12, idx + 2)) * 3)
        external_penalty = 0
        if key == "资源周期" and external.get("resourceWeak"):
            external_penalty = 18
        score = round(max(0, min(100, heat * 0.62 + min(amount / 600000, 1) * 18 + coverage * 2 + anchor_bonus - external_penalty)), 1)
        if score >= 72:
            stage = "主升跟踪"
        elif score >= 55:
            stage = "轮动观察"
        elif score >= 38:
            stage = "低位潜伏"
        else:
            stage = "等待资金"
        paths.append({
            "name": name,
            "key": key,
            "months": months,
            "yearMonths": month_names,
            "score": score,
            "stage": stage,
            "heat": round(heat, 1),
            "amountText": format_amount_wan(amount),
            "drivers": drivers,
            "watch": watch,
        })
    paths.sort(key=lambda item: (-item["score"], item["name"]))
    return paths


def diversify_rps_rows(rows: list[dict], limit: int = 10) -> list[dict]:
    selected: list[dict] = []
    used_codes: set[str] = set()
    category_counts: dict[str, int] = {}
    for row in rows:
        code = str(row.get("code"))
        cat = str(row.get("category") or "综合观察")
        if code in used_codes or category_counts.get(cat, 0) >= 3:
            continue
        selected.append(row)
        used_codes.add(code)
        category_counts[cat] = category_counts.get(cat, 0) + 1
        if len(selected) >= limit:
            return selected
    for row in rows:
        code = str(row.get("code"))
        if code in used_codes:
            continue
        selected.append(row)
        used_codes.add(code)
        if len(selected) >= limit:
            break
    return selected


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
    record_screener_history(rows[:10])
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
    lhb = longhubang_for_code(stock.code)
    growth = aggressive_growth_profile(stock.code, name, category["name"], change, amount) if aggressive else {"score": 0, "label": "评审", "logic": "多Agent评审筛选", "risk": "按趋势和成交量确认"}
    lhb_bonus = 2 if lhb.get("onList") and float(lhb.get("netBuy") or 0) > 0 else 1 if lhb.get("onList") else 0
    score = max(category["base"] + trend_score + money_score + daily_profile["score"] + medium["score"] + growth["score"] + lhb_bonus - risk_penalty, 0)
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
            f"龙虎榜：{lhb.get('reason')}，净买入{lhb.get('netBuyText')}",
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
        "longhubang": lhb,
        "horizon": medium["horizon"],
        "tier": tier["tier"],
        "useCase": tier["useCase"],
        "decision": tier["decision"],
    }


def local_screener_rows(aggressive: bool = False, review: bool = False) -> list[dict]:
    try:
        from simulate_t_random import Stock, build_random_pool, fallback_stock_pool
    except Exception:
        return []
    try:
        pool = build_random_pool()
    except Exception:
        pool = fallback_stock_pool()
    lhb_map = fetch_longhubang_map()
    if lhb_map:
        by_code = {stock.code: stock for stock in pool}
        lhb_ranked = sorted(
            lhb_map.items(),
            key=lambda item: (float(item[1].get("score") or 0), float(item[1].get("netBuy") or 0)),
            reverse=True,
        )
        lhb_stocks = []
        for code, info in lhb_ranked[:12]:
            if code in by_code:
                continue
            prefix = "sh" if code.startswith(("6", "9")) else "sz"
            lhb_stocks.append(Stock(str(info.get("name") or code), code, prefix + code))
        pool = lhb_stocks + pool
    if len(pool) > 180:
        priority_codes = {"601899", "601012", "000063", "300502", "002050", "600519", "601088", "600030", "601318"}
        priority_codes.update(list(lhb_map.keys())[:20])
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
        return diversify_review_rows(rows, limit=10)
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
    lhb = row.get("longhubang") or {}
    lhb_bonus = 0.5 if lhb.get("onList") and float(lhb.get("netBuy") or 0) > 0 else 0.2 if lhb.get("onList") else 0
    review_score = round(min(10, uzi_score * 0.38 + trading_score * 0.34 + kronos_score * 0.28 + lhb_bonus), 2)
    row["reviewScore"] = review_score
    row["screenMode"] = "评审团选股"
    row["tier"] = "评审通过" if review_score >= 7.2 else "观察候选" if review_score >= 5.8 else "暂缓"
    row["useCase"] = "1-3个月候选，盘中用黄线确认"
    row["decision"] = f"评审分 {review_score}/10｜{row['tier']}｜不追高，等价格和成交量确认"
    row["agents"] = [
        f"TradingAgents：行业{category}，中期分{medium_score}/10，先看催化和趋势共振",
        f"UZI评审：商业/基本面/风险综合 {uzi_score}/10，结论：{row['tier']}",
        f"Kronos路径员：选股阶段做波动适配 {kronos_score}/10，盘中再用分钟线确认路径",
        f"龙虎榜员：{lhb.get('reason', '近5日未上龙虎榜')}，净买入{lhb.get('netBuyText', '--')}",
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


def screener_history_path(email: str | None = None) -> Path:
    return user_data_path(email or REQUEST_EMAIL.get(""), "screener_history.json")


def load_screener_history(email: str | None = None) -> list[dict]:
    path = screener_history_path(email)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("runs") if isinstance(data, dict) else data
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def recent_screener_penalty_map(email: str | None = None) -> dict[str, float]:
    """Penalize recently selected codes so review picks rotate without becoming random."""
    penalty: dict[str, float] = {}
    for run_idx, run in enumerate(reversed(load_screener_history(email)[-SCREENER_HISTORY_LIMIT:])):
        codes = run.get("codes") if isinstance(run, dict) else []
        weight = max(0.15, 1.0 - run_idx * 0.12)
        for rank, code in enumerate(codes or []):
            code = str(code)
            if not code:
                continue
            penalty[code] = penalty.get(code, 0.0) + weight * max(0.35, 1.0 - rank * 0.05)
    return penalty


def apply_screener_rotation(rows: list[dict], email: str | None = None) -> list[dict]:
    penalties = recent_screener_penalty_map(email)
    now_seed = int(datetime.now().strftime("%Y%m%d%H"))
    for row in rows:
        code = str(row.get("code") or "")
        base = float(row.get("reviewScore") or row.get("mediumScore") or row.get("score") or 0)
        penalty = min(1.8, penalties.get(code, 0.0) * 0.42)
        jitter_seed = sum(ord(ch) for ch in f"{code}{now_seed}") % 100
        exploration = (jitter_seed / 100.0) * 0.28
        row["rotationPenalty"] = round(penalty, 2)
        row["selectionScore"] = round(base - penalty + exploration, 3)
        if penalty >= 0.6:
            row.setdefault("reasons", []).insert(0, f"近期已推荐过，轮动降权{penalty:.1f}分")
    return rows


def diversify_review_rows(rows: list[dict], limit: int = 10, email: str | None = None) -> list[dict]:
    """Build a rotating, cross-industry review shortlist from a high-score pool."""
    rows = apply_screener_rotation(rows, email)
    rows.sort(key=lambda item: (-float(item.get("selectionScore") or 0), -float(item.get("reviewScore") or 0), -float(item.get("mediumScore") or 0), str(item.get("code") or "")))
    high_pool = rows[: min(len(rows), max(limit * 4, 28))]
    selected: list[dict] = []
    used_codes: set[str] = set()
    category_counts: dict[str, int] = {}
    max_per_category = 2

    for category in sorted({str(row.get("category") or "其他行业/待识别") for row in high_pool}):
        candidates = [row for row in high_pool if row.get("category") == category and str(row.get("code") or "") not in used_codes]
        if not candidates:
            continue
        best = candidates[0]
        selected.append(best)
        used_codes.add(str(best.get("code")))
        category_counts[category] = category_counts.get(category, 0) + 1
        if len(selected) >= limit:
            break

    for row in high_pool + rows:
        code = str(row.get("code") or "")
        category = str(row.get("category") or "其他行业/待识别")
        if not code or code in used_codes:
            continue
        if category_counts.get(category, 0) >= max_per_category and len(selected) < limit - 1:
            continue
        selected.append(row)
        used_codes.add(code)
        category_counts[category] = category_counts.get(category, 0) + 1
        if len(selected) >= limit:
            break

    selected.sort(key=lambda item: (-float(item.get("selectionScore") or 0), -float(item.get("reviewScore") or 0), str(item.get("category") or ""), str(item.get("code") or "")))
    for row in selected:
        row["selectionNote"] = "高分池轮动入选" if float(row.get("rotationPenalty") or 0) < 0.6 else "重复推荐降权后仍入选"
    return selected[:limit]


def record_screener_history(rows: list[dict], email: str | None = None) -> None:
    if not rows:
        return
    path = screener_history_path(email)
    try:
        history = load_screener_history(email)
        history.append({
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "codes": [str(row.get("code") or "") for row in rows],
            "names": [str(row.get("name") or "") for row in rows],
            "categories": [str(row.get("category") or "") for row in rows],
        })
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"runs": history[-SCREENER_HISTORY_LIMIT:]}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


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
    if ai_config.get("provider") in {"ChatGPT", "Claude", "ThirdParty"}:
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
    provider = str(ai_config.get("provider") or "ThirdParty")
    key = str(ai_config.get("key") or "").strip()
    if not key and provider == "ChatGPT":
        key = os.environ.get("OPENAI_API_KEY", "").strip() or load_desktop_env().get("OPENAI_API_KEY", "")
    base_raw = str(ai_config.get("base") or "").strip()
    if not base_raw and provider == "ChatGPT":
        base_raw = "https://api.openai.com/v1"
    base = normalize_openai_base(base_raw)
    model = str(ai_config.get("model") or ("gpt-4o-mini" if provider == "ChatGPT" else "claude-sonnet-4" if provider == "Claude" else "gpt-4o-mini")).strip()
    if provider == "Claude" and not base_raw:
        return {"ok": False, "configured": bool(key), "message": "Claude 请填写兼容 OpenAI 的 Claude API 地址，或使用第三方API中转。"}
    if not base:
        return {"ok": False, "configured": bool(key), "message": "请填写第三方 API 地址，例如 https://你的域名/v1。"}
    if not key:
        return {"ok": False, "configured": False, "message": "请填写 API Key。"}
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
    if ai.get("provider") in {"ChatGPT", "Claude", "ThirdParty"}:
        return bool(ai.get("key") and ai.get("base"))
    return bool(load_gemini_key())


def apply_ai_agents_fast(rows: list[dict]) -> bool:
    ai = load_ai_config()
    if ai.get("provider") in {"ChatGPT", "Claude", "ThirdParty"}:
        return apply_openai_compatible_agents_fast(rows)
    return apply_gemini_agents_fast(rows)


def apply_openai_compatible_agents_fast(rows: list[dict]) -> bool:
    global LAST_GEMINI_ERROR
    LAST_GEMINI_ERROR = ""
    ai = load_ai_config()
    key = str(ai.get("key") or "").strip()
    base_raw = str(ai.get("base") or "").strip()
    if not base_raw and ai.get("provider") == "ChatGPT":
        base_raw = "https://api.openai.com/v1"
    base = normalize_openai_base(base_raw)
    if not key and ai.get("provider") == "ChatGPT":
        key = os.environ.get("OPENAI_API_KEY", "").strip() or load_desktop_env().get("OPENAI_API_KEY", "")
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
    provider = str(settings.get("aiProvider") or "ChatGPT").strip()
    provider = {"OpenAI": "ChatGPT", "OpenAI Compatible": "ThirdParty", "OpenAI兼容": "ThirdParty", "OpenAICompatible": "ThirdParty", "第三方API": "ThirdParty"}.get(provider, provider)
    if provider not in {"ChatGPT", "Gemini", "Claude", "ThirdParty"}:
        provider = "ChatGPT"
    key = str(settings.get("aiKey") or "").strip()
    model_defaults = {"ChatGPT": "gpt-4o-mini", "Gemini": "gemini-2.5-flash", "Claude": "claude-sonnet-4", "ThirdParty": "gpt-4o-mini"}
    model = str(settings.get("aiModel") or model_defaults[provider]).strip()
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
<title>做T神器 - A股智能交易助手</title>
<style>
:root{--ink:#2b170f;--muted:#7a6658;--line:#f0dfc7;--red:#ef2f22;--red2:#c91510;--gold:#d99a36}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font:14px/1.65 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:#fffaf3}a{text-decoration:none;color:inherit}.hero-image{position:relative;width:100%;min-height:100vh;background:#fff4e6;overflow:hidden}.hero-image>img{width:100%;height:100vh;min-height:760px;object-fit:cover;object-position:center top;display:block}.hotspot{position:absolute;z-index:4;border-radius:999px;text-indent:-9999px;overflow:hidden;transition:background .16s ease,box-shadow .16s ease}.hotspot:hover{background:rgba(255,255,255,.18);box-shadow:0 0 0 2px rgba(239,47,34,.22) inset}.hs-home{left:38.5%;top:5.2%;width:5.2%;height:5.2%}.hs-features{left:44.7%;top:5.2%;width:5.2%;height:5.2%}.hs-strategy{left:51.0%;top:5.2%;width:5.2%;height:5.2%}.hs-price{left:57.4%;top:5.2%;width:5.2%;height:5.2%}.hs-help{left:63.7%;top:5.2%;width:5.2%;height:5.2%}.hs-about{left:69.8%;top:5.2%;width:7.3%;height:5.2%}.hs-login{display:none}.hero-topbar{position:absolute;left:0;right:0;top:0;z-index:9;height:86px;background:rgba(255,250,243,.94);border-bottom:1px solid rgba(217,154,54,.22);backdrop-filter:blur(10px);display:flex;align-items:center}.hero-nav-inner{width:min(1280px,calc(100vw - 36px));margin:0 auto;display:flex;align-items:center;justify-content:space-between;gap:20px}.hero-brand{display:flex;align-items:center;gap:12px;font-size:22px;font-weight:950;color:#2b170f}.hero-brand img{height:40px;width:auto;display:block}.hero-nav{display:flex;align-items:center;gap:clamp(14px,2vw,34px);color:#5f4c3b;font-size:16px;font-weight:850}.hero-nav a{position:relative}.hero-nav a.active{color:#ef2f22}.hero-nav a.active:after{content:"";position:absolute;left:50%;bottom:-12px;width:34px;height:4px;border-radius:99px;background:#ef2f22;transform:translateX(-50%)}.nav-auth{height:40px;min-width:122px;padding:0 18px;border-radius:999px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#ffe2b5,#d79d58);border:1px solid rgba(255,255,255,.82);box-shadow:0 12px 28px rgba(151,79,18,.18);color:#6a3d13;font-size:15px;font-weight:950;letter-spacing:0;white-space:nowrap}.nav-auth:hover{filter:brightness(1.04);transform:translateY(-1px)}.hs-cta{left:18.5%;top:73.2%;width:18.2%;height:8.6%;border-radius:34px}.mobile-actions{display:none}.btn{height:42px;border:1px solid rgba(217,154,54,.32);border-radius:12px;background:rgba(255,255,255,.92);padding:0 18px;display:inline-flex;align-items:center;font-weight:950;box-shadow:0 10px 24px rgba(151,79,18,.08)}.btn.primary{background:linear-gradient(135deg,var(--red),var(--red2));color:#fff;border-color:var(--red)}.band{width:min(1180px,calc(100vw - 44px));margin:0 auto;padding:64px 0}.section-title{font-size:34px;line-height:1.15;margin:0 0 10px}.section-sub{color:var(--muted);margin:0;max-width:620px}.feature-panel{width:min(1180px,calc(100vw - 44px));margin:0 auto;padding:54px 0 48px;border-bottom:1px solid #f0dfc7}.feature-head{display:flex;justify-content:space-between;gap:24px;align-items:end;margin-bottom:28px}.feature-head h2{font-size:34px;line-height:1.12;margin:0}.feature-head p{margin:0;color:var(--muted);max-width:520px}.feature-grid{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid #f0dfc7;border-radius:18px;overflow:hidden;background:#fff}.feature-item{min-height:150px;padding:22px;border-right:1px solid #f0dfc7}.feature-item:last-child{border-right:0}.feature-item b{display:block;font-size:18px;margin-bottom:10px}.feature-item p{margin:0;color:#6b5543}.flow-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:0;margin-top:18px;border:1px solid #f0dfc7;border-radius:16px;overflow:hidden;background:#2b170f;color:#fff}.flow-step{padding:18px 20px;border-right:1px solid rgba(255,255,255,.14)}.flow-step:last-child{border-right:0}.flow-step span{display:block;color:#e6c18d;font-weight:950;margin-bottom:4px}.flow-step b{display:block;font-size:16px}.flow-step small{display:block;color:#ead7c5;margin-top:4px}.pricing{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.price{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px}.price.featured{border-color:var(--red);box-shadow:0 14px 34px rgba(239,47,34,.10)}.money{font-size:30px;font-weight:950;margin:6px 0}.list{padding:0;margin:10px 0 0;list-style:none}.list li{padding:5px 0;color:#5f4c3b}.cta{margin:10px auto 0;width:min(1180px,calc(100vw - 44px));background:linear-gradient(135deg,#2b170f,#6b2b13);color:#fff;border-radius:16px;padding:26px;display:flex;align-items:center;justify-content:space-between;gap:16px}.cta h2{margin:0;font-size:28px}.cta p{margin:4px 0 0;color:#ead7c5}.footer{width:min(1180px,calc(100vw - 44px));margin:0 auto;padding:28px 0 40px;color:#7a6658}@media(max-width:1280px){.hero-topbar{height:72px}.hero-brand img{height:34px}.hero-brand span{display:none}.hero-nav{display:none}.nav-auth{height:36px;min-width:108px;padding:0 16px;font-size:14px}}@media(max-width:900px){.hero-image>img{height:auto;min-height:0;object-fit:contain}.hotspot{display:none}.hero-topbar{height:66px}.hero-brand img{height:34px}.hero-brand span{display:none}.hero-nav{display:none}.nav-auth{height:38px;min-width:112px;padding:0 16px;font-size:14px}.mobile-actions{display:flex;gap:10px;flex-wrap:wrap;padding:14px 16px 24px;background:#fff4e6}.feature-head{display:block}.feature-head h2,.section-title{font-size:26px}.feature-head p{margin-top:8px}.feature-grid,.flow-strip,.pricing{grid-template-columns:1fr}.feature-item,.flow-step{border-right:0;border-bottom:1px solid #f0dfc7}.feature-item:last-child,.flow-step:last-child{border-bottom:0}.cta{display:block}}
</style>
</head>
<body>
<section class="hero-image" aria-label="做T神器首页首屏">
  <img src="/assets/home-hero.png" alt="做T神器 A股智能交易助手">
  <div class="hero-topbar">
    <div class="hero-nav-inner">
      <a class="hero-brand" href="/"><img src="/assets/logo.png" alt="&#20570;T&#31070;&#22120;"><span>&#20570;T&#31070;&#22120;</span></a>
      <nav class="hero-nav" aria-label="&#39318;&#39029;&#23548;&#33322;"><a class="active" href="/">&#39318;&#39029;</a><a href="#features">&#21151;&#33021;</a><a href="/commercial">&#31574;&#30053;</a><a href="#pricing">&#20215;&#26684;</a><a href="/account">&#24110;&#21161;</a><a href="/admin">&#20851;&#20110;&#25105;&#20204;</a></nav>
      <a id="authPill" class="nav-auth" href="/login">&#30331;&#24405; / &#27880;&#20876;</a>
    </div>
  </div>
  <a class="hotspot hs-home" href="/">首页</a>
  <a class="hotspot hs-features" href="#features">功能</a>
  <a class="hotspot hs-strategy" href="/commercial">策略</a>
  <a class="hotspot hs-price" href="#pricing">价格</a>
  <a class="hotspot hs-help" href="/account">帮助</a>
  <a class="hotspot hs-about" href="/admin">关于我们</a>
  <a id="heroLoginLink" class="hotspot hs-login" href="/login">登录 / 注册</a>
  <a class="hotspot hs-cta" href="/app">立即体验</a>
  <div class="mobile-actions"><a class="btn primary" href="/app">立即体验</a><a id="mobileLoginLink" class="btn" href="/login">登录 / 注册</a><a class="btn" href="#features">功能</a><a class="btn" href="#pricing">价格</a></div>
</section>
<section id="features" class="feature-panel">
  <div class="feature-head">
    <h2>核心能力，放进一个工作台。</h2>
    <p>首页只讲清楚产品价值，不再堆宣传图。真正复杂的监控、模拟、选股和策略配置，都进入控制台处理。</p>
  </div>
  <div class="feature-grid">
    <div class="feature-item"><b>盘中监控</b><p>多股实时刷新，围绕黄线、VWAP、量能和分时结构判断可做区间。</p></div>
    <div class="feature-item"><b>买卖点提醒</b><p>只推送强信号，把低吸、高抛、接回、止损写成可执行价格带。</p></div>
    <div class="feature-item"><b>模拟测试</b><p>用随机样本和历史缓存验证策略，过滤追高接刀和无效交易。</p></div>
    <div class="feature-item"><b>选股研究</b><p>多角色评审结合龙虎榜、RPS、行业催化和AI复核，输出候选池。</p></div>
  </div>
  <div class="flow-strip">
    <div class="flow-step"><span>01</span><b>选择股票池</b><small>导入关注股票或手动添加</small></div>
    <div class="flow-step"><span>02</span><b>盘前看方向</b><small>外盘、板块和目标股偏向</small></div>
    <div class="flow-step"><span>03</span><b>盘中等价格</b><small>只在关键价格带提醒</small></div>
    <div class="flow-step"><span>04</span><b>收盘做复盘</b><small>记录失败原因并优化策略</small></div>
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
<section class="cta"><div><h2>盘前看方向，盘中等价格带。</h2><p>把冲动交易压下来，把可复盘的动作留下来。</p></div><div><a class="btn primary" href="/app">进入控制台</a> <a id="ctaRegisterLink" class="btn" href="/register">注册体验账号</a></div></section>
<footer class="footer">做T神器 · A股智能交易助手 · 策略研究工具，不承诺收益。</footer>
<script>
async function syncLandingAuth(){
  try{
    const data=await fetch('/api/account',{cache:'no-store'}).then(r=>r.json());
    const pill=document.getElementById('authPill');
    if(!data.loggedIn){
      if(pill){pill.href='/login';pill.textContent='登录 / 注册'}
      return;
    }
    const login=document.getElementById('heroLoginLink');
    const mobile=document.getElementById('mobileLoginLink');
    const cta=document.getElementById('ctaRegisterLink');
    if(pill){pill.href='/account';pill.textContent='会员中心'}
    if(login){login.href='/account';login.textContent='会员中心'}
    if(mobile){mobile.href='/account';mobile.textContent='会员中心'}
    if(cta){cta.href='/account';cta.textContent='会员中心'}
  }catch(e){}
}
document.addEventListener('DOMContentLoaded',syncLandingAuth);
</script>
</body>
</html>"""

AUTH_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>账号 - T神器</title>
<style>
:root{--ink:#2b170f;--muted:#8a6b52;--line:#f0dfc7;--blue:#b86b18;--green:#2fb878;--red:#e83324;--gold:#d99a1b;--bg:#fff8ee}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.6 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 88% 0,#ffe2aa,transparent 32%),linear-gradient(135deg,#b4783b,#fff5e8 42%,#fffaf4);display:flex;align-items:center;justify-content:center;padding:18px}
a,button,input{font:inherit}a{text-decoration:none;color:inherit}.card{width:min(980px,100%);display:grid;grid-template-columns:1.05fr 420px;gap:18px;background:rgba(255,255,255,.92);border:1px solid rgba(255,255,255,.9);border-radius:18px;box-shadow:0 28px 80px rgba(151,79,18,.20);padding:26px}.hero{padding:18px}.brand{font-size:18px;font-weight:950;margin-bottom:44px}.hero h1{font-size:42px;line-height:1.08;margin:0 0 12px}.hero p{margin:0;color:#6b5543}.points{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:28px}.point{background:#fff;border:1px solid var(--line);border-radius:16px;padding:13px}.point b{display:block;font-size:18px}.point span{color:#8a6b52;font-size:12px}.form{background:#fff;border:1px solid var(--line);border-radius:22px;padding:22px;box-shadow:0 14px 38px rgba(151,79,18,.09)}.form h2{margin:0 0 6px;font-size:24px}.sub{color:var(--muted);margin-bottom:18px}.field{margin-bottom:12px}.field label{display:block;font-size:12px;color:#8a6b52;font-weight:900;margin-bottom:6px}.field input{width:100%;height:42px;border:1px solid var(--line);border-radius:12px;padding:0 12px;font-weight:850}.field input:focus{outline:2px solid rgba(37,99,235,.14);border-color:#9ec0ff}.btns{display:grid;gap:9px;margin-top:14px}button{height:42px;border:1px solid var(--line);border-radius:12px;background:#fff;padding:0 14px;font-weight:950;cursor:pointer}button.primary{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff;border-color:#e83324}.msg{min-height:22px;margin-top:12px;color:#6b5543}.msg.bad{color:#dc3545}.links{display:flex;justify-content:space-between;margin-top:16px;color:#6b5543}.links a{color:#b86b18;font-weight:900}@media(max-width:850px){.card{grid-template-columns:1fr}.hero h1{font-size:32px}.points{grid-template-columns:1fr}}
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
:root{--ink:#2b170f;--muted:#8a6b52;--line:#f0dfc7;--blue:#b86b18;--green:#2fb878;--red:#e83324;--gold:#d99a1b;--bg:#fff8ee}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.6 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 88% 0,#ffe2aa,transparent 32%),linear-gradient(135deg,#b4783b,#fff5e8 42%,#fffaf4);padding:24px}a,button{font:inherit;text-decoration:none;color:inherit}button{height:38px;border:1px solid var(--line);border-radius:12px;background:#fff;padding:0 14px;font-weight:950;cursor:pointer;box-shadow:0 8px 20px rgba(151,79,18,.07)}button.primary{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff;border-color:#e83324}.shell{width:min(1180px,100%);margin:auto}.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}.brand{font-size:24px;font-weight:950}.sub{color:var(--muted)}.actions{display:flex;gap:9px;flex-wrap:wrap}.panel{background:rgba(255,255,255,.93);border:1px solid rgba(255,255,255,.9);border-radius:18px;box-shadow:0 28px 80px rgba(151,79,18,.18);padding:22px}.grid{display:grid;grid-template-columns:1.15fr .85fr;gap:14px;margin-top:14px}.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px}.card h2,.card h3{margin:0 0 10px}.kv{display:grid;grid-template-columns:150px 1fr;border-top:1px solid #eef2f4}.kv div{padding:12px 0;border-bottom:1px solid #f3e7d8}.kv div:nth-child(odd){color:#8a6b52;font-weight:900}.plan{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.price{border:1px solid var(--line);border-radius:16px;padding:15px;background:#fff}.price.active{border-color:#9ee4bd;background:#f2fff7}.price b{font-size:20px}.price ul{margin:10px 0 0;padding-left:18px;color:#6b5543}.empty{text-align:center;padding:50px;color:#6b5543}@media(max-width:850px){.grid,.plan{grid-template-columns:1fr}.top{display:block}.actions{margin-top:10px}}
</style>
</head>
<body>
<main class="shell">
  <div class="top">
    <div><div class="brand">会员中心</div><div class="sub">本地内测账号，后续可升级为云端商业版。</div></div>
    <div class="actions"><a href="/app"><button>控制台</button></a><a href="/recharge"><button class="primary">激活码充值</button></a><a href="/commercial"><button>功能中心</button></a><a href="/"><button>商业页</button></a></div>
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
      <div class="card"><h3>账号信息</h3><div class="kv"><div>邮箱</div><div>${a.email||'--'}</div><div>昵称</div><div>${a.nickname||'--'}</div><div>套餐</div><div>${a.plan||'体验版'}</div><div>到期时间</div><div>${a.planExpireAt||'体验权限'}</div><div>注册时间</div><div>${a.createdAt||'--'}</div><div>监控额度</div><div>${a.watchLimit||1} 只股票</div><div>AI复核额度</div><div>${a.aiReviewLimit||5} 次/日</div></div></div>
      <div class="card"><h3>快捷入口</h3><p><a href="/app"><button class="primary">进入控制台</button></a></p><p><a href="/recharge"><button class="primary">激活码充值</button></a></p><p><a href="/admin"><button>用户管理</button></a><a href="/commercial"><button>配置商业功能</button></a></p><p><button onclick="logout()">退出登录</button></p></div>
    </div>
    <div class="card" style="margin-top:14px"><h3>套餐规划</h3><div class="plan"><div class="price active"><b>体验版</b><p>免费试用</p><ul><li>基础单股监控</li><li>盘前方向预览</li><li>模拟测试体验</li></ul></div><div class="price"><b>月卡</b><p>¥9.9 / 月</p><ul><li>多股实时监控</li><li>模拟测试与复盘</li><li>AI买卖点复核</li></ul></div><div class="price"><b>永久版</b><p>¥99</p><ul><li>核心功能长期使用</li><li>策略模板更新</li><li>优先体验新增功能</li></ul></div></div></div>`;
}
async function logout(){await fetch('/api/logout',{method:'POST'});location.href='/login'}
loadAccount();
</script>
</body>
</html>"""


RECHARGE_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>激活码充值 - T神器</title>
<style>
:root{--ink:#2b170f;--muted:#8a6b52;--line:#f0dfc7;--blue:#b86b18;--red:#e83324;--green:#2fb878;--gold:#d99a1b}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.6 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 88% 0,#ffe2aa,transparent 32%),linear-gradient(135deg,#b4783b,#fff5e8 42%,#fffaf4);padding:24px}a,button,input{font:inherit}a{text-decoration:none;color:inherit}.shell{width:min(1120px,100%);margin:auto}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}.brand{font-size:24px;font-weight:950}.sub{color:var(--muted)}.actions{display:flex;gap:9px;flex-wrap:wrap}button{height:40px;border:1px solid var(--line);border-radius:12px;background:#fff;padding:0 15px;font-weight:950;cursor:pointer;box-shadow:0 8px 20px rgba(151,79,18,.07)}button.primary{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff;border-color:#e83324}.hero{background:rgba(255,255,255,.94);border:1px solid rgba(255,255,255,.9);border-radius:18px;box-shadow:0 28px 80px rgba(151,79,18,.18);padding:24px;display:grid;grid-template-columns:1fr 360px;gap:18px}.hero h1{font-size:38px;line-height:1.1;margin:0 0 10px}.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px}.plans{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:14px}.plan{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px}.plan.featured{border-color:#e83324;box-shadow:0 18px 45px rgba(232,51,36,.13)}.price{font-size:30px;font-weight:950;margin:8px 0}.plan p,.card p{color:#6b5543;margin:0}.form label{display:block;font-size:12px;color:var(--muted);font-weight:900;margin-bottom:7px}.form input{width:100%;height:46px;border:1px solid var(--line);border-radius:14px;padding:0 14px;font-size:18px;font-weight:950;letter-spacing:.6px;text-transform:uppercase}.msg{min-height:24px;margin-top:12px;color:#6b5543}.msg.ok{color:#168a4f}.msg.bad{color:#dc3545}.tips{display:grid;gap:10px;margin-top:14px}.tip{background:#fff8ee;border:1px solid #f4e5cf;border-radius:14px;padding:12px;color:#6b5543}.tip b{color:#2b170f}.kv{display:grid;grid-template-columns:110px 1fr;border-top:1px solid #eef2f4;margin-top:12px}.kv div{padding:10px 0;border-bottom:1px solid #f3e7d8}.kv div:nth-child(odd){color:#8a6b52;font-weight:900}@media(max-width:850px){.top{display:block}.actions{margin-top:10px}.hero,.plans{grid-template-columns:1fr}.hero h1{font-size:30px}}
</style>
</head>
<body>
<main class="shell">
  <div class="top">
    <div><div class="brand">激活码充值</div><div class="sub">月卡 9.9 元，永久版 99 元；当前为本地商业化原型。</div></div>
    <div class="actions"><a href="/app"><button>控制台</button></a><a href="/account"><button>会员中心</button></a><a href="/commercial"><button>功能中心</button></a></div>
  </div>
  <section class="hero">
    <div>
      <h1>输入激活码，开通你的做T神器权限。</h1>
      <p>正式上线后，支付成功会自动生成激活码；用户复制激活码到这里兑换即可。这样比直接改账号更适合早期售卖和人工发码。</p>
      <div class="plans">
        <div class="plan"><h3>体验版</h3><div class="price">试用</div><p>基础单股监控、盘前方向预览。</p></div>
        <div class="plan featured"><h3>月卡</h3><div class="price">¥9.9</div><p>多股监控、模拟复盘、AI复核，31天。</p></div>
        <div class="plan"><h3>永久版</h3><div class="price">¥99</div><p>核心功能长期使用，策略模板更新。</p></div>
      </div>
    </div>
    <div class="card form">
      <label>激活码</label>
      <input id="code" placeholder="例如 T9-DEMO-MONTH" autocomplete="off">
      <button class="primary" style="width:100%;margin-top:12px" onclick="redeem()">立即兑换</button>
      <div id="msg" class="msg"></div>
      <div id="account" class="kv"></div>
      <div class="tips">
        <div class="tip"><b>内测月卡：</b>T9-DEMO-MONTH</div>
        <div class="tip"><b>内测永久：</b>T99-DEMO-LIFE</div>
        <div class="tip">正式商业版会改成后台批量生成一次性激活码，并绑定订单号。</div>
      </div>
    </div>
  </section>
</main>
<script>
const $=id=>document.getElementById(id);
function esc(v){return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]))}
function renderAccount(a){$('account').innerHTML=`<div>当前套餐</div><div>${esc(a.plan||'体验版')}</div><div>到期时间</div><div>${esc(a.planExpireAt||'体验权限')}</div><div>监控额度</div><div>${esc(a.watchLimit||1)} 只股票</div><div>AI额度</div><div>${esc(a.aiReviewLimit||5)} 次/日</div>`}
async function loadAccount(){const data=await fetch('/api/account').then(r=>r.json()).catch(()=>({}));if(data.loggedIn)renderAccount(data.account||{})}
async function redeem(){
  const msg=$('msg');msg.className='msg';msg.textContent='正在兑换...';
  try{
    const res=await fetch('/api/redeem',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:$('code').value})});
    const data=await res.json();
    if(!data.ok){msg.className='msg bad';msg.textContent=data.message||'兑换失败';return}
    msg.className='msg ok';msg.textContent=data.message||'兑换成功';
    renderAccount(data.account||{});
  }catch(e){msg.className='msg bad';msg.textContent='服务异常：'+(e.message||e)}
}
loadAccount();
</script>
</body>
</html>"""


ADMIN_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>用户管理 - 做T神器</title>
<style>
:root{--ink:#2b170f;--muted:#8a6b52;--line:#f0dfc7;--red:#e83324;--gold:#d69a38}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.6 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 88% 0,#ffe2aa,transparent 34%),linear-gradient(135deg,#b4783b,#fff5e8 42%,#fffaf4)}
a,button{font:inherit;text-decoration:none;color:inherit}button{height:38px;border:1px solid var(--line);border-radius:12px;background:#fff;padding:0 14px;font-weight:950;cursor:pointer;box-shadow:0 8px 20px rgba(151,79,18,.08)}button.primary{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff;border-color:#e83324}.shell{width:min(1180px,calc(100vw - 28px));margin:18px auto}.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}.brand{display:flex;align-items:center;gap:12px;font-size:24px;font-weight:950}.brand img{width:118px}.sub{color:var(--muted)}.actions{display:flex;gap:9px;flex-wrap:wrap}.panel{background:rgba(255,255,255,.94);border:1px solid rgba(255,255,255,.9);border-radius:18px;box-shadow:0 28px 80px rgba(151,79,18,.18);padding:18px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px}.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px}.card span{display:block;color:var(--muted);font-size:12px;font-weight:900}.card b{display:block;font-size:26px;margin-top:4px}table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:16px;overflow:hidden}th,td{padding:12px 14px;border-bottom:1px solid #f3e7d8;text-align:left}th{font-size:12px;color:var(--muted)}.tag{display:inline-flex;border-radius:999px;background:#fff1dc;color:#a65b18;padding:4px 9px;font-weight:900}.on{background:#eafff1;color:#24935d}.empty{text-align:center;padding:44px;color:var(--muted)}@media(max-width:850px){.top{display:block}.actions{margin-top:10px}.cards{grid-template-columns:1fr}th,td{font-size:12px;padding:10px 8px}}
</style>
</head>
<body>
<main class="shell">
  <div class="top"><div><div class="brand"><img src="/assets/logo.png" alt="做T神器"><span>用户管理</span></div><div class="sub">本机内测后台：查看注册用户、套餐和当前登录状态。</div></div><div class="actions"><a href="/app"><button>工作台</button></a><a href="/account"><button>会员中心</button></a><button class="primary" onclick="loadUsers()">刷新</button></div></div>
  <section class="panel">
    <div class="cards"><div class="card"><span>注册用户</span><b id="userCount">--</b></div><div class="card"><span>在线会话</span><b id="onlineCount">--</b></div><div class="card"><span>数据文件</span><b>本机JSON</b></div></div>
    <div style="overflow:auto"><table><thead><tr><th>邮箱</th><th>昵称</th><th>套餐</th><th>注册时间</th><th>监控额度</th><th>AI额度</th><th>状态</th></tr></thead><tbody id="rows"><tr><td colspan="7" class="empty">加载中...</td></tr></tbody></table></div>
  </section>
</main>
<script>
const $=id=>document.getElementById(id);
function esc(v){return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]))}
async function loadUsers(){
  try{
    const data=await (await fetch('/api/admin/users',{cache:'no-store'})).json();
    if(!data.ok)throw new Error(data.message||'读取失败');
    const rows=data.users||[];
    $('userCount').textContent=rows.length;
    $('onlineCount').textContent=rows.filter(x=>x.loggedIn).length;
    $('rows').innerHTML=rows.length?rows.map(u=>`<tr><td>${esc(u.email)}</td><td>${esc(u.nickname)}</td><td><span class="tag">${esc(u.plan||'体验版')}</span></td><td>${esc(u.createdAt||'--')}</td><td>${esc(u.watchLimit||1)}只</td><td>${esc(u.aiReviewLimit||5)}次/日</td><td><span class="tag ${u.loggedIn?'on':''}">${u.loggedIn?'已登录':'离线'}</span></td></tr>`).join(''):'<tr><td colspan="7" class="empty">暂无注册用户。</td></tr>';
  }catch(e){$('rows').innerHTML=`<tr><td colspan="7" class="empty">${esc(e.message||e)}</td></tr>`}
}
loadUsers();
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
:root{--ink:#2b170f;--muted:#8a6b52;--line:#f0dfc7;--red:#e83324;--green:#2fb878;--blue:#b86b18;--gold:#d99a1b;--bg:#fff8ee}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.6 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 88% 0,#ffe2aa,transparent 32%),linear-gradient(135deg,#b4783b,#fff5e8 42%,#fffaf4)}
a,button{font:inherit}a{text-decoration:none;color:inherit}button{height:36px;border:1px solid var(--line);border-radius:11px;background:#fff;padding:0 14px;font-weight:900;cursor:pointer;box-shadow:0 8px 20px rgba(151,79,18,.07)}button.primary{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff;border-color:#e83324}
.shell{width:min(1280px,calc(100vw - 28px));margin:14px auto 28px}.top{height:54px;display:flex;align-items:center;justify-content:space-between}.brand{font-size:22px;font-weight:950}.sub{color:var(--muted)}.actions{display:flex;gap:8px;align-items:center}.actions button,button{height:32px;border-radius:9px;box-shadow:none}.hero{background:rgba(255,255,255,.92);border:1px solid rgba(255,255,255,.86);border-radius:14px;padding:18px;box-shadow:none;display:grid;grid-template-columns:1fr 320px;gap:16px}.hero h1{font-size:30px;line-height:1.12;margin:0 0 8px}.hero p{color:#6b5543;margin:0}.status{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.stat{background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px 12px}.stat span{display:block;color:var(--muted);font-size:12px}.stat b{font-size:20px}.grid{display:grid;grid-template-columns:230px 1fr;gap:10px;margin-top:10px}.panel{background:rgba(255,255,255,.95);border:1px solid rgba(255,255,255,.86);border-radius:14px;box-shadow:none;overflow:hidden}.head{height:40px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 12px;font-weight:950}.body{padding:10px}.tabs{display:grid;gap:6px}.tab{width:100%;justify-content:space-between;box-shadow:none;background:#fff8ee}.tab.active{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff}.form{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.field{background:#fff8ee;border:1px solid #f4e5cf;border-radius:10px;padding:9px}.field.full{grid-column:1/-1}.field label{display:block;color:var(--muted);font-size:12px;font-weight:900}.field input,.field select,.field textarea{width:100%;margin-top:5px;border:1px solid var(--line);border-radius:8px;background:#fff;padding:0 9px;font-weight:850}.field input,.field select{height:30px}.field textarea{height:78px;padding:9px;resize:vertical;line-height:1.5}.toggle{display:flex;gap:8px;align-items:center;margin-top:7px;color:#6b5543}.preview{margin-top:10px;border:1px solid var(--line);border-radius:12px;background:#fff;overflow:hidden}.preview-row{display:grid;grid-template-columns:130px 1fr;gap:10px;padding:10px 12px;border-bottom:1px solid #f3e7d8}.preview-row:last-child{border-bottom:0}.tag{display:inline-flex;border-radius:8px;padding:3px 7px;background:#fff1dc;color:#a65b18;font-weight:900;margin:0 5px 5px 0}.flow{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.step{background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px}.step b{display:block;font-size:18px}.step p{margin:4px 0 0;color:#6b5543}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px}.card h3{margin:0 0 6px}.card p{margin:0;color:#6b5543}.price-line{font-size:22px;font-weight:950;margin:3px 0 6px}.todo{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.todo div{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px}.todo b{display:block;margin-bottom:6px}.warn{font-size:12px;color:#8a6b52;margin-top:8px}@media(max-width:900px){.hero,.grid,.form,.flow,.cards,.todo{grid-template-columns:1fr}.top{height:auto;display:block}.actions{margin-top:10px;flex-wrap:wrap}}
</style>
</head>
<body>
<main class="shell">
  <div class="top">
    <div><div class="brand">商业功能中心</div><div class="sub">先把核心功能做成可配置，再接账号、支付和云端部署。</div></div>
    <div class="actions"><a href="/account"><button>账号</button></a><a href="/app"><button class="primary">控制台</button></a></div>
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
const state={module:'strategy',strategySource:'使用官方默认策略',risk:'稳健',maxDaily:'1',buyDev:'-1.30',sellDev:'1.50',takeProfit:'0.75',strategyText:'',aiReview:true,secondConfirm:true,pathGate:true,alert:'强提醒',plan:'专业版'};
const modules={
strategy:{title:'自定义策略',html:`<div class="form"><div class="field"><label>策略来源</label><select id="strategySource" onchange="setVal('strategySource',this.value)"><option>使用官方默认策略</option><option>复制默认后自定义</option><option>使用控制台设置</option></select></div><div class="field"><label>策略风格</label><select id="risk" onchange="setVal('risk',this.value)"><option>稳健</option><option>平衡</option><option>激进</option></select></div><div class="field"><label>每日最多交易</label><input id="maxDaily" type="number" min="0" max="5" value="1" oninput="setVal('maxDaily',this.value)"></div><div class="field"><label>正T低吸阈值</label><input id="buyDev" type="number" step="0.05" value="-1.30" oninput="setVal('buyDev',this.value)"></div><div class="field"><label>反T高抛阈值</label><input id="sellDev" type="number" step="0.05" value="1.50" oninput="setVal('sellDev',this.value)"></div><div class="field"><label>止盈目标%</label><input id="takeProfit" type="number" step="0.05" value="0.75" oninput="setVal('takeProfit',this.value)"></div><div class="field"><label>二次确认</label><div class="toggle"><input id="secondConfirm" type="checkbox" checked onchange="setBool('secondConfirm',this.checked)">低点回踩不破/高点反抽不过</div></div><div class="field"><label>路径预判</label><div class="toggle"><input id="pathGate" type="checkbox" checked onchange="setBool('pathGate',this.checked)">先判断低开修复/冲高回落/单边弱</div></div><div class="field full"><label>个人策略文本</label><textarea id="strategyText" placeholder="把你的做T规则粘贴到这里，例如：围绕黄线，低开急跌只等右侧拐头；冲高无量先反T；每天最多一笔。" oninput="setVal('strategyText',this.value)"></textarea></div><div class="field full"><button class="primary" onclick="applyStrategyToSettings()">应用到控制台策略</button><span id="strategyApplyMsg" class="warn"></span></div></div><p class="warn">应用后会写入当前账号设置，并同步给实时监控和模拟测试。正式买卖提醒仍以量价确认和风控为准。</p>`},
ai:{title:'AI集中复核',html:`<div class="form"><div class="field"><label>AI复核</label><div class="toggle"><input id="aiReview" type="checkbox" checked onchange="setBool('aiReview',this.checked)">买卖点出现后先让AI讨论是否最优</div></div><div class="field"><label>复核重点</label><select><option>是否追高/接刀</option><option>大方向是否一致</option><option>价格带是否合理</option></select></div><div class="field"><label>输出格式</label><select><option>一句话结论 + 价格</option><option>五角色短评</option><option>详细复盘</option></select></div><div class="field"><label>模型策略</label><select><option>Gemini优先，本地兜底</option><option>本地规则优先</option><option>人工确认</option></select></div></div>`},
alert:{title:'提醒规则',html:`<div class="form"><div class="field"><label>提醒强度</label><select id="alert" onchange="setVal('alert',this.value)"><option>强提醒</option><option>普通提醒</option><option>只记录不提醒</option></select></div><div class="field"><label>单股每日提醒</label><select><option>买卖各1次</option><option>最多2次</option><option>不限但限频</option></select></div><div class="field"><label>推送渠道</label><select><option>微信ClawBot</option><option>浏览器声音</option><option>短信/邮件预留</option></select></div><div class="field"><label>提醒内容</label><select><option>股票名 + 价格带 + 原因</option><option>简约一句话</option><option>详细角色分析</option></select></div></div>`},
member:{title:'会员能力',html:`<div class="cards"><div class="card"><h3>体验版</h3><p>免费试用，基础单股监控、盘前风向、模拟体验。</p></div><div class="card"><h3>月卡</h3><p>¥9.9/月，多股监控、强提醒、AI买卖点复核。</p></div><div class="card"><h3>永久版</h3><p>¥99，核心功能长期使用，策略模板持续更新。</p></div></div><p class="warn">所有页面只做研究辅助，不承诺收益。</p>`}
};
function showModule(name,btn){state.module=name;document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));btn.classList.add('active');render()}
function setVal(k,v){state[k]=v;renderSummary()}
function setBool(k,v){state[k]=v;renderSummary()}
function render(){const m=modules[state.module];document.getElementById('moduleTitle').textContent=m.title;document.getElementById('moduleBody').innerHTML=m.html;if(state.module==='strategy'){['strategySource','risk','maxDaily','buyDev','sellDev','takeProfit','strategyText'].forEach(k=>{const el=document.getElementById(k);if(el)el.value=state[k]||''});['secondConfirm','pathGate'].forEach(k=>{const el=document.getElementById(k);if(el)el.checked=!!state[k]})}renderSummary()}
function renderSummary(){document.getElementById('summaryTags').innerHTML=[state.strategySource||'官方默认策略',state.risk,`每日最多${state.maxDaily}笔`,state.secondConfirm?'二次确认':'允许快速拐头',state.pathGate?'路径预判':'不做路径闸门',state.aiReview?'AI复核':'本地规则',state.alert,state.plan].map(x=>`<span class="tag">${x}</span>`).join('');document.getElementById('userPreview').textContent=`用户将看到：官方默认策略或个人策略、盘前方向、候选买卖点、建议价格带、止盈止损和失效条件。`;document.getElementById('backendPreview').textContent=`需要接入：用户策略配置表、默认策略模板、参数校验、模拟验证、版本回滚、提醒记录和复盘训练库。`}
async function applyStrategyToSettings(){const msg=document.getElementById('strategyApplyMsg');if(msg)msg.textContent=' 正在应用...';const payload={strategyMode:'自定义策略',customStrategy:state.strategyText||'',maxSignalsPerDay:state.maxDaily||'1',lowBuyDev:state.buyDev||'-1.30',highSellDev:state.sellDev||'1.50',normal_take_profit_pct:state.takeProfit||'0.75'};try{const data=await(await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})).json();if(!data.ok)throw new Error('保存失败');localStorage.setItem('commercialDraft',JSON.stringify(state));if(msg)msg.textContent=' 已应用。回到控制台设置可查看，模拟测试和实时监控会同步使用。'}catch(e){if(msg)msg.textContent=' 应用失败：'+(e.message||e)}}
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
:root{--bg:#eef6f6;--panel:#ffffff;--ink:#2b170f;--muted:#8a6b52;--line:#f0dfc7;--green:#2fb878;--red:#e83324;--blue:#b86b18;--yellow:#d99a1b}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;font:13px/1.5 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 88% 0,#ffe2aa 0,transparent 36%),linear-gradient(135deg,#b4783b,#fff5e8 42%,#fffaf4);overflow:hidden}
button,input{font:inherit}button{height:34px;border:1px solid var(--line);border-radius:11px;background:linear-gradient(180deg,#fff,#fff8ee);padding:0 13px;font-weight:850;cursor:pointer;box-shadow:0 8px 18px rgba(151,79,18,.07);transition:transform .12s ease,box-shadow .12s ease,border-color .12s ease}button:hover{transform:translateY(-1px);box-shadow:0 12px 24px rgba(151,79,18,.12);border-color:#f0dfc7}button.primary{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff;border-color:#e83324}button:disabled{opacity:.6;cursor:wait}
.shell{min-height:100vh;padding:18px;display:flex;align-items:center;justify-content:center}.panel{position:relative;width:min(1560px,calc(100vw - 28px));height:min(920px,calc(100vh - 28px));background:rgba(255,255,255,.94);border:1px solid rgba(255,255,255,.85);border-radius:20px;box-shadow:0 28px 80px rgba(151,79,18,.20);padding:18px;display:grid;grid-template-rows:auto auto auto auto minmax(0,1fr);gap:12px;overflow:hidden}.top{display:flex;align-items:center;justify-content:space-between}.title{font-size:22px;font-weight:950}.app-brand{display:flex;align-items:center;gap:12px}.app-logo{width:112px;height:auto;border-radius:8px;background:#fff}.sub{color:var(--muted);font-size:12px}.top-actions{display:flex;gap:8px;align-items:center}.workbench-links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.workbench-card{height:auto;min-height:76px;border-radius:14px;padding:12px 14px;text-align:left;display:flex;align-items:center;justify-content:space-between;background:#fff;border-color:#f0dfc7;box-shadow:0 12px 28px rgba(151,79,18,.08)}.workbench-card.primary-card{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff;border-color:#e83324}.workbench-card b{display:block;font-size:16px;line-height:1.2}.workbench-card span{display:block;margin-top:5px;font-size:12px;color:#8a6b52;font-weight:750}.workbench-card.primary-card span{color:rgba(255,255,255,.72)}.workbench-card i{display:grid;place-items:center;width:34px;height:34px;border-radius:12px;background:#fff1dc;color:#a65b18;font-style:normal;font-weight:950;flex:0 0 auto}.workbench-card.primary-card i{background:rgba(255,255,255,.14);color:#fff}.settings-panel,.ai-panel{position:absolute;right:18px;top:62px;z-index:10;width:min(430px,calc(100vw - 44px));background:rgba(255,255,255,.98);border:1px solid var(--line);border-radius:16px;box-shadow:0 22px 60px rgba(151,79,18,.18);padding:12px}.settings-panel[hidden],.ai-panel[hidden]{display:none}.settings-head,.ai-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-weight:950}.settings-group{border-top:1px solid var(--line);padding-top:10px;margin-top:10px}.settings-title{font-size:11px;color:var(--muted);font-weight:950;margin:0 0 8px}.settings-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px}.settings-actions button{box-shadow:none}.settings-note{font-size:11px;color:#8a6b52;line-height:1.6}.ai-config-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px}.ai-config-grid label{font-size:11px;color:#8a6b52;font-weight:900}.ai-config-grid label.wide{grid-column:1/-1}.ai-config-grid input,.ai-config-grid select{width:100%;height:34px;margin-top:4px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:0 10px;color:#2b170f;font-weight:800}.settings-messages{max-height:170px;overflow:auto;border-top:1px solid var(--line);padding-top:10px;color:#6b5543;line-height:1.65}.ai-panel{top:112px;width:min(520px,calc(100vw - 44px));max-height:68vh;overflow:auto}.ai-body{font-size:12px;line-height:1.75;color:#6b5543}.ai-body b{display:block;margin:8px 0 3px}.ai-chip{display:inline-flex;margin:0 5px 5px 0;border-radius:999px;background:#fff1dc;color:#a65b18;padding:4px 8px;font-weight:900}
.actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.field{position:relative}.field span{position:absolute;top:1px;left:10px;font-size:9px;color:var(--muted);font-weight:800}.field input{height:34px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:12px 10px 0;width:116px}.stock-manager{display:flex;align-items:center;gap:8px;flex:1 1 520px;min-width:420px;padding:7px 9px;border:1px solid #f0dfc7;border-radius:17px;background:linear-gradient(180deg,#fff,#fff8ee);box-shadow:0 12px 28px rgba(151,79,18,.08)}.stock-manager-title{font-size:11px;color:#8a6b52;font-weight:950;white-space:nowrap}.stock-manager input{height:30px;border:1px solid #f0dfc7;border-radius:10px;background:#fff;padding:0 10px;font-weight:800;color:#2b170f;width:190px}.watch-tags{display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-width:180px;flex:1}.tag{display:inline-flex;align-items:center;gap:6px;border-radius:999px;background:linear-gradient(180deg,#fff5e5,#ffecd1);color:#a65b18;padding:5px 9px;font-weight:850;box-shadow:inset 0 0 0 1px rgba(151,79,18,.08);cursor:pointer}.tag.active{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff}.tag button{height:18px;width:18px;border:0;border-radius:50%;padding:0;background:#fff1dc;color:#b86b18;box-shadow:none;line-height:18px}.tag.active button{background:rgba(255,255,255,.18);color:#fff}.bar{height:3px;background:linear-gradient(90deg,#4cc9f0,#35b978);border-radius:99px;opacity:0}.bar.on{opacity:1;animation:pulse .9s infinite alternate}@keyframes pulse{from{filter:brightness(.8)}to{filter:brightness(1.2)}}
.premarket{display:grid;grid-template-columns:230px minmax(0,1fr) 330px;gap:10px;align-items:stretch}.pm-card{background:rgba(255,255,255,.94);border:1px solid var(--line);border-radius:17px;padding:10px 12px;box-shadow:0 12px 28px rgba(151,79,18,.07)}.pm-title{font-size:12px;color:var(--muted);font-weight:900}.pm-score{font-size:26px;font-weight:950;letter-spacing:.2px}.pm-signal{display:inline-flex;border-radius:999px;padding:4px 9px;font-weight:950;background:#fff1dc;color:#a65b18}.pm-signal.bull{background:#fff0f1;color:var(--red)}.pm-signal.bear{background:#ebfff2;color:var(--green)}.pm-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(86px,1fr));gap:8px}.pm-item{background:linear-gradient(180deg,#fffaf4,#fff3df);border:1px solid #f4e5cf;border-radius:13px;padding:8px 9px}.pm-item b{display:block;font-size:12px}.pm-item span{font-size:12px;font-weight:950}.pm-reasons{font-size:12px;color:#6b5543;line-height:1.7}
.live{min-height:0;overflow:auto;overflow-x:hidden;background:#fff;border:1px solid var(--line);border-radius:18px;box-shadow:0 14px 34px rgba(151,79,18,.09)}.monitor-table{min-width:0;width:100%}.monitor-head,.monitor-row{display:grid;grid-template-columns:168px 86px minmax(330px,1.2fr) 72px 98px 154px minmax(210px,.9fr) 70px;gap:10px;align-items:center}.monitor-head{position:sticky;top:0;z-index:2;height:40px;padding:0 14px;background:linear-gradient(180deg,#fffaf4,#fff3df);border-bottom:1px solid var(--line);color:#8a6b52;font-size:11px;font-weight:950}.monitor-row{min-height:138px;padding:10px 14px;border-bottom:1px solid #edf1f3}.monitor-row:hover{background:#fffaf4}.monitor-row:last-child{border-bottom:0}.monitor-row.strong-signal{background:linear-gradient(90deg,rgba(236,95,107,.08),rgba(255,255,255,0));box-shadow:inset 4px 0 0 rgba(236,95,107,.75)}.rank-dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:9px;background:#aab4bd}.rank-dot.up{background:var(--red)}.rank-dot.down{background:var(--green)}.live-name{font-size:15px;font-weight:950}.live-code,.live-time{color:var(--muted);margin-left:5px}.live-price{font-size:23px;font-weight:950}.live-pos{color:var(--red)}.live-neg{color:var(--green)}.live-chart{width:100%;height:118px;display:block;border-radius:12px;background:linear-gradient(180deg,#ffffff,#fbfcfc)}.chart-note{display:flex;gap:8px;align-items:center;margin-top:4px;color:#8a6b52;font-size:10px;font-weight:850}.chart-note span{display:inline-flex;align-items:center;gap:3px}.kv{font-size:12px;color:var(--muted);line-height:1.6}.kv b{display:block;color:var(--ink);font-size:15px}.signal-pill{display:inline-flex;border-radius:999px;padding:5px 9px;background:#fff1dc;color:#6b5543;font-weight:900}.signal-pill.hot{background:#fff0f1;color:#ec5f6b}.live-signal{font-size:12px;color:#6b5543;line-height:1.55;margin-top:4px}.money-line{border-radius:13px;background:linear-gradient(180deg,#fff8ee,#fff1dc);padding:8px 10px;color:#6b5543;font-size:12px;line-height:1.48;max-height:88px;overflow:hidden}.signal-toasts{position:absolute;right:18px;top:116px;z-index:30;display:flex;flex-direction:column;gap:10px;width:min(390px,calc(100vw - 44px));pointer-events:none}.signal-toast{pointer-events:auto;color:#fff;border:1px solid rgba(255,255,255,.18);border-radius:16px;box-shadow:0 20px 55px rgba(15,25,35,.26);padding:12px 14px;animation:toastIn .18s ease-out}.signal-toast.buy{background:linear-gradient(135deg,#e5484d,#7f1d1d)}.signal-toast.sell{background:linear-gradient(135deg,#16a34a,#14532d)}.signal-toast .toast-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:7px}.signal-toast .toast-name{font-weight:950;font-size:15px}.signal-toast .toast-signal{border-radius:999px;background:rgba(255,255,255,.18);padding:3px 8px;font-size:12px;font-weight:900;white-space:nowrap}.signal-toast .toast-price{color:rgba(255,255,255,.82);font-size:12px}.signal-toast .toast-reason{color:#fff;font-size:12px;line-height:1.55;margin-bottom:7px}.signal-toast .toast-agents{color:rgba(255,255,255,.86);font-size:12px;line-height:1.6}.signal-toast button{height:24px;padding:0 7px;border:0;border-radius:8px;background:rgba(255,255,255,.16);color:#fff;box-shadow:none}.signal-toast.fade{opacity:0;transform:translateY(-6px);transition:.25s}.op{display:flex;gap:6px;flex-wrap:wrap}.op button{height:28px;padding:0 9px;border-radius:8px;color:#b86b18;background:#fff8ee;border-color:#f0dfc7;box-shadow:none}.op button.ai{color:#fff;background:#e83324;border-color:#e83324}.empty-live{padding:42px;text-align:center;color:var(--muted);font-weight:850}@keyframes toastIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}
.bottom{display:none}.box{background:#fff;border:1px solid var(--line);border-radius:16px;overflow:hidden;display:flex;flex-direction:column;min-width:0;min-height:0}.head{height:42px;padding:0 14px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);font-weight:900}.badge{font-size:11px;color:#35a66d;background:#eafff1;border-radius:999px;padding:4px 9px}.content{padding:12px 14px;overflow:auto;line-height:1.75}.empty{color:var(--muted)}pre{margin:0;padding:12px 14px;flex:1;overflow:auto;white-space:pre-wrap;word-break:break-word;font:12px/1.65 "Microsoft YaHei UI",Consolas,monospace}.msg{margin:0 0 8px}.msg time{color:var(--muted);margin-right:8px}.wechat-focus{outline:2px solid #8fe8b1;box-shadow:0 0 0 5px rgba(69,201,129,.14)}
@media(max-width:1050px){html,body{overflow:auto}.shell{padding:10px}.panel{width:calc(100vw - 20px);height:auto;min-height:calc(100vh - 20px)}.metrics{grid-template-columns:repeat(2,1fr)}.bottom{grid-template-columns:1fr}.workbench-links{grid-template-columns:1fr}.premarket{grid-template-columns:1fr}.live{overflow:auto}.monitor-table{min-width:1180px}.monitor-head,.monitor-row{grid-template-columns:154px 82px 330px 72px 96px 132px 190px 64px}}

/* Console polish: compact, calm, trading-desk style. */
html,body{font-size:13px;background:linear-gradient(135deg,#2b170f 0,#7e3b1c 32%,#fff4e5 32%,#fffaf4 100%)}body:before{content:"";position:fixed;inset:0;pointer-events:none;background:linear-gradient(rgba(255,255,255,.06) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.05) 1px,transparent 1px);background-size:32px 32px;opacity:.28}.shell{padding:14px;align-items:stretch}.panel{width:min(1720px,calc(100vw - 28px));height:calc(100vh - 28px);border-radius:16px;padding:14px;gap:10px;background:rgba(255,252,246,.96);border:1px solid rgba(255,255,255,.88);box-shadow:0 22px 70px rgba(43,23,15,.24);grid-template-rows:auto auto auto auto minmax(0,1fr)}button{height:32px;border-radius:9px;box-shadow:none;background:#fff;border-color:#ead9bf;color:#2b170f}button:hover{box-shadow:0 8px 18px rgba(151,79,18,.11)}.top{min-height:62px;padding:10px 12px;border-radius:14px;background:linear-gradient(135deg,#21100a,#4f1f13 58%,#9d3a23);color:#fff;border:1px solid rgba(255,255,255,.16);box-shadow:0 14px 34px rgba(43,23,15,.20)}.app-brand{gap:10px}.app-logo{width:98px;border-radius:6px}.title{font-size:20px;letter-spacing:0}.top .sub{color:rgba(255,245,232,.72)}.top-actions{gap:7px}.top-actions button{height:32px;background:rgba(255,255,255,.10);border-color:rgba(255,255,255,.22);color:#fff}.top-actions #status{min-width:72px;height:28px;border-radius:999px;display:grid;place-items:center;background:rgba(255,255,255,.12);color:#ffe9c7;font-weight:900}.workbench-links{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.workbench-card{min-height:62px;border-radius:12px;padding:10px 12px;border:1px solid #ead9bf;background:linear-gradient(180deg,#fff,#fff8ef);box-shadow:0 8px 22px rgba(151,79,18,.07)}.workbench-card.primary-card{background:linear-gradient(135deg,#e83324,#b9130e);box-shadow:0 10px 24px rgba(232,51,36,.18)}.workbench-card b{font-size:15px}.workbench-card span span{margin-top:3px}.workbench-card i{width:30px;height:30px;border-radius:9px}.actions{align-items:stretch}.stock-manager{width:100%;min-width:0;display:grid;grid-template-columns:auto minmax(260px,1fr) 220px auto;gap:9px;padding:10px 12px;border-radius:13px;background:#fff;border:1px solid #ead9bf;box-shadow:0 8px 22px rgba(151,79,18,.06)}.stock-manager-title{align-self:center;color:#5e3723}.watch-tags{min-width:0}.stock-manager input{width:100%;height:32px}.tag{border-radius:9px;padding:5px 8px;background:#fff7eb;border:1px solid #f0dfc7;box-shadow:none}.tag.active{background:linear-gradient(135deg,#2b170f,#7a2b18);color:#fff;border-color:#7a2b18}.tag button{background:rgba(255,255,255,.52)}.bar{height:2px;background:linear-gradient(90deg,#e83324,#d99a1b,#2fb878)}.premarket{grid-template-columns:250px minmax(0,1fr) 360px;gap:8px}.pm-card{border-radius:12px;padding:11px 12px;background:#fff;border:1px solid #ead9bf;box-shadow:0 8px 22px rgba(151,79,18,.06)}.pm-score{font-size:28px;line-height:1.1}.pm-list{grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:7px}.pm-item{border-radius:10px;background:#fff9ef;border-color:#f0dfc7}.live{border-radius:14px;border-color:#ead9bf;box-shadow:0 10px 28px rgba(151,79,18,.08);background:#fff}.monitor-head,.monitor-row{grid-template-columns:156px 82px minmax(300px,1.05fr) 68px 94px 150px minmax(220px,.88fr) 68px;gap:9px}.monitor-head{height:38px;padding:0 12px;background:#2b170f;color:#ffe9c7;border-bottom:1px solid #3e2418}.monitor-row{min-height:126px;padding:9px 12px;border-bottom:1px solid #f2e5d1}.monitor-row:hover{background:#fff9ef}.monitor-row.strong-signal{background:linear-gradient(90deg,rgba(232,51,36,.09),rgba(255,249,239,.72));box-shadow:inset 3px 0 0 #e83324}.rank-dot{width:8px;height:8px;margin-right:7px}.live-name{font-size:14px}.live-price{font-size:21px}.live-chart{height:104px;border-radius:10px;background:linear-gradient(180deg,#fff,#fffaf2)}.chart-note{gap:6px;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.signal-pill{border-radius:9px;padding:4px 8px;background:#fff6e6}.money-line{max-height:76px;border-radius:10px;background:#fff8ee;border:1px solid #f0dfc7}.op{gap:5px}.op button{height:26px;border-radius:8px}.settings-panel,.ai-panel{border-radius:13px;border-color:#ead9bf;box-shadow:0 22px 58px rgba(43,23,15,.20)}::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-track{background:#fff6e8}::-webkit-scrollbar-thumb{background:#d8b98a;border:3px solid #fff6e8;border-radius:999px}@media(max-width:1050px){.panel{width:calc(100vw - 20px);height:auto;min-height:calc(100vh - 20px)}.top{align-items:flex-start;gap:10px}.stock-manager{grid-template-columns:1fr}.premarket{grid-template-columns:1fr}.monitor-table{min-width:1120px}.monitor-head,.monitor-row{grid-template-columns:150px 78px 300px 66px 90px 138px 200px 62px}}

/* Minimal density pass. */
body:before{display:none}.panel{box-shadow:0 12px 36px rgba(43,23,15,.16);gap:8px;grid-template-rows:auto auto auto auto minmax(0,1fr)}.top{min-height:52px;padding:8px 10px;box-shadow:none}.app-logo{width:82px}.title{font-size:18px}.workbench-links{grid-template-columns:repeat(2,minmax(0,1fr));justify-content:start}.workbench-card{min-height:38px;height:38px;padding:8px 12px}.workbench-card span span,.workbench-card i{display:none}.workbench-card b{font-size:14px}.stock-manager{padding:8px 10px}.premarket{grid-template-columns:210px minmax(0,1fr) 300px}.pm-card{box-shadow:none}.pm-score{font-size:22px}.pm-item{padding:6px 8px}.monitor-head{height:34px}.monitor-row{min-height:96px;padding:7px 10px}.live-chart{height:78px}.chart-note{display:none}.money-line{max-height:58px;padding:6px 8px}.live-signal{line-height:1.35}.signal-pill{padding:3px 7px}.op{display:grid}.op button{height:24px}.monitor-head,.monitor-row{grid-template-columns:148px 76px minmax(260px,.95fr) 62px 86px 132px minmax(200px,.78fr) 58px}.premarket .pm-card:nth-child(2){overflow:hidden}.settings-panel,.ai-panel{top:54px}

/* Final console skin: lighter header, calmer red, compact logo. */
button.primary,.workbench-card.primary-card{background:linear-gradient(135deg,#c72a1f,#8f1f16);border-color:#a92319;color:#fff;box-shadow:0 10px 24px rgba(143,31,22,.16)}
.tag.active{background:linear-gradient(135deg,#7a2b18,#3b1810);border-color:#7a2b18}.op button.ai{background:#8f1f16;border-color:#8f1f16}.rank-dot.up{background:#c72a1f}.live-pos{color:#c72a1f}.signal-pill.hot{color:#c72a1f;background:#fff1ec}
.top{min-height:68px;padding:10px 14px;background:linear-gradient(180deg,rgba(255,255,255,.92),rgba(255,248,238,.86));color:#2b170f;border:1px solid #ead9bf;box-shadow:0 10px 30px rgba(151,79,18,.08)}
.app-brand{gap:12px}.app-logo{width:54px;height:54px;object-fit:cover;object-position:left center;border-radius:14px;background:#fff;border:1px solid #f0dfc7;box-shadow:0 8px 18px rgba(151,79,18,.10)}.title{font-size:20px}.top .sub{color:#8a6b52}.top-actions button{height:34px;background:#fff;border-color:#ead9bf;color:#5a321f}.top-actions #status{height:30px;min-width:66px;background:#fff6e8;color:#9a5a18;border:1px solid #ead9bf}
.workbench-links{max-width:740px;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.workbench-card{height:42px;border-radius:13px;background:#fff;border-color:#ead9bf;padding:10px 12px}.workbench-card.primary-card{background:linear-gradient(135deg,#c72a1f,#9d241a)}.workbench-card b{font-size:15px}.stock-manager{border-radius:14px;box-shadow:none}.pm-card,.live{box-shadow:none}
.settings-panel{top:82px;width:min(560px,calc(100vw - 36px));max-height:calc(100vh - 108px);overflow:auto;padding:0;border-radius:14px;background:rgba(255,255,255,.98)}
.settings-head{position:sticky;top:0;z-index:2;margin:0;padding:12px 16px;background:rgba(255,255,255,.96);border-bottom:1px solid #f0dfc7;backdrop-filter:blur(8px)}
.settings-head button{height:34px;border-radius:10px}.settings-group{padding:10px 16px 12px;margin-top:0}.settings-title{margin-bottom:7px}.settings-actions{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.settings-actions button{height:34px}.ai-config-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.ai-config-grid input,.ai-config-grid select{height:34px;border-radius:10px}.settings-note{padding:0 16px 14px;font-size:11px}.settings-panel::-webkit-scrollbar{width:8px}.settings-panel::-webkit-scrollbar-thumb{background:#d8b98a;border:2px solid #fff6e8;border-radius:999px}

/* Final layout tuning: quiet workbench entries and a tighter settings drawer. */
.workbench-links{max-width:620px;gap:10px}
.workbench-card,.workbench-card.primary-card{position:relative;height:38px;background:linear-gradient(180deg,#fff,#fff9f0);color:#2b170f;border:1px solid #ead9bf;box-shadow:none}
.workbench-card.primary-card{border-color:#e6c9a3}
.workbench-card.primary-card:before{content:"";position:absolute;left:0;top:8px;bottom:8px;width:3px;border-radius:999px;background:#c72a1f}
.workbench-card.primary-card span{color:#2b170f}.workbench-card b{font-size:14px}.workbench-card span span{display:none}
.top{min-height:62px}.app-logo{width:48px;height:48px;border-radius:12px}.title{font-size:18px}.top-actions button{height:32px;padding:0 12px}
.stock-manager{min-height:44px;padding:7px 10px}.premarket{grid-template-columns:220px minmax(0,1fr) 320px}.pm-card{min-height:122px}.live{border-radius:12px}
.settings-panel{top:76px;right:14px;width:min(500px,calc(100vw - 32px));max-height:calc(100vh - 104px)}
.settings-actions button{height:32px}.ai-config-grid input,.ai-config-grid select{height:32px}.settings-group{padding:9px 14px 11px}.settings-head{padding:10px 14px}
.hero-image>img{height:calc(100vh + 118px);min-height:878px;transform:translateY(-118px);margin-bottom:-118px}
@media(max-width:900px){.hero-image>img{height:auto;min-height:0;transform:none;margin-bottom:0}}
</style>
</head>
<body>
<div class="shell"><main class="panel">
  <div id="signalToasts" class="signal-toasts"></div>
  <div class="top">
    <div class="app-brand"><img class="app-logo" src="/assets/logo.png" alt="做T神器"><div><div class="title">T神器控制台</div><div class="sub">多股票实时监控</div></div></div>
    <div class="top-actions"><button onclick="location.href='/recharge'">会员码</button><button id="settingsBtn" onclick="toggleSettings()">设置</button><div id="status" class="sub">就绪</div></div>
  </div>
  <section class="workbench-links" aria-label="工作台入口">
    <button class="workbench-card primary-card" onclick="location.href='/simulation'"><span><b>模拟测试</b><span>胜率、买卖点、复盘</span></span><i>测</i></button>
    <button class="workbench-card" onclick="location.href='/research'"><span><b>选股研究</b><span>AI评审、龙虎榜、RPS筛选</span></span><i>研</i></button>
  </section>
  <div id="settingsPanel" class="settings-panel" hidden>
    <div class="settings-head"><span>系统设置</span><button onclick="toggleSettings(false)">关闭</button></div>
    <div class="settings-group" style="border-top:0;margin-top:0;padding-top:0">
      <div class="settings-title">账号与商业</div>
      <div class="settings-actions">
        <button onclick="location.href='/account'">账号中心</button>
        <button onclick="location.href='/recharge'">激活码充值</button>
        <button onclick="location.href='/commercial'">功能中心</button>
        <button onclick="location.href='/landing'">商业首页</button>
      </div>
    </div>
    <div class="settings-group">
      <div class="settings-title">AI模型配置</div>
      <div class="ai-config-grid">
        <label>服务商<select id="aiProvider"><option value="ChatGPT">ChatGPT</option><option value="Gemini">Gemini</option><option value="Claude">Claude</option><option value="ThirdParty">第三方API</option></select></label>
        <label>模型<input id="aiModel" placeholder="如 gpt-4o-mini / gemini-2.5-flash / claude-sonnet-4" /></label>
        <label class="wide">API地址<input id="aiBase" placeholder="ChatGPT/Gemini/Claude可留空；第三方API填 https://域名/v1" /></label>
        <label class="wide">代理地址<input id="aiProxy" placeholder="如 http://127.0.0.1:10808，可留空" /></label>
        <label class="wide">API Key<input id="aiKey" type="password" placeholder="未配置" autocomplete="off" /></label>
      </div>
      <div class="settings-actions">
        <button onclick="saveAiSettings()">保存AI配置</button>
        <button onclick="checkAiFromSettings()">检测AI</button>
        <button onclick="clearAiKey()">清除Key</button>
      </div>
      <div id="aiConfigStatus" class="settings-note">AI Key 和第三方中转地址只保存在本机配置，界面不会明文回显 Key。</div>
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
const labels={signal:'多股信号',simulate:'随机股票当日做T模拟',simulate5:'随机股票近5轮缓存测试'};
let tradeManual=false,audioCtx=null,lastAlertKeys=new Map(),toastKeys=new Map(),settingsTimer=null;
window.addEventListener('DOMContentLoaded',async()=>{document.addEventListener('click',initAudio,{once:true});loadSettings();await loadWatchlist();loadPremarket();loadRealtime();setInterval(loadRealtime,3000);setInterval(loadPremarket,60000);});
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
function fillAiSettings(s){signalPrefs.maxSignalsPerDay=Number(s.maxSignalsPerDay||2);signalPrefs.signalCooldown=Number(s.signalCooldown||10);if($('aiProvider'))$('aiProvider').value=({OpenAI:'ChatGPT','OpenAI兼容':'ThirdParty',OpenAICompatible:'ThirdParty','第三方API':'ThirdParty'}[s.aiProvider]||(s.aiProvider||'ChatGPT'));if($('aiModel'))$('aiModel').value=s.aiModel||(({ChatGPT:'gpt-4o-mini',Gemini:'gemini-2.5-flash',Claude:'claude-sonnet-4',ThirdParty:'gpt-4o-mini'}[$('aiProvider')?.value])||'gpt-4o-mini');if($('aiBase'))$('aiBase').value=s.aiBase||'';if($('aiProxy'))$('aiProxy').value=s.aiProxy||'';if($('aiKey'))$('aiKey').placeholder=s.aiKeyConfigured?(s.aiKeyMasked||'已配置'):'未配置';['marketDataApi','newsApi','quoteApi','customStrategy','strategyMode','maxSignalsPerDay','lowBuyDev','highSellDev','signalCooldown'].forEach(k=>{if($(k))$(k).value=s[k]||''});if($('aiConfigStatus'))$('aiConfigStatus').textContent=s.aiKeyConfigured?'AI Key 已配置。需要更换时直接输入新 Key 并保存。':'未配置 AI Key，本地规则仍可使用。'}
function aiOptions(extra={}){return {...simOptions(),aiProvider:$('aiProvider')?.value||'ChatGPT',aiModel:$('aiModel')?.value||'gpt-4o-mini',aiBase:$('aiBase')?.value||'',aiProxy:$('aiProxy')?.value||'',aiKey:$('aiKey')?.value||'',customStrategy:$('customStrategy')?.value||'',strategyMode:$('strategyMode')?.value||'官方默认策略',maxSignalsPerDay:$('maxSignalsPerDay')?.value||'2',lowBuyDev:$('lowBuyDev')?.value||'-1.20',highSellDev:$('highSellDev')?.value||'1.40',signalCooldown:$('signalCooldown')?.value||'10',...extra}}
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
let realtimeBusy=false,realtimeLastOk=0;
async function loadRealtime(){if(realtimeBusy)return;realtimeBusy=true;try{const ctrl=new AbortController();const timer=setTimeout(()=>ctrl.abort(),2600);const data=await (await fetch('/api/realtime',{cache:'no-store',signal:ctrl.signal})).json();clearTimeout(timer);realtimeLastOk=Date.now();renderRealtime(data.stocks||[])}catch(e){if(Date.now()-realtimeLastOk>15000)$('live').innerHTML='<div class="empty-live">实时监控暂不可用：'+escapeHtml(e.message||'请求超时')+'</div>'}finally{realtimeBusy=false}}
async function loadPremarket(){try{const q=premarketTargetCode?'?code='+encodeURIComponent(premarketTargetCode):'';const data=await (await fetch('/api/premarket'+q,{cache:'no-store'})).json();renderPremarket(data)}catch(e){$('pmReason').textContent='外盘读取失败：'+e.message}}
function renderPremarket(data){const z=data.target||data.zijin||{},rows=data.rows||[];const signal=z.signal||'观望';const cls=signal==='偏多'?'bull':signal==='偏空'?'bear':'';if($('pmTargetTitle'))$('pmTargetTitle').textContent=(z.name||'目标股')+'开盘前风向';document.querySelector('.pm-score').textContent=(z.score??'--')+'分';const pill=document.querySelector('.pm-signal');pill.textContent=signal+'｜'+(z.category||'综合')+'｜刷新 '+(data.updatedAt||'--');pill.className='pm-signal '+cls;$('pmList').innerHTML=rows.length?rows.map(r=>{const ch=Number(r.change||0),c=ch>=0?'live-pos':'live-neg';const price=Number(r.price||0);return `<div class="pm-item"><b>${escapeHtml(r.name)}</b><span class="${c}">${price>=100?price.toFixed(1):price.toFixed(2)} ${ch>=0?'+':''}${ch.toFixed(2)}%</span><div class="sub">${escapeHtml(r.time||'时间未知')}</div></div>`}).join(''):'<div class="muted">暂无外盘数据</div>';$('pmReason').innerHTML=`<b>${escapeHtml(z.action||'等待盘中确认')}</b><br>${(z.reasons||[]).map(escapeHtml).join('<br>')}<br><span class="sub">外盘为 Yahoo 5分钟快照，按当前主监控股票降权计算，只作为盘前方向。</span>`}
function renderRealtime(rows){if(!rows.length){$('live').innerHTML='<div class="empty-live">暂无监控股票，请先添加代码。</div>';return}$('live').innerHTML=`<div class="monitor-table"><div class="monitor-head"><span>股票</span><span>现价</span><span>分时结构</span><span>涨跌</span><span>均价/偏离</span><span>买卖点</span><span>多角色结论</span><span>操作</span></div>${rows.map(r=>{maybeBeep(r);showSignalToast(r);const change=Number(r.change||0);const cls=change>=0?'live-pos':'live-neg';const tradable=/低吸|高抛|买入|卖出/.test(r.signal||'');const closed=r.marketStatus==='休市中';const signal=closed?'休市中':(r.signal||'观察');const dot=change>=0?'up':'down';const agents=(Array.isArray(r.agents)&&r.agents.length?r.agents:[(r.smartMoney&&r.smartMoney.text)||'主力行为：普通行情模式，仅供辅助参考']).map(escapeHtml).join('<br>');return `<div class="monitor-row ${tradable?'strong-signal':''}"><div><span class="rank-dot ${dot}"></span><span class="live-name">${escapeHtml(r.name)}</span><span class="live-code">${escapeHtml(r.code)}</span><div class="kv">更新时间 ${escapeHtml(r.time||'--:--')}</div></div><div class="live-price ${cls}">${Number(r.price||0).toFixed(2)}</div><div>${liveSpark(r)}</div><div class="${cls}" style="font-weight:950">${change.toFixed(2)}%</div><div class="kv"><b>${Number(r.avg||0).toFixed(2)}</b>偏离 ${Number(r.dev||0).toFixed(2)}%</div><div><span class="signal-pill ${tradable?'hot':''}">${escapeHtml(signal)}</span><div class="live-signal">${escapeHtml(r.reason||'暂无高质量买卖点')}</div></div><div class="money-line">${agents}</div><div class="op"><button class="ai" onclick="aiIntraday('${escapeHtml(r.code)}')">AI</button><button onclick="focusStock('${escapeHtml(r.code)}')">详情</button></div></div>`}).join('')}</div>`}
function alertSide(sig){if(/低吸|买入/.test(sig||''))return 'buy';if(/高抛|卖出/.test(sig||''))return 'sell';return ''}
function showSignalToast(r){const sig=r.signal||'',side=alertSide(sig);if(!side)return;const today=new Date().toISOString().slice(0,10);const dailyKey=`toast:${today}:${r.code}:${side}`;if(Number(localStorage.getItem(dailyKey)||0)>=Math.max(1,signalPrefs.maxSignalsPerDay||2))return;const key=[r.code,side].join(':');const now=Date.now();if(toastKeys.has(key)&&now-toastKeys.get(key)<Math.max(1,signalPrefs.signalCooldown||10)*60000)return;toastKeys.set(key,now);localStorage.setItem(dailyKey,String(Number(localStorage.getItem(dailyKey)||0)+1));const box=$('signalToasts');if(!box)return;const isBuy=side==='buy';const agents=(Array.isArray(r.agents)&&r.agents.length?r.agents:[]).slice(0,4).map(escapeHtml).join('<br>');const el=document.createElement('div');el.className='signal-toast '+(isBuy?'buy':'sell');el.innerHTML=`<div class="toast-top"><div><div class="toast-name">${escapeHtml(r.name)} ${escapeHtml(r.code)}</div><div class="toast-price">${escapeHtml(r.time||'--:--')}｜现价 ${Number(r.price||0).toFixed(2)}｜偏离 ${Number(r.dev||0).toFixed(2)}%</div></div><span class="toast-signal">${isBuy?'买入':'卖出'}｜${escapeHtml(sig)}</span><button title="关闭">×</button></div><div class="toast-reason">${escapeHtml(r.reason||'等待量价确认')}</div><div class="toast-agents">${agents||'多角色结论生成中'}</div>`;box.prepend(el);const close=()=>{el.classList.add('fade');setTimeout(()=>el.remove(),260)};el.querySelector('button').onclick=close;setTimeout(close,60000);[...box.children].slice(2).forEach(x=>x.remove())}
function focusStock(code){location.href='/research?code='+encodeURIComponent(code)}
async function aiIntraday(code){toggleAi(true);const body=$('aiBody');body.innerHTML='<span class="ai-chip">Gemini</span><span class="ai-chip">大方向</span><span class="ai-chip">路径预判</span><span class="ai-chip">买卖点复核</span><br>正在集中讨论当前买卖点...';try{const ctrl=new AbortController();const timer=setTimeout(()=>ctrl.abort(),11000);const data=await (await fetch('/api/gemini_intraday?code='+encodeURIComponent(code),{cache:'no-store',signal:ctrl.signal})).json();clearTimeout(timer);if(!data.ok){body.textContent=data.message||'Gemini 暂无返回';return}const a=data.analysis||{},s=data.stock||{};const agents=Array.isArray(a.agents)?a.agents:[];const key=formatKeyPrices(a.keyPrices);const path=formatPathForecast(a.pathForecast);body.innerHTML=`<span class="ai-chip">${escapeHtml(s.股票||code)}</span><span class="ai-chip">现价 ${escapeHtml(s.现价??'--')}</span><span class="ai-chip">昨收 ${escapeHtml(s.昨收??'--')}</span><span class="ai-chip">黄线偏离 ${escapeHtml(s.黄线偏离??'--')}%</span><b>大方向</b>${escapeHtml(a.macroView||a.trend||'观察')}<b>今日路径预判</b>${escapeHtml(path)}<b>关键价位</b>${escapeHtml(key)}<b>买卖点复核</b>${escapeHtml(a.pointReview||'当前未形成最优买卖点，先按观察处理')}<b>趋势判断</b>${escapeHtml(a.trend||'证据不足，先观察')}<b>操作计划</b>${escapeHtml(a.action||'不强行交易')}<br>买入：${escapeHtml(a.buyPlan||'等待低位确认')}<br>卖出：${escapeHtml(a.sellPlan||'等待高位确认')}<b>失效条件</b>${escapeHtml(a.invalidation||'跌破/突破关键价后重新评估')}<b>多角色研判</b>${agents.map(escapeHtml).join('<br>')||'暂无多角色返回'}<p class="sub">模型：${escapeHtml(data.model||'Gemini')}。该观点30分钟内会同步给监控观察员参考。</p>`;loadRealtime()}catch(e){body.textContent='Gemini 分析超时或网络不可达：'+(e.message||e)}}
function formatPathForecast(v){if(!v)return '证据不足，暂按震荡处理';if(typeof v==='string')return v;const map={mostLikely:'主路径',alternative:'备选路径',bullish:'偏多路径',bearish:'偏空路径',neutral:'震荡路径'};return Object.entries(v).map(([k,val])=>`${map[k]||k}：${val}`).join('｜')}
function formatKeyPrices(v){if(!v)return '等待确认';if(typeof v==='string')return v;const map={support:'支撑位',resistance:'压力位',vwap:'黄线均价',buyLow:'低吸区',sellHigh:'高抛区',stopLoss:'止损位',takeProfit:'止盈位'};return Object.entries(v).map(([k,val])=>`${map[k]||k}：${val}`).join('｜')}
function liveSpark(row){const series=(row.prices||[]).filter(x=>Number(x.price)>0);const c=Number(row.change)>=0?'#ec5f6b':'#35b978';if(series.length<2)return '<svg class="live-chart" viewBox="0 0 420 132"><line x1="14" y1="62" x2="406" y2="62" stroke="#d7dde2" stroke-width="2"/><text x="210" y="72" text-anchor="middle" fill="#94a3af" font-size="12" font-weight="900">等待分时数据</text></svg>';const step=Math.max(1,Math.floor(series.length/120));const points=series.filter((_,i)=>i%step===0);const values=points.map(x=>Number(x.price));const rawMin=Math.min(...values),rawMax=Math.max(...values),rawSpan=Math.max(rawMax-rawMin,.01);const pad=rawSpan*.16;const min=rawMin-pad,max=rawMax+pad,span=Math.max(max-min,.01);const yOf=v=>94-((Number(v)-min)/span)*82;const xy=points.map((p,i)=>({x:18+(i/(points.length-1))*384,y:yOf(p.price),price:Number(p.price),time:p.time,vol:Number(p.volume||p.vol||0)}));const pts=xy.map(p=>`${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');const avg=Number(row.avg||0);const avgLine=avg>0?`<line x1="18" y1="${yOf(avg).toFixed(1)}" x2="402" y2="${yOf(avg).toFixed(1)}" stroke="#f3c545" stroke-width="1.5" stroke-dasharray="5 4"/><text x="22" y="${Math.max(13,yOf(avg)-4).toFixed(1)}" fill="#c18a00" font-size="10" font-weight="900">黄线 ${avg.toFixed(2)}</text>`:'';const b2=rawMin+rawSpan*.10,b1=rawMin+rawSpan*.24,s1=rawMax-rawSpan*.24,s2=rawMax-rawSpan*.10;const level=(v,label,color)=>`<line x1="18" y1="${yOf(v).toFixed(1)}" x2="402" y2="${yOf(v).toFixed(1)}" stroke="${color}" stroke-width="1" stroke-dasharray="3 4" opacity=".75"/><text x="404" y="${Math.max(12,Math.min(94,yOf(v)+3)).toFixed(1)}" fill="${color}" font-size="10" font-weight="950">${label}</text>`;const volSrc=xy.map((p,i)=>p.vol||Math.abs((p.price-(xy[i-1]?.price||p.price)))*1000+.1);const maxVol=Math.max(...volSrc,.1);const bars=xy.map((p,i)=>{const h=Math.max(2,(volSrc[i]/maxVol)*22);const color=i&&p.price>=xy[i-1].price?'#ec5f6b':'#35b978';return `<rect x="${(p.x-1).toFixed(1)}" y="${(124-h).toFixed(1)}" width="2" height="${h.toFixed(1)}" fill="${color}" opacity=".42"/>`}).join('');const norm=t=>String(t||'').replace(':','');const mark=(time,label,color,dy)=>{if(!time||time==='--:--')return '';const target=norm(time);let idx=xy.findIndex(p=>norm(p.time)>=target);if(idx<0)idx=xy.length-1;const p=xy[idx],ty=Math.max(10,Math.min(98,p.y+dy));return `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="5.5" fill="${color}" stroke="#fff" stroke-width="2"/><text x="${p.x.toFixed(1)}" y="${ty.toFixed(1)}" text-anchor="middle" font-size="10" font-weight="950" fill="${color}">${label}</text>`};const sig=String(row.signal||'');const last=xy[xy.length-1];const liveMark=/低吸|买入/.test(sig)?`<g><circle cx="${last.x.toFixed(1)}" cy="${last.y.toFixed(1)}" r="6" fill="#2563eb" stroke="#fff" stroke-width="2"/><text x="${last.x.toFixed(1)}" y="${Math.max(11,last.y-10).toFixed(1)}" text-anchor="middle" font-size="10" font-weight="950" fill="#2563eb">买</text></g>`:(/高抛|卖出/.test(sig)?`<g><circle cx="${last.x.toFixed(1)}" cy="${last.y.toFixed(1)}" r="6" fill="#ec5f6b" stroke="#fff" stroke-width="2"/><text x="${last.x.toFixed(1)}" y="${Math.max(11,last.y-10).toFixed(1)}" text-anchor="middle" font-size="10" font-weight="950" fill="#ec5f6b">卖</text></g>`:'');return `<svg class="live-chart" viewBox="0 0 420 132"><rect x="0" y="0" width="420" height="132" fill="transparent"/><line x1="18" y1="94" x2="402" y2="94" stroke="#edf1f3"/><line x1="18" y1="12" x2="402" y2="12" stroke="#edf1f3"/><line x1="18" y1="124" x2="402" y2="124" stroke="#edf1f3"/>${level(s2,'S2','#ec5f6b')}${level(s1,'S1','#f59e0b')}${avgLine}${level(b1,'B1','#2563eb')}${level(b2,'B2','#0ea5e9')}<polyline points="${pts}" fill="none" stroke="${c}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>${bars}${mark(row.buyTime,'B','#2563eb',-9)}${mark(row.sellTime,'S','#ec5f6b',15)}${liveMark}</svg><div class="chart-note"><span>蓝=B区低吸</span><span>红=S区高抛</span><span>黄=分时均价</span><span>底部=量能</span></div>`}
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
:root{--ink:#2b170f;--muted:#8a6b52;--line:#f0dfc7;--green:#2fb878;--red:#e83324;--yellow:#d99a1b;--gold:#d99a1b}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:13px/1.55 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:var(--ink);background:radial-gradient(circle at 88% 0,#ffe2aa,transparent 34%),linear-gradient(135deg,#b4783b,#fff5e8 42%,#fffaf4)}
button,input{font:inherit}button{height:36px;border:1px solid var(--line);border-radius:11px;background:#fff;padding:0 15px;font-weight:850;cursor:pointer;box-shadow:0 8px 18px rgba(151,79,18,.07)}button.primary{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff;border-color:#e83324}button:disabled{opacity:.6;cursor:wait}
.page{min-height:100vh;padding:14px;display:grid;grid-template-rows:auto auto auto auto minmax(0,1fr);gap:9px}.top,.controls,.panel,.metric,.progress{background:rgba(255,255,255,.94);border:1px solid rgba(255,255,255,.85);border-radius:16px;box-shadow:0 14px 38px rgba(25,45,50,.12)}.top{height:58px;display:flex;align-items:center;justify-content:space-between;padding:0 16px}.title{font-size:20px;font-weight:950}.sub,.muted{color:var(--muted)}.controls{padding:9px 11px;display:flex;flex-wrap:wrap;gap:8px;align-items:end}.field span{display:block;font-size:10px;color:var(--muted);font-weight:850}.field input{width:132px;height:32px;border:1px solid var(--line);border-radius:10px;padding:0 10px}.field.wide input{width:220px}.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.metric{padding:10px 13px}.metric .k{font-size:10px;color:var(--muted);font-weight:850}.metric .v{font-size:19px;font-weight:950}.win{color:var(--yellow)}.pos{color:var(--green)!important}.neg{color:var(--red)!important}.layout{display:grid;grid-template-rows:minmax(0,1fr) auto;gap:9px;min-height:0}.panel{overflow:hidden;display:flex;flex-direction:column;min-height:0}.head{height:38px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 14px;font-weight:950}.badge{font-size:11px;color:#35a66d;background:#eafff1;border-radius:999px;padding:4px 9px}.rows{flex:1;overflow:auto;padding:0}.sim-table{min-width:1120px}.sim-head,.sim-row{display:grid;grid-template-columns:190px 110px minmax(320px,1fr) 90px 130px minmax(240px,.75fr);gap:12px;align-items:center}.sim-head{position:sticky;top:0;z-index:2;height:34px;padding:0 14px;background:#fff8ee;border-bottom:1px solid var(--line);color:#8a6b52;font-size:11px;font-weight:950}.sim-row{min-height:68px;border-bottom:1px solid #f1f3f4;padding:7px 14px}.sim-row:last-child{border-bottom:0}.stock{font-weight:950;font-size:14px}.code{color:var(--muted);margin-left:6px}.reason{color:#56616b;font-size:12px;line-height:1.45}.status{font-weight:900}.chart{width:100%;height:52px;display:block}.empty{padding:40px 12px;text-align:center;color:var(--muted)}.side{display:grid;grid-template-columns:1fr 1fr;gap:9px;min-height:138px;max-height:178px}.content{padding:10px 14px;overflow:auto;line-height:1.58;font-size:12px}.content b{display:block;margin:4px 0}.content ul{margin:4px 0 0;padding-left:18px}.run-item{border-bottom:1px solid #f2f4f5;padding:6px 0}.bar{height:3px;background:linear-gradient(90deg,#4cc9f0,#35b978);border-radius:99px;opacity:0}.bar.on{opacity:1;animation:pulse .9s infinite alternate}.progress{display:none;padding:8px 10px}.progress.on{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.step{border:1px solid #f4e5cf;border-radius:11px;padding:7px 8px;background:#f9fbfb}.step b{display:block;font-size:12px}.step span{font-size:11px;color:var(--muted)}.step.active{border-color:#bdebd2;background:#effff5}.step.done b{color:var(--green)}@keyframes pulse{from{filter:brightness(.8)}to{filter:brightness(1.2)}}
@media(max-width:1050px){.metrics{grid-template-columns:repeat(2,1fr)}.sim-table{min-width:980px}.side{grid-template-columns:1fr;max-height:none}}

/* Compact simulation layout. */
body{background:#fffaf4}.page{padding:12px;grid-template-rows:auto auto auto minmax(0,1fr);gap:8px}.top,.controls,.panel,.metric{border-radius:12px;box-shadow:none;border-color:#ead9bf}.top{height:50px;padding:0 12px}.title{font-size:18px}.top .sub{display:none}button{height:30px;border-radius:8px;box-shadow:none}.controls{padding:8px;gap:6px;align-items:center}.field span{display:none}.field input{width:112px;height:30px;border-radius:8px}.field.wide input{width:190px}.metrics{grid-template-columns:repeat(6,minmax(0,1fr));gap:6px}.metric{padding:7px 10px}.metric .k{font-size:10px}.metric .v{font-size:16px}.progress{display:none!important}.layout{gap:8px}.head{height:34px;padding:0 12px}.sim-head{height:32px}.sim-row{min-height:56px;padding:6px 12px}.chart{height:42px}.side{max-height:126px;min-height:108px}.content{padding:8px 12px;line-height:1.45}.run-item{padding:4px 0}.sim-head,.sim-row{grid-template-columns:170px 90px minmax(260px,1fr) 70px 110px minmax(210px,.7fr)}@media(max-width:1050px){.top{height:auto;padding:10px;gap:8px}.controls{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.field input,.field.wide input{width:100%}.metrics{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<div class="page">
  <div class="top"><div><div class="title">模拟测试</div><div class="sub">随机股票、监控同步、日内曲线、买卖点、历史缓存压力测试</div></div><div><button onclick="location.href='/app'">返回监控</button> <button onclick="location.href='/research'">选股研究</button></div></div>
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
    <button onclick="runSim('simulate5')">近5轮缓存测试</button>
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
window.addEventListener('DOMContentLoaded',async()=>{await loadSettings();await syncWatchlistStocks(false);loadHistory();restoreLatestSim();});
function pct(id,fallback){const n=Number($(id).value||fallback);return Math.max(0.05,Math.min(2,n))}
function options(){return {cash:Number($('cashInput').value||100000),trade:Number($('tradeInput').value||20000),sample:Number($('sampleInput').value||10),stocks:($('stocksInput')?.value||'').trim(),vwap_take_profit_pct:pct('vwapProfitInput',0.25),normal_take_profit_pct:pct('normalProfitInput',0.6),late_take_profit_pct:pct('lateProfitInput',0.45)}}
function setBusy(on){document.querySelectorAll('button').forEach(b=>b.disabled=on)}
async function runSim(name){setBusy(true);$('status').textContent='运行中';$('loading').classList.add('on');startProgress();try{if(!$('stocksInput').value.trim())await syncWatchlistStocks(false);const payload=options();saveSettings();const res=await fetch('/api/run/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});markProgress(3);const data=await res.json();markProgress(4);updateStats(data.stats||{});renderRows(data.stocks||[]);renderReview((data.stats||{}).review||{},(data.stats||{}).history||{});$('status').textContent=data.summary||'完成';await loadHistory(false);finishProgress()}catch(e){$('status').textContent='失败：'+e.message;stopProgress()}$('loading').classList.remove('on');setBusy(false)}
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
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.55 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:#2b170f;background:radial-gradient(circle at 88% 0,#ffe2aa,transparent 34%),linear-gradient(135deg,#b4783b,#fff5e8 42%,#fffaf4)}button,a.btn{height:36px;border:1px solid #f0dfc7;border-radius:11px;background:#fff;color:#2b170f;text-decoration:none;padding:0 15px;font-weight:850;cursor:pointer;box-shadow:0 8px 18px rgba(151,79,18,.07);display:inline-flex;align-items:center}button.primary{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff;border-color:#e83324}button:disabled{opacity:.6;cursor:wait}input,select{height:36px;border:1px solid #f0dfc7;border-radius:11px;background:#fff;padding:0 12px;font:inherit;font-weight:800}.shell{width:min(1680px,calc(100vw - 28px));min-height:calc(100vh - 36px);margin:18px auto;padding:18px;border-radius:22px;background:rgba(255,255,255,.94);box-shadow:0 24px 70px rgba(151,79,18,.16);display:grid;grid-template-rows:auto auto auto 1fr auto;gap:14px}.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.title{font-size:23px;font-weight:950}.sub,.muted{color:#8a6b52}.actions,.single-bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.actions{justify-content:flex-end}.single-bar{padding:10px 12px;background:#fff;border:1px solid #f0dfc7;border-radius:16px;box-shadow:0 10px 30px rgba(151,79,18,.06)}.single-bar input{width:170px}.status{min-height:24px;color:#6b5543;font-weight:750}.grid{display:grid;grid-template-columns:300px 1fr;gap:14px;min-height:0}.panel{background:#fff;border:1px solid #f0dfc7;border-radius:16px;overflow:hidden;box-shadow:0 10px 30px rgba(151,79,18,.06)}.head{height:46px;padding:0 16px;border-bottom:1px solid #f0dfc7;display:flex;align-items:center;justify-content:space-between;font-weight:950}.body{padding:12px;overflow:auto}.cat{width:100%;justify-content:space-between;margin:5px 0;box-shadow:none;background:#fff8ee}.cat.active{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff}.badge{border-radius:999px;background:#eafff1;color:#35a66d;padding:4px 9px;font-size:12px;font-weight:850}table{width:100%;border-collapse:collapse}th,td{padding:12px 14px;border-bottom:1px solid #f3e7d8;text-align:left;vertical-align:top}th{font-size:12px;color:#8a6b52}.stock{font-weight:950}.code{color:#8a6b52;margin-left:7px}.score{font-weight:950;color:#d99a1b}.up{color:#ec5f6b}.down{color:#35b978}.tag{display:inline-flex;border-radius:999px;background:#fff1dc;padding:4px 8px;color:#6b5543;font-size:12px;font-weight:750}.tag.lhb-on{background:#fff0f1;color:#d71912}.agents{line-height:1.75;color:#303942}.detail{padding:16px}.detail h3{margin:0 0 8px}.empty{text-align:center;padding:35px;color:#8a6b52;font-weight:800}@media(max-width:900px){.grid{grid-template-columns:1fr}.top{display:block}.actions{justify-content:flex-start;margin-top:12px}th,td{padding:10px 8px;font-size:12px}}

/* Compact research layout. */
body{background:#fffaf4;font-size:13px}.shell{width:min(1720px,calc(100vw - 24px));min-height:calc(100vh - 24px);margin:12px auto;padding:12px;border-radius:14px;box-shadow:none;border:1px solid #ead9bf;grid-template-rows:auto auto auto minmax(0,1fr) auto;gap:8px}.top{min-height:46px;align-items:center}.title{font-size:18px}.top .sub{display:none}button,a.btn,input,select{height:30px;border-radius:8px;box-shadow:none}.actions{gap:6px}.single-bar{padding:8px;border-radius:11px;box-shadow:none}.single-bar .muted{display:none}.status{min-height:18px;font-size:12px}.grid{grid-template-columns:1fr;gap:8px}.grid>aside.panel{order:0}.grid>section.panel{order:1}.panel{border-radius:11px;box-shadow:none;border-color:#ead9bf}.head{height:34px;padding:0 12px}.body{padding:8px;display:flex;gap:6px;overflow:auto}.cat{width:auto;min-width:max-content;height:28px;margin:0;padding:0 10px;border-radius:8px}.badge{padding:3px 7px}.grid .panel:nth-child(2)>div[style]{max-height:calc(100vh - 238px)!important}.detail{padding:12px;max-height:220px;overflow:auto}th,td{padding:8px 10px}th{position:sticky;top:0;background:#fff8ee;z-index:2}.agents{line-height:1.45;max-height:62px;overflow:hidden}.tag{border-radius:8px;padding:3px 7px}@media(max-width:900px){.shell{width:calc(100vw - 16px);margin:8px auto;padding:8px}.top{display:block}.actions{margin-top:8px}.single-bar{display:grid;grid-template-columns:1fr 120px auto}.single-bar b{grid-column:1/-1}.single-bar input{width:100%}.grid .panel:nth-child(2)>div[style]{max-height:none!important}}
</style>
</head>
<body>
<main class="shell">
  <div class="top"><div><div class="title">A股选股研究</div><div class="sub">多Agent评审 + AI选股，输出10只跨行业候选</div></div><div class="actions"><a class="btn" href="/app">返回监控</a><a class="btn" href="/rps">RPS主线</a><a class="btn" href="/longhubang">龙虎榜</a><button class="primary" id="reviewBtn" onclick="loadData('review')">评审选股</button><button id="geminiBtn" onclick="loadData('gemini')">AI选股</button><button id="checkBtn" onclick="checkAi()">检测AI</button></div></div>
  <div class="status" id="status">准备加载本地选股...</div>
  <section class="single-bar">
    <b>单股研究</b>
    <input id="singleInput" placeholder="输入代码，如 600580" onkeydown="if(event.key==='Enter')researchSingle()" />
    <select id="singleMode"><option value="review">评审研究</option><option value="gemini">AI研究</option></select>
    <button id="singleBtn" onclick="researchSingle()">开始研究</button>
    <span class="muted">输出技术、消息、基本面、资金、风控和3个月预测</span>
  </section>
  <section class="grid"><aside class="panel"><div class="head">分类池 <span class="badge" id="count">0只</span></div><div class="body" id="categories"></div></aside><section class="panel"><div class="head">候选股票</div><div style="overflow:auto;max-height:62vh"><table><thead><tr><th>股票</th><th>分类</th><th>层级</th><th>3个月预测</th><th>中期分</th><th>价格</th><th>涨跌</th><th>龙虎榜</th><th>多Agent结论</th></tr></thead><tbody id="rows"><tr><td colspan="9" class="empty">加载中...</td></tr></tbody></table></div></section></section>
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
function renderRows(){const rows=currentRows();$('count').textContent=rows.length+'只';if(!rows.length){$('rows').innerHTML='<tr><td colspan="9" class="empty">暂无候选</td></tr>';return}$('rows').innerHTML=rows.map((r,idx)=>{const agents=Array.isArray(r.agents)&&r.agents.length?r.agents:['技术员：本地规则初筛','资金员：等待量价确认','风控员：控制仓位'];const medium=r.mediumAnalysis||{},forecast=r.forecast||{},lhb=r.longhubang||{};const lhbText=lhb.onList?`<span class="tag lhb-on">上榜 ${esc(lhb.score??'--')}</span><div class="muted">净买入 ${esc(lhb.netBuyText||'--')}</div><div class="muted">${esc(lhb.reason||'龙虎榜上榜')}</div>`:`<span class="tag">未上榜</span><div class="muted">${esc(lhb.reason||'近5日未上龙虎榜')}</div>`;return `<tr onclick="showDetail(${idx})" style="cursor:pointer"><td><span class="stock">${esc(r.name)}</span><span class="code">${esc(r.code)}</span></td><td><span class="tag">${esc(r.category||'综合观察')}</span><div class="muted">${esc(r.categoryReason||r.reason||'')}</div></td><td><span class="tag">${esc(r.tier||'等待确认')}</span></td><td><span class="tag">${esc(forecast.label||'观察')}</span><div class="muted">${esc(forecast.expected||'--')}｜置信${esc(forecast.confidence||'--')}</div></td><td class="score">${esc(r.mediumScore??'--')}/10</td><td>${esc(r.price??'--')}</td><td class="${clsChange(r.change)}">${esc(r.change??'--')}%</td><td>${lhbText}</td><td><div class="agents">${agents.slice(0,5).map(esc).join('<br>')}</div><div class="muted">催化：${esc(medium.catalyst||'等待消息确认')}</div></td></tr>`}).join('')}
function showDetail(idx){const r=currentRows()[idx];if(!r)return;const agents=Array.isArray(r.agents)?r.agents:[];const reasons=Array.isArray(r.reasons)?r.reasons:[];const daily=r.dailyAnalysis||{},medium=r.mediumAnalysis||{},forecast=r.forecast||{},lhb=r.longhubang||{};const lhbTag=lhb.onList?`<span class="tag lhb-on">龙虎榜 ${esc(lhb.score??'--')}｜净买入 ${esc(lhb.netBuyText||'--')}</span>`:`<span class="tag">近5日未上龙虎榜</span>`;$('detail').style.display='block';$('detail').innerHTML=`<h3>${esc(r.name)} <span class="code">${esc(r.code)}</span></h3><p><span class="tag">${esc(r.category||'综合观察')}</span> <span class="tag">${esc(r.tier||'等待确认')}</span> <span class="tag">${esc(r.useCase||'等待放量')}</span> <span class="tag">3个月 ${esc(forecast.label||'观察')}</span> <span class="tag">弹性 ${esc(forecast.expected||'--')}</span> <span class="tag">中期分 ${esc(r.mediumScore??'--')}/10</span> ${lhbTag}</p><p class="muted">${esc(r.decision||r.categoryReason||'本地多因子观察')}</p><div class="agents">${agents.map(esc).join('<br>')}</div><p><b>龙虎榜证据</b><br>${esc(lhb.reason||'近5日未上龙虎榜')}｜净买入：${esc(lhb.netBuyText||'--')}｜日期：${esc(lhb.date||'--')}</p><p><b>未来1-3个月预测</b><br>结论：${esc(forecast.label||'观察')}｜预估弹性：${esc(forecast.expected||'--')}｜置信度：${esc(forecast.confidence||'--')}<br>依据：${esc(forecast.basis||'行业催化+趋势资金')}</p><p><b>未来1-3个月逻辑</b><br>逻辑：${esc(medium.logic||'等待更多数据')}<br>催化：${esc(medium.catalyst||'等待消息确认')}<br>风险：${esc(medium.risk||'控制仓位')}<br>周期：${esc(r.horizon||'1-3个月观察')}</p><p><b>全方面调研框架</b><br>日线：${esc(daily.daily||'等待更多数据')}<br>新闻：${esc(daily.news||'等待新闻确认')}<br>资金：${esc(daily.money||'等待成交确认')}<br>结论：${esc(r.decision||'等待价格、成交量和消息共振')}</p><p class="muted">${reasons.map(x=>'· '+esc(x)).join('<br>')}</p>`}
function renderSingleDetail(r){const agents=Array.isArray(r.agents)?r.agents:[];const reasons=Array.isArray(r.reasons)?r.reasons:[];const daily=r.dailyAnalysis||{},medium=r.mediumAnalysis||{},forecast=r.forecast||{},lhb=r.longhubang||{};const lhbTag=lhb.onList?`<span class="tag lhb-on">龙虎榜 ${esc(lhb.score??'--')}｜净买入 ${esc(lhb.netBuyText||'--')}</span>`:`<span class="tag">近5日未上龙虎榜</span>`;$('detail').style.display='block';$('detail').innerHTML=`<h3>单股研究：${esc(r.name)} <span class="code">${esc(r.code)}</span></h3><p><span class="tag">${esc(r.category||'综合观察')}</span> <span class="tag">${esc(r.tier||'等待确认')}</span> <span class="tag">${esc(r.useCase||'等待放量')}</span> <span class="tag">价格 ${esc(r.price??'--')}</span> <span class="tag">涨跌 ${esc(r.change??'--')}%</span> <span class="tag">评分 ${esc(r.score??'--')}</span> ${lhbTag}</p><p><b>多Agent结论</b></p><div class="agents">${agents.map(esc).join('<br>')}</div><p><b>龙虎榜证据</b><br>${esc(lhb.reason||'近5日未上龙虎榜')}｜净买入：${esc(lhb.netBuyText||'--')}｜日期：${esc(lhb.date||'--')}</p><p><b>3个月预测</b><br>结论：${esc(forecast.label||'观察')}｜预估弹性：${esc(forecast.expected||'--')}｜置信度：${esc(forecast.confidence||'--')}｜依据：${esc(forecast.basis||'行业催化+趋势资金')}</p><p><b>研究拆解</b><br>技术：${esc(daily.daily||'等待更多数据')}<br>消息：${esc(daily.news||'等待新闻确认')}<br>基本面/行业：${esc(medium.logic||'等待更多数据')}<br>资金：${esc(daily.money||'等待成交确认')}<br>风控：${esc(medium.risk||daily.risk||'控制仓位')}</p><p class="muted">${reasons.map(x=>'· '+esc(x)).join('<br>')}</p>`;$('detail').scrollIntoView({behavior:'smooth',block:'start'})}
async function researchSingle(){const code=($('singleInput').value||'').trim();if(!code){$('status').textContent='请输入股票代码，例如 600580。';return}setBusy(true);$('status').textContent='正在进行单股多因子研究...';try{const mode=$('singleMode').value||'local';const res=await fetch('/api/single_research?code='+encodeURIComponent(code)+'&mode='+encodeURIComponent(mode),{cache:'no-store'});const data=await res.json();if(!data.ok)throw new Error(data.message||'单股研究失败');$('status').textContent=(data.message||'单股研究完成')+(data.updatedAt?'｜更新时间 '+esc(data.updatedAt):'');renderSingleDetail(data.stock)}catch(e){$('status').textContent='单股研究失败：'+(e.message||e)}finally{setBusy(false)}}
async function loadData(mode){setBusy(true);const loadingText=mode==='gemini'?'正在请求 AI 选股，超时会自动切回评审选股...':'正在让TradingAgents、UZI评审、Kronos路径因子共同选股...';$('status').textContent=loadingText;$('rows').innerHTML='<tr><td colspan="9" class="empty">加载中...</td></tr>';try{const ctrl=new AbortController();const timer=setTimeout(()=>ctrl.abort(),mode==='gemini'?8500:9000);const res=await fetch('/api/screener?mode='+encodeURIComponent(mode),{cache:'no-store',signal:ctrl.signal});clearTimeout(timer);const data=await res.json();allRows=normalizeList(data);activeCategory='全部';const stamp=data.updatedAt?`｜更新时间 ${esc(data.updatedAt)}`:'';$('status').textContent=(data.aiMessage||data.message||(mode==='gemini'?'AI选股已完成。':'评审团选股已完成。'))+stamp;renderCategories();renderRows()}catch(e){$('status').textContent=mode==='gemini'?'AI 暂时无响应，已切换本地选股。':'本地选股请求失败，请刷新页面。';if(mode==='gemini')return loadData('review');$('rows').innerHTML=`<tr><td colspan="9" class="empty">${esc(e.message||e)}</td></tr>`}finally{setBusy(false)}}
async function checkAi(){setBusy(true);$('status').textContent='正在检测 AI...';try{const ctrl=new AbortController();const timer=setTimeout(()=>ctrl.abort(),7000);const data=await(await fetch('/api/gemini_status',{cache:'no-store',signal:ctrl.signal})).json();clearTimeout(timer);$('status').textContent=(data.ok?'AI 可用：':'AI 异常：')+(data.message||'无返回')}catch(e){$('status').textContent='AI 检测超时或网络不可达，本地选股仍可使用。'}finally{setBusy(false)}}
document.addEventListener('DOMContentLoaded',()=>{const code=new URLSearchParams(location.search).get('code');if(code){$('singleInput').value=code;researchSingle()}else{loadData('review')}});
</script>
</body>
</html>"""

LONGHUBANG_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>龙虎榜排名</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.55 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:#2b170f;background:radial-gradient(circle at 88% 0,#ffe2aa,transparent 34%),linear-gradient(135deg,#b4783b,#fff5e8 42%,#fffaf4)}button,a.btn{height:36px;border:1px solid #f0dfc7;border-radius:11px;background:#fff;color:#2b170f;text-decoration:none;padding:0 15px;font-weight:850;cursor:pointer;box-shadow:0 8px 18px rgba(151,79,18,.07);display:inline-flex;align-items:center}button.primary{background:linear-gradient(135deg,#ff3b24,#d71912);color:#fff;border-color:#e83324}.shell{width:min(1680px,calc(100vw - 28px));min-height:calc(100vh - 36px);margin:18px auto;padding:18px;border-radius:22px;background:rgba(255,255,255,.94);box-shadow:0 24px 70px rgba(151,79,18,.16);display:grid;grid-template-rows:auto auto 1fr;gap:14px}.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.title{font-size:24px;font-weight:950}.sub,.muted{color:#8a6b52}.actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.status{min-height:24px;color:#6b5543;font-weight:750}.panel{background:#fff;border:1px solid #f0dfc7;border-radius:16px;overflow:hidden;box-shadow:0 10px 30px rgba(151,79,18,.06)}.table-wrap{overflow:auto;max-height:calc(100vh - 160px)}table{width:100%;border-collapse:collapse;min-width:1320px}th,td{padding:12px 14px;border-bottom:1px solid #f3e7d8;text-align:left;vertical-align:top}th{position:sticky;top:0;background:#fff8ee;z-index:2;font-size:12px;color:#8a6b52}.rank{font-size:20px;font-weight:950;color:#d99a1b}.stock{font-weight:950}.code{display:block;color:#8a6b52;margin-top:2px}.tag{display:inline-flex;border-radius:999px;background:#fff1dc;padding:4px 8px;color:#6b5543;font-size:12px;font-weight:850;margin:0 4px 4px 0}.tag.hot{background:#fff0f1;color:#d71912}.tag.inst{background:#fff1dc;color:#a65b18}.money{font-weight:950}.up{color:#ec5f6b}.down{color:#35b978}.seat-list{display:grid;gap:7px}.seat{border:1px solid #f4e5cf;background:#fffaf4;border-radius:12px;padding:9px;min-width:330px}.seat b{display:block;margin-bottom:4px}.seat-meta{display:flex;gap:8px;flex-wrap:wrap;color:#6b5543;font-size:12px;font-weight:850}.reason{max-width:280px;line-height:1.7}.empty{text-align:center;padding:42px;color:#8a6b52;font-weight:850}@media(max-width:900px){.top{display:block}.actions{justify-content:flex-start;margin-top:12px}.shell{width:min(100%,calc(100vw - 14px));padding:12px}.table-wrap{max-height:none}th,td{padding:10px 8px;font-size:12px}}
</style>
</head>
<body>
<main class="shell">
  <div class="top">
    <div><div class="title">龙虎榜排名</div><div class="sub">股票上榜排名 + 营业部/游资席位 + 机构席位买卖明细</div></div>
    <div class="actions"><a class="btn" href="/app">返回监控</a><a class="btn" href="/research">选股研究</a><a class="btn" href="/rps">RPS主线</a><button class="primary" id="refreshBtn" onclick="loadLhb()">刷新</button></div>
  </div>
  <div class="status" id="status">正在加载龙虎榜...</div>
  <section class="panel">
    <div class="table-wrap"><table>
      <thead><tr><th>排名</th><th>股票</th><th>上榜原因</th><th>买入</th><th>卖出</th><th>净额</th><th>买方营业部/游资席位</th><th>卖方营业部/游资席位</th><th>机构席位</th></tr></thead>
      <tbody id="rows"><tr><td colspan="9" class="empty">加载中...</td></tr></tbody>
    </table></div>
  </section>
</main>
<script>
const $=id=>document.getElementById(id);
function esc(v){return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]))}
function clsMoney(v){const n=Number(v||0);return n>0?'up':n<0?'down':''}
function pct(v){const n=Number(v);return Number.isFinite(n)?(n>0?'+':'')+n.toFixed(2)+'%':'--'}
function seatBlock(seat,mode){const primary=mode==='sell'?`卖出 ${esc(seat.sellText||'--')}`:`买入 ${esc(seat.buyText||'--')}`;return `<div class="seat"><b>${esc(seat.name||seat.shortName||'未知席位')}</b><div>${esc(primary)}｜净额 <span class="${clsMoney(seat.net)}">${esc(seat.netText||'--')}</span></div><div class="seat-meta"><span>${esc(seat.type||'营业部/游资席位')}</span><span>买 ${esc(seat.buyText||'--')}</span><span>卖 ${esc(seat.sellText||'--')}</span></div></div>`}
function orgBlock(org){return `<div class="seat"><b>${esc(org.name||'机构专用')}</b><div>买入 ${esc(org.buyText||'--')}｜卖出 ${esc(org.sellText||'--')}｜净额 <span class="${clsMoney(org.net)}">${esc(org.netText||'--')}</span></div><div class="seat-meta"><span>买入次数 ${esc(org.buyTimes??0)}</span><span>卖出次数 ${esc(org.sellTimes??0)}</span></div></div>`}
function render(rows){if(!rows.length){$('rows').innerHTML='<tr><td colspan="9" class="empty">今天暂无龙虎榜数据，交易日收盘后再看。</td></tr>';return}$('rows').innerHTML=rows.map((r,i)=>`<tr><td class="rank">#${i+1}</td><td><span class="stock">${esc(r.name)}</span><span class="code">${esc(r.code)}｜${esc(r.date||'--')}</span><span class="tag hot">涨跌 ${pct(r.change)}</span><span class="tag">换手 ${pct(r.turnover)}</span></td><td class="reason">${esc(r.reason||'龙虎榜上榜')}</td><td class="money up">${esc(r.buyText||'--')}</td><td class="money down">${esc(r.sellText||'--')}</td><td class="money ${clsMoney(r.net)}">${esc(r.netText||'--')}</td><td><div class="seat-list">${(r.buyTop||[]).map(x=>seatBlock(x,'buy')).join('')||'<span class="muted">暂无营业部明细</span>'}</div></td><td><div class="seat-list">${(r.sellTop||[]).map(x=>seatBlock(x,'sell')).join('')||'<span class="muted">暂无营业部明细</span>'}</div></td><td><div class="seat-list">${(r.organizations||[]).map(orgBlock).join('')||'<span class="muted">暂无机构专用明细</span>'}</div></td></tr>`).join('')}
async function loadLhb(){const btn=$('refreshBtn');btn.disabled=true;$('status').textContent='正在读取东方财富龙虎榜席位明细...';try{const data=await(await fetch('/api/longhubang_rank?limit=50',{cache:'no-store'})).json();if(!data.ok)throw new Error(data.message||'龙虎榜数据暂不可用');$('status').textContent=`${data.message||'龙虎榜已更新'}｜更新时间 ${esc(data.updatedAt||'--')}｜说明：公开数据不披露散户个人，只披露机构、通道和营业部席位。`;render(data.rows||[])}catch(e){$('status').textContent='龙虎榜读取失败：'+(e.message||e);$('rows').innerHTML=`<tr><td colspan="9" class="empty">${esc(e.message||e)}</td></tr>`}finally{btn.disabled=false}}
document.addEventListener('DOMContentLoaded',loadLhb);
</script>
</body>
</html>"""

RPS_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>RPS主线雷达</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;font:14px/1.55 "Microsoft YaHei UI",Segoe UI,system-ui,sans-serif;color:#2b170f;background:radial-gradient(circle at 88% 0,#ffe2aa,transparent 34%),linear-gradient(135deg,#b4783b,#fff5e8 42%,#fffaf4)}button,a.btn{height:36px;border:1px solid #f0dfc7;border-radius:11px;background:#fff;color:#2b170f;text-decoration:none;padding:0 15px;font-weight:900;cursor:pointer;box-shadow:0 8px 18px rgba(151,79,18,.07);display:inline-flex;align-items:center}.primary{background:linear-gradient(135deg,#ff3b24,#d71912)!important;color:#fff!important;border-color:#e83324!important}.shell{width:min(1680px,calc(100vw - 28px));min-height:calc(100vh - 36px);margin:18px auto;padding:18px;border-radius:18px;background:rgba(255,255,255,.94);box-shadow:0 24px 70px rgba(151,79,18,.16);display:grid;grid-template-rows:auto auto auto auto auto 1fr;gap:14px}.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.title{font-size:24px;font-weight:950}.sub,.muted{color:#8a6b52}.actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.card,.panel{background:#fff;border:1px solid #f0dfc7;border-radius:16px;box-shadow:0 10px 30px rgba(151,79,18,.06);overflow:hidden}.card{padding:14px}.card span{display:block;color:#8a6b52;font-size:12px;font-weight:900}.card b{display:block;font-size:24px;margin-top:3px}.up{color:#ec5f6b}.down{color:#35b978}.flow-grid{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:12px}.flow-chart{width:100%;height:310px;display:block;background:linear-gradient(180deg,#fff,#fbfdfd)}.flow-bars{display:grid;gap:8px}.flow-row{display:grid;grid-template-columns:104px 1fr 74px;gap:8px;align-items:center}.flow-row b{font-size:12px}.flow-track{height:9px;background:#f7e7cf;border-radius:99px;overflow:hidden}.flow-track i{display:block;height:100%;border-radius:99px}.matrix-wrap{overflow:auto;max-height:420px}.matrix{border-collapse:separate;border-spacing:0;width:max-content;min-width:100%}.matrix th,.matrix td{padding:7px 9px;text-align:center;border:1px solid #f0dfc7;font-size:12px}.matrix th{position:sticky;top:0;background:#fff8ee;z-index:1}.matrix .rank-col{position:sticky;left:0;background:#fff8ee;font-weight:950;z-index:2}.sector-cell{min-width:88px;border-radius:6px;font-weight:900;color:#24303a}.grid{display:grid;grid-template-columns:430px 1fr;gap:14px;min-height:0}.head{height:46px;border-bottom:1px solid #f0dfc7;padding:0 16px;display:flex;align-items:center;justify-content:space-between;font-weight:950}.body{padding:12px;overflow:auto}.theme{display:grid;grid-template-columns:1fr 72px;gap:8px;align-items:center;border-bottom:1px solid #f0f2f4;padding:10px 2px}.theme:last-child{border-bottom:0}.bar{height:8px;border-radius:99px;background:#f7e7cf;overflow:hidden;margin-top:7px}.bar i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#35b978,#f0bd3c,#ec5f6b)}.path{border-bottom:1px solid #f0f2f4;padding:12px 2px}.path:last-child{border-bottom:0}.path-top{display:flex;justify-content:space-between;gap:10px;align-items:center}.path b{font-size:14px}.stage{border-radius:999px;background:#fff1dc;color:#a65b18;padding:3px 8px;font-size:12px;font-weight:950;white-space:nowrap}.path-meta{margin-top:6px;color:#6b5543;line-height:1.65}.path-score{height:7px;background:#f7e7cf;border-radius:99px;overflow:hidden;margin-top:8px}.path-score i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#2563eb,#35b978,#ec5f6b)}table{width:100%;border-collapse:collapse}th,td{padding:12px 14px;border-bottom:1px solid #f3e7d8;text-align:left;vertical-align:top}th{font-size:12px;color:#8a6b52}.stock{font-weight:950}.code{color:#8a6b52;margin-left:7px}.tag{display:inline-flex;border-radius:999px;background:#fff1dc;padding:4px 8px;color:#6b5543;font-size:12px;font-weight:850}.rps{font-size:20px;font-weight:950;color:#d99a1b}.agents{line-height:1.75;color:#303942}.empty{text-align:center;padding:36px;color:#8a6b52;font-weight:850}.note{background:#fff8ee;border:1px solid #f4e5cf;border-radius:14px;padding:12px;color:#6b5543;line-height:1.75}@media(max-width:1000px){.cards{grid-template-columns:repeat(2,1fr)}.grid,.flow-grid{grid-template-columns:1fr}.top{display:block}.actions{justify-content:flex-start;margin-top:10px}th,td{padding:10px 8px;font-size:12px}}
</style>
</head>
<body>
<main class="shell">
  <div class="top">
    <div><div class="title">RPS主线雷达</div><div class="sub">大盘资金动向、板块热度、相对强度排名、龙虎榜观察位</div></div>
    <div class="actions"><a class="btn" href="/app">返回监控</a><a class="btn" href="/research">选股研究</a><button class="primary" onclick="loadRps()">刷新主线</button></div>
  </div>
  <div class="cards">
    <div class="card"><span>扫描样本</span><b id="sample">--</b></div>
    <div class="card"><span>上涨家数</span><b id="up" class="up">--</b></div>
    <div class="card"><span>下跌家数</span><b id="down" class="down">--</b></div>
    <div class="card"><span>最强主线</span><b id="leader">--</b></div>
    <div class="card"><span>主线热度</span><b id="heat">--</b></div>
  </div>
  <section class="panel">
    <div class="head">大盘/板块资金走势 <span id="flowMode" class="muted">本地估算</span></div>
    <div class="body flow-grid">
      <div id="flowChart"><div class="empty">正在生成资金曲线...</div></div>
      <div>
        <div id="flowSummary" class="note">等待资金动向...</div>
        <div id="flowBars" class="flow-bars" style="margin-top:10px"></div>
      </div>
    </div>
  </section>
  <section class="panel">
    <div class="head">强势主线矩阵 <span id="matrixMode" class="muted">最近交易日排名</span></div>
    <div class="body matrix-wrap" id="rankMatrix"><div class="empty">正在生成RPS矩阵...</div></div>
  </section>
  <section class="grid">
    <aside class="panel">
      <div class="head">板块资金动向 <span id="updated" class="muted">--</span></div>
      <div class="body" id="themes"><div class="empty">正在读取板块热度...</div></div>
      <div class="head">今年主线轨迹 <span class="muted">路径推断</span></div>
      <div class="body" id="paths"><div class="empty">正在生成年度主线...</div></div>
      <div class="body"><div class="note"><b>龙虎榜观察</b><br>当前为席位观察位：优先标记RPS高、成交活跃、主线热度高的股票。接入东方财富/同花顺/L2后，可升级为真实龙虎榜净买入、机构席位、游资席位跟踪。</div></div>
    </aside>
    <section class="panel">
      <div class="head">RPS强势候选 <span class="muted">只负责找主线，不直接等于买点</span></div>
      <div style="overflow:auto;max-height:68vh"><table><thead><tr><th>股票</th><th>主线</th><th>RPS</th><th>价格</th><th>涨跌</th><th>成交</th><th>资金/多Agent结论</th></tr></thead><tbody id="rows"><tr><td colspan="7" class="empty">加载中...</td></tr></tbody></table></div>
    </section>
  </section>
</main>
<script>
const $=id=>document.getElementById(id);
function esc(v){return String(v??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]))}
function cls(v){const n=Number(v);return n>0?'up':n<0?'down':''}
async function loadRps(){
  $('rows').innerHTML='<tr><td colspan="7" class="empty">正在扫描主线...</td></tr>';
  try{
    const data=await (await fetch('/api/rps?limit=10',{cache:'no-store'})).json();
    if(!data.ok)throw new Error(data.message||'RPS读取失败');
    const m=data.market||{};
    $('sample').textContent=m.sample??'--';$('up').textContent=m.up??'--';$('down').textContent=m.down??'--';$('leader').textContent=m.leader||'--';$('heat').textContent=(m.leaderHeat??'--')+'分';$('updated').textContent=data.updatedAt||'';
    renderFundFlow(data.fundFlow||{});renderRankMatrix(data.rankMatrix||{});renderThemes(data.themes||[]);renderPaths(data.paths||[]);renderRows(data.rows||[]);
  }catch(e){$('rows').innerHTML=`<tr><td colspan="7" class="empty">${esc(e.message||e)}</td></tr>`}
}
function renderThemes(themes){
  if(!themes.length){$('themes').innerHTML='<div class="empty">暂无板块热度。</div>';return}
  $('themes').innerHTML=themes.map(t=>`<div class="theme"><div><b>${esc(t.name)}</b><div class="muted">上涨 ${esc(t.positive)}/${esc(t.count)}｜均涨 ${esc(t.avgChange)}%｜成交 ${esc(t.amountText||'--')}</div><div class="muted">${esc(t.externalReason||'实时样本强弱')}</div><div class="bar"><i style="width:${Math.max(3,Math.min(100,Number(t.heat)||0))}%"></i></div></div><div class="rps">${esc(t.heat)}</div></div>`).join('');
}
function renderFundFlow(flow){
  const series=flow.series||[],bars=flow.bars||[];
  $('flowMode').textContent=flow.mode||'本地估算';
  $('flowSummary').textContent=flow.summary||'暂无资金走势。';
  if(!series.length){$('flowChart').innerHTML='<div class="empty">暂无资金曲线。</div>';$('flowBars').innerHTML='';return}
  const values=series.flatMap(s=>(s.points||[]).map(p=>Number(p.value)||0));
  const min=Math.min(...values,0),max=Math.max(...values,0),span=Math.max(max-min,1);
  const W=980,H=310,L=58,R=34,T=22,B=42;
  const x=(i,n)=>L+(i/Math.max(1,n-1))*(W-L-R);
  const y=v=>T+(max-v)/span*(H-T-B);
  const grid=[0,.25,.5,.75,1].map(t=>{const yy=T+t*(H-T-B);return `<line x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}" stroke="#edf1f3"/><text x="10" y="${yy+4}" fill="#94a3af" font-size="11">${fmtFlow(max-(span*t))}</text>`}).join('');
  const zero=`<line x1="${L}" y1="${y(0)}" x2="${W-R}" y2="${y(0)}" stroke="#9aa5ae" stroke-dasharray="4 5"/>`;
  const lines=series.map(s=>{const pts=(s.points||[]).map((p,i)=>`${x(i,s.points.length).toFixed(1)},${y(Number(p.value)||0).toFixed(1)}`).join(' ');const last=s.points?.[s.points.length-1]||{};return `<polyline points="${pts}" fill="none" stroke="${esc(s.color||'#2563eb')}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><text x="${W-R-4}" y="${y(Number(last.value)||0)-4}" text-anchor="end" fill="${esc(s.color||'#2563eb')}" font-size="11" font-weight="900">${esc(s.name)} ${esc(s.netText)}</text>`}).join('');
  const labels=(series[0]?.points||[]).map((p,i)=>`<text x="${x(i,series[0].points.length)}" y="${H-14}" text-anchor="middle" fill="#94a3af" font-size="10">${esc(p.time)}</text>`).join('');
  $('flowChart').innerHTML=`<svg class="flow-chart" viewBox="0 0 ${W} ${H}">${grid}${zero}${labels}${lines}</svg>`;
  const maxAbs=Math.max(...bars.map(b=>Math.abs(Number(b.netWan)||0)),1);
  $('flowBars').innerHTML=bars.map(b=>{const pos=Number(b.netWan)>=0;return `<div class="flow-row"><b>${esc(b.name)}</b><div class="flow-track"><i style="width:${Math.max(3,Math.min(100,Math.abs(Number(b.netWan)||0)/maxAbs*100))}%;background:${pos?'#ec5f6b':'#35b978'}"></i></div><span class="${pos?'up':'down'}">${esc(b.netText)}</span></div>`}).join('');
}
function fmtFlow(v){const n=Number(v)||0;if(Math.abs(n)>=10000)return (n/10000).toFixed(1)+'亿';return n.toFixed(0)+'万'}
function renderRankMatrix(matrix){
  const cols=matrix.columns||[],rankCount=Number(matrix.rankCount||0);
  $('matrixMode').textContent=matrix.mode||'最近交易日排名';
  if(!cols.length||!rankCount){$('rankMatrix').innerHTML='<div class="empty">暂无RPS矩阵。</div>';return}
  const colors=['#dff4ff','#e8f5e9','#fff3cd','#fde2e2','#ede7f6','#e0f2f1','#ffe8cc','#f3e8ff'];
  let html='<table class="matrix"><thead><tr><th class="rank-col">排名</th>'+cols.map(c=>`<th>${esc(c.date)}</th>`).join('')+'</tr></thead><tbody>';
  for(let r=0;r<rankCount;r++){
    html+=`<tr><td class="rank-col">${r+1}</td>`;
    cols.forEach(c=>{const name=(c.items||[])[r]||'--';const color=colors[Math.abs(hashText(name))%colors.length];html+=`<td><div class="sector-cell" style="background:${color}">${esc(name)}</div></td>`});
    html+='</tr>';
  }
  html+='</tbody></table>';
  $('rankMatrix').innerHTML=html;
}
function hashText(s){let h=0;for(let i=0;i<String(s).length;i++)h=(h*31+String(s).charCodeAt(i))|0;return h}
function renderPaths(paths){
  if(!paths.length){$('paths').innerHTML='<div class="empty">暂无年度主线。</div>';return}
  $('paths').innerHTML=paths.map(p=>`<div class="path"><div class="path-top"><b>${esc(p.name)}</b><span class="stage">${esc(p.stage)} ${esc(p.score)}分</span></div><div class="path-meta">活跃阶段：${esc(p.months)}｜当前热度 ${esc(p.heat)}｜成交 ${esc(p.amountText||'--')}</div><div class="path-meta">驱动：${esc(p.drivers)}</div><div class="path-meta">验证：${esc(p.watch)}</div><div class="path-score"><i style="width:${Math.max(3,Math.min(100,Number(p.score)||0))}%"></i></div></div>`).join('');
}
function renderRows(rows){
  if(!rows.length){$('rows').innerHTML='<tr><td colspan="7" class="empty">暂无强势候选。</td></tr>';return}
  $('rows').innerHTML=rows.map(r=>`<tr><td><span class="stock">${esc(r.name)}</span><span class="code">${esc(r.code)}</span></td><td><span class="tag">${esc(r.category)}</span><div class="muted">${esc(r.signal)}</div></td><td><span class="rps">${esc(r.rps)}</span><div class="muted">热度${esc(r.themeHeat)}</div></td><td>${esc(r.price)}</td><td class="${cls(r.change)}">${esc(r.change)}%</td><td>${esc(r.amountText)}</td><td><div class="agents">${(r.agents||[]).map(esc).join('<br>')}</div><div class="muted">${esc(r.reason||'')}</div></td></tr>`).join('');
}
document.addEventListener('DOMContentLoaded',loadRps);
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



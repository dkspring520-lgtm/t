#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Notification router for the dabao stock monitor."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    import requests
except Exception:
    requests = None

BASE_DIR = Path(__file__).resolve().parent
RESULT_PATH = BASE_DIR / "monitor_result.json"
DESKTOP_ENV = Path.home() / "Desktop" / "1.env"

WXPUSHER_URL = "https://wxpusher.zjiecode.com/api/send/message"
PUSHPLUS_URL = "https://www.pushplus.plus/send"


def load_desktop_env() -> None:
    if not DESKTOP_ENV.exists():
        return
    try:
        lines = DESKTOP_ENV.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


load_desktop_env()


@dataclass(frozen=True)
class NotifyConfig:
    title: str = os.environ.get("MONITOR_NOTIFY_TITLE", "紫金矿业 601899 监控")
    openclaw_weixin_target: str = os.environ.get("OPENCLAW_WEIXIN_TARGET", "")
    openclaw_weixin_account: str = os.environ.get("OPENCLAW_WEIXIN_ACCOUNT", "")
    hermes_target: str = os.environ.get("HERMES_SEND_TARGET", "") if os.environ.get("ENABLE_HERMES_SEND") == "1" else ""
    clawbot_webhook_url: str = os.environ.get("CLAWBOT_WEBHOOK_URL", "")
    wxpusher_app_token: str = os.environ.get("WXPUSHER_APP_TOKEN", "")
    wxpusher_uids: str = os.environ.get("WXPUSHER_UIDS") or os.environ.get("WXPUSHER_UID") or ""
    pushplus_token: str = os.environ.get("PUSHPLUS_TOKEN", "")
    local_notify: bool = os.environ.get("LOCAL_NOTIFY", "1").lower() not in {"0", "false", "no"}


@dataclass(frozen=True)
class SendResult:
    ok: bool
    channel: str
    info: Any

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "channel": self.channel, "info": self.info}


def load_result(symbol: str) -> Dict[str, Any]:
    try:
        from monitor import run

        payload = run(symbol)
        try:
            with RESULT_PATH.open("w", encoding="utf-8") as f:
                json.dump(payload.get("json", payload), f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return payload
    except Exception as exc:
        if RESULT_PATH.exists():
            try:
                with RESULT_PATH.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"ok": False, "error": str(exc), "symbol": symbol}


def build_message(payload: Dict[str, Any]) -> str:
    if payload.get("markdown"):
        return str(payload["markdown"])

    quote = payload.get("quote") or payload.get("json", {}).get("quote") or {}
    symbol = payload.get("symbol") or "sh601899"
    name = payload.get("name") or quote.get("name") or "紫金矿业"
    price = payload.get("price", quote.get("price"))
    change_pct = payload.get("change_pct", quote.get("change"))
    avg = payload.get("avg", quote.get("avg"))
    time_value = payload.get("time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [f"{name} {symbol}", f"时间: {time_value}"]
    if price is not None:
        lines.append(f"现价: {price}")
    if change_pct is not None:
        lines.append(f"涨跌: {change_pct}%")
    if avg is not None:
        lines.append(f"均价: {float(avg):.2f}")

    signals = payload.get("signals") or payload.get("json", {}).get("signals") or []
    if signals:
        lines.extend(["", "信号:"])
        for signal in signals[:6]:
            lines.append(_format_signal(signal))
    return "\n".join(lines)


def _format_signal(signal: Any) -> str:
    if isinstance(signal, dict):
        sig_type = signal.get("type", "")
        level = signal.get("level") or signal.get("severity", "")
        text = signal.get("text", "")
    else:
        sig_type = getattr(getattr(signal, "type", None), "value", "")
        level = getattr(getattr(signal, "severity", None), "value", "")
        text = getattr(signal, "text", "")
    return f"- [{sig_type}/{level}] {text}"


def _json_or_text(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text[:500]


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def _require_requests(channel: str) -> Optional[SendResult]:
    if requests is None:
        return SendResult(False, channel, "requests not installed")
    return None


def send_hermes(config: NotifyConfig, text: str) -> SendResult:
    cmd = [
        "hermes",
        "send",
        "--to",
        config.hermes_target,
        "--subject",
        config.title,
        text,
    ]
    last_output = ""
    for attempt in range(3):
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            output = (proc.stdout or proc.stderr or "").strip()
            last_output = output or f"exit {proc.returncode}"
            if proc.returncode == 0:
                return SendResult(True, "hermes", last_output)

            cooldown = _extract_cooldown_seconds(last_output)
            if attempt < 2 and cooldown is not None:
                time.sleep(min(cooldown + 2.0, 65.0))
                continue
            return SendResult(False, "hermes", last_output)
        except Exception as exc:
            last_output = str(exc)
            if attempt == 2:
                return SendResult(False, "hermes", last_output)
    return SendResult(False, "hermes", last_output or "send failed")


def send_clawbot(config: NotifyConfig, text: str) -> SendResult:
    if requests is None:
        return SendResult(False, "clawbot", "requests not installed")
    payloads = (
        {"text": text, "content": text, "msg": text},
        {"content": text},
        {"msg": text},
    )
    last_info: Any = ""
    for payload in payloads:
        try:
            resp = requests.post(config.clawbot_webhook_url, json=payload, timeout=15)
            try:
                data = resp.json()
            except Exception:
                data = resp.text[:500]
            last_info = data
            if 200 <= resp.status_code < 300:
                return SendResult(True, "clawbot", data)
        except Exception as exc:
            last_info = str(exc)
    return SendResult(False, "clawbot", last_info)


def _openclaw_target(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("weixin:"):
        value = value.split(":", 1)[1]
    return value


def send_openclaw_weixin(config: NotifyConfig, text: str) -> SendResult:
    target = _openclaw_target(config.openclaw_weixin_target or os.environ.get("HERMES_SEND_TARGET", ""))
    if not target:
        return SendResult(False, "openclaw-weixin", "未配置 OpenClaw 微信目标。")

    cmd = [
        "openclaw.cmd",
        "message",
        "send",
        "--channel",
        "openclaw-weixin",
        "--target",
        target,
        "--message",
        text,
    ]
    account = (config.openclaw_weixin_account or "").strip()
    if account:
        cmd.extend(["--account", account])
    try:
        env = os.environ.copy()
        env["NO_COLOR"] = "1"
        proc = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=45,
        )
        output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip())
        return SendResult(proc.returncode == 0, "openclaw-weixin", output or f"exit {proc.returncode}")
    except Exception as exc:
        return SendResult(False, "openclaw-weixin", str(exc))


def _extract_cooldown_seconds(text: str) -> Optional[float]:
    if "rate limited" not in text.lower() and "cooldown" not in text.lower():
        return None
    match = re.search(r"cooldown active for\s+([0-9.]+)s", text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return 30.0
    return 30.0


def send_wxpusher(config: NotifyConfig, text: str) -> SendResult:
    missing = _require_requests("wxpusher")
    if missing:
        return missing

    payload: Dict[str, Any] = {
        "appToken": config.wxpusher_app_token,
        "content": f"## {config.title}\n\n{text}",
        "summary": config.title[:100],
        "contentType": 3,
    }
    uids = _split_csv(config.wxpusher_uids)
    if uids:
        payload["uids"] = uids

    try:
        resp = requests.post(WXPUSHER_URL, json=payload, timeout=10)
        data = _json_or_text(resp)
        ok = resp.status_code == 200 and isinstance(data, dict) and data.get("code") == 1000
        return SendResult(ok, "wxpusher", data)
    except Exception as exc:
        return SendResult(False, "wxpusher", str(exc))


def send_pushplus(config: NotifyConfig, text: str) -> SendResult:
    missing = _require_requests("pushplus")
    if missing:
        return missing

    payload = {
        "token": config.pushplus_token,
        "title": config.title,
        "content": text,
        "template": "markdown",
    }
    try:
        resp = requests.post(PUSHPLUS_URL, json=payload, timeout=10)
        data = _json_or_text(resp)
        ok = resp.status_code == 200 and isinstance(data, dict) and data.get("code") == 200
        return SendResult(ok, "pushplus", data)
    except Exception as exc:
        return SendResult(False, "pushplus", str(exc))


def send_local(config: NotifyConfig, text: str) -> SendResult:
    ps_script = r"""
Add-Type -AssemblyName System.Windows.Forms
$title = [Console]::In.ReadLine()
$body = [Console]::In.ReadToEnd()
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.BalloonTipTitle = $title
$icon.BalloonTipText = $body.Substring(0, [Math]::Min(240, $body.Length))
$icon.Visible = $true
$icon.ShowBalloonTip(8000)
Start-Sleep -Seconds 9
$icon.Dispose()
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            input=f"{config.title}\n{text}",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        output = (proc.stderr or proc.stdout or "").strip()
        return SendResult(proc.returncode == 0, "local", output or "shown locally")
    except Exception as exc:
        return SendResult(False, "local", str(exc))


def choose_channel(config: NotifyConfig) -> tuple[str, Callable[[NotifyConfig, str], SendResult]]:
    if config.openclaw_weixin_target or os.environ.get("HERMES_SEND_TARGET", "").startswith("weixin:"):
        return "openclaw-weixin", send_openclaw_weixin
    if config.clawbot_webhook_url:
        return "clawbot", send_clawbot
    if config.hermes_target and os.environ.get("ENABLE_HERMES_SEND") == "1":
        return "hermes", send_hermes
    if config.wxpusher_app_token and os.environ.get("ENABLE_THIRD_PARTY_PUSH") == "1":
        return "wxpusher", send_wxpusher
    if config.pushplus_token and os.environ.get("ENABLE_THIRD_PARTY_PUSH") == "1":
        return "pushplus", send_pushplus
    if config.local_notify:
        return "local", send_local
    return "none", lambda _config, _text: SendResult(
        False,
        "none",
        "未配置通知通道。要直推微信，请先完成 OpenClaw 微信扫码登录。",
    )


def send_notification(config: NotifyConfig, text: str) -> SendResult:
    _, sender = choose_channel(config)
    return sender(config, text)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        config = NotifyConfig(title="通知链路自检")
        if not config.openclaw_weixin_target and not os.environ.get("HERMES_SEND_TARGET", "").startswith("weixin:") and not config.clawbot_webhook_url and not config.hermes_target and not config.wxpusher_app_token and not config.pushplus_token:
            result = SendResult(False, "none", "未配置 OpenClaw 微信目标；已跳过 Hermes。")
        else:
            result = send_notification(config, "本地 no-agent 通知测试。")
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0 if result.ok else 1

    symbol = sys.argv[1] if len(sys.argv) > 1 else "sh601899"
    payload = load_result(symbol)
    if payload.get("ok") is False:
        print(json.dumps({"ok": False, "channel": "monitor", "info": payload}, ensure_ascii=False, indent=2))
        return 1

    result = send_notification(NotifyConfig(), build_message(payload))
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

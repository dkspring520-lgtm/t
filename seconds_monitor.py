#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Second-level local monitor for intraday T signals."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from monitor_config import load_watchlist, parse_watchlist_text
from notify import NotifyConfig, send_notification
from stock_t_signal import Signal, StockConfig, analyze

BASE_DIR = Path(__file__).resolve().parent
PID_FILE = BASE_DIR / "seconds_monitor.pid"
STATE_FILE = BASE_DIR / "seconds_monitor_state.json"
LOG_FILE = BASE_DIR / "seconds_monitor.log"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--cooldown", type=float, default=180.0)
    parser.add_argument("--daily-limit", type=int, default=2)
    parser.add_argument("--daily-total-limit", type=int, default=1)
    parser.add_argument("--stocks", default="", help="逗号分隔股票，如 sh601899,sz000063")
    parser.add_argument("--sound-mode", choices=["soft", "strong", "none"], default="soft")
    parser.add_argument("--no-voice", action="store_true")
    parser.add_argument("--no-sound", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv[1:])

    write_pid()
    state = load_state()
    try:
        while True:
            stocks = parse_watchlist_text(args.stocks) if args.stocks else load_watchlist()
            sound_mode = "none" if args.no_sound else args.sound_mode
            sent = run_once(state, args.cooldown, args.daily_limit, args.daily_total_limit, stocks, sound_mode, not args.no_voice)
            save_state(state)
            if args.once:
                msg = f"秒级监控检查完成：{len(stocks)}只股票"
                msg += f"，推送{sent}条" if sent else "，暂无高质量信号"
                print(msg)
                return 0
            time.sleep(max(args.interval, 2.0))
    finally:
        if not args.once:
            cleanup_pid()


def run_once(state: dict, cooldown: float, daily_limit: int, daily_total_limit: int, stocks: list[StockConfig], sound_mode: str, voice: bool) -> int:
    sent = 0
    today = datetime.now().strftime("%Y%m%d")
    for stock in stocks:
        try:
            signals = analyze(stock)
        except Exception as exc:
            log(f"{stock.name} 检查失败：{exc}")
            continue
        for signal in signals:
            if should_send(signal, state, cooldown, daily_limit, daily_total_limit, today):
                text = build_message(signal)
                alert_sound(sound_mode)
                speak_alert(signal, voice)
                result = send_notification(NotifyConfig(title="强提醒：做T买卖点"), text)
                key = signal_key(signal)
                daily_key = daily_count_key(signal.code, today)
                total_key = daily_total_count_key(today)
                state[key] = {"ts": time.time(), "text": text, "ok": result.ok}
                state[daily_key] = int(state.get(daily_key) or 0) + 1
                state[total_key] = int(state.get(total_key) or 0) + 1
                log(("已推送：" if result.ok else "推送失败：") + text.replace("\n", " / "))
                sent += 1
    return sent


def should_send(signal: Signal, state: dict, cooldown: float, daily_limit: int, daily_total_limit: int, today: str) -> bool:
    total_count = int(state.get(daily_total_count_key(today)) or 0)
    if total_count >= max(daily_total_limit, 0):
        return False
    daily_count = int(state.get(daily_count_key(signal.code, today)) or 0)
    if daily_count >= max(daily_limit, 0):
        return False
    item = state.get(signal_key(signal)) or {}
    last_ts = float(item.get("ts") or 0)
    return time.time() - last_ts >= cooldown


def signal_key(signal: Signal) -> str:
    return f"{signal.code}:{signal.action}"


def daily_count_key(code: str, today: str) -> str:
    return f"daily:{today}:{code}"


def daily_total_count_key(today: str) -> str:
    return f"daily:{today}:all"


def build_message(signal: Signal) -> str:
    action = "强制买入" if "低吸" in signal.action else "强制卖出" if "高抛" in signal.action else signal.action
    return (
        f"【强提醒】{signal.name} {signal.code}\n"
        f"{signal.time} 现价 {signal.price:.2f}\n"
        f"动作：{action}\n"
        f"{signal.reason}"
    )


def alert_sound(mode: str) -> None:
    if mode == "none" or os.name != "nt":
        return
    try:
        import winsound

        if mode == "strong":
            for freq in (880, 1180, 880):
                winsound.Beep(freq, 180)
                time.sleep(0.08)
        else:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def speak_alert(signal: Signal, enabled: bool) -> None:
    if not enabled or os.name != "nt":
        return
    action = "强制买入" if "低吸" in signal.action else "强制卖出" if "高抛" in signal.action else signal.action
    text = f"{signal.name}，现价{signal.price:.2f}，{action}。"
    try:
        import win32com.client  # type: ignore

        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        speaker.Rate = 1
        speaker.Volume = 90
        speaker.Speak(text, 1)
    except Exception:
        try:
            script = (
                "Add-Type -AssemblyName System.Speech;"
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                "$s.Rate=1;$s.Volume=90;"
                "$s.Speak([Console]::In.ReadToEnd())|Out-Null"
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).communicate(text, timeout=6)
        except Exception:
            pass


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def write_pid() -> None:
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def cleanup_pid() -> None:
    try:
        PID_FILE.unlink()
    except Exception:
        pass


def log(text: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

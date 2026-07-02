#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Control helper for seconds_monitor.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PID_FILE = BASE_DIR / "seconds_monitor.pid"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "start":
        return start()
    if cmd == "stop":
        return stop()
    if cmd == "restart":
        stop()
        return start()
    if cmd == "status":
        return status()
    print("未知命令")
    return 1


def start() -> int:
    if is_running():
        print("秒级监控正在运行")
        return 0
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else sys.executable)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            exe,
            "-B",
            str(BASE_DIR / "seconds_monitor.py"),
            "--interval",
            "10",
            "--cooldown",
            "180",
            "--sound-mode",
            "soft",
        ],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    print("秒级监控已启动：10秒轮询，自动读取监控股票")
    return 0


def stop() -> int:
    pid = read_pid()
    if not pid:
        print("秒级监控未运行")
        return 0
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        PID_FILE.unlink()
    except Exception:
        pass
    print("秒级监控已停止")
    return 0


def status() -> int:
    print("秒级监控正在运行" if is_running() else "秒级监控未运行")
    return 0


def is_running() -> bool:
    pid = read_pid()
    if pid and pid_exists(pid):
        return True
    existing = find_monitor_pids()
    if existing:
        try:
            PID_FILE.write_text(str(existing[0]), encoding="utf-8")
        except Exception:
            pass
        return True
    return False


def pid_exists(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def find_monitor_pids() -> list[int]:
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*seconds_monitor.py*' -and $_.Name -like 'python*' } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        return [int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()]
    except Exception:
        return []


def read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

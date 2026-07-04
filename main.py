# -*- coding: utf:utf-8 -*-
import os
import sys
import time
import schedule
import requests
import logging
from datetime import datetime, timedelta
from env_bootstrap import apply_local_env

apply_local_env()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetcher.tonghuashun import tonghuashun_fetcher
from fetcher.global_markets import global_fetcher
from fetcher.ai_analyzer import analyze_zijin_news, pick_stocks, build_push_content

SENDKEY = "SCT364467Tc7fPZhyAlZSq9IJlNxFRQePA"
PUSH_URL = "https://sctapi.ftqq.com/{}.send".format(SENDKEY)
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "seen.txt")
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)

# --- Logging ---
log = logging.getLogger("zijin")
log.setLevel(logging.DEBUG)
if not log.handlers:
    fh = logging.FileHandler(os.path.join(LOG_DIR, "stock-news.log"), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(fh)
    efh = logging.FileHandler(os.path.join(LOG_DIR, "stock-news-error.log"), encoding="utf-8")
    efh.setLevel(logging.WARNING)
    efh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(efh)

MIN_PUSH_INTERVAL = 30 * 60
QUOTE_ALERT_THRESHOLD = 3.0
last_push_time = 0

seen = set()
pending = []
raw_headlines = []

try:
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        seen = set(line.strip() for line in f if line.strip())
except Exception:
    pass


def save_seen(s):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            for item in list(s)[-300:]:
                f.write(item + "\n")
    except Exception:
        pass


def get_beijing_time():
    """Return current Beijing time."""
    return datetime.utcnow() + timedelta(hours=8)


def push(title, desp):
    global last_push_time
    try:
        log.info("PUSH -> %s", title)
        r = requests.post(PUSH_URL, data={"title": title, "desp": desp}, timeout=20)
        log.info("push status: %s body: %s", r.status_code, r.text[:300])
        last_push_time = time.time()
        return r.status_code == 200
    except Exception as e:
        log.warning("push err: %s", e)
        return False


def scan():
    global pending, raw_headlines
    log.info("  scanning...")
    msgs = []

    try:
        items = tonghuashun_fetcher.fetch_hot_stocks(12)
        log.info("tonghuashun: %d", len(items))
        msgs.extend(items)
    except Exception as e:
        log.warning("tonghuashun err: %s", e)

    try:
        items = global_fetcher.fetch_all()
        log.info("global: %d", len(items))
        msgs.extend(items)
    except Exception as e:
        log.warning("global err: %s", e)

    new = []
    new_headlines = []
    quote_alerts = []

    for m in msgs:
        mid = m.get("id", "")
        t = m.get("title", "")
        st = (m.get("source_type") or "").strip()
        if not t:
            continue

        if st == "quote":
            chg = m.get("change_pct", 0)
            if abs(chg) >= QUOTE_ALERT_THRESHOLD:
                quote_alerts.append(t)
            continue

        if st == "quote_alert":
            quote_alerts.append(t)
            continue

        key = mid or t[:40]
        if key in seen:
            continue
        seen.add(key)

        title_en = m.get("title_en", "")
        if title_en:
            new_headlines.append(title_en)
        else:
            new_headlines.append(t)

        new.append(m)

    pending = new
    raw_headlines = new_headlines
    log.info("  new: %d  quote_alerts: %d  headlines: %d",
             len(new), len(quote_alerts), len(new_headlines))
    save_seen(seen)
    return quote_alerts


def noon_stock_job():
    """At noon 12:00, collect news + call AI for stock picks."""
    log.info("=" * 50)
    log.info("NOON stock pick job")
    log.info("=" * 50)

    # Scan news first
    quote_alerts = scan()

    # AI news analysis (always)
    log.info("  calling AI news analysis...")
    news_analysis = analyze_zijin_news(raw_headlines)
    if news_analysis:
        impactful = news_analysis.get("impactful_news", [])
        log.info("  AI news: %d impactful items, signal=%s",
                 len(impactful), news_analysis.get("overall_signal", "?"))
    else:
        log.info("  AI news: no response")

    # AI stock picks (noon only)
    log.info("  calling AI stock picks (noon)...")
    stock_picks = pick_stocks()
    if stock_picks:
        picks = stock_picks.get("picks", [])
        log.info("  AI picks: %d stocks", len(picks))
    else:
        log.info("  AI picks: no response")

    # Build and send
    header, body = build_push_content(
        news_analysis=news_analysis,
        stock_picks=stock_picks,
        quote_alerts=quote_alerts if quote_alerts else None,
    )

    if header and body:
        push(header, body)
    else:
        log.info("noon push: nothing to send")

    log.info("=" * 50)


def job():
    """30-min job: fetch news, AI analysis, always push."""
    global last_push_time
    now = get_beijing_time()
    h = now.hour
    mn = now.minute
    is_noon = (h == 12 and mn < 10)

    # Log with Beijing time
    log.info("=" * 50)
    log.info("BJT %s job", now.strftime("%H:%M:%S"))
    log.info("=" * 50)

    # At noon, use the dedicated noon job instead
    if is_noon:
        noon_stock_job()
        return

    # --- 30-min push logic: always scan & analyze, then push ---
    quote_alerts = scan()

    # AI news analysis (every cycle)
    log.info("  calling AI news analysis...")
    news_analysis = analyze_zijin_news(raw_headlines)
    if news_analysis:
        impactful = news_analysis.get("impactful_news", [])
        log.info("  AI news: %d impactful items, signal=%s",
                 len(impactful), news_analysis.get("overall_signal", "?"))
    else:
        log.info("  AI news: no response")

    # No stock picks outside noon
    stock_picks = None

    # Rate limit guard (30 min)
    if time.time() - last_push_time < MIN_PUSH_INTERVAL - 5:
        log.info("rate limited: %.0fs", time.time() - last_push_time)
        return

    # Build and send push (always)
    header, body = build_push_content(
        news_analysis=news_analysis,
        stock_picks=None,
        quote_alerts=quote_alerts if quote_alerts else None,
    )

    if header and body:
        push(header, body)
    else:
        log.info("skip push: build_push_content returned None")

    log.info("=" * 50)


if __name__ == "__main__":
    log.info("v12 start (30min push + BJT)")
    job()
    schedule.every(30).minutes.do(job)
    log.info("running (30min, AI)")
    while True:
        schedule.run_pending()
        time.sleep(1)

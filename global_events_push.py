#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每小时国内外大事件采集，no-agent 直推微信。"""

from __future__ import annotations

import concurrent.futures
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "global_events_seen.json"
LOG_FILE = BASE_DIR / "global_events_push.log"
DESKTOP_ENV = Path.home() / "Desktop" / "1.env"
CN_TZ = timezone(timedelta(hours=8))
FRESH_HOURS = int(os.environ.get("NEWS_FRESH_HOURS", "6"))
IMPORTANT_FRESH_HOURS = int(os.environ.get("NEWS_IMPORTANT_FRESH_HOURS", "24"))
FUTURES = [
    ("黄金", "GC=F", "$/oz"),
    ("白银", "SI=F", "$/oz"),
    ("铜", "HG=F", "$/lb"),
    ("WTI原油", "CL=F", "$/bbl"),
    ("布油", "BZ=F", "$/bbl"),
]

AD_KEYWORDS = (
    "广告", "推广", "赞助", "商业合作", "软文", "优惠", "折扣", "领取", "报名", "课程", "训练营",
    "直播预告", "活动预告", "招商", "加盟", "下载", "app", "抽奖", "福利", "优惠券",
    "advertisement", "sponsored", "sponsor", "promo", "promotion", "webinar", "sign up",
)

RSS_FEEDS = [
    ("Google综合", "https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("Google财经", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("Google国际", "https://news.google.com/rss/headlines/section/topic/WORLD?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("Google美国财经", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en"),
    ("Google美国国际", "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en"),
    ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("NYT国际", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("NYT商业", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("卫报国际", "https://www.theguardian.com/world/rss"),
    ("WSJ世界", "https://feeds.a.dj.com/rss/RSSWorldNews.xml"),
    ("WSJ市场", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("CNBC头条", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CNBC全球", "https://www.cnbc.com/id/100727362/device/rss/rss.html"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Bitcoin.com", "https://news.bitcoin.com/feed/"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/.rss/full/"),
    ("CryptoBriefing", "https://cryptobriefing.com/feed/"),
]

SEARCH_QUERIES = [
    ("紫金矿业", "紫金矿业 OR Zijin Mining OR 601899 OR 金矿 铜矿 锂矿"),
    ("黄金铜美元", "黄金 金价 白银 铜价 伦铜 美元 美联储 降息 加息 通胀"),
    ("全球经济", "全球经济 美股 纳指 债券 美元 原油 黄金 衰退 GDP CPI"),
    ("国内政策", "A股 证监会 央行 人民币 出口 关税 GDP 政策 降准"),
    ("政治冲突", "战争 冲突 制裁 地缘 原油 中东 俄乌 避险"),
    ("重大灾难", "地震 洪水 火灾 爆炸 空难 台风 海啸 灾难 事故"),
    ("能源资源", "原油 天然气 煤炭 电力 铜 铝 锂 稀土 大宗商品"),
    ("BTC加密", "Bitcoin BTC 比特币 Ethereum ETH 加密货币 crypto stablecoin ETF Coinbase Binance Circle DeFi"),
    ("X热点", "X Twitter trending breaking news market BTC gold oil Fed earthquake war"),
]

RULES = [
    (("紫金矿业", "紫金", "矿茅", "zijin", "601899", "金矿", "铜矿", "锂矿", "黄金", "铜价", "伦铜"), "紫金矿业相关", 9),
    (("黄金", "金价", "白银", "铜价", "伦铜", "美元", "美联储", "降息", "加息", "通胀", "贵金属", "有色"), "金属与美元", 7),
    (("a股", "证监会", "央行", "人民币", "出口", "关税", "gdp", "政策", "降准"), "A股宏观", 5),
    (("战争", "冲突", "制裁", "地缘", "中东", "俄乌", "避险", "原油", "iran", "israel", "war", "conflict", "sanction"), "政治冲突", 5),
    (("美股", "纳指", "全球股市", "债券", "收益率", "衰退", "cpi", "pmi", "inflation", "stock", "futures", "fed", "bank", "economy"), "全球经济", 4),
    (("bitcoin", "btc", "比特币", "ethereum", "eth", "加密货币", "crypto", "stablecoin", "coinbase", "binance", "etf", "defi", "circle"), "BTC加密", 5),
    (("地震", "洪水", "火灾", "爆炸", "空难", "台风", "海啸", "灾难", "事故", "earthquake", "flood", "fire", "blast", "crash", "disaster", "killed"), "重大灾难", 4),
    (("原油", "天然气", "煤炭", "电力", "铝", "锂", "稀土", "大宗商品"), "能源资源", 4),
]


def load_desktop_env() -> None:
    if not DESKTOP_ENV.exists():
        return
    for line in DESKTOP_ENV.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def log(text: str) -> None:
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{stamp} {text}\n")


def opener() -> urllib.request.OpenerDirector:
    proxies = {}
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        value = os.environ.get(key) or os.environ.get(key.lower())
        if value:
            proxies[key.split("_", 1)[0].lower()] = value
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))


def fetch_futures_snapshot() -> list[dict]:
    rows: list[dict] = []
    for name, symbol, unit in FUTURES:
        try:
            encoded = urllib.parse.quote(symbol, safe="")
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=2d&interval=5m"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = json.loads(opener().open(req, timeout=8).read().decode("utf-8", "replace"))
            result = (data.get("chart", {}).get("result") or [None])[0] or {}
            meta = result.get("meta", {})
            price = float(meta.get("regularMarketPrice") or 0)
            prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
            ts = int(meta.get("regularMarketTime") or 0)
            change = (price - prev) / prev * 100 if price and prev else 0.0
            rows.append({"name": name, "price": price, "change": change, "unit": unit, "time": ts})
        except Exception as exc:
            log(f"期货行情失败：{name}，原因：{exc}")
    return rows


def futures_line(rows: list[dict]) -> str:
    if not rows:
        return "行情暂不可用"
    parts = []
    for row in rows:
        sign = "+" if row["change"] > 0 else ""
        price = f"{row['price']:.2f}" if row["price"] < 100 else f"{row['price']:.1f}"
        parts.append(f"{row['name']} {price} {sign}{row['change']:.2f}%")
    return "｜".join(parts)


def source_urls() -> list[tuple[str, str]]:
    urls = list(RSS_FEEDS)
    for label, query in SEARCH_QUERIES:
        google = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}
        )
        bing = "https://www.bing.com/news/search?" + urllib.parse.urlencode(
            {"q": query, "format": "rss", "mkt": "zh-CN"}
        )
        urls.append((f"{label}-Google", google))
        urls.append((f"{label}-Bing", bing))
    return urls


def decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030", "big5", "latin1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def fetch_titles(name: str, url: str) -> list[dict]:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/rss+xml,application/xml,text/xml,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            },
        )
        raw = opener().open(req, timeout=10).read()
        text = decode_bytes(raw)
    except Exception as exc:
        log(f"新闻源失败：{name}，原因：{exc}")
        return []

    rows: list[dict] = []
    try:
        root = ET.fromstring(text)
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for item in items[:18]:
            title = clean_text(_node_text(item, "title"))
            if title and len(title) >= 6 and title not in {"Google 新闻", "Bing"}:
                rows.append({"source": name, "title": title, "published": parse_item_time(item)})
    except Exception:
        for item in re.findall(r"<item\b.*?</item>", text, re.S | re.I)[:18]:
            match = re.search(r"<title[^>]*>(.*?)</title>", item, re.S | re.I)
            title = clean_text(match.group(1)) if match else ""
            if title and len(title) >= 6:
                pub = ""
                pub_match = re.search(r"<(?:pubDate|published|updated)[^>]*>(.*?)</(?:pubDate|published|updated)>", item, re.S | re.I)
                if pub_match:
                    pub = clean_text(pub_match.group(1))
                rows.append({"source": name, "title": title, "published": parse_time_text(pub)})
    return rows


def _node_text(item: ET.Element, tag: str) -> str:
    node = item.find(tag)
    if node is None:
        node = item.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
    return "".join(node.itertext()) if node is not None else ""


def parse_item_time(item: ET.Element) -> str:
    for tag in ("pubDate", "published", "updated"):
        value = _node_text(item, tag)
        if value:
            return parse_time_text(value)
    return ""


def parse_time_text(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
    except Exception:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def row_dt(row: dict) -> datetime | None:
    value = str(row.get("published") or "")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def row_time_label(row: dict) -> str:
    dt = row_dt(row)
    if not dt:
        return "时间未知"
    return dt.astimezone(CN_TZ).strftime("%m-%d %H:%M")


def is_fresh_row(row: dict) -> bool:
    dt = row_dt(row)
    if not dt:
        return False
    age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    if age_hours < 0:
        return True
    label = str(row.get("label") or "")
    limit = IMPORTANT_FRESH_HOURS if any(x in label for x in ("紫金矿业相关", "重大灾难", "政治冲突")) else FRESH_HOURS
    return age_hours <= limit


def clean_text(value: str) -> str:
    value = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", value or "", flags=re.S)
    value = re.sub(r"<.*?>", "", value)
    value = html.unescape(value)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"\s+-\s+[^-]{2,50}$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def is_ad_or_noise(title: str) -> bool:
    low = re.sub(r"\s+", " ", (title or "").lower())
    if not low:
        return True
    if any(word.lower() in low for word in AD_KEYWORDS):
        return True
    if re.search(r"(限时|免费|立减|扫码|入群|私信|开奖|中奖|特惠|券|返现)", title or ""):
        return True
    return False


def item_key(title: str) -> str:
    normalized = re.sub(r"\W+", "", title.lower())
    return hashlib.sha1(normalized.encode("utf-8", "ignore")).hexdigest()[:16]


def classify(title: str) -> tuple[int, str]:
    labels: list[str] = []
    score = 0
    low = title.lower()
    for words, label, weight in RULES:
        if any(word.lower() in low for word in words):
            labels.append(label)
            score += weight
    if not labels:
        return 1, "全球要闻"
    return score, "、".join(labels[:2])


def is_relevant_news(title: str, score: int) -> bool:
    low = title.lower()
    if is_ad_or_noise(title):
        return False
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", title))
    if not has_cjk:
        return False
    if score > 1:
        return True
    include = (
        "经济", "金融", "股市", "美股", "a股", "美元", "黄金", "铜", "原油", "央行", "通胀",
        "gdp", "cpi", "pmi", "market", "stock", "inflation", "fed", "tariff",
        "war", "conflict", "earthquake", "flood", "disaster", "crash",
        "bitcoin", "btc", "ethereum", "crypto", "stablecoin", "coinbase", "binance", "etf",
        "比特币", "以太坊", "加密货币", "稳定币",
    )
    exclude = (
        "禁毒", "宣传活动", "铁路累计", "旅客超", "创业大赛", "众创杯", "文旅", "演出",
    )
    if any(x in low for x in exclude):
        return False
    return any(x in low for x in include)


def event_view(title: str) -> tuple[str, str]:
    low = title.lower()
    if any(x in low for x in ["紫金", "zijin", "金矿", "铜矿", "黄金", "金价大涨", "铜价上涨"]):
        return "偏利好", "金价、铜价和资源扩张是核心变量。"
    if any(x in low for x in ["降息", "美元走弱", "避险", "冲突", "制裁", "地缘", "战争"]):
        return "偏利好", "避险情绪可能支撑黄金和资源资产。"
    if any(x in low for x in ["加息", "美元走强", "金价下跌", "铜价下跌", "需求放缓", "衰退", "暴跌"]):
        return "偏利空", "金属价格承压，短线防回撤。"
    if any(x in low for x in ["bitcoin", "btc", "比特币", "ethereum", "eth", "加密货币", "stablecoin", "coinbase", "binance"]):
        if any(x in low for x in ["etf", "inflow", "surge", "rally", "record", "上涨", "流入", "新高", "突破"]):
            return "偏利好", "风险资产情绪偏强，关注美股和美元联动。"
        if any(x in low for x in ["hack", "outflow", "ban", "lawsuit", "crash", "下跌", "黑客", "监管", "诉讼", "暴跌"]):
            return "风险事件", "加密资产波动加大，关注风险偏好传导。"
        return "观察", "加密资产消息，关注风险偏好和美元流动性。"
    if any(x in low for x in ["地震", "洪水", "火灾", "爆炸", "空难", "台风", "海啸", "高温", "热浪"]):
        return "风险事件", "关注市场情绪和供应链扰动。"
    if any(x in low for x in ["央行", "证监会", "人民币", "政策", "降准", "关税", "工业企业利润", "物流", "投资", "链博会"]):
        return "宏观观察", "关注A股资金面和人民币反应。"
    return "观察", "方向暂不明确。"


def load_seen() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_seen(seen: dict) -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - 24 * 3600
    compact = {key: value for key, value in seen.items() if float(value or 0) >= cutoff}
    STATE_FILE.write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8")


def compact_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    return title[:34] + ("..." if len(title) > 34 else "")


def short_label(label: str) -> str:
    for name in ("紫金矿业相关", "金属与美元", "全球经济", "BTC加密", "A股宏观", "政治冲突", "重大灾难", "能源资源"):
        if name in label:
            return name
    return label or "全球要闻"


def view_mark(view: str) -> str:
    if view == "偏利好":
        return "[利好]"
    if view == "偏利空":
        return "[利空]"
    if view == "风险事件":
        return "[风险]"
    if view == "宏观观察":
        return "[宏观]"
    return "[观察]"


def build_message(items: list[dict]) -> str:
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    futures = fetch_futures_snapshot()
    if not items:
        return f"【国内外大事件】{now}\n\n商品快照\n{futures_line(futures)}\n\n状态：暂未抓到可用新闻\n处理：检查网络、代理或新闻源"

    views = [event_view(item["title"])[0] for item in items]
    metal_bias = any(row["name"] in {"黄金", "白银", "铜"} and row["change"] > 0.5 for row in futures)
    oil_risk = any("油" in row["name"] and abs(row["change"]) > 1.2 for row in futures)
    if "偏利好" in views or metal_bias:
        overview = "资源线偏强，紫金矿业继续重点观察。"
    elif "偏利空" in views:
        overview = "短线偏谨慎，防金属价格回落拖累。"
    elif "风险事件" in views or oil_risk:
        overview = "外部风险升温，先看避险资产和大宗商品。"
    else:
        overview = "暂未形成强方向，保持观察。"

    zijin_items = [item for item in items if "紫金矿业相关" in str(item.get("label", ""))]
    market_items = [item for item in items if item not in zijin_items]
    lines = [f"【国内外大事件】{now}", "", "商品快照", futures_line(futures), "", "总览", overview]
    if zijin_items:
        z_view, z_note = event_view(zijin_items[0]["title"])
        lines.extend(["", "紫金", f"{view_mark(z_view)} {z_view}：{z_note}"])
    else:
        lines.extend(["", "紫金", "[观察] 暂无直接消息，主要看金价、铜价、美元。"])

    lines.extend(["", "重点事件"])
    ordered = (zijin_items[:2] + market_items)[:5]
    for idx, item in enumerate(ordered, 1):
        view, note = event_view(item["title"])
        label = short_label(str(item.get("label") or "全球要闻"))
        mark = view_mark(view)
        lines.append(f"{idx}. {mark} [{label}] {compact_title(item['title'])}")
        lines.append(f"   时间：{row_time_label(item)}")
        lines.append(f"   {note}")

    lines.extend(["", "提醒", "只提醒，不追高；等价格、量能和买卖点确认。"])
    return "\n".join(lines)[:1400]


def push_text(text: str) -> bool:
    openclaw_target = os.environ.get("OPENCLAW_WEIXIN_TARGET", "").strip()
    old_target = os.environ.get("HERMES_SEND_TARGET", "").strip()
    if not openclaw_target and old_target.startswith("weixin:"):
        openclaw_target = old_target.split(":", 1)[1]
    if openclaw_target:
        return push_openclaw_weixin(openclaw_target, text)

    webhook = os.environ.get("CLAWBOT_WEBHOOK_URL", "").strip()
    if webhook:
        return push_clawbot_webhook(webhook, text)

    log("未配置 OpenClaw 微信目标，本次只写入日志，未启动 Hermes")
    return False


def push_openclaw_weixin(target: str, text: str) -> bool:
    target = target.strip()
    if target.startswith("weixin:"):
        target = target.split(":", 1)[1]
    if send_openclaw_message_direct(target, text):
        return True
    return send_openclaw_message(target, text)


def wechat_blocks(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    head = []
    events = []
    action = []
    for line in lines:
        if line.startswith(("🟢", "🔴", "🟠", "🔵", "🟡")) or line.startswith(("偏利", "风险", "宏观", "观察")):
            events.append(line)
        elif line.startswith("操作："):
            action.append(line)
        elif line == "重点事件：":
            continue
        elif events:
            events.append(line)
        else:
            head.append(line)

    blocks = []
    if head:
        blocks.append("｜".join(head))
    if events:
        blocks.append("｜".join(events[:8]))
    if action:
        blocks.append("｜".join(action))
    clean = [re.sub(r"\s+", " ", block).strip() for block in blocks if block.strip()]
    return [block[:700] for block in clean[:3]]


def send_openclaw_message(target: str, text: str) -> bool:
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
    account = os.environ.get("OPENCLAW_WEIXIN_ACCOUNT", "").strip()
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
        detail = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip())
        if proc.returncode == 0:
            log("OpenClaw 微信直推成功")
            return True
        log(f"OpenClaw 微信直推失败：{detail[:300]}")
        return False
    except Exception as exc:
        log(f"OpenClaw 微信直推异常：{exc}")
        return False


def send_openclaw_message_direct(target: str, text: str) -> bool:
    """Use the Weixin plugin API directly; avoids OpenClaw model/CLI failures."""
    send_js = os.environ.get("OPENCLAW_SEND_JS", "").strip()
    account_json = os.environ.get("OPENCLAW_ACCOUNT_JSON", "").strip()
    context_json = os.environ.get("OPENCLAW_CONTEXT_JSON", "").strip()
    if not send_js or not account_json or not context_json:
        log("OpenClaw no-agent 未配置 OPENCLAW_SEND_JS/OPENCLAW_ACCOUNT_JSON/OPENCLAW_CONTEXT_JSON")
        return False
    script = r"""
const fs = require('fs');
(async () => {
  const mod = process.env.OPENCLAW_SEND_JS;
  const { sendMessageWeixin } = await import('file:///' + mod.replace(/\\/g,'/'));
  const acct = JSON.parse(fs.readFileSync(process.env.OPENCLAW_ACCOUNT_JSON,'utf8'));
  const ctxs = JSON.parse(fs.readFileSync(process.env.OPENCLAW_CONTEXT_JSON,'utf8'));
  const to = process.argv[1] || acct.userId;
  const contextToken = ctxs[to] || ctxs[acct.userId];
  if (!contextToken) throw new Error('context token missing; send a WeChat message to refresh it');
  const result = await sendMessageWeixin({
    to,
    text: process.argv[2] || '',
    opts: { baseUrl: acct.baseUrl, token: acct.token, contextToken, timeoutMs: 20000 }
  });
  console.log(JSON.stringify({ok:true, messageId: result.messageId}));
})().catch(err => { console.error(String(err)); process.exit(1); });
"""
    try:
        proc = subprocess.run(
            ["node", "-e", script, target, text],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "OPENCLAW_SEND_JS": send_js, "OPENCLAW_ACCOUNT_JSON": account_json, "OPENCLAW_CONTEXT_JSON": context_json},
            timeout=35,
        )
        detail = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip())
        if proc.returncode == 0:
            log("OpenClaw 微信 no-agent 直推成功")
            return True
        log(f"OpenClaw 微信 no-agent 直推失败：{detail[:300]}")
        return False
    except Exception as exc:
        log(f"OpenClaw 微信 no-agent 直推异常：{exc}")
        return False


def push_clawbot_webhook(webhook: str, text: str) -> bool:
    payload = json.dumps({"text": text, "content": text, "msg": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "dabao-news-push/3.0"},
        method="POST",
    )
    try:
        raw = opener().open(req, timeout=15).read().decode("utf-8", "ignore")
        log(f"ClawBot 直推成功：{raw[:120]}")
        return True
    except Exception as exc:
        log(f"ClawBot 直推失败：{exc}")
        return False


def collect() -> list[dict]:
    rows: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(fetch_titles, name, url) for name, url in source_urls()]
        for future in concurrent.futures.as_completed(futures, timeout=35):
            try:
                rows.extend(future.result())
            except Exception:
                pass

    dedup: dict[str, dict] = {}
    for row in rows:
        title = row["title"]
        key = item_key(title)
        score, label = classify(title)
        if not is_relevant_news(title, score):
            continue
        candidate = {**row, "score": score, "label": label}
        if key not in dedup or score > dedup[key]["score"]:
            dedup[key] = candidate

    seen = load_seen()
    now_ts = datetime.now(timezone.utc).timestamp()
    fresh: list[dict] = []
    fallback: list[dict] = []
    for row in dedup.values():
        fallback.append(row)
        if item_key(row["title"]) not in seen and is_fresh_row(row):
            fresh.append(row)

    recent_fallback = [row for row in fallback if is_fresh_row(row)]
    picked = fresh or recent_fallback
    picked.sort(key=lambda item: (item["score"], row_dt(item).timestamp() if row_dt(item) else 0, bool(re.search(r"[\u4e00-\u9fff]", item["title"]))), reverse=True)
    selected = select_diverse(picked)
    for row in selected:
        seen[item_key(row["title"])] = now_ts
    save_seen(seen)
    log(f"抓取新闻 {len(rows)} 条，去重 {len(dedup)} 条，近期 {len(recent_fallback)} 条，新消息 {len(fresh)} 条，推送 {len(selected[:6])} 条")
    return selected[:6]


def select_diverse(rows: list[dict]) -> list[dict]:
    """紫金优先，但保留全球经济、政治和灾难大事件的席位。"""
    quotas = [
        ("紫金矿业相关", 2),
        ("金属与美元", 1),
        ("全球经济", 1),
        ("BTC加密", 1),
        ("A股宏观", 1),
        ("政治冲突", 1),
        ("重大灾难", 1),
        ("能源资源", 1),
    ]
    selected: list[dict] = []
    used: set[str] = set()
    for label, limit in quotas:
        count = 0
        for row in rows:
            key = item_key(row["title"])
            if key in used or label not in str(row.get("label", "")):
                continue
            selected.append(row)
            used.add(key)
            count += 1
            if count >= limit or len(selected) >= 8:
                break
    for row in rows:
        if len(selected) >= 8:
            break
        key = item_key(row["title"])
        if key not in used:
            selected.append(row)
            used.add(key)
    return selected


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    load_desktop_env()
    try:
        items = collect()
        text = build_message(items)
        print(text)
        push_text(text)
        log(f"采集完成：推送 {len(items)} 条")
        return 0
    except Exception as exc:
        msg = f"国内外大事件任务异常：{exc}"
        print(msg)
        log(msg)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

import requests
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)


def analyze_impact(title, content=""):
    """Classify news as bullish/bearish for Zijin Mining.
    Works on original English text BEFORE translation."""
    full = (title + " " + content).lower()

    strong_bull = [
        "gold surge", "gold rally", "gold jump", "gold hits record",
        "copper surge", "copper rally", "copper jump",
        "zijin beat", "zijin profit", "zijin upgrade", "zijin expansion",
        "supply shortage", "mine reopening", "production boost",
        "fed cut", "rate cut", "stimulus", "tariff delay",
    ]
    strong_bear = [
        "gold plunge", "gold crash", "gold tumble", "gold slump",
        "gold fall", "gold drop", "gold decline",
        "copper plunge", "copper crash", "copper tumble",
        "copper fall", "copper drop", "copper decline",
        "zijin miss", "zijin loss", "zijin downgrade",
        "recession", "trade war escalate", "tariff hike",
        "oversupply", "demand crash", "mine accident",
        "forced lab", "production cut", "environmental violation",
    ]

    bull_score = sum(2 for w in strong_bull if w in full)
    bear_score = sum(2 for w in strong_bear if w in full)

    if re.search(r"\bban\b", full):
        bear_score += 2
    if re.search(r"\bsanction\b", full):
        bear_score += 2
    if "forced lab" in full:
        bear_score += 3

    for commodity in ["gold", "copper"]:
        if commodity in full:
            for bear_word in ["tumbl", "fall", "slump", "plunge", "crash", "drop", "decline", "weak"]:
                if bear_word in full:
                    bear_score += 1
                    break
            for bull_word in ["surge", "rally", "jump", "rise", "gain", "boost", "recover"]:
                if bull_word in full:
                    bull_score += 1
                    break

    weak_bull = ["rise", "gain", "boost", "higher", "recover", "bullish", "expansion", "acquire"]
    weak_bear = ["fall", "drop", "decline", "lower", "weak", "bearish", "recession", "loss", "cut"]
    bull_score += sum(1 for w in weak_bull if re.search(r"\b" + re.escape(w) + r"\b", full))
    bear_score += sum(1 for w in weak_bear if re.search(r"\b" + re.escape(w) + r"\b", full))

    if bear_score > bull_score and bear_score >= 2:
        return "\u5229\u7a7a"
    if bull_score > bear_score and bull_score >= 2:
        return "\u5229\u597d"
    if bull_score > 0 and bull_score > bear_score:
        return "\u504f\u591a"
    if bear_score > 0 and bear_score > bull_score:
        return "\u504f\u7a7a"
    return "\u4e2d\u6027"


# --- Chinese summary from English keywords ---
_TOPIC_PATTERNS = [
    (r"gold\s+(plunge|crash|tumble|slump|fall|drop|decline)", "\u91d1\u4ef7\u5927\u8dcc"),
    (r"gold\s+(surge|rally|jump|soar|rise|gain|boost|record)", "\u91d1\u4ef7\u5927\u6da8"),
    (r"gold\s+(price|hit|future)", "\u91d1\u4ef7"),
    (r"copper\s+(plunge|crash|tumble|slump|fall|drop|decline)", "\u94dc\u4ef7\u5927\u8dcc"),
    (r"copper\s+(surge|rally|jump|soar|rise|gain|boost|record)", "\u94dc\u4ef7\u5927\u6da8"),
    (r"copper\s+(price|future)", "\u94dc\u4ef7"),
    (r"zijin", "\u7d2b\u91d1\u77ff\u4e1a"),
    (r"federal\s+reserve|fed\s+(cut|rate|chair|hawk|dove|keep|hold)", "\u7f8e\u8054\u50a8"),
    (r"inflation|cpi", "\u901a\u80c0"),
    (r"interest\s+rate|rate\s+(cut|hike|hold)", "\u5229\u7387"),
    (r"trade\s+war|tariff", "\u8d38\u6613\u6218/\u5173\u7a0e"),
    (r"recession", "\u7ecf\u6d4e\u8870\u9000"),
    (r"stimulus", "\u523a\u6fc0\u653f\u7b56"),
    (r"sanction", "\u5236\u88c1"),
    (r"mine\s+(accident|closure)", "\u77ff\u5c71"),
    (r"earnings|profit|revenue", "\u8d22\u62a5/\u76c8\u5229"),
    (r"production|output", "\u4ea7\u80fd"),
    (r"demand", "\u9700\u6c42"),
    (r"supply", "\u4f9b\u5e94"),
]

_DIRECTION_PATTERNS = [
    (r"\b(plunge|crash|tumble|slump)\b", "\u66b4\u8dcc"),
    (r"\b(surge|soar|rally|jump)\b", "\u5927\u6da8"),
    (r"\b(fall|drop|decline|lower|weak)\b", "\u4e0b\u8dcc"),
    (r"\b(rise|gain|boost|recover|rebound|higher)\b", "\u4e0a\u6da8"),
    (r"\b(beat|outperform|upgrade)\b", "\u8d85\u9884\u671f"),
    (r"\b(miss|downgrade|loss)\b", "\u4e0d\u53ca\u9884\u671f"),
]


def make_cn_summary(title, content=""):
    """Generate a short ALL-CHINESE summary from English text using keyword patterns."""
    full = (title + " " + content).lower()
    topics = []
    for pattern, cn in _TOPIC_PATTERNS:
        if re.search(pattern, full):
            if cn not in topics:
                topics.append(cn)
    directions = []
    for pattern, cn in _DIRECTION_PATTERNS:
        if re.search(pattern, full):
            if cn not in directions:
                directions.append(cn)
    parts = topics[:2] + directions[:1]
    if not parts:
        return ""
    return "".join(parts)


class TonghuashunFetcher:
    """
    English financial news + market data for Zijin Mining.
    Sources: Google News RSS + Yahoo Finance + EastMoney.
    Shows: [icon] Chinese summary | English original title.
    Quotes: NOT pushed to notification (only logged).
    """

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
    }

    YAHOO_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Referer": "https://finance.yahoo.com/",
    }

    def __init__(self):
        self.session = requests.Session()

    def _get_google_news(self, query, label, count=5):
        """Google News RSS with Chinese summary + impact analysis."""
        msgs = []
        try:
            url = "https://news.google.com/rss/search?q={0}&hl=en-US&gl=US&ceid=US:en".format(
                requests.utils.quote(query)
            )
            r = self.session.get(url, headers=self.HEADERS, timeout=15)
            if r.status_code != 200:
                return msgs
            from xml.etree import ElementTree as ET
            root = ET.fromstring(r.text)
            for idx, item in enumerate(root.findall(".//item")[:count]):
                title_en = (item.findtext("title") or "").strip()
                desc_en = (item.findtext("description") or "").strip()
                desc_en = re.sub(r"<[^>]+>", "", desc_en).strip()
                link = (item.findtext("link") or "").strip()

                source_elem = item.find("source")
                source_name = (source_elem.text or "").strip() if source_elem is not None else ""

                # Classify using ORIGINAL English
                tag = analyze_impact(title_en, desc_en)

                # Generate ALL-Chinese summary
                cn_summary = make_cn_summary(title_en, desc_en)

                if tag in ("\u5229\u597d", "\u504f\u591a"):
                    icon = "+"
                elif tag in ("\u5229\u7a7a", "\u504f\u7a7a"):
                    icon = "-"
                else:
                    icon = "="

                # Display: [icon] Chinese summary | English original
                if cn_summary:
                    display = "[{0}] {1} | {2}".format(icon, cn_summary, title_en[:50])
                else:
                    display = "[{0}] {1}".format(icon, title_en[:70])

                content_parts = []
                if desc_en:
                    content_parts.append(desc_en[:200])
                if link:
                    content_parts.append(link)
                content = "\n".join(content_parts)

                msgs.append({
                    "id": "gn_{0}_{1}".format(label.replace(" ", "").replace("/", ""), idx),
                    "title": display,
                    "title_en": title_en,
                    "content": content,
                    "source": source_name or label,
                    "source_type": "news_{0}".format(tag),
                    "time": datetime.now(),
                    "priority": 2 if tag in ("\u5229\u597d", "\u5229\u7a7a") else 1,
                })
        except Exception as e:
            logger.warning("google news %s err: %s", query, e)
        return msgs

    def _get_yh_quote(self, ticker, name):
        """Yahoo Finance quote. Returns data but source_type=quote so main.py skips push."""
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/{0}".format(ticker)
            params = {"range": "1d", "interval": "5m"}
            r = self.session.get(url, params=params, headers=self.YAHOO_HEADERS, timeout=10)
            data = r.json().get("chart", {}).get("result", [{}])[0]
            meta = data.get("meta", {})
            price = meta.get("regularMarketPrice", 0)
            prev = meta.get("previousClose", 0) or meta.get("chartPreviousClose", 0)
            chg = round((price - prev) / prev * 100, 2) if prev else 0

            cn_map = {"ZIJIN": "\u7d2b\u91d1\u77ff\u4e1a(HK)", "GOLD": "COMEX\u9ec4\u91d1", "COPPER": "COMEX\u94dc"}
            cn_name = cn_map.get(name, name)

            icon = "+" if chg > 0 else "-"
            return {
                "id": "yh_{0}".format(ticker.replace(".", "_").replace("=", "")),
                "title": "[{0}] {1} ${2} ({3}%)".format(icon, cn_name, price, chg),
                "title_en": "",
                "content": "\u73b0\u4ef7:{0} \u6628\u6536:{1} \u6da8\u8dcc:{2}%".format(price, prev, chg),
                "source": "Yahoo",
                "source_type": "quote",
                "time": datetime.now(),
                "priority": 1,
            }
        except Exception as e:
            logger.warning("yahoo %s err: %s", ticker, e)
            return None

    def _get_em_quote_safe(self):
        """EastMoney Zijin A-share quote. source_type=quote so main.py skips push."""
        try:
            r = self.session.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={"secid": "1.601899", "fields": "f43,f44,f45,f46,f60,f170"},
                timeout=10,
            )
            text = r.text.strip()
            if not text.startswith("{"):
                return None
            data = r.json().get("data") or {}
            if not data:
                return None
            price = data.get("f43", 0)
            prev = data.get("f60", 0)
            chg = data.get("f170", 0)
            if prev and prev > 1000:
                price = round(price / 100, 2)
                prev = round(prev / 100, 2)
                chg = round(chg / 100, 2)

            icon = "+" if chg > 0 else "-"
            return {
                "id": "em_zijin",
                "title": "[{0}] \u7d2b\u91d1\u77ff\u4e1a {1} ({2}%)".format(icon, price, chg),
                "title_en": "",
                "content": "\u73b0\u4ef7:{0} \u6628\u6536:{1}".format(price, prev),
                "source": "\u4e1c\u65b9\u8d22\u5bcc",
                "source_type": "quote",
                "time": datetime.now(),
                "priority": 1,
            }
        except Exception as e:
            logger.warning("em err: %s", e)
            return None

    def fetch_hot_stocks(self, count=10):
        msgs = []

        queries = [
            ("Zijin Mining OR zijin", "\u7d2b\u91d1\u77ff\u4e1a"),
            ("gold price OR metal prices", "\u9ec4\u91d1\u91d1\u5c5e"),
            ("copper price OR LME copper", "\u94dc\u4ef7"),
        ]
        for q, label in queries:
            msgs.extend(self._get_google_news(q, label, count=4))

        # Quotes: still fetch (for logging) but main.py will NOT push them
        for ticker, name in [("2899.HK", "ZIJIN"), ("GC=F", "GOLD"), ("HG=F", "COPPER")]:
            q = self._get_yh_quote(ticker, name)
            if q:
                msgs.append(q)

        em = self._get_em_quote_safe()
        if em:
            msgs.append(em)

        msgs.sort(key=lambda x: x.get("priority", 1), reverse=True)

        logger.info("fetcher: %d msgs", len(msgs))
        return msgs


tonghuashun_fetcher = TonghuashunFetcher()

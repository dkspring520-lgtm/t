import requests
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)


# ==================== Conservative AI Impact Analysis ====================

def analyze_impact(title, content=""):
    """Conservative classification - only mark bull/bear for truly significant events."""
    full = (title + " " + content).lower()

    # Strong bull: very specific major events
    strong_bull = [
        "gold hits record", "gold all-time high", "gold record high",
        "copper hits record", "copper all-time high",
        "zijin beat", "zijin profit surge", "zijin upgrade",
        "fed cut rates", "fed rate cut", "interest rate cut",
        "mine reopening", "supply crisis", "supply shortage",
        "central bank gold buying", "gold demand surge",
    ]
    # Strong bear: very specific major events
    strong_bear = [
        "gold crash", "gold plunge", "gold selloff", "gold tumbl",
        "copper crash", "copper plunge", "copper selloff",
        "zijin loss", "zijin downgrade", "zijin ban",
        "recession fear", "trade war escalate",
        "forced labour", "forced labor", "environmental violation",
        "mine accident", "mine collapse", "production halt",
        "export ban", "sanction zijin", "sanction china",
    ]

    bull_score = sum(2 for w in strong_bull if w in full)
    bear_score = sum(2 for w in strong_bear if w in full)

    if re.search(r"\bban\b", full) and any(k in full for k in ["zijin", "copper", "mining", "china"]):
        bear_score += 3
    if re.search(r"\bsanction\b", full) and any(k in full for k in ["zijin", "china"]):
        bear_score += 2
    if "forced lab" in full:
        bear_score += 3

    weak_bull = ["surge", "rally", "soar", "record high", "breakthrough"]
    weak_bear = ["plunge", "crash", "selloff", "collapse", "halt"]
    bull_score += sum(1 for w in weak_bull if w in full)
    bear_score += sum(1 for w in weak_bear if w in full)

    # Conservative thresholds: need score >= 3 for strong labels
    if bear_score >= 3 and bear_score > bull_score:
        return "\u5229\u7a7a"
    if bull_score >= 3 and bull_score > bear_score:
        return "\u5229\u597d"
    if bull_score >= 2 and bull_score > bear_score:
        return "\u504f\u591a"
    if bear_score >= 2 and bear_score > bull_score:
        return "\u504f\u7a7a"
    return "\u4e2d\u6027"


# ==================== Full Chinese Translation ====================

# Phrase-level translations (matched first, longer phrases first)
_PHRASE_MAP = {
    "gold prices are tumbling": "\u91d1\u4ef7\u5927\u5e45\u4e0b\u8dcc",
    "but don't expect that to last": "\u4f46\u9884\u8ba1\u4e0d\u4f1a\u6301\u7eed\u592a\u4e45",
    "don't expect that to last long": "\u9884\u8ba1\u4e0d\u4f1a\u6301\u7eed\u592a\u4e45",
    "fed keeps rates unchanged": "\u7f8e\u8054\u50a8\u7ef4\u6301\u5229\u7387\u4e0d\u53d8",
    "hawkish fed signals": "\u7f8e\u8054\u50a8\u9e70\u6d3e\u4fe1\u53f7",
    "fed chairman": "\u7f8e\u8054\u50a8\u4e3b\u5e2d",
    "gold surges as fed cuts rates": "\u7f8e\u8054\u50a8\u964d\u606f \u91d1\u4ef7\u5927\u6da8",
    "on track for third weekly": "\u5c06\u8fde\u7eed\u4e09\u5468",
    "weekly loss": "\u5468\u4e8f\u635f",
    "weekly decline": "\u5468\u4e0b\u8dcc",
    "rate cut": "\u964d\u606f",
    "rate hike": "\u52a0\u606f",
    "interest rate": "\u5229\u7387",
    "gold surge": "\u91d1\u4ef7\u5927\u6da8",
    "gold rally": "\u91d1\u4ef7\u4e0a\u6da8",
    "gold plunge": "\u91d1\u4ef7\u66b4\u8dcc",
    "gold crash": "\u91d1\u4ef7\u5d29\u8dcc",
    "gold selloff": "\u91d1\u4ef7\u629b\u552e",
    "gold slides": "\u91d1\u4ef7\u4e0b\u6ed1",
    "gold falls": "\u91d1\u4ef7\u4e0b\u8dcc",
    "gold drops": "\u91d1\u4ef7\u4e0b\u8dcc",
    "gold tumbles": "\u91d1\u4ef7\u5927\u8dcc",
    "gold slips": "\u91d1\u4ef7\u5c0f\u8dcc",
    "gold rises": "\u91d1\u4ef7\u4e0a\u6da8",
    "gold gains": "\u91d1\u4ef7\u4e0a\u6da8",
    "gold recovers": "\u91d1\u4ef7\u53cd\u5f39",
    "copper price": "\u94dc\u4ef7",
    "copper surges": "\u94dc\u4ef7\u5927\u6da8",
    "copper plunge": "\u94dc\u4ef7\u66b4\u8dcc",
    "copper falls": "\u94dc\u4ef7\u4e0b\u8dcc",
    "copper drops": "\u94dc\u4ef7\u4e0b\u8dcc",
    "copper prices": "\u94dc\u4ef7",
    "copper demand": "\u94dc\u9700\u6c42",
    "zijin mining": "\u7d2b\u91d1\u77ff\u4e1a",
    "zijin gold": "\u7d2b\u91d1\u77ff\u4e1a",
    "central bank": "\u592e\u884c",
    "central banks": "\u592e\u884c",
    "gold reserves": "\u9ec4\u91d1\u50a8\u5907",
    "gold rush": "\u6dd8\u91d1\u70ed",
    "mining company": "\u77ff\u4e1a\u516c\u53f8",
    "mining companies": "\u77ff\u4e1a\u516c\u53f8",
    "gold price": "\u91d1\u4ef7",
    "gold target": "\u91d1\u4ef7\u76ee\u6807",
    "copper scrap": "\u94dc\u5e9f\u6599",
    "copper projects": "\u94dc\u77ff\u9879\u76ee",
    "copper forecast": "\u94dc\u4ef7\u9884\u6d4b",
    "ai demand": "AI\u9700\u6c42",
    "battery truck": "\u7535\u6c60\u5361\u8f66",
    "electric vehicle": "\u7535\u52a8\u8f66",
    "gold fever": "\u6dd8\u91d1\u70ed\u6f6e",
    "record rally": "\u5386\u53f2\u53cd\u5f39",
    "iran peace": "\u4f0a\u6717\u548c\u5e73",
    "fed rate": "\u7f8e\u8054\u50a8\u5229\u7387",
    "trade war": "\u8d38\u6613\u6218",
    "rate decision": "\u5229\u7387\u51b3\u7b56",
    "trade war tariff": "\u8d38\u6613\u6218\u5173\u7a0e",
    "gold demand": "\u9ec4\u91d1\u9700\u6c42",
    "supply shortage": "\u4f9b\u5e94\u7d27\u5f20",
    "mining industry": "\u77ff\u4e1a",
    "hong kong listing": "\u6e2f\u80a1\u4e0a\u5e02",
    "hong kong": "\u6e2f\u80a1",
    "south china morning": "\u5357\u534e\u65e9\u62a5",
    "wall street": "\u534e\u5c14\u8857",
}

# Word-level translations (matched second, longer words first)
_WORD_MAP = {
    # Gold/copper
    "gold": "\u9ec4\u91d1", "copper": "\u94dc", "precious": "\u8d35\u91d1\u5c5e",
    "metal": "\u91d1\u5c5e", "metals": "\u91d1\u5c5e", "mineral": "\u77ff\u4ea7",
    # Zijin
    "zijin": "\u7d2b\u91d1", "mining": "\u91c7\u77ff", "miner": "\u77ff\u4f01",
    "mine": "\u77ff\u5c71", "ores": "\u77ff\u77f3",
    # Fed/Macro
    "federal": "\u8054\u90a6", "reserve": "\u50a8\u5907",
    "inflation": "\u901a\u80c0", "recession": "\u8870\u9000",
    "stimulus": "\u523a\u6fc0", "tariff": "\u5173\u7a0e",
    "sanction": "\u5236\u88c1", "embargo": "\u7981\u8fd0",
    "dollar": "\u7f8e\u5143", "yuan": "\u4eba\u6c11\u5e01",
    "commodity": "\u5927\u5b97\u5546\u54c1", "commodities": "\u5927\u5b97\u5546\u54c1",
    "bond": "\u503a\u5238", "treasury": "\u56fd\u503a",
    # Business
    "earnings": "\u8d22\u62a5", "profit": "\u76c8\u5229", "revenue": "\u8425\u6536",
    "acquisition": "\u6536\u8d2d", "expansion": "\u6269\u5f20",
    "investment": "\u6295\u8d44", "valuation": "\u4f30\u503c",
    "production": "\u4ea7\u80fd", "output": "\u4ea7\u51fa",
    "supply": "\u4f9b\u5e94", "demand": "\u9700\u6c42",
    "regulation": "\u76d1\u7ba1", "compliance": "\u5408\u89c4",
    "environmental": "\u73af\u4fdd", "violation": "\u8fdd\u89c4",
    # Directions
    "surge": "\u5927\u6da8", "soar": "\u98d9\u5347", "rally": "\u53cd\u5f39",
    "jump": "\u8df3\u6da8", "rise": "\u4e0a\u6da8", "gain": "\u4e0a\u6da8",
    "plunge": "\u66b4\u8dcc", "crash": "\u5d29\u8dcc", "selloff": "\u629b\u552e",
    "tumble": "\u5927\u8dcc", "slump": "\u66b4\u8dcc",
    "fall": "\u4e0b\u8dcc", "drop": "\u4e0b\u8dcc", "decline": "\u4e0b\u8dcc",
    "weak": "\u8d70\u5f31", "lower": "\u8d70\u4f4e",
    "higher": "\u8d70\u9ad8", "bullish": "\u770b\u591a", "bearish": "\u770b\u7a7a",
    "hawkish": "\u9e70\u6d3e", "dovish": "\u9e3d\u6d3e",
    "tumbling": "\u5927\u8dcc", "weakening": "\u8d70\u5f31",
    "strengthen": "\u8d70\u5f3a", "lift": "\u63d0\u632f",
    # Regions & names
    "china": "\u4e2d\u56fd", "russia": "\u4fc4\u7f57\u65af", "iran": "\u4f0e\u6717",
    "serbia": "\u585e\u5c14\u7ef4\u4e9a", "congo": "\u521a\u679c",
    "peru": "\u79d8\u9c81", "panama": "\u5df4\u62ff\u9a6c",
    "australia": "\u6fb3\u5927\u5229\u4e9a", "bloomberg": "\u5e03\u9686\u4f2f",
    "reuters": "\u8def\u900f", "cnbc": "CNBC", "wsj": "\u534e\u5c14\u8857\u65e5\u62a5",
    "yahoo": "\u96c5\u864e", "money.com": "\u8d22\u7ecf\u7f51",
    "forced": "\u5f3a\u8feb", "labor": "\u52b3\u52a8", "labour": "\u52b3\u52a8",
    "record": "\u5386\u53f2\u65b0\u9ad8",
    # Connectors (removed in cleanup)
}


def translate_to_cn(text):
    """Full Chinese translation. Replaces English with Chinese, strips leftovers."""
    if not text:
        return ""

    result = text

    # Step 1: phrase-level (longer first)
    for en, cn in sorted(_PHRASE_MAP.items(), key=lambda x: -len(x[0])):
        result = re.sub(re.escape(en), cn, result, flags=re.IGNORECASE)

    # Step 2: word-level (longer first)
    for en, cn in sorted(_WORD_MAP.items(), key=lambda x: -len(x[0])):
        result = re.sub(r'\b' + re.escape(en) + r'\b', cn, result, flags=re.IGNORECASE)

    # Step 3: clean up leftover English noise words
    noise_words = [
        r'\bas\b', r'\bthe\b', r'\ban\b', r'\ba\b', r'\bof\b', r'\bin\b',
        r'\bat\b', r'\bfor\b', r'\bto\b', r'\bis\b', r'\bare\b',
        r'\bwas\b', r'\bwere\b', r'\bhas\b', r'\bhad\b', r'\bhave\b',
        r'\bthis\b', r'\bthat\b', r'\bwith\b', r'\bfrom\b', r'\bby\b',
        r'\bon\b', r'\bor\b', r'\band\b', r'\bbut\b', r'\bnot\b',
        r'\bmay\b', r'\bcould\b', r'\bwill\b', r'\bshould\b', r'\bwould\b',
        r'\bcan\b', r'\bdo\b', r'\bdid\b', r'\bbe\b', r'\bsome\b',
        r'\bmore\b', r'\bmost\b', r'\bother\b', r'\btheir\b', r'\bits\b',
        r'\bhis\b', r'\bher\b', r'\bour\b', r'\byour\b', r'\bwho\b',
        r'\bwhich\b', r'\bwhat\b', r'\bwhen\b', r'\bwhere\b', r'\bhow\b',
        r'\balso\b', r'\bstill\b', r'\bjust\b', r'\bonly\b', r'\bvery\b',
        r'\beven\b', r'\bwhile\b', r'\bafter\b', r'\bbefore\b', r'\bsince\b',
        r'\bown\b', r'\bnew\b',
    ]
    for pattern in noise_words:
        result = re.sub(pattern, '', result, flags=re.IGNORECASE)

    # Step 4: clean up leftover short English fragments (2-5 chars that don't look meaningful)
    result = re.sub(r'\b[A-Za-z]{2,4}\b', '', result)

    # Step 5: clean up whitespace and punctuation
    result = re.sub(r'\s+', '', result)
    result = re.sub(r'[,\s]+\.', '.', result)
    result = re.sub(r'^[|\-\s]+', '', result)
    result = re.sub(r'[|\-\s]+$', '', result)

    # If result is too short or empty, return original
    if len(result.strip()) < 4:
        return text[:60]

    return result


def make_cn_title(title_en, content_en="", tag="\u4e2d\u6027"):
    """Generate fully Chinese title: [icon] Chinese translation"""
    cn = translate_to_cn(title_en)
    if not cn or len(cn) < 4:
        cn = translate_to_cn(title_en + " " + (content_en or ""))

    if tag in ("\u5229\u597d", "\u504f\u591a"):
        icon = "+"
    elif tag in ("\u5229\u7a7a", "\u504f\u7a7a"):
        icon = "-"
    else:
        icon = "="

    return "[{0}] {1}".format(icon, cn)


# ==================== Quote Alert Thresholds ====================
# Quotes are normally NOT pushed, but MAJOR moves get special alerts
QUOTE_ALERT_THRESHOLD = 3.0  # Only push quote alert if change > 3%


class TonghuashunFetcher:
    """
    English financial news + market data for Zijin Mining.
    News: fully translated to Chinese.
    Quotes: NOT pushed unless change exceeds QUOTE_ALERT_THRESHOLD (3%).
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
        """Google News RSS - fully translated to Chinese."""
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

                # Classify using ORIGINAL English (more accurate)
                tag = analyze_impact(title_en, desc_en)

                # Generate fully Chinese title
                display = make_cn_title(title_en, desc_en, tag)

                # Also translate content for push body
                cn_content = translate_to_cn(desc_en[:200]) if desc_en else ""

                content_parts = []
                if cn_content:
                    content_parts.append(cn_content[:200])
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
        """Yahoo Finance quote. Returns as 'quote' type.
        If change > QUOTE_ALERT_THRESHOLD, returns as 'quote_alert' instead."""
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/{0}".format(ticker)
            params = {"range": "1d", "interval": "5m"}
            r = self.session.get(url, params=params, headers=self.YAHOO_HEADERS, timeout=10)
            data = r.json().get("chart", {}).get("result", [{}])[0]
            meta = data.get("meta", {})
            price = meta.get("regularMarketPrice", 0)
            prev = meta.get("previousClose", 0) or meta.get("chartPreviousClose", 0)
            chg = round((price - prev) / prev * 100, 2) if prev else 0

            cn_map = {
                "ZIJIN": "\u7d2b\u91d1\u77ff\u4e1a(\u6e2f)",
                "GOLD": "COMEX\u9ec4\u91d1",
                "COPPER": "COMEX\u94dc",
            }
            cn_name = cn_map.get(name, name)

            icon = "+" if chg > 0 else "-"

            # Major move alert: threshold > 3%
            is_alert = abs(chg) >= QUOTE_ALERT_THRESHOLD
            src_type = "quote_alert" if is_alert else "quote"

            if is_alert:
                alert_tag = "\u5229\u597d" if chg > 0 else "\u5229\u7a7a"
                alert_icon = "\u26a0"  # warning sign
                display_title = "[{0}] {1}\u91cd\u5927\u53d8\u52a8! ${2} ({3}%)".format(
                    alert_icon, cn_name, price, chg)
                priority = 3  # highest
            else:
                display_title = "[{0}] {1} ${2} ({3}%)".format(icon, cn_name, price, chg)
                alert_tag = "\u4e2d\u6027"
                priority = 1

            return {
                "id": "yh_{0}".format(ticker.replace(".", "_").replace("=", "")),
                "title": display_title,
                "title_en": "",
                "content": "\u73b0\u4ef7:{0} \u6628\u6536:{1} \u6da8\u8dcc:{2}%".format(price, prev, chg),
                "source": "Yahoo",
                "source_type": src_type,
                "tag": alert_tag,
                "time": datetime.now(),
                "priority": priority,
                "change_pct": chg,
            }
        except Exception as e:
            logger.warning("yahoo %s err: %s", ticker, e)
            return None

    def _get_em_quote(self):
        """EastMoney Zijin A-share quote. Same alert logic."""
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
            is_alert = abs(chg) >= QUOTE_ALERT_THRESHOLD
            src_type = "quote_alert" if is_alert else "quote"

            if is_alert:
                alert_tag = "\u5229\u597d" if chg > 0 else "\u5229\u7a7a"
                display_title = "[\u26a0] \u7d2b\u91d1\u77ff\u4e1a\u91cd\u5927\u53d8\u52a8! \u00a5{0} ({1}%)".format(price, chg)
                priority = 3
            else:
                display_title = "[{0}] \u7d2b\u91d1\u77ff\u4e1a \u00a5{1} ({2}%)".format(icon, price, chg)
                alert_tag = "\u4e2d\u6027"
                priority = 1

            return {
                "id": "em_zijin",
                "title": display_title,
                "title_en": "",
                "content": "\u73b0\u4ef7:{0} \u6628\u6536:{1}".format(price, prev),
                "source": "\u4e1c\u65b9\u8d22\u5bcc",
                "source_type": src_type,
                "tag": alert_tag,
                "time": datetime.now(),
                "priority": priority,
                "change_pct": chg,
            }
        except Exception as e:
            logger.warning("em err: %s", e)
            return None

    def fetch_hot_stocks(self, count=10):
        """Fetch news (fully Chinese) + quotes (only push alerts > 3%)."""
        msgs = []

        # --- News: Google News RSS for 6 topic areas ---
        queries = [
            ("Zijin Mining OR zijin", "\u7d2b\u91d1\u77ff\u4e1a"),
            ("gold price OR metal prices", "\u9ec4\u91d1\u91d1\u5c5e"),
            ("copper price OR LME copper", "\u94dc\u4ef7"),
            ("federal reserve interest rate decision", "\u7f8e\u8054\u50a8\u5229\u7387"),
            ("trade war tariff China", "\u8d38\u6613\u6218\u5173\u7a0e"),
            ("mining industry supply demand", "\u77ff\u4e1a\u4f9b\u9700"),
        ]
        for q, label in queries:
            msgs.extend(self._get_google_news(q, label, count=4))

        # --- Quotes: fetch but only alert on major moves ---
        for ticker, name in [("2899.HK", "ZIJIN"), ("GC=F", "GOLD"), ("HG=F", "COPPER")]:
            q = self._get_yh_quote(ticker, name)
            if q:
                msgs.append(q)

        em = self._get_em_quote()
        if em:
            msgs.append(em)

        # Sort: alerts first, then impactful news, then quotes
        msgs.sort(key=lambda x: x.get("priority", 1), reverse=True)

        logger.info("fetcher: %d msgs", len(msgs))
        return msgs


tonghuashun_fetcher = TonghuashunFetcher()

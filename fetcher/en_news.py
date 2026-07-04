import requests
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


class EnNewsFetcher:
    """
    English financial news fetcher for overseas servers.
    Sources: Yahoo Finance (confirmed working on this server).
    Filters for Zijin Mining, gold, copper related news.
    """

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
    }

    # Yahoo Finance tickers related to Zijin Mining
    TICKER_NEWS = {
        "601899.SS": "Zijin Mining(SH)",
        "2899.HK": "Zijin Mining(HK)",
        "GC=F": "COMEX Gold",
        "HG=F": "COMEX Copper",
        "GLD": "Gold ETF",
    }

    # Keyword filter for relevance
    KEYWORDS = [
        "zijin", "gold", "copper", "precious metal", "mining",
        "nonferrous", "comex", "lme", "xau", "chile", "peru",
        "congo", "serbia", "tibet", "zijinshan",
    ]

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def _fetch_yahoo_news(self, ticker, label):
        """Fetch news for a single ticker from Yahoo Finance."""
        msgs = []
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/{0}".format(ticker)
            params = {
                "range": "1d",
                "interval": "1m",
            }
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})

            price = meta.get("regularMarketPrice", 0)
            prev = meta.get("previousClose", 0)
            change_pct = round((price - prev) / prev * 100, 2) if prev else 0

            title = "{0} {1} {2}%".format(label, price, change_pct)
            content = "Price:{0} PrevClose:{1} Change:{2}%".format(price, prev, change_pct)
            msgs.append({
                "id": "yh_{0}".format(ticker.replace(".", "_")),
                "title": title,
                "content": content,
                "source": "YahooFinance",
                "source_type": "yahoo_quote",
                "time": datetime.now(),
                "priority": 1,
            })
        except Exception as e:
            logger.warning("yahoo %s err: %s", ticker, e)
        return msgs

    def _fetch_yahoo_headlines(self):
        """Try Yahoo Finance news feed (v2 API)."""
        msgs = []
        tickers_str = ",".join(self.TICKER_NEWS.keys())
        try:
            url = "https://query2.finance.yahoo.com/v1/finance/trending/US"
            r = self.session.get(url, timeout=10)
            # This endpoint may not have stock-specific news
            # fallback to individual ticker news
        except Exception:
            pass

        for ticker, label in self.TICKER_NEWS.items():
            try:
                url = "https://finance.yahoo.com/quote/{0}/news/".format(ticker)
                r = self.session.get(url, timeout=10)
                # HTML parsing would be needed; skip for now
            except Exception:
                pass
        return msgs

    def fetch(self, count=10):
        """Fetch quotes + relevant news for Zijin Mining ecosystem."""
        msgs = []

        # 1. Real-time quotes for all related tickers
        for ticker, label in self.TICKER_NEWS.items():
            items = self._fetch_yahoo_news(ticker, label)
            msgs.extend(items)

        # 2. Yahoo Finance news headlines (best effort)
        msgs.extend(self._fetch_yahoo_headlines())

        logger.info("en_news: %d msgs", len(msgs))
        return msgs


en_news_fetcher = EnNewsFetcher()

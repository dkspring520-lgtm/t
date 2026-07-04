# -*- coding: utf-8 -*-
"""
AI-powered news analyzer for Zijin Mining stock alerts.
Uses GPT-5.5 via supxh.xin proxy to:
1. Analyze global news affecting Zijin (gold/copper/mining)
2. Judge bullish/bearish impact
3. Recommend 1-3 A-share stocks at noon (find potential 100%+ gainers)
4. Generate fully Chinese push notification
"""
import requests
import json
import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

API_KEY = "sk-gueCKHDrlmffcNumpGLMf97V5cRFjcHZULUmzv4MWgyxnZuq"
API_BASE = "https://api.supxh.xin"
MODEL = "gpt-5.5"

NEWS_SYSTEM_PROMPT = (
    "你是一个专注于有色金属矿业（特别是紫金矿业601899）的专业财经分析师。"
    "你的任务是：分析当前国际重大新闻事件，判断它们对紫金矿业的影响。\n\n"
    "分析规则：\n"
    "1. 只关注真正重大的事件，忽略日常小幅波动\n"
    "2. 从以下维度判断对紫金矿业的影响：\n"
    "   - 黄金价格变动（紫金最大利润来源）\n"
    "   - 铜价变动（紫金第二大利润来源）\n"
    "   - 美联储利率政策（影响金价）\n"
    "   - 地缘冲突（影响避险情绪和供应）\n"
    "   - 中国经济政策（影响需求）\n"
    "   - 行业监管/环保/制裁（影响紫金海外矿山）\n"
    "3. 利空利好要准确，不要过度解读\n"
    "4. 输出必须是纯JSON，不要markdown代码块\n\n"
    "输出格式（严格JSON）：\n"
    '{"impactful_news": [{"title": "中文标题20字内", "impact": "利好/利空/中性", '
    '"reason": "一句话30字内", "urgency": "高/中/低"}], '
    '"market_summary": "一句话总结30字内", "overall_signal": "看多/看空/观望"}'
)

STOCK_PICK_SYSTEM_PROMPT = (
    "你是一个传奇级A股操盘手，擅长挖掘有翻倍潜力的股票。"
    "你的核心能力：在市场早期发现即将爆发的标的，在大众还没反应过来时入场。\n\n"
    "你的任务是：每天精选1-3只A股，这些股票在未来1-3个月有翻倍（涨幅100%+）的可能性。\n\n"
    "选股必须综合以下5个维度深度分析，每个维度都要找到翻倍证据：\n\n"
    "1.【消息面】是否有重大政策/行业革命/公司重组等爆炸性催化剂\n"
    "   - 寻找：国家级政策首批受益者、行业颠覆性事件、并购重组\n"
    "2.【基本面】业绩拐点是否出现、估值是否有10倍以上扩张空间\n"
    "   - 寻找：利润拐点、从亏损转盈利、新产品即将量产、机构密集调研\n"
    "3.【数据面】是否有超级主力资金持续流入\n"
    "   - 寻找：北向资金连续增持、龙虎榜机构席位、融资余额暴增\n"
    "4.【技术面】是否在长期底部刚放量突破\n"
    "   - 寻找：底部横盘1年以上突破、历史级别量能、MACD月线金叉\n"
    "5.【未来面】未来1-3个月是否有确定性极强的催化事件\n"
    "   - 寻找：产品发布、政策落地、财报超预期、行业会议\n\n"
    "翻倍股特征（必须命中至少3条）：\n"
    "- 处于行业爆发前夜（如当年锂电/光储/AI）\n"
    "- 公司是细分赛道绝对龙头或唯一标的\n"
    "- 市值50-300亿（太小流动性差，太大弹性不足）\n"
    "- 股价在历史底部区域或刚突破长期平台\n"
    "- 有明确的翻倍催化剂而非模糊预期\n\n"
    "硬性要求：\n"
    "- 必须给出为什么这只股票能翻倍的核心逻辑\n"
    "- 宁缺毋滥，没有把握就只推1只甚至不推\n"
    "- 入场价格区间要精确，止损位要严格\n"
    "- 每只股必须说清楚翻倍路径——从现在到翻倍中间会发生什么\n\n"
    "输出必须是纯JSON，不要markdown代码块：\n"
    '{"picks": [{"code": "000001", "name": "平安银行", "price": "当前价格", '
    '"market_cap": "市值区间", "direction": "中线翻倍/短线爆发", '
    '"double_logic": "翻倍核心逻辑40字内", '
    '"message_face": "消息面一句话", "fundamental": "基本面一句话", '
    '"data_face": "数据面一句话", "tech_face": "技术面一句话", '
    '"future_face": "未来催化一句话", '
    '"entry": "入场区间", "stop_loss": "止损价", "target": "翻倍目标价", '
    '"time_frame": "预计1-3个月/3-6个月", "confidence": "高/中/低", '
    '"risk": "最大风险一句话"}], '
    '"market_mood": "当前A股情绪10字内"}'
)


def _call_ai(messages, max_retries=2):
    headers = {
        "Authorization": "Bearer {}".format(API_KEY),
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 2500,
    }
    for attempt in range(max_retries + 1):
        try:
            r = requests.post(
                "{}/v1/chat/completions".format(API_BASE),
                headers=headers,
                json=payload,
                timeout=60,
            )
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"]
                content = content.strip()
                if content.startswith("```"):
                    lines = content.split("\n")
                    content = "\n".join(lines[1:])
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()
                return content
            else:
                logger.warning("AI API %d: %s", r.status_code, r.text[:200])
                if attempt < max_retries:
                    time.sleep(3)
        except Exception as e:
            logger.warning("AI API err (attempt %d): %s", attempt, e)
            if attempt < max_retries:
                time.sleep(5)
    return None


def analyze_zijin_news(raw_headlines):
    if not raw_headlines:
        headlines_text = "当前无新抓取的新闻标题，请根据近期市场整体情况分析对紫金矿业的影响。"
    else:
        headlines_text = "\n".join(
            "{}. {}".format(i + 1, h) for i, h in enumerate(raw_headlines[:25])
        )

    user_msg = (
        "以下是今天抓取的财经新闻标题（可能有中英混杂，请自行理解翻译）：\n\n"
        "{}\n\n"
        "请分析其中对紫金矿业(601899)有重大影响的事件。"
        "只输出真正有影响力的重大事件，忽略小幅日常波动。"
        "如果这些新闻都不重要，返回空的impactful_news列表。"
    ).format(headlines_text)

    messages = [
        {"role": "system", "content": NEWS_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    result = _call_ai(messages)
    if not result:
        return None

    try:
        return json.loads(result)
    except json.JSONDecodeError:
        logger.warning("AI news response not valid JSON: %s", result[:300])
        import re
        m = re.search(r'\{[\s\S]*\}', result)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
        return None


def pick_stocks():
    today = datetime.now().strftime("%Y-%m-%d")
    user_msg = (
        "今天是{}。请从A股市场精选1-3只有翻倍潜力的股票。\n\n"
        "必须综合5维度分析：消息面、基本面、数据面、技术面、未来面。"
        "每只必须说清楚翻倍路径——从现在到翻倍中间会发生什么。\n\n"
        "核心要求：\n"
        "1. 必须有翻倍（涨幅100%+）的可能性，不是只涨10-20%\n"
        "2. 宁缺毋滥，没有真正翻倍把握就少推或不推\n"
        "3. 每只给出翻倍核心逻辑和翻倍路径\n"
        "4. 入场价、止损价、目标价都给精确数字\n"
        "5. 说清楚最大风险是什么\n"
        "6. 市值50-300亿优先，行业爆发前夜优先"
    ).format(today)

    messages = [
        {"role": "system", "content": STOCK_PICK_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    result = _call_ai(messages)
    if not result:
        return None

    try:
        return json.loads(result)
    except json.JSONDecodeError:
        logger.warning("AI stock pick response not valid JSON: %s", result[:300])
        import re
        m = re.search(r'\{[\s\S]*\}', result)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
        return None


def build_push_content(news_analysis, stock_picks, quote_alerts=None):
    now = datetime.now()
    h = now.hour
    mn = now.minute
    is_scheduled = (h == 9 and mn < 6) or (h == 15 and mn < 12) or (h == 20 and mn < 6)
    is_noon = (h == 12 and mn < 10)

    bull_n = 0
    bear_n = 0
    if news_analysis and "impactful_news" in news_analysis:
        for n in news_analysis["impactful_news"]:
            if n.get("impact") == "\u5229\u597d":
                bull_n += 1
            elif n.get("impact") == "\u5229\u7a7a":
                bear_n += 1

    has_picks = stock_picks and stock_picks.get("picks")

    if not news_analysis and not quote_alerts and not has_picks:
        return None, None

    # Build title
    if is_noon and has_picks:
        header = "AI\u8350\u80a1|{}\u53ea\u7ffb\u500d\u6f5c\u529b\u80a1".format(len(stock_picks["\u0070\u0069\u0063\u006b\u0073"]))
    elif h == 9 and mn < 6:
        header = "\u76d8\u524d\u6668\u62a5|\u7d2b\u91d1\u77ff\u4e1a"
    elif h == 15 and mn < 12:
        header = "\u76d8\u540e\u603b\u7ed3|\u7d2b\u91d1\u77ff\u4e1a"
    elif h == 20 and mn < 6:
        header = "\u665a\u95f4\u5feb\u9012|\u7d2b\u91d1\u77ff\u4e1a"
    elif bull_n or bear_n:
        header = "\u7d2b\u91d1|{}\u5229\u597d{}\u5229\u7a7a".format(bull_n, bear_n)
    elif quote_alerts:
        header = "\u7d2b\u91d1|\u884c\u60c5\u5f02\u52a8"
    else:
        return None, None

    body = ""

    # Strategy hint
    if h == 9 and mn < 6:
        body += "\u7b56\u7565\uff1a\u9ad8\u5f00\u51cf\u4ed3\u505aT\uff0c\u4f4e\u5f00\u52a0\u4ed3\n\n"
    elif h == 15 and mn < 12:
        body += "\u7b56\u7565\uff1a\u5173\u6ce8\u9694\u591c\u7f8e\u80a1/\u9ec4\u91d1\n\n"
    elif h == 20 and mn < 6:
        body += "\u7b56\u7565\uff1a\u5173\u6ce8\u7f8e\u80a1\u5f00\u76d8/\u5927\u5b97\u5546\u54c1\n\n"

    # Quote alerts
    if quote_alerts:
        body += "=== \u26a0\ufe0f \u884c\u60c5\u91cd\u5927\u53d8\u52a8 ===\n"
        for q in quote_alerts:
            body += "{}\n".format(q)
        body += "\n"

    # AI stock picks (noon)
    if has_picks:
        picks = stock_picks["\u0070\u0069\u0063\u006b\u0073"]
        body += "=== AI\u7cbe\u9009 \u7ffb\u500d\u6f5c\u529b\u80a1 ===\n"
        for i, p in enumerate(picks, 1):
            conf = p.get("\u0063\u006f\u006e\u0066\u0069\u0064\u0065\u006e\u0063\u0065", "")
            conf_icon = "\u2b50" if conf == "\u9ad8" else "\u25cb" if conf == "\u4f4e" else "\u25cf"
            body += "{}.{} {}({}) | {} | \u5e02\u503c{}\n".format(
                i, conf_icon,
                p.get("\u006e\u0061\u006d\u0065", ""), p.get("\u0063\u006f\u0064\u0065", ""),
                p.get("\u0064\u0069\u0072\u0065\u0063\u0074\u0069\u006f\u006e", ""),
                p.get("\u006d\u0061\u0072\u006b\u0065\u0074\u005f\u0063\u0061\u0070", "")
            )
            body += "   >> \u7ffb\u500d\u903b\u8f91: {}\n".format(p.get("\u0064\u006f\u0075\u0062\u006c\u0065\u005f\u006c\u006f\u0067\u0069\u0063", ""))
            body += "   \u5165\u573a: {} | \u6b62\u635f: {} | \u76ee\u6807: {}\n".format(
                p.get("\u0065\u006e\u0074\u0072\u0079", ""), p.get("\u0073\u0074\u006f\u0070\u005f\u006c\u006f\u0073\u0073", ""),
                p.get("\u0074\u0061\u0072\u0067\u0065\u0074", "")
            )
            body += "   \u9884\u8ba1\u5468\u671f: {}\n".format(p.get("\u0074\u0069\u006d\u0065\u005f\u0066\u0072\u0061\u006d\u0065", ""))
            body += "   \u6d88\u606f\u9762: {}\n".format(p.get("\u006d\u0065\u0073\u0073\u0061\u0067\u0065\u005f\u0066\u0061\u0063\u0065", ""))
            body += "   \u57fa\u672c\u9762: {}\n".format(p.get("\u0066\u0075\u006e\u0064\u0061\u006d\u0065\u006e\u0074\u0061\u006c", ""))
            body += "   \u6570\u636e\u9762: {}\n".format(p.get("\u0064\u0061\u0074\u0061\u005f\u0066\u0061\u0063\u0065", ""))
            body += "   \u6280\u672f\u9762: {}\n".format(p.get("\u0074\u0065\u0063\u0068\u005f\u0066\u0061\u0063\u0065", ""))
            body += "   \u672a\u6765\u9762: {}\n".format(p.get("\u0066\u0075\u0074\u0075\u0072\u0065\u005f\u0066\u0061\u0063\u0065", ""))
            body += "   \u26a0 \u98ce\u9669: {}\n".format(p.get("\u0072\u0069\u0073\u006b", ""))
            body += "\n"
        mood = stock_picks.get("\u006d\u0061\u0072\u006b\u0065\u0074\u005f\u006d\u006f\u006f\u0064", "")
        if mood:
            body += "\u5e02\u573a\u60c5\u7eea: {}\n".format(mood)
        body += "\n"

    # AI news analysis (skip full details at noon)
    if news_analysis and not is_noon:
        if news_analysis.get("\u006d\u0061\u0072\u006b\u0065\u0074\u005f\u0073\u0075\u006d\u006d\u0061\u0072\u0079"):
            body += "\u3010\u5e02\u573a\u7efc\u8ff0\u3011{}\n\n".format(news_analysis["\u006d\u0061\u0072\u006b\u0065\u0074\u005f\u0073\u0075\u006d\u006d\u0061\u0072\u0079"])

        impactful = news_analysis.get("\u0069\u006d\u0070\u0061\u0063\u0074\u0066\u0075\u006c\u005f\u006e\u0065\u0077\u0073", [])
        if impactful:
            bull_items = [n for n in impactful if n.get("\u0069\u006d\u0070\u0061\u0063\u0074") == "\u5229\u597d"]
            bear_items = [n for n in impactful if n.get("\u0069\u006d\u0070\u0061\u0063\u0074") == "\u5229\u7a7a"]
            neutral_items = [n for n in impactful if n.get("\u0069\u006d\u0070\u0061\u0063\u0074") == "\u4e2d\u6027"]

            if bull_items:
                body += "=== \u5229\u597d\u6d88\u606f ===\n"
                for i, n in enumerate(bull_items, 1):
                    urg = ">>" if n.get("\u0075\u0072\u0067\u0065\u006e\u0063\u0079") == "\u9ad8" else "  "
                    body += "{}.{}\u3010\u5229\u597d\u3011{}\n".format(i, urg, n["\u0074\u0069\u0074\u006c\u0065"])
                    body += "   {}\n".format(n.get("\u0072\u0065\u0061\u0073\u006f\u006e", ""))
                body += "\n"

            if bear_items:
                body += "=== \u5229\u7a7a\u6d88\u606f ===\n"
                for i, n in enumerate(bear_items, 1):
                    urg = ">>" if n.get("\u0075\u0072\u0067\u0065\u006e\u0063\u0079") == "\u9ad8" else "  "
                    body += "{}.{}\u3010\u5229\u7a7a\u3011{}\n".format(i, urg, n["\u0074\u0069\u0074\u006c\u0065"])
                    body += "   {}\n".format(n.get("\u0072\u0065\u0061\u0073\u006f\u006e", ""))
                body += "\n"

            if neutral_items and is_scheduled:
                body += "=== \u5176\u4ed6\u5173\u6ce8 ===\n"
                for i, n in enumerate(neutral_items[:3], 1):
                    body += "{}. {}\n".format(i, n["\u0074\u0069\u0074\u006c\u0065"])
                body += "\n"

        signal = news_analysis.get("\u006f\u0076\u0065\u0072\u0061\u006c\u006c\u005f\u0073\u0069\u0067\u006e\u0061\u006c", "")
        if signal:
            icon = "\u2191" if signal == "\u770b\u591a" else "\u2193" if signal == "\u770b\u7a7a" else "\u2192"
            body += "\u7efc\u5408\u4fe1\u53f7: {} {}\n".format(icon, signal)

    # At noon, brief news summary after stock picks
    if is_noon and news_analysis:
        summary = news_analysis.get("\u006d\u0061\u0072\u006b\u0065\u0074\u005f\u0073\u0075\u006d\u006d\u0061\u0072\u0079", "")
        signal = news_analysis.get("\u006f\u0076\u0065\u0072\u0061\u006c\u006c\u005f\u0073\u0069\u0067\u006e\u0061\u006c", "")
        if summary or signal:
            body += "---\n"
            body += "\u7d2b\u91d1\u77ff\u4e1a: {} {}".format(
                summary,
                "({})".format(signal) if signal else ""
            )
        body += "\n"

    body += "---\n"
    body += now.strftime("%Y-%m-%d %H:%M:%S")

    return header, body

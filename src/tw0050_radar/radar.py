from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html import escape, unescape
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import feedparser
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "constituents_0050.json"

MODE_LABELS = {
    "premarket": "盤前新聞",
    "intraday": "盤中新聞",
    "postmarket": "盤後新聞",
}

MODE_LOOKBACK_HOURS = {
    "premarket": 14,
    "intraday": 5,
    "postmarket": 6,
}

MARKET_KEYWORDS = [
    "美股",
    "nasdaq",
    "費半",
    "sox",
    "台積電 adr",
    "tsmc adr",
    "美債",
    "殖利率",
    "美元指數",
    "台幣",
    "新台幣",
    "匯率",
    "fed",
    "fomc",
    "降息",
    "升息",
    "通膨",
    "cpi",
    "ppi",
]

EVENT_KEYWORDS = [
    "重大訊息",
    "法說",
    "法說會",
    "月營收",
    "財報",
    "財測",
    "併購",
    "投資",
    "擴產",
    "停工",
    "制裁",
    "關稅",
    "出口管制",
    "注意股",
    "處置股",
    "三大法人",
    "外資",
    "投信",
    "自營商",
    "融資",
    "融券",
    "借券",
]

INTRADAY_KEYWORDS = [
    "爆量",
    "急漲",
    "急跌",
    "拉回",
    "轉強",
    "轉弱",
    "大漲",
    "重挫",
    "跌停",
    "漲停",
    "權值股",
    "台股",
    "大盤",
    "類股",
]

NOISE_KEYWORDS = [
    "抽獎",
    "優惠",
    "開戶",
    "贈品",
    "信用卡",
    "房貸",
]


@dataclass(frozen=True)
class Constituent:
    ticker: str
    name: str
    aliases: tuple[str, ...]

    @property
    def keywords(self) -> tuple[str, ...]:
        return (self.ticker, self.name, *self.aliases)


@dataclass(frozen=True)
class NewsSource:
    name: str
    url: str


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    source: str
    published_at: datetime | None
    summary: str
    score: int
    matched: tuple[str, ...]
    reasons: tuple[str, ...]


def main() -> None:
    load_dotenv()
    args = parse_args()
    constituents, heavyweights = load_constituents()
    lookback_hours = args.lookback_hours or int(
        os.getenv("DIGEST_LOOKBACK_HOURS", str(MODE_LOOKBACK_HOURS[args.mode]))
    )
    max_articles = args.max_articles or int(os.getenv("DIGEST_MAX_ARTICLES", "3"))
    
    articles = fetch_articles(
        mode=args.mode,
        constituents=constituents,
        heavyweights=heavyweights,
        lookback_hours=lookback_hours,
    )[:max_articles]

    subject = f"0050 成分股{MODE_LABELS[args.mode]}｜{datetime.now().strftime('%Y-%m-%d')}"
    plain = render_plain_text(args.mode, articles)
    html = render_html(args.mode, articles)

    if smtp_is_configured():
        send_email(subject=subject, plain=plain, html=html)
        print(f"Sent {len(articles)} articles: {subject}")
    else:
        print(f"{subject}\n")
        print(plain)
        print("\nSMTP is not configured, so the digest was printed instead of emailed.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="0050 constituent news radar")
    parser.add_argument(
        "--mode",
        choices=sorted(MODE_LABELS),
        default=os.getenv("DIGEST_MODE", "premarket"),
        help="Digest mode: premarket, intraday, or postmarket.",
    )
    parser.add_argument("--lookback-hours", type=int, default=None)
    parser.add_argument("--max-articles", type=int, default=None)
    return parser.parse_args()


def load_constituents(path: Path = CONFIG_PATH) -> tuple[list[Constituent], set[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    constituents = [
        Constituent(
            ticker=item["ticker"],
            name=item["name"],
            aliases=tuple(item.get("aliases", [])),
        )
        for item in payload["constituents"]
    ]
    return constituents, set(payload.get("heavyweights", []))


def fetch_articles(
    *,
    mode: str,
    constituents: list[Constituent],
    heavyweights: set[str],
    lookback_hours: int,
) -> list[Article]:
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    scored: list[Article] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    for source in build_sources(mode, constituents):
        parsed = feedparser.parse(source.url)
        for entry in parsed.entries:
            title = clean_text(getattr(entry, "title", ""))
            url = canonical_url(getattr(entry, "link", ""))
            if not title or not url or url in seen_urls:
                continue

            title_key = dedupe_key(title)
            if title_key in seen_titles:
                continue

            published_at = entry_datetime(entry)
            if published_at and published_at < cutoff:
                continue

            summary = clean_text(getattr(entry, "summary", "") or getattr(entry, "description", ""))[:600]
            score, matched, reasons = score_article(mode, title, summary, constituents, heavyweights)
            if score < minimum_score(mode):
                continue

            seen_urls.add(url)
            seen_titles.add(title_key)
            scored.append(
                Article(
                    title=title,
                    url=url,
                    source=source.name,
                    published_at=published_at,
                    summary=summary,
                    score=score,
                    matched=tuple(matched),
                    reasons=tuple(reasons),
                )
            )

    scored.sort(key=lambda article: (article.score, article.published_at or datetime.min.replace(tzinfo=UTC)), reverse=True)
    return scored


def build_sources(mode: str, constituents: list[Constituent]) -> list[NewsSource]:
    top_names = " OR ".join(item.name for item in constituents[:20])
    top_tickers = " OR ".join(item.ticker for item in constituents[:20])

    common = [
        NewsSource("證交所新聞", "https://www.twse.com.tw/rss/news.xml"),
        NewsSource("金管會新聞稿", "https://www.fsc.gov.tw/ch/home.jsp?id=96&parentpath=0,2&mcustomize=news_rss.jsp"),
        google_news("0050 成分股重大新聞", f"0050 OR 台灣50 OR {top_names} OR {top_tickers}"),
        google_news("公開資訊觀測站重大訊息", "公開資訊觀測站 重大訊息 OR 月營收 OR 法說會"),
        google_news("台股權值股新聞", "台股 權值股 OR 半導體 OR 金融股 OR AI伺服器"),
    ]

    if mode == "premarket":
        return [
            google_news("海外市場盤前", "美股 OR Nasdaq OR 費半 OR 美債殖利率 OR 美元指數 OR 台幣匯率"),
            google_news("台積電 ADR", "台積電 ADR OR TSMC ADR OR Taiwan Semiconductor"),
            google_news("半導體海外新聞", "semiconductor OR Nvidia OR AI server OR foundry OR CoWoS", lang="en-US", region="US"),
            *common,
        ]

    if mode == "intraday":
        return [
            google_news("0050 盤中異動", f"({top_names}) 爆量 OR 急漲 OR 急跌 OR 漲停 OR 跌停 OR 權值股"),
            google_news("台股盤中焦點", "台股 盤中 OR 權值股 OR 類股 轉強 OR 類股 轉弱"),
            *common,
        ]

    return [
        google_news("0050 盤後籌碼", f"({top_names}) 三大法人 OR 外資買超 OR 外資賣超 OR 融資 OR 融券"),
        google_news("注意處置重大訊息", "注意股 OR 處置股 OR 重大訊息 OR 月營收 OR 法說會"),
        google_news("台股盤後整理", "台股 收盤 OR 三大法人 OR 權值股 OR 成交量"),
        *common,
    ]


def google_news(name: str, query: str, *, lang: str = "zh-TW", region: str = "TW") -> NewsSource:
    encoded_query = quote_plus(f"({query}) when:1d")
    ceid_lang = "zh-Hant" if lang == "zh-TW" else lang.split("-", maxsplit=1)[0]
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl={lang}&gl={region}&ceid={region}:{ceid_lang}"
    return NewsSource(name=name, url=url)


def score_article(
    mode: str,
    title: str,
    summary: str,
    constituents: list[Constituent],
    heavyweights: set[str],
) -> tuple[int, list[str], list[str]]:
    text = f"{title}\n{summary}"
    lower_text = text.lower()
    score = 0
    matched: list[str] = []
    reasons: list[str] = []

    for item in constituents:
        if any(keyword.lower() in lower_text for keyword in item.keywords):
            label = f"{item.ticker} {item.name}"
            matched.append(label)
            score += 5
            if item.ticker in heavyweights:
                score += 5
                reasons.append(f"命中 0050 權重股 {label}")
            else:
                reasons.append(f"命中 0050 成分股 {label}")

    market_hits = hits(lower_text, MARKET_KEYWORDS)
    event_hits = hits(lower_text, EVENT_KEYWORDS)
    intraday_hits = hits(lower_text, INTRADAY_KEYWORDS)

    if market_hits:
        score += 3 * market_hits
        reasons.append("涉及海外市場、利率、匯率或總經因子")

    if event_hits:
        score += 4 * event_hits
        reasons.append("涉及公告、籌碼、法說、營收或監理事件")

    if mode == "intraday" and intraday_hits:
        score += 4 * intraday_hits
        reasons.append("符合盤中異動關鍵字")

    if mode == "premarket" and any(keyword in lower_text for keyword in ["adr", "nasdaq", "費半", "美債", "美元"]):
        score += 5
        reasons.append("符合盤前海外市場檢查項")

    if mode == "postmarket" and any(keyword in lower_text for keyword in ["三大法人", "融資", "融券", "注意股", "處置股", "月營收"]):
        score += 5
        reasons.append("符合盤後籌碼或公告檢查項")

    if len(set(matched)) >= 2:
        score += 4
        reasons.append("同時影響多檔 0050 成分股")

    if hits(lower_text, NOISE_KEYWORDS) and not event_hits:
        score -= 5

    if not matched and not market_hits and not event_hits:
        score = 0

    return score, list(dict.fromkeys(matched)), list(dict.fromkeys(reasons))


def minimum_score(mode: str) -> int:
    return {"premarket": 6, "intraday": 8, "postmarket": 8}[mode]


def hits(lower_text: str, keywords: list[str]) -> int:
    return sum(1 for keyword in keywords if keyword.lower() in lower_text)


def entry_datetime(entry: object) -> datetime | None:
    for field in ("published", "updated", "created"):
        value = getattr(entry, field, None)
        if not value:
            continue
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (TypeError, ValueError):
            continue
    return None


def clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(value or ""))
    return " ".join(text.replace("\n", " ").split())


def canonical_url(url: str) -> str:
    if "news.google.com/rss/articles/" not in url:
        return url.strip()

    parsed = urlparse(url)
    query_url = parse_qs(parsed.query).get("url", [""])[0]
    if query_url:
        return unquote(query_url)
    return url.split("?", maxsplit=1)[0]


def dedupe_key(title: str) -> str:
    normalized = re.sub(r"\s*[-|｜]\s*[^-|｜]{1,30}$", "", title.lower())
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)
    return normalized[:90]


def render_plain_text(mode: str, articles: list[Article]) -> str:
    if not articles:
        return f"這次沒有找到符合條件的 0050 成分股{MODE_LABELS[mode]}。"

    lines = [f"0050 成分股{MODE_LABELS[mode]}重點：", ""]
    for index, article in enumerate(articles, 1):
        published = article.published_at.astimezone().strftime("%Y-%m-%d %H:%M") if article.published_at else "時間未知"
        lines.extend(
            [
                f"{index}. {article.title}",
                f"重要性：{article.score}",
                f"來源：{article.source}｜時間：{published}",
                f"關聯標的：{', '.join(article.matched) if article.matched else '市場整體'}",
                f"入選原因：{'；'.join(article.reasons) if article.reasons else '符合關鍵字與時效條件'}",
                f"摘要：{article.summary or '無'}",
                f"連結：{article.url}",
                "",
            ]
        )
    return "\n".join(lines)


def render_html(mode: str, articles: list[Article]) -> str:
    if not articles:
        return f"<p>這次沒有找到符合條件的 0050 成分股{escape(MODE_LABELS[mode])}。</p>"

    items = []
    for article in articles:
        published = article.published_at.astimezone().strftime("%Y-%m-%d %H:%M") if article.published_at else "時間未知"
        matched = ", ".join(article.matched) if article.matched else "市場整體"
        reasons = "；".join(article.reasons) if article.reasons else "符合關鍵字與時效條件"
        items.append(
            f"""
            <li>
              <h3><a href="{escape(article.url)}">{escape(article.title)}</a></h3>
              <p><strong>重要性：</strong>{article.score}</p>
              <p><strong>來源：</strong>{escape(article.source)}｜<strong>時間：</strong>{escape(published)}</p>
              <p><strong>關聯標的：</strong>{escape(matched)}</p>
              <p><strong>入選原因：</strong>{escape(reasons)}</p>
              <p><strong>摘要：</strong>{escape(article.summary or "無")}</p>
            </li>
            """
        )
    return f"""
    <html>
      <body>
        <h2>0050 成分股{escape(MODE_LABELS[mode])}重點</h2>
        <ol>
          {''.join(items)}
        </ol>
      </body>
    </html>
    """


def smtp_is_configured() -> bool:
    required = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO"]
    return all(os.getenv(key) for key in required)


def send_email(*, subject: str, plain: str, html: str) -> None:
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.environ["SMTP_USERNAME"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    email_from = os.environ["EMAIL_FROM"]
    email_to = [item.strip() for item in os.environ["EMAIL_TO"].split(",") if item.strip()]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_from
    message["To"] = ", ".join(email_to)
    message.set_content(plain)
    message.add_alternative(html, subtype="html")

    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.starttls()
        smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)


if __name__ == "__main__":
    main()


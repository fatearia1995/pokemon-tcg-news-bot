from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from functools import lru_cache
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)

TW_INFO_URL = "https://asia.pokemon-card.com/tw/info/"
JP_HOME_URL = "https://www.pokemon-card.com/"
EN_PRESS_HOME_URL = "https://press.pokemon.com/en/"
EN_PRESS_SCHEDULE_URL = "https://press.pokemon.com/en/Items/Schedule/Pokemon-Trading-Card-Game?types=3"
DEFAULT_CONFIG_PATH = Path(__file__).with_name("sources.example.json")

REGION_TITLES = {
    "tw": "繁體中文版",
    "international": "國際版",
    "jp": "日文版",
}

TCG_KEYWORDS = [
    "pokemon tcg",
    "trading card game",
    "pokémon tcg",
    "elite trainer box",
    "booster",
    "prerelease",
    "play! pokémon",
    "league cup",
    "league challenge",
    "world championships",
    "pokeca",
    "ポケカ",
    "ポケモンカード",
    "拡張パック",
    "スタートデッキ",
    "カード",
    "卡牌",
    "集換式卡牌",
    "訓練家",
    "大師球聯盟賽",
    "new trainer journey",
    "pokemon center elite trainer box",
]

NEGATIVE_KEYWORDS = [
    "pocket",
    "tcg live",
]

ALERT_KEYWORDS = [
    "preorder",
    "pre-order",
    "restock",
    "in stock",
    "back up",
    "back live",
    "available now",
    "drop",
    "queue",
    "queue up",
    "should be dropping",
    "go live",
    "live now",
    "releasing",
    "release tomorrow",
    "early access",
    "invitation",
    "etb",
    "elite trainer box",
    "booster box",
    "booster bundle",
    "display box",
    "3-pack blister",
    "premium collection",
    "pokemon center",
    "target",
    "gamestop",
    "best buy",
    "walmart",
    "amazon",
    "restocks",
    "補貨",
    "預購",
    "開賣",
    "現貨",
    "排隊",
    "抽選",
    "再販",
    "予約",
    "抽選販売",
    "再入荷",
    "在庫",
]

SUMMARY_BLACKLIST = [
    "為您傳遞寶可夢集換式卡牌遊戲的規則",
    "thank you for your interest",
    "back to news",
    "Pokémon/Nintendo/Creatures/GAME FREAK",
    "©Pokémon",
    "trademarks of Nintendo",
]


@dataclass(slots=True)
class NewsItem:
    region: str
    source_type: str
    source_name: str
    title: str
    url: str
    published_at: str
    category: str | None = None
    summary: str | None = None
    account: str | None = None


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    return session


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def is_tcg_relevant(text: str) -> bool:
    lowered = text.casefold()
    if any(keyword in lowered for keyword in NEGATIVE_KEYWORDS):
        return False
    return any(keyword.casefold() in lowered for keyword in TCG_KEYWORDS)


def parse_date(date_text: str | None) -> datetime | None:
    if not date_text:
        return None

    raw = clean_text(date_text)

    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        pass

    for pattern in (
        r"(?P<y>20\d{2})[./-](?P<m>\d{1,2})[./-](?P<d>\d{1,2})",
        r"(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>20\d{2})",
    ):
        match = re.search(pattern, raw)
        if match:
            year = int(match.group("y"))
            month = int(match.group("m"))
            day = int(match.group("d"))
            return datetime(year, month, day, tzinfo=UTC)

    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue

    return None


def fetch_soup(session: requests.Session, url: str) -> BeautifulSoup:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    if "charset" not in (response.headers.get("content-type") or "").lower():
        response.encoding = response.apparent_encoding or response.encoding
    return BeautifulSoup(response.text, "html.parser")


def extract_first_paragraph(soup: BeautifulSoup) -> str:
    for container_selector in (
        "article",
        ".Section",
        ".bodytext",
        ".entry-content",
        ".post-content",
        ".info-column-item-detail",
        ".single",
        "main",
    ):
        container = soup.select_one(container_selector)
        if not container:
            continue
        for paragraph in container.find_all("p"):
            text = clean_text(paragraph.get_text(" ", strip=True))
            if len(text) < 30:
                continue
            lowered = text.casefold()
            if any(blocked.casefold() in lowered for blocked in SUMMARY_BLACKLIST):
                continue
            return text

    for paragraph in soup.find_all("p"):
        text = clean_text(paragraph.get_text(" ", strip=True))
        lowered = text.casefold()
        if len(text) >= 30 and not any(blocked.casefold() in lowered for blocked in SUMMARY_BLACKLIST):
            return text
    return ""


def truncate(text: str, limit: int = 180) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def item_text(item: NewsItem) -> str:
    return " ".join(
        part for part in [item.title, item.summary or "", item.source_name, item.account or ""] if part
    )


def is_alert_item(item: NewsItem) -> bool:
    score = alert_score(item)
    if item.source_type == "x":
        return score >= 20
    return item.category == "Product Schedule" or score >= 35


def is_low_signal_x_item(item: NewsItem) -> bool:
    lowered = item.title.casefold()
    return lowered.startswith("pinned:")


def alert_score(item: NewsItem) -> int:
    text = item_text(item).casefold()
    score = 0

    if any(keyword in text for keyword in ("preorder", "pre-order", "預購", "予約")):
        score += 30
    if any(keyword in text for keyword in ("restock", "restocks", "補貨", "再入荷", "在庫")):
        score += 25
    if any(keyword in text for keyword in ("pokemon center", "target", "gamestop", "best buy", "walmart", "amazon")):
        score += 15
    if any(keyword in text for keyword in ("etb", "elite trainer box", "booster box", "booster bundle", "display box")):
        score += 15
    if any(keyword in text for keyword in ("queue", "go live", "live now", "back up", "available now", "drop")):
        score += 10

    return score


def summarize_for_alert(item: NewsItem) -> str:
    if item.summary:
        return truncate(item.summary, 110)

    text = clean_text(item.title)
    return truncate(text, 110)


def display_title(item: NewsItem, translate: bool = False, limit: int = 120) -> str:
    title = clean_text(item.title)
    if not translate or item.region == "tw":
        return truncate(title, limit)

    try:
        return truncate(translate_text(title), limit)
    except Exception:
        return truncate(title, limit)


@lru_cache(maxsize=256)
def translate_text(text: str, target_lang: str = "zh-TW") -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""

    response = requests.get(
        "https://translate.googleapis.com/translate_a/single",
        params={
            "client": "gtx",
            "sl": "auto",
            "tl": target_lang,
            "dt": "t",
            "q": cleaned,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    translated_parts = []
    for chunk in payload[0]:
        if chunk and chunk[0]:
            translated_parts.append(chunk[0])
    return "".join(translated_parts).strip() or cleaned


def maybe_translate_summary(summary: str | None, region: str, translate_enabled: bool) -> str | None:
    if not summary:
        return summary
    if not translate_enabled or region == "tw":
        return summary

    try:
        return truncate(translate_text(summary))
    except Exception:
        return summary


def unique_by_key(items: Iterable[NewsItem]) -> list[NewsItem]:
    seen: set[tuple[str, str]] = set()
    unique_items: list[NewsItem] = []
    for item in items:
        key = (item.region, item.url or item.title)
        if key in seen:
            continue
        seen.add(key)
        unique_items.append(item)
    return unique_items


def fetch_tw_items(session: requests.Session, limit: int) -> list[NewsItem]:
    soup = fetch_soup(session, TW_INFO_URL)
    items: list[NewsItem] = []
    for anchor in soup.select("div.info-column-list.news-list a.info-column-item")[:limit]:
        title = clean_text(anchor.select_one(".info-column-item-title").get_text(" ", strip=True))
        category_node = anchor.select_one(".category")
        category = clean_text(category_node.get_text(" ", strip=True)) if category_node else None
        url = urljoin(TW_INFO_URL, anchor.get("href", ""))
        detail_soup = fetch_soup(session, url)
        detail_html = str(detail_soup)
        date_match = re.search(r"(20\d{2}\.\d{1,2}\.\d{1,2})", detail_html)
        published = parse_date(date_match.group(1) if date_match else None)
        summary = extract_first_paragraph(detail_soup)
        items.append(
            NewsItem(
                region="tw",
                source_type="official_site",
                source_name="寶可夢卡牌訓練家網站（台灣）",
                title=title,
                url=url,
                published_at=(published or datetime.now(UTC)).isoformat(),
                category=category,
                summary=truncate(summary),
            )
        )
    return items


def fetch_jp_items(session: requests.Session, limit: int) -> list[NewsItem]:
    soup = fetch_soup(session, JP_HOME_URL)
    candidates: list[tuple[str, str]] = []
    seen_urls: set[str] = set()

    for anchor in soup.select('a[href^="/info/"]'):
        raw_href = (anchor.get("href", "") or "").strip()
        href = urljoin(JP_HOME_URL, raw_href.split()[0] if raw_href else "")
        if href in seen_urls:
            continue
        seen_urls.add(href)

        title = clean_text(anchor.get_text(" ", strip=True))
        if not title:
            image = anchor.find("img", alt=True)
            if image:
                title = clean_text(image.get("alt"))

        if len(title) < 6:
            continue

        date_match = re.search(r"(20\d{2}\.\d{1,2}\.\d{1,2})", anchor.get_text(" ", strip=True))
        candidates.append((title, href, date_match.group(1) if date_match else ""))
        if len(candidates) >= limit:
            break

    items: list[NewsItem] = []
    for title, url, date_text in candidates:
        detail_soup = fetch_soup(session, url)
        summary = extract_first_paragraph(detail_soup)
        items.append(
            NewsItem(
                region="jp",
                source_type="official_site",
                source_name="ポケモンカードゲーム公式サイト",
                title=re.sub(r"\s+(イベント|商品|キャンペーン|コラム|その他)\s+20\d{2}\.\d{1,2}\.\d{1,2}$", "", title),
                url=url,
                published_at=(parse_date(date_text) or datetime.now(UTC)).isoformat(),
                category="公式サイト",
                summary=truncate(summary),
            )
        )
    return items


def fetch_en_press_schedule_items(session: requests.Session, limit: int) -> list[NewsItem]:
    soup = fetch_soup(session, EN_PRESS_SCHEDULE_URL)
    rows = soup.select("table.tableSchedule tr")
    items: list[NewsItem] = []

    for row in rows:
        product_link = row.select_one("a.prod-name")
        date_node = row.select_one("td.td-date")
        if not product_link or not date_node:
            continue

        title = clean_text(product_link.get_text(" ", strip=True))
        if not is_tcg_relevant(title):
            continue

        published = parse_date(date_node.get_text(" ", strip=True))
        if not published:
            continue
        url = urljoin(EN_PRESS_SCHEDULE_URL, product_link.get("href", ""))
        release_text = clean_text(date_node.get_text(" ", strip=True))
        items.append(
            NewsItem(
                region="international",
                source_type="official_site",
                source_name="The Pokémon Company Press Site",
                title=title,
                url=url,
                published_at=(published or datetime.now(UTC)).isoformat(),
                category="Product Schedule",
                summary=f"官方產品時程表列出的發售日期為 {release_text}。",
            )
        )
        if len(items) >= limit:
            break

    return items


def fetch_en_press_release_items(session: requests.Session, limit: int) -> list[NewsItem]:
    home_soup = fetch_soup(session, EN_PRESS_HOME_URL)
    items: list[NewsItem] = []
    for node in home_soup.select("div.newsItem.media"):
        headline = node.select_one("div.headline a")
        if not headline:
            continue
        raw_href = headline.get("href", "")
        if not raw_href.startswith("/en/releases/"):
            continue

        title = clean_text(headline.get_text(" ", strip=True))
        if not is_tcg_relevant(title):
            continue

        href = urljoin(EN_PRESS_HOME_URL, raw_href)
        spans = [clean_text(span.get_text(" ", strip=True)) for span in node.select("span")]
        published = parse_date(spans[0] if spans else None)
        intro = node.select_one("div.intro")
        summary = truncate(clean_text(intro.get_text(" ", strip=True)) if intro else "")

        items.append(
            NewsItem(
                region="international",
                source_type="official_site",
                source_name="The Pokémon Company Press Site",
                title=title,
                url=href,
                published_at=(published or datetime.now(UTC)).isoformat(),
                category="Press Release",
                summary=summary,
            )
        )
        if len(items) >= limit:
            break

    return items


def convert_nitter_link_to_x(link: str) -> str:
    match = re.search(r"https://nitter\.net/([^/]+)/status/(\d+)", link)
    if not match:
        return link
    handle = match.group(1)
    status_id = match.group(2)
    return f"https://x.com/{handle}/status/{status_id}"


def load_config(path: Path) -> dict:
    if not path.exists():
        return {"x_accounts": []}
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_x_items(session: requests.Session, config: dict, per_account_limit: int) -> list[NewsItem]:
    items: list[NewsItem] = []
    for account in config.get("x_accounts", []):
        handle = account["handle"]
        rss_url = f"https://nitter.net/{handle}/rss"
        response = session.get(rss_url, timeout=30)
        if response.status_code != 200 or "xml" not in (response.headers.get("content-type") or ""):
            continue

        root = ET.fromstring(response.content)
        for item_node in root.findall("./channel/item")[:per_account_limit]:
            title = clean_text(item_node.findtext("title"))
            description = clean_text(item_node.findtext("description"))
            if title.casefold().startswith("pinned:"):
                continue
            combined = f"{title} {description}"
            if not is_tcg_relevant(combined):
                continue

            published = parse_date(item_node.findtext("pubDate"))
            link = convert_nitter_link_to_x(item_node.findtext("link") or "")
            items.append(
                NewsItem(
                    region=account["region"],
                    source_type="x",
                    source_name=account["source_name"],
                    title=truncate(title, 140),
                    url=link,
                    published_at=(published or datetime.now(UTC)).isoformat(),
                    category="X",
                    summary=truncate(description),
                    account=f"@{handle}",
                )
            )

    return items


def filter_recent(items: Iterable[NewsItem], days: int) -> list[NewsItem]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    recent_items: list[NewsItem] = []
    for item in items:
        try:
            published = datetime.fromisoformat(item.published_at)
        except ValueError:
            published = datetime.now(UTC)
        if published >= cutoff:
            recent_items.append(item)
    return recent_items


def sort_items(items: Iterable[NewsItem]) -> list[NewsItem]:
    return sorted(items, key=lambda item: item.published_at, reverse=True)


def limit_items_per_region(items: list[NewsItem], per_region: int) -> list[NewsItem]:
    grouped: dict[str, list[NewsItem]] = {key: [] for key in REGION_TITLES}
    for item in items:
        grouped.setdefault(item.region, []).append(item)

    limited: list[NewsItem] = []
    for region in REGION_TITLES:
        region_items = grouped.get(region, [])
        prioritized = sorted(
            region_items,
            key=lambda item: (
                1 if is_alert_item(item) else 0,
                1 if item.source_type == "x" else 0,
                alert_score(item),
                item.published_at,
            ),
            reverse=True,
        )
        limited.extend(prioritized[:per_region])

    return sort_items(limited)


def format_markdown(items: list[NewsItem], days: int) -> str:
    report_date = datetime.now().astimezone().strftime("%Y-%m-%d")
    grouped: dict[str, list[NewsItem]] = {key: [] for key in REGION_TITLES}
    for item in items:
        grouped.setdefault(item.region, []).append(item)
    alert_items = sorted(
        [item for item in items if is_alert_item(item) and not is_low_signal_x_item(item)],
        key=lambda item: (alert_score(item), item.published_at),
        reverse=True,
    )[:8]

    lines = [
        f"# 寶可夢實體卡最新消息彙整（{report_date}）",
        "",
        f"- 生成時間：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}",
        f"- 觀察區間：最近 {days} 天",
        "- 說明：繁中目前未納入官方 X 帳號，因未找到穩定、公開且可長期抓取的繁中卡牌官方 X 來源。",
        "",
    ]

    lines.append("## 預購／補貨快訊")
    lines.append("")
    if alert_items:
        for item in alert_items:
            published = datetime.fromisoformat(item.published_at).astimezone().strftime("%Y-%m-%d")
            source_suffix = f" {item.account}" if item.account else ""
            lines.append(f"- {published}｜{item.source_name}{source_suffix}｜{item.title}")
            lines.append(f"  快訊：{summarize_for_alert(item)}")
            lines.append(f"  來源：{item.url}")
        lines.append("")
    else:
        lines.extend(["目前沒有抓到明確的預購或補貨快訊。", ""])

    for region, title in REGION_TITLES.items():
        region_items = grouped.get(region, [])
        lines.append(f"## {title}")
        if not region_items:
            lines.extend(["", "目前沒有符合條件的新項目。", ""])
            continue

        lines.append("")
        for item in region_items:
            published = datetime.fromisoformat(item.published_at).astimezone().strftime("%Y-%m-%d")
            source_suffix = f" {item.account}" if item.account else ""
            lines.append(
                f"- {published}｜{item.source_name}{source_suffix}｜{item.title}"
            )
            if item.summary:
                lines.append(f"  摘要：{item.summary}")
            lines.append(f"  來源：{item.url}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def format_json(items: list[NewsItem]) -> str:
    return json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2) + "\n"


def build_telegram_message(items: list[NewsItem]) -> str:
    grouped: dict[str, list[NewsItem]] = {key: [] for key in REGION_TITLES}
    for item in items:
        grouped.setdefault(item.region, []).append(item)
    alert_items = sorted(
        [item for item in items if is_alert_item(item) and not is_low_signal_x_item(item)],
        key=lambda item: (alert_score(item), item.published_at),
        reverse=True,
    )[:6]
    official_items = [
        item
        for item in items
        if item.source_type != "x" and not is_alert_item(item)
    ][:3]

    lines = [
        f"寶可夢實體卡最新消息摘要（{datetime.now().astimezone().strftime('%Y-%m-%d')}）",
        "這次推送已優先整理預購／補貨／卡盒開賣快訊。",
        "",
    ]

    lines.append("預購／補貨雷達：")
    if alert_items:
        for index, item in enumerate(alert_items, start=1):
            source_suffix = f" {item.account}" if item.account else ""
            lines.append(f"{index}. {display_title(item, translate=True)}")
            lines.append(f"   來源：{item.source_name}{source_suffix}")
            lines.append(f"   重點：{summarize_for_alert(item)}")
            lines.append(f"   連結：{item.url}")
    else:
        lines.append("- 目前沒有抓到明確的預購或補貨快訊。")
    lines.append("")

    if official_items:
        lines.append("官方補充：")
        for item in official_items:
            lines.append(f"- {display_title(item, translate=True, limit=90)}")
            if item.summary:
                lines.append(f"  {truncate(item.summary, 90)}")
            lines.append(f"  {item.url}")
        lines.append("")

    return "\n".join(lines).strip()


def split_telegram_text(text: str, limit: int = 3500) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.splitlines():
        extra = len(line) + 1
        if current and current_len + extra > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = extra
        else:
            current.append(line)
            current_len += extra

    if current:
        chunks.append("\n".join(current))
    return chunks


def send_to_telegram(report_path: Path, items: list[NewsItem]) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 環境變數。")

    api_base = f"https://api.telegram.org/bot{token}"
    summary_text = build_telegram_message(items)

    for chunk in split_telegram_text(summary_text):
        response = requests.post(
            f"{api_base}/sendMessage",
            data={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": "true"},
            timeout=30,
        )
        response.raise_for_status()

    with report_path.open("rb") as file_obj:
        response = requests.post(
            f"{api_base}/sendDocument",
            data={
                "chat_id": chat_id,
                "caption": f"寶可夢實體卡最新消息完整報告（{report_path.stem}）",
            },
            files={"document": (report_path.name, file_obj, "text/markdown")},
            timeout=60,
        )
        response.raise_for_status()


def collect_items(args: argparse.Namespace) -> list[NewsItem]:
    session = build_session()
    config = load_config(Path(args.config))
    collected: list[NewsItem] = []
    errors: list[str] = []

    collectors = [
        ("tw_official", lambda: fetch_tw_items(session, args.site_limit)),
        ("jp_official", lambda: fetch_jp_items(session, args.site_limit)),
        ("en_schedule", lambda: fetch_en_press_schedule_items(session, args.site_limit)),
        ("en_press", lambda: fetch_en_press_release_items(session, args.site_limit)),
    ]

    if not args.no_x:
        collectors.append(("x", lambda: fetch_x_items(session, config, args.x_limit)))

    for name, collector in collectors:
        try:
            collected.extend(collector())
        except Exception as exc:  # pragma: no cover - best effort collector
            errors.append(f"{name}: {exc}")

    items = unique_by_key(collected)
    items = filter_recent(items, args.days)
    items = [
        NewsItem(
            region=item.region,
            source_type=item.source_type,
            source_name=item.source_name,
            title=item.title,
            url=item.url,
            published_at=item.published_at,
            category=item.category,
            summary=maybe_translate_summary(item.summary, item.region, not args.no_translate),
            account=item.account,
        )
        for item in items
    ]
    items = sort_items(items)

    if args.per_region:
        items = limit_items_per_region(items, args.per_region)

    if errors:
        print("部分來源抓取失敗：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)

    return items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抓取寶可夢實體卡相關的官方網站與 X 來源，輸出最新消息摘要。"
    )
    parser.add_argument("--days", type=int, default=45, help="只保留最近幾天的消息，預設 45。")
    parser.add_argument("--per-region", type=int, default=5, help="每個區域保留幾則，預設 5。")
    parser.add_argument("--site-limit", type=int, default=8, help="每個官方網站來源最多抓幾則候選。")
    parser.add_argument("--x-limit", type=int, default=8, help="每個 X 帳號最多抓幾則候選。")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", type=str, default="", help="輸出檔案路徑。未指定時輸出到 stdout。")
    parser.add_argument("--config", type=str, default=str(DEFAULT_CONFIG_PATH), help="來源設定 JSON 檔。")
    parser.add_argument("--no-x", action="store_true", help="不抓取 X 來源。")
    parser.add_argument("--no-translate", action="store_true", help="不要把非繁中摘要翻譯成繁體中文。")
    parser.add_argument("--telegram", action="store_true", help="輸出完成後，把摘要與完整報告發送到 Telegram。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    items = collect_items(args)
    output = format_markdown(items, args.days) if args.format == "markdown" else format_json(items)

    if args.output:
        output_path = Path(args.output)
    else:
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        extension = "md" if args.format == "markdown" else "json"
        output_path = Path("outputs") / f"pokemon_tcg_news_{today}.{extension}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8-sig")

    if args.telegram:
        send_to_telegram(output_path, items)

    print(f"已輸出：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Belarus News Scraper — учебный парсер заголовков новостей Onlíner.

Собирает заголовки, ссылки и даты публикаций из трёх разделов Onlíner
и сохраняет их в data/news.csv. Тексты статей не загружаются.
"""

from __future__ import annotations

import csv
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

MINSK_TZ = timezone(timedelta(hours=3), "Europe/Minsk")
MAX_PER_CATEGORY = 30
MAX_EXTRA_PAGES = 2  # сколько раз максимум нажимаем «Загрузить ещё»
REQUEST_TIMEOUT = 20
REQUEST_DELAY = 1.5  # пауза между запросами, секунды
USER_AGENT = (
    "Mozilla/5.0 (compatible; BelarusNewsScraper/1.0; educational project; "
    "+https://github.com/pavelgpt8888-sys/Belarus-News-Scraper)"
)

OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "news.csv"
CSV_FIELDS = ["category", "source", "title", "url", "published_at", "collected_at"]

# Путь статьи на Onlíner: /ГГГГ/ММ/ДД/slug
ARTICLE_PATH_RE = re.compile(r"^/(\d{4})/(\d{2})/(\d{2})/[^/]+$")


@dataclass(frozen=True)
class Source:
    key: str
    url: str
    category: str


SOURCES = [
    Source("tech", "https://tech.onliner.by/", "Технологии"),
    Source("money", "https://money.onliner.by/", "Экономика и деньги"),
    Source("people", "https://people.onliner.by/", "Общество"),
]


class ScrapeError(Exception):
    """Источник недоступен или вернул страницу без новостей."""


def normalize_url(href: str, base_url: str) -> str | None:
    """Абсолютная ссылка на статью того же раздела без query/fragment, иначе None."""
    absolute = urljoin(base_url, href.strip())
    parts = urlsplit(absolute)
    base_host = urlsplit(base_url).netloc
    if parts.scheme not in ("http", "https") or parts.netloc != base_host:
        return None
    path = parts.path.rstrip("/")
    if not ARTICLE_PATH_RE.match(path):
        return None
    return urlunsplit(("https", parts.netloc, path, "", ""))


def date_from_url(url: str) -> str:
    match = ARTICLE_PATH_RE.match(urlsplit(url).path)
    if not match:
        return ""
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return ""


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_cards(html: str, base_url: str) -> list[dict]:
    """Извлекает карточки новостей раздела: ленту и крупные плитки.

    Меню портала (b-main-navigation), реклама и ссылки на комментарии
    отбрасываются: берутся только ссылки внутри карточек news-tiles/news-tidings,
    ведущие на статьи этого же поддомена.
    """
    soup = BeautifulSoup(html, "lxml")
    items: list[dict] = []

    # Лента новостей: заголовок, ссылка и точное время публикации.
    for card in soup.select(".news-tidings__item"):
        link = card.select_one("a.news-tidings__link[href]") or card.select_one(
            "a.news-tidings__stub[href]"
        )
        if not link:
            continue
        url = normalize_url(link["href"], base_url)
        # Заголовок дублируется в двух span (десктоп/мобильная версия) — берём один.
        title_el = link.select_one("span") or link
        title = clean_text(title_el.get_text(" "))
        if not url or not title:
            continue
        published = date_from_url(url)
        timestamp = card.get("data-post-date", "")
        if timestamp.isdigit():
            published = datetime.fromtimestamp(int(timestamp), MINSK_TZ).isoformat(
                timespec="minutes"
            )
        items.append({"title": title, "url": url, "published_at": published})

    # Крупные плитки (подборка редакции): точного времени нет, дата берётся из ссылки.
    # Идут после ленты и используются, если свежих новостей меньше лимита.
    for card in soup.select(".news-tiles__item"):
        link = card.select_one("a.news-tiles__stub[href]")
        title_el = card.select_one(".news-tiles__subtitle")
        if not link or not title_el:
            continue
        url = normalize_url(link["href"], base_url)
        title = clean_text(title_el.get_text(" "))
        if url and title:
            items.append({"title": title, "url": url, "published_at": date_from_url(url)})

    return items


def has_time(card: dict) -> bool:
    return "T" in card["published_at"]


def last_post_timestamp(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    stamps = [
        el["data-post-date"]
        for el in soup.select(".news-tidings__item[data-post-date]")
        if el["data-post-date"].isdigit()
    ]
    return min(stamps, key=int) if stamps else None


def fetch(session: requests.Session, url: str, **kwargs) -> requests.Response:
    """GET с одной повторной попыткой при сетевой ошибке или ошибке сервера 5xx.

    На 403/429 не повторяем: это сигнал ограничения доступа, обходить его нельзя.
    """
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            last_error = exc
        else:
            if response.status_code < 500:
                break
            last_error = ScrapeError(f"HTTP {response.status_code}")
        if attempt == 0:
            time.sleep(5)
    else:
        raise ScrapeError(f"{url}: сайт недоступен ({last_error})")

    if response.status_code != 200:
        raise ScrapeError(f"{url}: HTTP {response.status_code} — доступ ограничен или страница не найдена")
    return response


def scrape_source(session: requests.Session, source: Source) -> list[dict]:
    response = fetch(session, source.url)
    html = response.text
    if "captcha" in html.lower()[:5000] and "news-tidings" not in html:
        raise ScrapeError(f"{source.url}: получена страница проверки (CAPTCHA), сбор остановлен")

    cards = parse_cards(html, source.url)
    if not cards:
        raise ScrapeError(
            f"{source.url}: на странице не найдено ни одной новости — "
            "возможно, изменилась вёрстка или получена страница-заглушка"
        )

    # Подгрузка следующих новостей так же, как кнопка «Загрузить ещё» на сайте.
    from_date = last_post_timestamp(html)
    for _ in range(MAX_EXTRA_PAGES):
        feed_urls = {c["url"] for c in cards if has_time(c)}
        if len(feed_urls) >= MAX_PER_CATEGORY or not from_date:
            break
        time.sleep(REQUEST_DELAY)
        try:
            more = fetch(
                session,
                source.url,
                params={"fromDate": from_date},
                headers={"X-Requested-With": "XMLHttpRequest"},
            ).json()
        except (ScrapeError, ValueError) as exc:
            print(f"  [{source.key}] не удалось загрузить дополнительные новости: {exc}")
            break
        body = more.get("body") or ""
        cards.extend(parse_cards(body, source.url))
        from_date = last_post_timestamp(body)
        if not more.get("isShowMoreAvailable"):
            break

    # Сначала лента с точным временем, затем плитки (sorted сохраняет порядок внутри групп).
    return sorted(cards, key=lambda c: not has_time(c))


def build_rows(source: Source, cards: list[dict], collected_at: str) -> list[dict]:
    rows, seen = [], set()
    for card in cards:
        if card["url"] in seen:
            continue
        seen.add(card["url"])
        rows.append(
            {
                "category": source.category,
                "source": f"Onlíner ({urlsplit(source.url).netloc})",
                "title": card["title"],
                "url": card["url"],
                "published_at": card["published_at"],
                "collected_at": collected_at,
            }
        )
        if len(rows) >= MAX_PER_CATEGORY:
            break
    return rows


def read_previous_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_atomic(path: Path, rows: list[dict]) -> None:
    """Пишет во временный файл и затем заменяет: при сбое старый CSV не повреждается."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def warn(message: str) -> None:
    print(f"ОШИБКА: {message}", file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning::{message}")


def main() -> int:
    collected_at = datetime.now(MINSK_TZ).isoformat(timespec="seconds")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9"})

    previous = read_previous_rows(OUTPUT_PATH)
    all_rows: list[dict] = []
    failed: list[str] = []

    for i, source in enumerate(SOURCES):
        if i:
            time.sleep(REQUEST_DELAY)
        print(f"[{source.key}] {source.url}")
        try:
            rows = build_rows(source, scrape_source(session, source), collected_at)
        except ScrapeError as exc:
            failed.append(source.key)
            warn(str(exc))
            # Сохраняем прошлые строки этой категории, чтобы не терять данные.
            kept = [r for r in previous if r.get("category") == source.category]
            if kept:
                print(f"  оставлены прежние данные категории: {len(kept)} строк")
            all_rows.extend(kept)
            continue
        print(f"  собрано новостей: {len(rows)}")
        all_rows.extend(rows)

    if len(failed) == len(SOURCES):
        warn("ни один источник не дал новостей — data/news.csv не изменён")
        return 1

    write_csv_atomic(OUTPUT_PATH, all_rows)
    print(f"Сохранено {len(all_rows)} строк в {OUTPUT_PATH.relative_to(OUTPUT_PATH.parent.parent)}")
    if failed:
        print(f"Источники с ошибками: {', '.join(failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

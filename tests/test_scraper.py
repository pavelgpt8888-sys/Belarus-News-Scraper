"""Офлайн-тесты парсера на небольшом искусственном HTML (без запросов к сайту)."""

import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scraper

BASE = "https://tech.onliner.by/"

PAGE = """
<html><body>
<div class="b-main-navigation__dropdown-news-item">
  <a class="b-main-navigation__dropdown-news-link" href="https://tech.onliner.by/2026/10/12/menu-item">Меню</a>
</div>
<div class="news-tiles__item">
  <a class="news-tiles__stub" href="https://tech.onliner.by/2026/10/10/tile-story"></a>
  <div class="news-tiles__subtitle">Плитка редакции</div>
</div>
<div class="news-tiles__item">
  <a class="news-tiles__stub" href="https://people.onliner.by/2026/10/10/other-section"></a>
  <div class="news-tiles__subtitle">Чужой раздел</div>
</div>
<div class="news-tidings__item" data-post-date="1791816606">
  <a class="news-tidings__stub" href="/2026/10/12/oneplus-16-2"></a>
  <div class="news-tidings__subtitle">
    <a class="news-tidings__link" href="/2026/10/12/oneplus-16-2/?utm_source=x#top">
      <span>OnePlus  представила
        смартфон</span><span>OnePlus представила смартфон</span>
    </a>
  </div>
  <a class="news-tidings__comment" href="https://tech.onliner.by/comments/2026/10/12/oneplus-16-2#comments">4</a>
</div>
<div class="news-tidings__item" data-post-date="1791813004">
  <div class="news-tidings__subtitle">
    <a class="news-tidings__link" href="https://tech.onliner.by/2026/10/12/oneplus-16-2"><span>Дубль</span></a>
  </div>
</div>
</body></html>
"""

SOURCE = scraper.Source("tech", BASE, "Технологии")


class ParseTests(unittest.TestCase):
    def test_extracts_only_section_news(self):
        cards = scraper.parse_cards(PAGE, BASE)
        urls = [c["url"] for c in cards]
        self.assertNotIn("https://tech.onliner.by/2026/10/12/menu-item", urls)
        self.assertNotIn("https://people.onliner.by/2026/10/10/other-section", urls)
        self.assertIn("https://tech.onliner.by/2026/10/10/tile-story", urls)

    def test_normalizes_url_title_and_date(self):
        card = scraper.parse_cards(PAGE, BASE)[0]
        self.assertEqual(card["url"], "https://tech.onliner.by/2026/10/12/oneplus-16-2")
        self.assertEqual(card["title"], "OnePlus представила смартфон")
        self.assertEqual(card["published_at"], "2026-10-12T17:50+03:00")

    def test_tile_date_from_url(self):
        tile = [c for c in scraper.parse_cards(PAGE, BASE) if "tile-story" in c["url"]][0]
        self.assertEqual(tile["published_at"], "2026-10-10")

    def test_build_rows_deduplicates_and_limits(self):
        cards = scraper.parse_cards(PAGE, BASE)
        rows = scraper.build_rows(SOURCE, cards, "2026-10-13T09:17:00+03:00")
        self.assertEqual(len(rows), 2)
        many = [{"title": f"t{i}", "url": f"{BASE}2026/10/12/n{i}", "published_at": ""} for i in range(50)]
        self.assertEqual(len(scraper.build_rows(SOURCE, many, "x")), scraper.MAX_PER_CATEGORY)


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data" / "news.csv"
        patches = [
            mock.patch.object(scraper, "OUTPUT_PATH", self.path),
            mock.patch.object(scraper, "REQUEST_DELAY", 0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _fake_scrape(self, failing):
        def fake(session, source):
            if source.key in failing:
                raise scraper.ScrapeError(f"{source.url}: HTTP 503")
            return [{"title": "Новость", "url": f"{source.url}2026/10/12/a", "published_at": ""}]
        return fake

    def _rows(self):
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    def test_one_failed_source_does_not_stop_others(self):
        with mock.patch.object(scraper, "scrape_source", self._fake_scrape({"money"})):
            self.assertEqual(scraper.main(), 0)
        self.assertEqual({r["category"] for r in self._rows()}, {"Технологии", "Общество"})
        self.assertTrue(self.path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_failed_source_keeps_previous_rows(self):
        with mock.patch.object(scraper, "scrape_source", self._fake_scrape(set())):
            scraper.main()
        with mock.patch.object(scraper, "scrape_source", self._fake_scrape({"money"})):
            scraper.main()
        self.assertIn("Экономика и деньги", {r["category"] for r in self._rows()})

    def test_all_failed_does_not_overwrite_csv(self):
        with mock.patch.object(scraper, "scrape_source", self._fake_scrape(set())):
            scraper.main()
        before = self.path.read_bytes()
        with mock.patch.object(scraper, "scrape_source", self._fake_scrape({"tech", "money", "people"})):
            self.assertEqual(scraper.main(), 1)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

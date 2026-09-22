from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from time import sleep
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import alerts
import certifi
import ssl


SEARCH_URL = "https://www.linkedin.com/jobs/search/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def _classes(attrs):
    return set((dict(attrs).get("class") or "").split())


class _CardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.card = None
        self.field = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        classes = _classes(attrs)
        if "base-search-card" in classes:
            if self.card:
                self._finish()
            self.card = {"title": "", "company": "", "location": "", "url": ""}
        if not self.card:
            return
        if "base-card__full-link" in classes:
            href = dict(attrs).get("href", "")
            self.card["url"] = unescape(href or "").split("?")[0]
        self.field = next((name for name, marker in (
            ("title", "base-search-card__title"),
            ("company", "base-search-card__subtitle"),
            ("location", "job-search-card__location"),
        ) if marker in classes), self.field)
        if self.field:
            self.text = []

    def handle_data(self, data):
        if self.card and self.field:
            self.text.append(data)

    def handle_endtag(self, tag):
        if self.card and self.field and tag in {"span", "a", "h3", "h4", "div"}:
            value = " ".join("".join(self.text).split())
            if value:
                self.card[self.field] = value
            self.field = None

    def _finish(self):
        if self.card and self.card["title"] and self.card["company"] and self.card["url"]:
            self.cards.append(self.card)
        self.card = None
        self.field = None
        self.text = []

    def close(self):
        super().close()
        self._finish()


def parse_search_results(markup: str) -> list[alerts.Job]:
    parser = _CardParser()
    parser.feed(markup)
    parser.close()
    jobs = []
    for card in parser.cards:
        match = alerts.JOB_ID_RE.search(card["url"])
        jobs.append(alerts.Job(
            title=card["title"],
            company=card["company"],
            location=card["location"],
            job_id=match.group(1) if match else "",
        ))
    return jobs


def fetch_jobs(query: str, location: str = "", pages: int = 1, delay: float = 1.0) -> list[alerts.Job]:
    if pages < 1:
        raise ValueError("pages must be at least 1")
    jobs = []
    for page in range(pages):
        params = {"keywords": query, "start": page * 25}
        if location:
            params["location"] = location
        request = Request(
            f"{SEARCH_URL}?{urlencode(params)}",
            headers={"User-Agent": USER_AGENT},
        )
        with urlopen(request, timeout=20, context=TLS_CONTEXT) as response:
            jobs.extend(parse_search_results(response.read().decode("utf-8", errors="replace")))
        if page < pages - 1 and delay:
            sleep(delay)
    return jobs
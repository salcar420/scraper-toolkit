"""
scraper-toolkit — a config-driven web scraper.

Point it at a YAML config describing a site (URL, item selector, fields) and it
produces clean CSV / JSON / Excel. Supports two fetch modes:

  - http     : fast, for server-rendered sites (requests + BeautifulSoup)
  - browser  : for JavaScript-rendered sites (Playwright, headless Chromium)

The whole point: a new client job becomes a 5-minute YAML file, not a new script.

Usage:
    python scrape.py configs/books.yaml
    python scrape.py configs/quotes-js.yaml --max-pages 3
    python scrape.py configs/books.yaml --format excel --out data/books

Config schema (see configs/*.yaml):
    name: books
    mode: http                       # http | browser
    start_url: https://.../page-1.html
    pagination:
        type: url_pattern            # url_pattern | next_link | none
        pattern: https://.../page-{n}.html
        start: 1
    wait_for: "article.product_pod"  # (browser mode) selector to await
    item_selector: "article.product_pod"
    fields:
        title:  { selector: "h3 a", attr: title }
        price:  { selector: ".price_color" }            # text by default
        rating: { selector: "p.star-rating", attr: class }
        url:    { selector: "h3 a", attr: href, absolute: true }
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("toolkit")


# --------------------------------------------------------------------------- #
# Field extraction (shared by both fetch modes, works on a BeautifulSoup node) #
# --------------------------------------------------------------------------- #
def extract_field(node, spec: dict, base_url: str):
    """Pull one field from an item node according to its config spec."""
    selector = spec.get("selector")
    target = node.select_one(selector) if selector else node
    if target is None:
        return None

    attr = spec.get("attr")
    if attr == "text" or attr is None:
        value = target.get_text(strip=True)
    elif attr == "class":
        value = " ".join(target.get("class", []))
    else:
        value = target.get(attr, "")

    if spec.get("absolute") and value:
        value = urljoin(base_url, value)
    return value


def parse_items(html: str, cfg: dict, page_url: str) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    items = []
    for node in soup.select(cfg["item_selector"]):
        row = {name: extract_field(node, spec, page_url) for name, spec in cfg["fields"].items()}
        items.append(row)
    return items


# --------------------------------------------------------------------------- #
# Fetch modes                                                                  #
# --------------------------------------------------------------------------- #
def make_session():
    import requests

    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return s


def fetch_http(session, url: str, retries: int = 3) -> str | None:
    import requests

    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.text
        except requests.RequestException as exc:
            wait = 2 ** attempt
            log.warning("http fetch failed (%s/%s) %s — retry in %ss: %s", attempt, retries, url, wait, exc)
            time.sleep(wait)
    return None


def scrape_browser(cfg: dict, max_pages: int | None, delay: float, headful: bool) -> list[dict]:
    """JS-rendered sites via Playwright. Handles url_pattern and next_link pagination."""
    from playwright.sync_api import sync_playwright

    pag = cfg.get("pagination", {"type": "none"})
    wait_for = cfg.get("wait_for", cfg["item_selector"])
    rows: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        page = browser.new_page()
        url = cfg["start_url"]
        n = pag.get("start", 1)
        pages_done = 0

        while url:
            if max_pages is not None and pages_done >= max_pages:
                break
            try:
                page.goto(url, timeout=30000)
                page.wait_for_selector(wait_for, timeout=10000)
            except Exception as exc:  # noqa: BLE001
                log.warning("browser page failed %s: %s", url, exc)
                break

            items = parse_items(page.content(), cfg, url)
            if not items:
                break
            rows.extend(items)
            pages_done += 1
            log.info("page %-3s -> %s items (total %s)", pages_done, len(items), len(rows))

            # decide next url
            if pag["type"] == "url_pattern":
                n += 1
                url = pag["pattern"].format(n=n)
                # url_pattern relies on a failed wait/empty items to stop
                if max_pages is None and pages_done > 200:
                    break
            elif pag["type"] == "next_link":
                nxt = page.query_selector(pag["selector"])
                url = urljoin(url, nxt.get_attribute("href")) if nxt else None
            else:
                url = None
            time.sleep(delay)

        browser.close()
    return rows


def scrape_http(cfg: dict, max_pages: int | None, delay: float) -> list[dict]:
    pag = cfg.get("pagination", {"type": "none"})
    session = make_session()
    rows: list[dict] = []
    url = cfg["start_url"]
    n = pag.get("start", 1)
    pages_done = 0

    while url:
        if max_pages is not None and pages_done >= max_pages:
            break
        html = fetch_http(session, url)
        if html is None:
            log.info("no page at %s — stopping", url)
            break
        items = parse_items(html, cfg, url)
        if not items:
            log.info("no items on %s — stopping", url)
            break
        rows.extend(items)
        pages_done += 1
        log.info("page %-3s -> %s items (total %s)", pages_done, len(items), len(rows))

        if pag["type"] == "url_pattern":
            n += 1
            url = pag["pattern"].format(n=n)
        elif pag["type"] == "next_link":
            from bs4 import BeautifulSoup

            nxt = BeautifulSoup(html, "lxml").select_one(pag["selector"])
            url = urljoin(url, nxt.get("href")) if nxt and nxt.get("href") else None
        else:
            url = None
        time.sleep(delay)

    return rows


# --------------------------------------------------------------------------- #
# Export                                                                       #
# --------------------------------------------------------------------------- #
def export(rows: list[dict], out: Path, fmt: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else []

    if fmt in ("csv", "all"):
        with out.with_suffix(".csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        log.info("wrote %s", out.with_suffix(".csv"))
    if fmt in ("json", "all"):
        out.with_suffix(".json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("wrote %s", out.with_suffix(".json"))
    if fmt in ("excel", "all"):
        try:
            from openpyxl import Workbook

            wb = Workbook()
            ws = wb.active
            ws.append(fields)
            for r in rows:
                ws.append([r.get(f) for f in fields])
            wb.save(out.with_suffix(".xlsx"))
            log.info("wrote %s", out.with_suffix(".xlsx"))
        except ImportError:
            log.warning("openpyxl not installed — skipping Excel export (pip install openpyxl)")


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Config-driven web scraper")
    ap.add_argument("config", type=Path, help="path to a YAML config")
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--format", choices=["csv", "json", "excel", "all"], default="all")
    ap.add_argument("--out", type=Path, default=None, help="output path stem (default: output/<name>)")
    ap.add_argument("--headful", action="store_true", help="(browser mode) show the browser")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    mode = cfg.get("mode", "http")
    out = args.out or Path("output") / cfg.get("name", args.config.stem)

    started = time.time()
    log.info("scraping '%s' (mode=%s)", cfg.get("name", "?"), mode)
    if mode == "browser":
        rows = scrape_browser(cfg, args.max_pages, args.delay, args.headful)
    else:
        rows = scrape_http(cfg, args.max_pages, args.delay)

    if not rows:
        log.error("no rows scraped — check the config selectors")
        return 1

    export(rows, out, args.format)
    log.info("DONE: %s rows in %.1fs", len(rows), time.time() - started)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# scraper-toolkit

A **config-driven web scraper**. Describe a site in a small YAML file and get clean
**CSV / JSON / Excel** out — no new code per site. Built so a new scraping job is a
5-minute config, not a from-scratch script.

Handles both kinds of sites:
- **`http` mode** — fast, for normal server-rendered pages (requests + BeautifulSoup)
- **`browser` mode** — for JavaScript-rendered pages (Playwright, headless Chromium)

…plus pagination (URL pattern *or* "Next" link), retries/backoff, polite delays,
and export to CSV, JSON and Excel.

---

## Quick start

```bash
pip install -r requirements.txt
python -m playwright install chromium     # only needed for browser mode

python scrape.py configs/books.yaml                 # http mode  → output/books.{csv,json,xlsx}
python scrape.py configs/quotes-js.yaml             # browser mode (JS site)
python scrape.py configs/books.yaml --max-pages 3 --format excel
```

## Add a new site in ~5 minutes

Create a YAML file describing the page. Example (`configs/books.yaml`):

```yaml
name: books
mode: http
start_url: https://books.toscrape.com/catalogue/page-1.html
pagination:
  type: url_pattern
  pattern: https://books.toscrape.com/catalogue/page-{n}.html
  start: 1
item_selector: "article.product_pod"     # one CSS selector per "row"
fields:
  title:  { selector: "h3 a", attr: title }
  price:  { selector: ".price_color" }              # text by default
  rating: { selector: "p.star-rating", attr: class }
  url:    { selector: "h3 a", attr: href, absolute: true }
```

That's the whole job. Run it and you get a clean dataset.

### Field options
| Key | Meaning |
|-----|---------|
| `selector` | CSS selector relative to the item node |
| `attr` | `text` (default), `class`, or any HTML attribute (`href`, `src`, …) |
| `absolute: true` | resolve relative URLs to absolute |

### Pagination types
| `type` | Use when |
|--------|----------|
| `url_pattern` | pages are `…/page-1`, `…/page-2`, … (`pattern` + `start`) |
| `next_link` | there's a "Next" link/button (`selector`) |
| `none` | single page |

## Why this exists

Most scraping gigs are the same shape: walk pages, pull a few fields, export clean
data. This toolkit captures that once — sessions, retries, pagination, export,
http-vs-browser — so each client job is just a config. The result is faster delivery
and consistent, reliable output.

## Tech

Python 3 · requests · BeautifulSoup (lxml) · Playwright · PyYAML · openpyxl.

---

*Part of a freelance web-scraping portfolio. Need a dataset? I can have a config like
the above running against your target the same day.*

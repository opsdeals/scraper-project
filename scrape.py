"""
OpsDeals scraper
================

Scrapes "deals" pages from a list of retailers and writes a CSV file
matching the format expected by the OpsDeals CSV bulk importer plugin:

    title,sale_price,original_price,store_name,affiliate_link,deal_badge,
    coupon_code,expiration_date,category,image_url,excerpt

This script does NOT try to write a hand-tuned CSS selector for every
single site. Instead, for each site it tries a list of common
"product card" selector patterns (the kinds of classes WooCommerce,
Shopify, and most other storefronts use) and picks whichever pattern
finds the most plausible product cards (cards that contain both a
link and a dollar amount).

Every run also writes output/debug_log.json, which records, per site:
  - the HTTP status code we got back
  - which selector pattern (if any) matched
  - how many product cards were found

If a site comes back with 0 cards or a non-200 status, that's the
signal that this site needs a custom selector or a different approach
(e.g. it's blocking bots). Share debug_log.json and we can refine the
config for that specific site.
"""

import csv
import json
import os
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# Matches things like $19.99, $1,299.00, $5
PRICE_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?")

# Common "product card" container patterns across WooCommerce, Shopify,
# BigCommerce, and generic storefronts. We try these in order and use
# whichever one produces the most cards that actually contain a price.
CARD_SELECTOR_CANDIDATES = [
    "ul.products li.product",
    "li.product",
    ".product-grid-item",
    ".product-item",
    ".product-card",
    ".productItem",
    ".grid__item",
    "li.grid__item",
    "[class*='product-tile']",
    "[class*='ProductCard']",
    "[class*='product-card']",
    "[class*='product-item']",
    ".product",
]

MAX_ITEMS_PER_SITE = 20
MAX_DESCRIPTIONS_PER_SITE = 10
REQUEST_DELAY_SECONDS = 1.5
SITE_DELAY_SECONDS = 2.5


def extract_item(card, base_url):
    """Pull title, prices, link, and image out of a single product card."""
    text = card.get_text(" ", strip=True)
    prices = PRICE_RE.findall(text)
    if not prices:
        return None

    def to_float(p):
        return float(p.replace("$", "").replace(",", "").strip())

    unique_prices = sorted(set(prices), key=to_float)
    sale_price = unique_prices[0]
    original_price = ""
    if len(unique_prices) > 1 and unique_prices[-1] != unique_prices[0]:
        original_price = unique_prices[-1]

    # Link
    a_tag = card.find("a", href=True)
    link = urljoin(base_url, a_tag["href"]) if a_tag else ""

    # Title
    title = ""
    for tag_name in ("h2", "h3", "h4"):
        tag = card.find(tag_name)
        if tag:
            candidate = tag.get_text(strip=True)
            if candidate:
                title = candidate
                break
    if not title and a_tag:
        title = (a_tag.get("title") or a_tag.get_text(strip=True) or "").strip()
    if not title:
        img_tag = card.find("img")
        if img_tag:
            title = (img_tag.get("alt") or "").strip()
    if not title:
        return None

    # Image (handle common lazy-load attributes)
    image_url = ""
    img_tag = card.find("img")
    if img_tag:
        for attr in ("data-src", "data-original", "data-srcset", "srcset", "src"):
            val = img_tag.get(attr)
            if val:
                first = val.split(",")[0].strip().split(" ")[0].strip()
                if first:
                    image_url = urljoin(base_url, first)
                    break

    deal_badge = "Price Drop" if original_price else ""

    return {
        "title": title[:200],
        "sale_price": sale_price,
        "original_price": original_price,
        "affiliate_link": link,
        "image_url": image_url,
        "deal_badge": deal_badge,
        "coupon_code": "",
        "expiration_date": "",
        "excerpt": "",
    }


def fetch_description(url):
    """Try to grab a short description from a product page's meta tags."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for attrs in ({"name": "description"}, {"property": "og:description"}):
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                desc = re.sub(r"\s+", " ", tag["content"]).strip()
                if desc:
                    return desc[:300]
    except requests.RequestException:
        pass
    return ""


def scrape_site(site):
    debug_info = {
        "store_name": site["store_name"],
        "url": site["url"],
        "status": None,
        "selector_used": None,
        "cards_found": 0,
        "items_extracted": 0,
        "error": None,
    }

    try:
        resp = requests.get(site["url"], headers=HEADERS, timeout=25)
        debug_info["status"] = resp.status_code
        resp.raise_for_status()
    except requests.RequestException as exc:
        debug_info["error"] = str(exc)
        return [], debug_info

    soup = BeautifulSoup(resp.text, "lxml")

    cards = []
    selector_used = None
    for selector in CARD_SELECTOR_CANDIDATES:
        found = soup.select(selector)
        if not found:
            continue
        valid = [c for c in found if PRICE_RE.search(c.get_text())]
        # Require at least 2 plausible product cards to avoid false positives
        if len(valid) >= 2:
            cards = valid
            selector_used = selector
            break

    debug_info["selector_used"] = selector_used
    debug_info["cards_found"] = len(cards)

    base_url = site.get("base_url") or site["url"]
    results = []
    for card in cards[:MAX_ITEMS_PER_SITE]:
        item = extract_item(card, base_url)
        if item:
            item["store_name"] = site["store_name"]
            item["category"] = site.get("category", "")
            results.append(item)

    debug_info["items_extracted"] = len(results)

    # Fetch descriptions for a limited number of items (extra requests)
    for item in results[:MAX_DESCRIPTIONS_PER_SITE]:
        if item["affiliate_link"]:
            item["excerpt"] = fetch_description(item["affiliate_link"])
            time.sleep(REQUEST_DELAY_SECONDS)

    return results, debug_info


def main():
    with open("sites.json", "r", encoding="utf-8") as f:
        sites = json.load(f)

    all_results = []
    debug_log = []

    for site in sites:
        print(f"Scraping {site['store_name']} ({site['url']}) ...")
        results, debug_info = scrape_site(site)
        print(
            f"  -> status={debug_info['status']} "
            f"selector={debug_info['selector_used']} "
            f"cards={debug_info['cards_found']} "
            f"items={debug_info['items_extracted']} "
            f"error={debug_info['error']}"
        )
        all_results.extend(results)
        debug_log.append(debug_info)
        time.sleep(SITE_DELAY_SECONDS)

    os.makedirs("output", exist_ok=True)

    fieldnames = [
        "title",
        "sale_price",
        "original_price",
        "store_name",
        "affiliate_link",
        "deal_badge",
        "coupon_code",
        "expiration_date",
        "category",
        "image_url",
        "excerpt",
    ]

    with open("output/deals.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in all_results:
            writer.writerow({k: item.get(k, "") for k in fieldnames})

    with open("output/debug_log.json", "w", encoding="utf-8") as f:
        json.dump(debug_log, f, indent=2)

    print(f"\nDone. Wrote {len(all_results)} rows to output/deals.csv")


if __name__ == "__main__":
    main()

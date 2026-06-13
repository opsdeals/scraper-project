# OpsDeals Scraper

Scrapes "deals" pages from a list of firearms/outdoor retailers and produces
`output/deals.csv` in the exact format expected by the OpsDeals CSV bulk
importer plugin:

```
title,sale_price,original_price,store_name,affiliate_link,deal_badge,coupon_code,expiration_date,category,image_url,excerpt
```

## How it works

For each site listed in `sites.json`, the scraper:

1. Downloads the deals page.
2. Tries a list of common "product card" patterns (the kinds of CSS classes
   WooCommerce, Shopify, BigCommerce, and most other storefronts use) and
   picks whichever pattern finds the most cards that contain both a link and
   a dollar amount.
3. Pulls out title, sale price, original price (if a higher price is also
   shown — used to set `deal_badge` to "Price Drop"), product link, and
   image URL from each card.
4. For the first 10 items per site, visits the product page and grabs the
   meta description to use as `excerpt`.
5. Writes everything to `output/deals.csv`, and writes a per-site summary to
   `output/debug_log.json`.

This is intentionally generic rather than hand-tuned per site, since
real-world results will vary — some sites will work great out of the box,
some will need a tweak, and some may block scraping entirely. The debug log
tells you which is which.

## Setting it up

1. Create a new GitHub repo and push these files to it (or upload them via
   the GitHub web UI: "Add file" → "Upload files").
2. Go to the **Actions** tab of the repo. You may need to click "I understand
   my workflows, go ahead and enable them."
3. Click into the **Scrape Deals** workflow, then click **Run workflow**
   (this is the `workflow_dispatch` trigger — lets you run it on demand
   instead of waiting for the daily schedule).
4. Once it finishes (usually under a couple minutes), check:
   - `output/deals.csv` — this is the file you upload to the OpsDeals
     importer (Deal → Import Deals in WP admin).
   - `output/debug_log.json` — per-site results (status code, how many
     items were found, etc).

The workflow also runs automatically once a day (see the `cron` schedule in
`.github/workflows/scrape.yml`) and will commit an updated `deals.csv`
whenever it finds new data.

## Running it locally (optional)

```bash
pip install -r requirements.txt
python scrape.py
```

Output goes to `output/deals.csv` and `output/debug_log.json`.

## Troubleshooting / iterating

After a run, open `output/debug_log.json`. For each site you'll see
something like:

```json
{
  "store_name": "Palmetto State Armory",
  "url": "https://palmettostatearmory.com/daily-deals-new.html",
  "status": 200,
  "selector_used": "ul.products li.product",
  "cards_found": 18,
  "items_extracted": 18,
  "error": null
}
```

- **`status` is not 200** (e.g. 403, 503): the site is likely blocking
  automated requests. This is common with larger retailers (Midway USA in
  particular runs aggressive bot-detection). These sites likely need a
  different approach (e.g. a headless browser) which is a bigger lift — for
  a proof of concept, it's reasonable to drop these and lean on the sites
  that do work.
- **`status` is 200 but `cards_found` is 0**: the generic selector patterns
  didn't match this site's HTML. Send me the URL and I can add a
  site-specific selector to `sites.json`/`scrape.py`.
- **`cards_found` > 0 but `items_extracted` is lower**: some cards didn't
  have a clear title — usually harmless, but worth a look if the number is
  way off.
- **Prices, images, or titles look wrong for a specific site**: same fix —
  send me that site's URL and the relevant bit of `debug_log.json` and I'll
  add a custom selector for it.

## A note on scraping etiquette

This is set up to run once a day and pause between requests, which is
intentionally conservative. Each retailer's terms of service may have its
own rules about automated access — worth a skim, especially before scaling
up frequency or volume. For getting accepted into affiliate programs, a
small, infrequent proof-of-concept run like this is generally what's needed
to demonstrate the site populates with real deals.

## Mapping to OpsDeals categories

Valid category slugs for the `category` column are:

```
accessories, ammo, edc, firearms, holsters-carry, lights, magazines, optics, packs, parts
```

`sites.json` has a `category` field per site — set to a sensible default
(or left blank for "mixed deals" pages). Since deals pages often mix
categories, you may want to leave these blank and assign categories
manually after import, or split them out later as the scraper matures.

# Prompt — Unified price monitor (price-monitor)

Use this prompt to recreate or evolve the project `C:\Projetos\price-monitor`.

---

## Goal

Build a Python price monitor (Windows) that:

1. Reads products from a JSON file (`produtos.json`)
2. Opens pages with Playwright + Chromium and a persistent profile (cookies/session)
3. Extracts title and current price (and list price when available)
4. Alerts when price ≤ `target_price` and/or discount ≥ `min_discount_percent`
5. Respects cooldown between alerts for the same product
6. Supports multiple retailers in the same project, with separate adapters

Do not use paid APIs (Keepa etc.). Scraping via a real browser.

---

## Project evolution (history)

1. **amazon-price-monitor** — first monitor (Amazon US)
2. **safeway-price-monitor** — mirror for Safeway
3. **instacart-price-monitor** — Instacart with OTP/SMS login (`auth`)
4. **price-monitor** — unification of the three + Target + Walmart (SerpApi) + CLI `add` for URLs

Old projects may remain on disk; day-to-day use is only `price-monitor`.

---

## Stack and layout

- Python 3.12+, venv in `.venv`
- Dependency: `playwright`
- Chromium in `%LOCALAPPDATA%\ms-playwright` via:
  `$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"`
- Package: `python -m price_monitor <command>`
- Suggested structure:

```
price-monitor/
  produtos.json / produtos.exemplo.json
  requirements.txt
  README.md
  .gitignore
  .profiles/{amazon,safeway,instacart,target}/
  .state/{amazon,safeway,instacart,target}.json
  price_monitor/
    __main__.py
    cli.py
    config.py
    models.py
    prices.py
    state.py
    alerts.py
    browser.py
    runner.py
    urls.py
    add_product.py
    walmart_api.py
    adapters/
      base.py
      amazon.py
      safeway.py
      instacart.py
      target.py
      walmart.py
      __init__.py
```

---

## Configuration (`produtos.json`)

```json
{
  "cooldown_hours": 24,
  "headless": true,
  "retailers": {
    "amazon": { "headless": true },
    "safeway": { "zip": "94080" },
    "instacart": { "zip": "94080", "retailer_slug": "safeway" },
    "target": { "headless": true },
    "walmart": { "headless": true }
  },
  "products": [
    {
      "retailer": "amazon",
      "url": "https://www.amazon.com/.../dp/ASIN...",
      "target_price": 6.0
    }
  ]
}
```

Rules per product:

- **Required:** `retailer`, `url`, `target_price` (> 0)
- **Optional:** `name`, `asin`/`product_id`, `min_discount_percent`, `reference_price`
- Prefer pasting the **full URL**; the system extracts the ID and normalizes to a canonical URL
- Per-store settings in `retailers.<name>` (zip, headless, retailer_slug)

---

## Retailers and specifics

### Amazon
- Extract ASIN from the URL; canonical: `https://www.amazon.com/dp/{ASIN}`
- Optional name from the URL slug
- Captcha: fails in headless; use `--no-headless` once to save the session in the profile
- List price / discount when available in the DOM/JSON

### Safeway
- Extract `product_id` from `product-details.{id}.html`
- **Incapsula** block: wait for auto-clear (no Enter / no manual captcha solving)
- **Do not** bypass captcha via API/stolen cookies
- Strategy without human interaction:
  1. Check in **headless** by default; system Chrome (`channel=chrome`)
  2. Command `warm --retailer safeway` (window; wait for auto-clear; save profile)
  3. On headless `check`: `headed_fallback` reopens a window once automatically if Incapsula blocks
- Prefer **JSON-LD** (`@type: Product`, `offers.price`)
- Ignore modal titles; fallback: `page.title()`
- Optional ZIP (`retailers.safeway.zip`)

### Instacart
- Phone/OTP login (**no password**): command `auth --retailer instacart`
- Headless checks reuse `.profiles/instacart`; expired session → exit code `2`
- Extract `product_id` and `retailerSlug` from the URL
- **Price:** DO NOT take the first `$` on the page (related cards)
  - Prefer text `Current price: $X.XX`
  - Then buy box / item selectors
  - Avoid `__NEXT_DATA__` / loose embedded JSON (many prices on the page)
  - Fallback: HTML window near `product_id`

### Target
- Extract TCIN from `/A-{tcin}` and `preselect` from the query
- Canonical: `/p/{slug}/-/A-{tcin}?preselect=...`
- Adapter in the same pattern as the others

### Walmart
- Extract `product_id` from the URL `/ip/.../{id}`
- Prefer **SerpApi** (`engine=walmart_product`) — avoids PerimeterX
- Env: `SERPAPI_API_KEY` (or `retailers.walmart.serpapi_api_key`)
- Optional: `store_id` / `SERPAPI_WALMART_STORE_ID` for store-specific price
- Price: `product_result.price_map.price` (+ `was_price` as list)
- Browser only with `browser_fallback=true` (PerimeterX usually blocks)

---

## CLI

```text
python -m price_monitor check [--config] [--retailer] [--headless|--no-headless] [--cooldown-hours]
python -m price_monitor auth --retailer instacart
python -m price_monitor warm --retailer safeway
python -m price_monitor add [URL ...] --target-price N [--min-discount-percent] [--reference-price] [--retailer] [--config]
python -m price_monitor serve [--host] [--port]
```

### `serve`
- Local dashboard at `http://127.0.0.1:8765`
- Lists products + last price (`.state/*/last_checks`)
- Button to run `check` (with log)
- Form to add a URL

### `add`
- No URLs → interactive mode (paste URL + target price until empty Enter)
- Detects retailer by host (`amazon.com`, `safeway.com`, `instacart.com`, `target.com`)
- Normalizes via adapter; writes minimal entry `{retailer, url, target_price}`
- `target_price` is **required** (`add` and `produtos.json`)
- If the same product already exists (`product_key`), **update** instead of duplicating

---

## Shared core

- **browser:** `launch_persistent_context` per profile, locale `en-US`, timezone `America/Los_Angeles`, Chrome UA, `--disable-blink-features=AutomationControlled`
- **prices:** `parse_price`, `calc_discount`
- **state:** cooldown by `product_key` in `.state/{retailer}.json`
- **alerts:** terminal + optional Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) and SMTP
- **runner:** groups products by retailer, one browser context per store, `set_location` only on the 1st product in the group
- **urls:** parse/canonicalization helpers + `detect_retailer_from_url`

---

## Usage commands (PowerShell)

```powershell
cd C:\Projetos\price-monitor
$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"

# Check everything
.\.venv\Scripts\python.exe -m price_monitor check

# By store
.\.venv\Scripts\python.exe -m price_monitor check --retailer amazon
.\.venv\Scripts\python.exe -m price_monitor check --retailer safeway
.\.venv\Scripts\python.exe -m price_monitor check --retailer instacart
.\.venv\Scripts\python.exe -m price_monitor check --retailer target

# Captcha / challenge
.\.venv\Scripts\python.exe -m price_monitor check --retailer safeway --no-headless
.\.venv\Scripts\python.exe -m price_monitor check --retailer amazon --no-headless

# Instacart OTP
.\.venv\Scripts\python.exe -m price_monitor auth --retailer instacart

# Add URLs
.\.venv\Scripts\python.exe -m price_monitor add
.\.venv\Scripts\python.exe -m price_monitor add "https://www.amazon.com/dp/B000R5NRPI" --target-price 6
```

---

## Bugs already fixed (do not regress)

1. **Instacart wrong price** — took $5.99 from a related card; correct ~$8.07 via “Current price”
2. **Safeway empty title/price** — Incapsula + short wait + modal `h1`; fixed with Incapsula wait + JSON-LD + junk title filter
3. **Playwright browsers path** — install Chromium in `%LOCALAPPDATA%\ms-playwright`, not in the sandbox cache
4. **Python on Windows** — prefer `.\.venv\Scripts\python.exe` (avoid Microsoft Store stub)

---

## Acceptance criteria

- `check` runs all 4 retailers headless (with valid profiles) and prints title + price
- Amazon alerts with cooldown when price ≤ target
- `add` detects store, normalizes URL, and persists to JSON
- Instacart without session → clear message + `auth`
- Safeway/Amazon with challenge in headless → error asking for `--no-headless`

---

## Constraints

- Personal/educational use; respect each site’s Terms of Use
- Do not create exploits; only defensive scraping of the public product page
- Do not commit `produtos.json` with secrets, browser profiles, or `.state` if they contain sensitive data
- Commits only when the user asks

# Unified price monitor (Amazon + Safeway + Instacart + Target + Walmart)

One project with a shared core and per-retailer adapters (Amazon, Safeway, Instacart, Target, Walmart).

Replaces the separate projects:

- `amazon-price-monitor`
- `safeway-price-monitor`
- `instacart-price-monitor`

The old projects can stay on disk; use this one going forward.

## Install (Windows)

```powershell
cd C:\Projetos\price-monitor
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item produtos.exemplo.json produtos.json
```

## Configuration

Edit `produtos.json`. Each product **must** include `"retailer": "amazon" | "safeway" | "instacart" | "target" | "walmart"`.

```json
{
  "cooldown_hours": 24,
  "headless": true,
  "retailers": {
    "amazon": { "headless": true },
    "safeway": { "zip": "94080" },
    "instacart": { "zip": "94080", "retailer_slug": "safeway" }
  },
  "products": [
    {
      "retailer": "amazon",
      "name": "Water",
      "url": "https://www.amazon.com/dp/B000R5NRPI",
      "asin": "B000R5NRPI",
      "target_price": 6.0
    }
  ]
}
```

- `target_price` is **required** (> 0)
- `min_discount_percent` / `reference_price` are **optional**
- `asin` / `product_id` are **optional** — extracted from the URL when omitted
- `name` is **optional** for Amazon/Instacart (derived from the URL slug if omitted)
- You can paste a **full URL** (with `ref=`, query string, etc.); the monitor normalizes it to the canonical form
- Per-retailer settings live under `retailers.<name>`

Minimal Amazon example (URL + target price only):

```json
{
  "retailer": "amazon",
  "url": "https://www.amazon.com/Arrowhead-Spring-Water-Bottles-Still-Bottled-Minerals-Electrolytes/dp/B000R5NRPI/ref=sr_1_1?keywords=arrowhead",
  "target_price": 6.0
}
```

The system extracts `ASIN=B000R5NRPI`, normalizes to `https://www.amazon.com/dp/B000R5NRPI`, and builds a name from the slug.

## Commands

```powershell
# All products in the JSON
.\.venv\Scripts\python.exe -m price_monitor check --config produtos.json

# One retailer only
.\.venv\Scripts\python.exe -m price_monitor check --retailer amazon
.\.venv\Scripts\python.exe -m price_monitor check --retailer safeway --no-headless
.\.venv\Scripts\python.exe -m price_monitor check --retailer instacart
.\.venv\Scripts\python.exe -m price_monitor check --retailer target

# Instacart OTP (SMS) — once / when the session expires
.\.venv\Scripts\python.exe -m price_monitor auth --retailer instacart

# Safeway: refresh cookies without pressing Enter (Incapsula clears in the window)
.\.venv\Scripts\python.exe -m price_monitor warm --retailer safeway

# Add a product by URL
.\.venv\Scripts\python.exe -m price_monitor add "URL" --target-price 5

# Local web dashboard
.\.venv\Scripts\python.exe -m price_monitor serve
# Open http://127.0.0.1:8765
# Create an account (login). The first user imports root produtos.json if it exists.
# Each user lives under .data/users/<name>/produtos.json
```

## Walmart (SerpApi)

Walmart uses PerimeterX on the site; the monitor prefers the **SerpApi Walmart Product API**.

1. Create an account at [serpapi.com](https://serpapi.com/) and copy the API key
2. Configure (PowerShell):

```powershell
$env:SERPAPI_API_KEY = "your-serpapi-key"
.\.venv\Scripts\python.exe -m price_monitor check --retailer walmart
```

Or set `retailers.walmart.serpapi_api_key` in `produtos.json`.

Optional (but **recommended**): your store’s `store_id` — without it SerpApi uses a default location and may return marketplace / out-of-stock pricing.

```json
"walmart": { "store_id": "2280", "zip": "94080" }
```

Or via env: `SERPAPI_WALMART_STORE_ID` / `SERPAPI_WALMART_ZIP`.

Store list: https://serpapi.com/walmart-stores

Without SerpApi, the browser fallback usually stalls on PerimeterX — use it only if `browser_fallback=true`.

Profiles and state (per store):

- `.profiles/amazon`, `.profiles/safeway`, `.profiles/instacart`
- `.state/amazon.json`, `.state/safeway.json`, `.state/instacart.json`

## Migrate sessions from the old projects

If you already authenticated in the separate monitors, copy the profiles:

```powershell
cd C:\Projetos\price-monitor
New-Item -ItemType Directory -Force -Path .profiles, .state | Out-Null

Copy-Item -Recurse -Force ..\amazon-price-monitor\.amazon-browser-profile .profiles\amazon
Copy-Item -Recurse -Force ..\safeway-price-monitor\.safeway-browser-profile .profiles\safeway
Copy-Item -Recurse -Force ..\instacart-price-monitor\.instacart-browser-profile .profiles\instacart

Copy-Item -Force ..\amazon-price-monitor\.amazon_monitor_state.json .state\amazon.json
Copy-Item -Force ..\safeway-price-monitor\.safeway_monitor_state.json .state\safeway.json
Copy-Item -Force ..\instacart-price-monitor\.instacart_monitor_state.json .state\instacart.json
```

## Alerts

Telegram / SMTP work the same as in the old monitors:

```powershell
$env:TELEGRAM_BOT_TOKEN = "..."
$env:TELEGRAM_CHAT_ID = "..."
# or SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO
```

Without those, the alert is printed in the terminal.

## Schedule

One task with `check` (all) or separate tasks with `--retailer`.

```powershell
C:\Projetos\price-monitor\.venv\Scripts\python.exe -m price_monitor check --config C:\Projetos\price-monitor\produtos.json
```

For Instacart, run `auth` manually when the session expires (exit code `2`).

## Notes

- Amazon/Safeway: captcha may require a visible window; Safeway uses `warm` / `headed_fallback` without Enter.
- Instacart: SMS login via `auth`; headless checks reuse the profile.
- Personal/educational use. Respect each site’s Terms of Service.

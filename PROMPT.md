# Prompt — Monitor unificado de preços (price-monitor)

Use este prompt para recriar ou evoluir o projeto `C:\Projetos\price-monitor`.

---

## Objetivo

Criar um monitor de preços em Python (Windows) que:

1. Lê produtos de um JSON (`produtos.json`)
2. Abre as páginas com Playwright + Chromium e perfil persistente (cookies/sessão)
3. Extrai título e preço atual (e preço de lista quando existir)
4. Alerta quando o preço ≤ `target_price` e/ou o desconto ≥ `min_discount_percent`
5. Respeita cooldown entre alertas do mesmo produto
6. Suporta vários varejistas no mesmo projeto, com adapters separados

Não usar APIs pagas (Keepa etc.). Scraping via navegador real.

---

## Evolução do projeto (histórico)

1. **amazon-price-monitor** — primeiro monitor (Amazon EUA)
2. **safeway-price-monitor** — espelho para Safeway
3. **instacart-price-monitor** — Instacart com login OTP/SMS (`auth`)
4. **price-monitor** — unificação dos três + Target + CLI `add` de URLs

Os projetos antigos podem ficar no disco; o uso diário é só `price-monitor`.

---

## Stack e layout

- Python 3.12+, venv em `.venv`
- Dependência: `playwright`
- Chromium em `%LOCALAPPDATA%\ms-playwright` via:
  `$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"`
- Pacote: `python -m price_monitor <comando>`
- Estrutura sugerida:

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
    adapters/
      base.py
      amazon.py
      safeway.py
      instacart.py
      target.py
      __init__.py
```

---

## Configuração (`produtos.json`)

```json
{
  "cooldown_hours": 24,
  "headless": true,
  "retailers": {
    "amazon": { "headless": true },
    "safeway": { "zip": "94080" },
    "instacart": { "zip": "94080", "retailer_slug": "safeway" },
    "target": { "headless": true }
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

Regras por produto:

- **Obrigatório:** `retailer`, `url`, `target_price` (> 0)
- **Opcional:** `name`, `asin`/`product_id`, `min_discount_percent`, `reference_price`
- Preferir colar a **URL completa**; o sistema extrai ID e normaliza para URL canônica
- Settings por loja em `retailers.<nome>` (zip, headless, retailer_slug)

---

## Varejistas e particularidades

### Amazon
- Extrair ASIN da URL; canônica: `https://www.amazon.com/dp/{ASIN}`
- Nome opcional a partir do slug da URL
- Captcha: em headless falha; usar `--no-headless` uma vez para gravar sessão no perfil
- Preço de lista / desconto quando disponíveis no DOM/JSON

### Safeway
- Extrair `product_id` de `product-details.{id}.html`
- Bloqueio **Incapsula**: esperar auto-liberação (sem Enter / sem resolver captcha manualmente)
- **Não** contornar captcha via API/cookies roubados
- Estratégia sem interação humana:
  1. Check em **headless** por padrão; Chrome do sistema (`channel=chrome`)
  2. Comando `warm --retailer safeway` (janela; espera auto-liberar; grava perfil)
  3. No `check` headless: `headed_fallback` reabre janela 1x automaticamente se Incapsula bloquear
- Priorizar **JSON-LD** (`@type: Product`, `offers.price`)
- Ignorar títulos de modal; fallback: `page.title()`
- ZIP opcional (`retailers.safeway.zip`)

### Instacart
- Login por telefone/OTP (**sem senha**): comando `auth --retailer instacart`
- Checks headless reusam `.profiles/instacart`; sessão expirada → exit code `2`
- Extrair `product_id` e `retailerSlug` da URL
- **Preço:** NÃO pegar o primeiro `$` da página (cards relacionados)
  - Preferir texto `Current price: $X.XX`
  - Depois buy box / seletores do item
  - Evitar `__NEXT_DATA__` / JSON embutido solto (muitos preços na página)
  - Fallback: janela de HTML perto do `product_id`

### Target
- Extrair TCIN de `/A-{tcin}` e `preselect` da query
- Canônica: `/p/{slug}/-/A-{tcin}?preselect=...`
- Adapter no mesmo padrão dos outros

---

## CLI

```text
python -m price_monitor check [--config] [--retailer] [--headless|--no-headless] [--cooldown-hours]
python -m price_monitor auth --retailer instacart
python -m price_monitor warm --retailer safeway
python -m price_monitor add [URL ...] --target-price N [--min-discount-percent] [--reference-price] [--retailer] [--config]
```

### `add`
- Sem URLs → modo interativo (cola URL + preço alvo até Enter vazio)
- Detecta varejista pelo host (`amazon.com`, `safeway.com`, `instacart.com`, `target.com`)
- Normaliza via adapter; grava entrada mínima `{retailer, url, target_price}`
- `target_price` é **obrigatório** (`add` e `produtos.json`)
- Se o mesmo produto já existir (`product_key`), **atualiza** em vez de duplicar

---

## Núcleo compartilhado

- **browser:** `launch_persistent_context` por perfil, locale `en-US`, timezone `America/Los_Angeles`, UA Chrome, `--disable-blink-features=AutomationControlled`
- **prices:** `parse_price`, `calc_discount`
- **state:** cooldown por `product_key` em `.state/{retailer}.json`
- **alerts:** terminal + opcional Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) e SMTP
- **runner:** agrupa produtos por retailer, um browser context por loja, `set_location` só no 1º produto do grupo
- **urls:** helpers de parse/canonicalização + `detect_retailer_from_url`

---

## Comandos de uso (PowerShell)

```powershell
cd C:\Projetos\price-monitor
$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"

# Checar tudo
.\.venv\Scripts\python.exe -m price_monitor check

# Por loja
.\.venv\Scripts\python.exe -m price_monitor check --retailer amazon
.\.venv\Scripts\python.exe -m price_monitor check --retailer safeway
.\.venv\Scripts\python.exe -m price_monitor check --retailer instacart
.\.venv\Scripts\python.exe -m price_monitor check --retailer target

# Captcha / challenge
.\.venv\Scripts\python.exe -m price_monitor check --retailer safeway --no-headless
.\.venv\Scripts\python.exe -m price_monitor check --retailer amazon --no-headless

# Instacart OTP
.\.venv\Scripts\python.exe -m price_monitor auth --retailer instacart

# Adicionar URLs
.\.venv\Scripts\python.exe -m price_monitor add
.\.venv\Scripts\python.exe -m price_monitor add "https://www.amazon.com/dp/B000R5NRPI" --target-price 6
```

---

## Bugs já corrigidos (não regredir)

1. **Instacart preço errado** — pegava $5.99 de card relacionado; correto ~$8.07 via “Current price”
2. **Safeway título/preço vazios** — Incapsula + wait curto + `h1` de modal; corrigido com wait Incapsula + JSON-LD + filtro de títulos lixo
3. **Playwright browsers path** — instalar Chromium em `%LOCALAPPDATA%\ms-playwright`, não no cache do sandbox
4. **Python no Windows** — preferir `.\.venv\Scripts\python.exe` (evitar stub da Microsoft Store)

---

## Critérios de aceite

- `check` roda os 4 varejistas em headless (com perfis válidos) e imprime título + preço
- Amazon alerta com cooldown quando preço ≤ alvo
- `add` detecta loja, normaliza URL e persiste no JSON
- Instacart sem sessão → mensagem clara + `auth`
- Safeway/Amazon com challenge em headless → erro pedindo `--no-headless`

---

## Restrições

- Uso pessoal/educacional; respeitar Termos de Uso de cada site
- Não criar exploits; só scraping defensivo da página pública do produto
- Não commitar `produtos.json` com secrets, perfis de browser, nem `.state` se contiverem dados sensíveis
- Commits só quando o usuário pedir

# Monitor unificado de preços (Amazon + Safeway + Instacart)

Um único projeto com núcleo compartilhado e adapters por varejista (Amazon, Safeway, Instacart, Target).

Substitui os projetos separados:

- `amazon-price-monitor`
- `safeway-price-monitor`
- `instacart-price-monitor`

Os projetos antigos podem permanecer no disco; use este daqui em diante.

## Instalação (Windows)

```powershell
cd C:\Projetos\price-monitor
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item produtos.exemplo.json produtos.json
```

## Configuração

Edite `produtos.json`. Cada produto **precisa** de `"retailer": "amazon" | "safeway" | "instacart"`.

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

- `target_price` é **obrigatório** (> 0)
- `min_discount_percent` / `reference_price` são **opcionais**
- `asin` / `product_id` são **opcionais** — o sistema extrai da URL
- `name` é **opcional** na Amazon/Instacart (usa o slug da URL se omitido)
- Pode colar a **URL completa** (com `ref=`, query string, etc.); o monitor limpa para a forma canônica
- Settings por varejista ficam em `retailers.<nome>`

Exemplo mínimo Amazon (só URL + preço alvo):

```json
{
  "retailer": "amazon",
  "url": "https://www.amazon.com/Arrowhead-Spring-Water-Bottles-Still-Bottled-Minerals-Electrolytes/dp/B000R5NRPI/ref=sr_1_1?keywords=arrowhead",
  "target_price": 6.0
}
```

O sistema extrai `ASIN=B000R5NRPI`, normaliza para `https://www.amazon.com/dp/B000R5NRPI` e monta um nome a partir do slug.

## Comandos

```powershell
# Todos os produtos do JSON
.\.venv\Scripts\python.exe -m price_monitor check --config produtos.json

# Só um varejista
.\.venv\Scripts\python.exe -m price_monitor check --retailer amazon
.\.venv\Scripts\python.exe -m price_monitor check --retailer safeway --no-headless
.\.venv\Scripts\python.exe -m price_monitor check --retailer instacart
.\.venv\Scripts\python.exe -m price_monitor check --retailer target

# Instacart OTP (SMS) — uma vez / quando a sessão expirar
.\.venv\Scripts\python.exe -m price_monitor auth --retailer instacart

# Safeway: renovar cookies sem Enter (Incapsula auto-libera na janela)
.\.venv\Scripts\python.exe -m price_monitor warm --retailer safeway

# Adicionar produto por URL
.\.venv\Scripts\python.exe -m price_monitor add "URL" --target-price 5

# Dashboard web local
.\.venv\Scripts\python.exe -m price_monitor serve
# Abra http://127.0.0.1:8765
# Crie uma conta (login). O primeiro usuário importa o produtos.json da raiz, se existir.
# Cada usuário fica em .data/users/<nome>/produtos.json
```

## Walmart (SerpApi)

O Walmart usa PerimeterX no site; o monitor prefere a **SerpApi Walmart Product API**.

1. Crie conta em [serpapi.com](https://serpapi.com/) e copie a API key
2. Configure (PowerShell):

```powershell
$env:SERPAPI_API_KEY = "sua-chave-serpapi"
.\.venv\Scripts\python.exe -m price_monitor check --retailer walmart
```

Ou preencha `retailers.walmart.serpapi_api_key` no `produtos.json`.

Opcional (mas **recomendado**): `store_id` da sua loja — sem isso a SerpApi usa uma localização default e pode devolver preço de marketplace/sem estoque.

```json
"walmart": { "store_id": "2280", "zip": "94080" }
```

Ou via env: `SERPAPI_WALMART_STORE_ID` / `SERPAPI_WALMART_ZIP`.

Lista de lojas: https://serpapi.com/walmart-stores

Sem SerpApi, o fallback de browser costuma travar no PerimeterX — use só se `browser_fallback=true`.

Perfis e estados (separados por loja):

- `.profiles/amazon`, `.profiles/safeway`, `.profiles/instacart`
- `.state/amazon.json`, `.state/safeway.json`, `.state/instacart.json`

## Migrar sessões dos projetos antigos

Se já autenticou nos monitores separados, copie os perfis:

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

## Alertas

Telegram / SMTP iguais aos monitores antigos:

```powershell
$env:TELEGRAM_BOT_TOKEN = "..."
$env:TELEGRAM_CHAT_ID = "..."
# ou SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO
```

Sem isso, o alerta sai no terminal.

## Agendar

Uma tarefa com `check` (todos) ou três tarefas com `--retailer`.

```powershell
C:\Projetos\price-monitor\.venv\Scripts\python.exe -m price_monitor check --config C:\Projetos\price-monitor\produtos.json
```

Para Instacart, rode `auth` manualmente quando a sessão expirar (exit code `2`).

## Observações

- Amazon/Safeway: captcha pode exigir janela; Safeway usa `warm` / `headed_fallback` sem Enter.
- Instacart: login por SMS via `auth`; checks headless reusam o perfil.
- Uso pessoal/educacional. Respeite os Termos de Uso de cada site.

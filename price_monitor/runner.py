from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, sync_playwright

from price_monitor.adapters import get_adapter
from price_monitor.alerts import dispatch_alert, notify_message
from price_monitor.browser import create_cdp_chrome_session, create_context
from price_monitor.config import (
    load_config,
    resolve_cooldown,
    resolve_headless,
    retailer_settings,
)
from price_monitor.models import ChallengeRequiredError, Product, SessionExpiredError
from price_monitor.prices import should_alert
from price_monitor.state import (
    cooldown_active,
    load_state,
    mark_alerted,
    mark_checked,
    save_state,
)


def profile_dir_for(base: Path, retailer: str) -> Path:
    return (base / ".profiles" / retailer).resolve()


def state_path_for(base: Path, retailer: str) -> Path:
    return (base / ".state" / f"{retailer}.json").resolve()


def _should_use_cdp(retailer: str, rsettings: dict[str, Any]) -> bool:
    if "use_cdp" in rsettings:
        return bool(rsettings["use_cdp"])
    return retailer == "walmart"


def _open_session(
    playwright,
    *,
    retailer: str,
    profile_dir: Path,
    headless: bool,
    timezone_id: str,
    rsettings: dict[str, Any],
    force_cdp: bool = False,
) -> tuple[BrowserContext, Page, Callable[[], None], str]:
    """
    Retorna (context, page, closer, mode).
    Walmart: Chrome CDP (janela real) — PerimeterX bloqueia o launch do Playwright.
    """
    use_cdp = force_cdp or (_should_use_cdp(retailer, rsettings) and not headless)
    # Em headless + walmart, ainda preferimos CDP com janela no fallback (force_cdp).
    if use_cdp or (force_cdp and retailer == "walmart"):
        print("  Browser: Chrome CDP (real window)")
        session = create_cdp_chrome_session(playwright, profile_dir)
        page = (
            session.context.pages[0]
            if session.context.pages
            else session.context.new_page()
        )
        return session.context, page, session.close, "cdp"

    context = create_context(
        playwright,
        profile_dir,
        headless=headless,
        timezone_id=timezone_id,
    )
    page = context.pages[0] if context.pages else context.new_page()
    return context, page, context.close, "playwright"


def _record_result(
    *,
    adapter,
    product: Product,
    scraped,
    state: dict[str, Any],
    state_path: Path,
    cooldown: float,
    email_to: str | None = None,
) -> None:
    key = adapter.product_key(product)
    title_preview = scraped.title or "(title not found)"
    print(f"  Title: {title_preview[:100]}")
    if scraped.current_price is not None:
        print(f"  Current price: ${scraped.current_price:.2f}")
    else:
        print("  Current price: not found")
    if scraped.list_price is not None:
        print(f"  List price: ${scraped.list_price:.2f}")
    if scraped.discount_percent is not None:
        print(f"  Discount: {scraped.discount_percent:.1f}%")

    alert, reason = should_alert(product, scraped)
    if not alert:
        status = "ok"
        if scraped.current_price is None:
            status = "unavailable"
            print("  Status: no price / unavailable")
        else:
            print("  Status: no alert")
        print()
        mark_checked(
            state,
            key,
            title=scraped.title,
            current_price=scraped.current_price,
            list_price=scraped.list_price,
            status=status,
        )
        save_state(state_path, state)
        return

    if cooldown_active(state, key, cooldown):
        print(f"  Status: alert eligible, but in cooldown ({reason})")
        print()
        mark_checked(
            state,
            key,
            title=scraped.title,
            current_price=scraped.current_price,
            list_price=scraped.list_price,
            status="cooldown",
            reason=reason,
        )
        save_state(state_path, state)
        return

    print(f"  Status: ALERT — {reason}")
    dispatch_alert(
        product, scraped, reason, brand=adapter.brand, email_to=email_to
    )
    mark_alerted(state, key, reason, scraped.current_price)
    mark_checked(
        state,
        key,
        title=scraped.title,
        current_price=scraped.current_price,
        list_price=scraped.list_price,
        status="alert",
        reason=reason,
    )
    save_state(state_path, state)
    print()


def run_check(
    *,
    config_path: Path,
    base_dir: Path,
    retailer_filter: str | None,
    headless: bool | None,
    cooldown_hours: float | None,
    state_dir: Path | None = None,
    profile_base: Path | None = None,
    url_filter: str | None = None,
    notify_email: str | None = None,
) -> int:
    products, settings = load_config(config_path, allow_empty=True)
    if retailer_filter:
        retailer_filter = retailer_filter.strip().lower()
        products = [p for p in products if p.retailer == retailer_filter]
        if not products:
            raise ValueError(f"No products for retailer={retailer_filter}")

    if url_filter:
        from price_monitor.add_product import _normalize_url_key

        wanted = {
            _normalize_url_key(url_filter),
        }
        filtered: list[Product] = []
        for product in products:
            keys = {_normalize_url_key(product.url)}
            raw = product.raw if isinstance(product.raw, dict) else {}
            if raw.get("url"):
                keys.add(_normalize_url_key(str(raw.get("url"))))
            if keys & wanted:
                filtered.append(product)
        products = filtered
        if not products:
            raise ValueError(f"No products for url={url_filter}")

    if not products:
        raise ValueError("No products configured.")

    by_retailer: dict[str, list[Product]] = defaultdict(list)
    for product in products:
        by_retailer[product.retailer].append(product)

    cooldown = resolve_cooldown(settings, cooldown_hours)
    exit_code = 0
    profiles_root = profile_base or base_dir
    states_root = state_dir or (base_dir / ".state")

    label = "single product" if url_filter else "Unified monitor"
    print(f"{label} — {len(products)} product(s) across {len(by_retailer)} retailer(s)")
    print(f"Global cooldown: {cooldown}h")
    print()

    for retailer, group in by_retailer.items():
        try:
            adapter = get_adapter(retailer)
        except ValueError:
            print(f"=== {retailer} ({len(group)} product(s)) — pending store, skipped ===")
            print("  Available within 24 hours after signup.")
            print()
            continue
        rsettings = retailer_settings(settings, retailer)
        use_headless = resolve_headless(
            settings, retailer, headless, adapter.default_headless
        )
        profile_dir = (profiles_root / ".profiles" / retailer).resolve()
        state_path = (states_root / f"{retailer}.json").resolve()
        state = load_state(state_path)

        print(f"=== {adapter.brand} ({len(group)} product(s)) ===")
        print(f"Profile: {profile_dir}")
        print(f"Headless: {use_headless}")
        print()

        if retailer == "instacart" and (
            not profile_dir.exists() or not any(profile_dir.iterdir())
        ):
            notify_message(
                "Empty Instacart profile — authenticate first.\n"
                "  python -m price_monitor auth --retailer instacart",
                subject="Instacart: authentication required",
                email_to=notify_email,
            )
            return 2

        # Walmart SerpApi: sem browser / sem PerimeterX
        can_api = getattr(adapter, "can_use_api", None)
        if callable(can_api) and can_api(rsettings):
            print("  Mode: SerpApi (no browser)")
            print()
            for idx, product in enumerate(group, start=1):
                print(f"[{retailer} {idx}/{len(group)}] {product.name}")
                print(f"  URL: {product.url}")
                try:
                    scraped = adapter.scrape_via_api(product, rsettings)
                except Exception as exc:
                    print(f"  ERROR: {exc}")
                    mark_checked(
                        state,
                        adapter.product_key(product),
                        title=None,
                        current_price=None,
                        list_price=None,
                        status="error",
                        error=str(exc),
                    )
                    save_state(state_path, state)
                    exit_code = 1
                    print()
                    continue

                _record_result(
                    adapter=adapter,
                    product=product,
                    scraped=scraped,
                    state=state,
                    state_path=state_path,
                    cooldown=cooldown,
                    email_to=notify_email,
                )

            save_state(state_path, state)
            continue

        with sync_playwright() as p:
            # Walmart: se headless, tenta Playwright; senão já abre CDP.
            force_cdp = retailer == "walmart" and not use_headless
            context, page, closer, mode = _open_session(
                p,
                retailer=retailer,
                profile_dir=profile_dir,
                headless=use_headless,
                timezone_id=adapter.default_timezone,
                rsettings=rsettings,
                force_cdp=force_cdp,
            )
            try:
                try:
                    adapter.prepare_session(page, rsettings, headless=use_headless)
                except SessionExpiredError:
                    return 2

                for idx, product in enumerate(group, start=1):
                    print(f"[{retailer} {idx}/{len(group)}] {product.name}")
                    print(f"  URL: {product.url}")
                    try:
                        scraped = adapter.scrape(
                            page,
                            product,
                            rsettings,
                            headless=use_headless,
                            set_location=(idx == 1),
                        )
                    except SessionExpiredError as exc:
                        notify_message(
                            str(exc),
                            subject="Instacart: authentication required",
                            email_to=notify_email,
                        )
                        return 2
                    except ChallengeRequiredError as exc:
                        fallback = bool(rsettings.get("headed_fallback", True))
                        if (
                            retailer in {"safeway", "walmart"}
                            and use_headless
                            and fallback
                        ):
                            print(
                                "  Challenge in headless — opening Chrome CDP "
                                "(resolve Press & Hold if it appears)..."
                            )
                            closer()
                            context, page, closer, mode = _open_session(
                                p,
                                retailer=retailer,
                                profile_dir=profile_dir,
                                headless=False,
                                timezone_id=adapter.default_timezone,
                                rsettings=rsettings,
                                force_cdp=(retailer == "walmart"),
                            )
                            use_headless = False
                            try:
                                adapter.prepare_session(
                                    page, rsettings, headless=False
                                )
                                scraped = adapter.scrape(
                                    page,
                                    product,
                                    rsettings,
                                    headless=False,
                                    set_location=(idx == 1),
                                )
                            except ChallengeRequiredError as exc2:
                                print(f"  ERROR: {exc2}")
                                return 1
                        else:
                            print(f"  ERROR: {exc}")
                            return 1
                    except Exception as exc:
                        print(f"  ERROR while scraping: {exc}")
                        mark_checked(
                            state,
                            adapter.product_key(product),
                            title=None,
                            current_price=None,
                            list_price=None,
                            status="error",
                            error=str(exc),
                        )
                        save_state(state_path, state)
                        exit_code = 1
                        continue

                    _record_result(
                        adapter=adapter,
                        product=product,
                        scraped=scraped,
                        state=state,
                        state_path=state_path,
                        cooldown=cooldown,
                        email_to=notify_email,
                    )
            finally:
                closer()

        save_state(state_path, state)

    return exit_code


def run_auth(*, base_dir: Path, retailer: str) -> int:
    adapter = get_adapter(retailer)
    if not adapter.supports_auth:
        print(f"{adapter.brand} does not use the auth command.")
        return 1

    profile_dir = profile_dir_for(base_dir, retailer)
    print(f"Auth {adapter.brand} — profile: {profile_dir}")

    with sync_playwright() as p:
        context = create_context(
            p,
            profile_dir,
            headless=False,
            timezone_id=adapter.default_timezone,
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            return adapter.run_auth(page)
        finally:
            context.close()


def run_warm(
    *,
    config_path: Path,
    base_dir: Path,
    retailer: str,
    reset_profile: bool = False,
) -> int:
    """
    Renova cookies/sessão com janela visível, sem pedir Enter.
    Walmart usa Chrome CDP (melhor contra PerimeterX).
    """
    import shutil

    retailer = retailer.strip().lower()
    adapter = get_adapter(retailer)
    warm_fn = getattr(adapter, "warm_session", None)
    if not callable(warm_fn):
        print(f"{adapter.brand} has no warm_session.")
        return 1

    products, settings = load_config(config_path)
    rsettings = retailer_settings(settings, retailer)
    group = [p for p in products if p.retailer == retailer]
    product_url = group[0].url if group else None

    profile_dir = profile_dir_for(base_dir, retailer)
    if reset_profile and profile_dir.exists():
        print(f"Deleting burned profile: {profile_dir}")
        shutil.rmtree(profile_dir, ignore_errors=True)

    print(f"Warm {adapter.brand} — profile: {profile_dir}")
    if retailer == "walmart":
        print(
            "Mode: Chrome CDP. If Press & Hold appears, HOLD in the window.\n"
            "The script waits up to 5 minutes (no Enter)."
        )
    else:
        print("Mode: visible window (no human interaction; just waits to auto-clear)")

    with sync_playwright() as p:
        _context, page, closer, _mode = _open_session(
            p,
            retailer=retailer,
            profile_dir=profile_dir,
            headless=False,
            timezone_id=adapter.default_timezone,
            rsettings=rsettings,
            force_cdp=(retailer == "walmart"),
        )
        try:
            ok = warm_fn(page, rsettings, product_url=product_url)
        finally:
            closer()

    return 0 if ok else 1

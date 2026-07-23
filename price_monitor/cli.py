from __future__ import annotations

import argparse
import sys
from pathlib import Path

from price_monitor.adapters import list_retailers
from price_monitor.add_product import add_url_to_config, prompt_add_interactive
from price_monitor.envfile import load_dotenv
from price_monitor.runner import run_auth, run_check, run_warm

DEFAULT_CONFIG = "produtos.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified price monitor (Amazon, Safeway, Instacart, Target, Walmart)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Check prices and send alerts.")
    check.add_argument("--config", default=DEFAULT_CONFIG)
    check.add_argument(
        "--retailer",
        choices=list_retailers(),
        default=None,
        help="Filter one retailer (default: all in the JSON).",
    )
    check.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force headless or a visible window (overrides JSON).",
    )
    check.add_argument("--cooldown-hours", type=float, default=None)

    auth = sub.add_parser(
        "auth",
        help="OTP authentication (Instacart) with a visible window.",
    )
    auth.add_argument(
        "--retailer",
        choices=list_retailers(),
        required=True,
        help="Retailer (currently only instacart uses auth).",
    )

    warm = sub.add_parser(
        "warm",
        help=(
            "Refresh session/cookies with a visible window, no Enter "
            "(Safeway/Incapsula auto-clears)."
        ),
    )
    warm.add_argument(
        "--retailer",
        choices=list_retailers(),
        required=True,
    )
    warm.add_argument("--config", default=DEFAULT_CONFIG)
    warm.add_argument(
        "--reset-profile",
        action="store_true",
        help="Delete the retailer profile before warm (useful if Walmart burned the session).",
    )

    add = sub.add_parser(
        "add",
        help="Add product(s) to the JSON from URL (detects the retailer).",
    )
    add.add_argument(
        "urls",
        nargs="*",
        help="Product URL(s). With no arguments, enters interactive mode.",
    )
    add.add_argument("--config", default=DEFAULT_CONFIG)
    add.add_argument(
        "--retailer",
        choices=list_retailers(),
        default=None,
        help="Force the retailer (default: detect from URL).",
    )
    add.add_argument(
        "--target-price",
        type=float,
        default=None,
        required=False,
        help="Maximum price for an alert (required).",
    )
    add.add_argument(
        "--min-discount-percent",
        type=float,
        default=None,
        help="Minimum discount %% for an alert.",
    )
    add.add_argument(
        "--reference-price",
        type=float,
        default=None,
        help="Reference price used to compute discount.",
    )

    serve = sub.add_parser(
        "serve",
        help="Start the local web dashboard (http://127.0.0.1:8765).",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    return parser


def _run_add(args: argparse.Namespace, base: Path) -> int:
    config_path = (base / args.config).resolve()
    if not args.urls:
        return prompt_add_interactive(
            config_path,
            default_target_price=args.target_price,
        )

    if args.target_price is None:
        print(
            "Error: --target-price is required when adding URL(s).",
            file=sys.stderr,
        )
        return 1

    errors = 0
    for url in args.urls:
        try:
            action, entry, _ = add_url_to_config(
                config_path,
                url,
                target_price=args.target_price,
                min_discount_percent=args.min_discount_percent,
                reference_price=args.reference_price,
                retailer=args.retailer,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            errors += 1
            continue
        label = "Added" if action == "added" else "Updated"
        print(
            f"{label}: {entry['retailer']} | {entry['url']} | "
            f"${entry['target_price']:.2f}"
        )
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base = Path.cwd()
    load_dotenv(base / ".env", override=True)

    try:
        if args.command == "auth":
            return run_auth(base_dir=base, retailer=args.retailer)

        if args.command == "warm":
            return run_warm(
                config_path=(base / args.config).resolve(),
                base_dir=base,
                retailer=args.retailer,
                reset_profile=bool(getattr(args, "reset_profile", False)),
            )

        if args.command == "add":
            return _run_add(args, base)

        if args.command == "serve":
            from price_monitor.web import run_server

            run_server(host=args.host, port=args.port)
            return 0

        return run_check(
            config_path=(base / args.config).resolve(),
            base_dir=base,
            retailer_filter=args.retailer,
            headless=args.headless,
            cooldown_hours=args.cooldown_hours,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

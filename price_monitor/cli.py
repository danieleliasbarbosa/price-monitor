from __future__ import annotations

import argparse
import sys
from pathlib import Path

from price_monitor.adapters import list_retailers
from price_monitor.add_product import add_url_to_config, prompt_add_interactive
from price_monitor.runner import run_auth, run_check, run_warm

DEFAULT_CONFIG = "produtos.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor unificado de preços (Amazon, Safeway, Instacart, Target)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Verifica preços e envia alertas.")
    check.add_argument("--config", default=DEFAULT_CONFIG)
    check.add_argument(
        "--retailer",
        choices=list_retailers(),
        default=None,
        help="Filtra um varejista (padrão: todos do JSON).",
    )
    check.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Força headless ou janela (sobrescreve JSON).",
    )
    check.add_argument("--cooldown-hours", type=float, default=None)

    auth = sub.add_parser(
        "auth",
        help="Autenticação OTP (Instacart) com janela visível.",
    )
    auth.add_argument(
        "--retailer",
        choices=list_retailers(),
        required=True,
        help="Varejista (hoje só instacart usa auth).",
    )

    warm = sub.add_parser(
        "warm",
        help=(
            "Renova sessão/cookies com janela visível, sem Enter "
            "(Safeway/Incapsula auto-libera)."
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
        help="Apaga o perfil do varejista antes do warm (útil se o Walmart queimou a sessão).",
    )

    add = sub.add_parser(
        "add",
        help="Adiciona produto(s) ao JSON a partir da URL (detecta o varejista).",
    )
    add.add_argument(
        "urls",
        nargs="*",
        help="URL(s) do produto. Sem argumentos, entra no modo interativo.",
    )
    add.add_argument("--config", default=DEFAULT_CONFIG)
    add.add_argument(
        "--retailer",
        choices=list_retailers(),
        default=None,
        help="Força o varejista (padrão: detecta pela URL).",
    )
    add.add_argument(
        "--target-price",
        type=float,
        default=None,
        required=False,
        help="Preço máximo para alerta (obrigatório).",
    )
    add.add_argument(
        "--min-discount-percent",
        type=float,
        default=None,
        help="Desconto mínimo %% para alerta.",
    )
    add.add_argument(
        "--reference-price",
        type=float,
        default=None,
        help="Preço de referência para calcular desconto.",
    )
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
            "Erro: --target-price é obrigatório ao adicionar URL(s).",
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
            print(f"Erro: {exc}", file=sys.stderr)
            errors += 1
            continue
        label = "Adicionado" if action == "added" else "Atualizado"
        print(
            f"{label}: {entry['retailer']} | {entry['url']} | "
            f"${entry['target_price']:.2f}"
        )
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base = Path.cwd()

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

        return run_check(
            config_path=(base / args.config).resolve(),
            base_dir=base,
            retailer_filter=args.retailer,
            headless=args.headless,
            cooldown_hours=args.cooldown_hours,
        )
    except FileNotFoundError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

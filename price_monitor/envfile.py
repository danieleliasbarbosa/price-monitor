"""Carrega variáveis de um arquivo .env no ambiente (sem sobrescrever as já definidas)."""

from __future__ import annotations

from pathlib import Path


def load_dotenv(path: Path, *, override: bool = False) -> None:
    import os

    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # Por padrão não sobrescreve o shell; com override=True o .env vence.
        if override or key not in os.environ or os.environ.get(key, "") == "":
            os.environ[key] = value

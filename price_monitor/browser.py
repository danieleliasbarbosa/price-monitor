from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext

# Reduz sinal óbvio de automação (não resolve captcha; só parece browser normal).
_STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""


def find_chrome_executable() -> Path | None:
    env = os.environ.get("CHROME_PATH") or os.environ.get("GOOGLE_CHROME_BIN")
    if env and Path(env).is_file():
        return Path(env)
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def create_context(
    playwright,
    profile_dir: Path,
    *,
    headless: bool,
    timezone_id: str = "America/Los_Angeles",
    channel: str | None = "chrome",
) -> BrowserContext:
    """
    Abre Chromium persistente.

    Por padrão tenta o Google Chrome instalado (`channel=chrome`).
    Se o Chrome não existir, cai para o Chromium do Playwright.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    common = dict(
        user_data_dir=str(profile_dir),
        headless=headless,
        locale="en-US",
        timezone_id=timezone_id,
        viewport={"width": 1365, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )

    context: BrowserContext | None = None
    last_error: Exception | None = None
    channels_to_try: list[str | None] = [channel, None] if channel else [None]

    for ch in channels_to_try:
        try:
            kwargs = dict(common)
            if ch:
                kwargs["channel"] = ch
            context = playwright.chromium.launch_persistent_context(**kwargs)
            break
        except Exception as exc:
            last_error = exc
            context = None

    if context is None:
        raise RuntimeError(f"Não foi possível abrir o navegador ({last_error})")

    try:
        context.add_init_script(_STEALTH_INIT)
    except Exception:
        pass
    return context


class CdpChromeSession:
    """Chrome real via remote debugging (menos detectável que launch_persistent)."""

    def __init__(
        self,
        browser: Browser,
        context: BrowserContext,
        proc: subprocess.Popen | None,
    ) -> None:
        self.browser = browser
        self.context = context
        self.proc = proc

    def close(self) -> None:
        try:
            self.browser.close()
        except Exception:
            pass
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass


def create_cdp_chrome_session(
    playwright,
    profile_dir: Path,
    *,
    port: int = 9222,
    start_url: str = "about:blank",
) -> CdpChromeSession:
    """
    Sobe o Google Chrome com --remote-debugging-port e conecta via CDP.
    O PerimeterX do Walmart costuma aceitar isso melhor que o Playwright launch.
    """
    chrome = find_chrome_executable()
    if chrome is None:
        raise RuntimeError(
            "Google Chrome não encontrado. Instale o Chrome ou defina CHROME_PATH."
        )

    profile_dir = profile_dir.resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Porta livre se 9222 estiver ocupada
    use_port = port
    if _port_open("127.0.0.1", use_port):
        for candidate in range(9223, 9240):
            if not _port_open("127.0.0.1", candidate):
                use_port = candidate
                break

    proc = subprocess.Popen(
        [
            str(chrome),
            f"--remote-debugging-port={use_port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            start_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _port_open("127.0.0.1", use_port):
            break
        if proc.poll() is not None:
            raise RuntimeError("Chrome CDP encerrou ao iniciar.")
        time.sleep(0.25)
    else:
        proc.kill()
        raise RuntimeError(f"Timeout aguardando Chrome CDP na porta {use_port}.")

    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{use_port}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    try:
        context.add_init_script(_STEALTH_INIT)
    except Exception:
        pass
    return CdpChromeSession(browser, context, proc)

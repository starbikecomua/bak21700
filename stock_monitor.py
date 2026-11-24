# -*- coding: utf-8 -*-
"""
NKON stock monitor — lightweight edition using curl_cffi.

No browser required. curl_cffi impersonates Chrome TLS/HTTP2 fingerprints
so Cloudflare passes the request. Memory usage ~50 MB vs ~600 MB for Firefox.

CLI:
  python stock_monitor.py               # monitor: check all + notify on change
  python stock_monitor.py --check-only  # print status, no Telegram
  python stock_monitor.py --simulate-stock
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
import tempfile
import urllib.parse
import urllib.request
import ssl
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Logging — UTF-8 so Ukrainian text renders correctly everywhere
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False),
)
log = logging.getLogger("nkon")

# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
ORIGIN    = "https://www.nkon.nl"
ENV_FILE  = Path(".env")
STATE_DIR = Path(".monitor")

MAX_RETRIES   = 3
RETRY_DELAY_S = 30

# Impersonate target — must be in curl_cffi.requests.BrowserType
IMPERSONATE = "chrome131"


@dataclass(frozen=True)
class Product:
    name:       str
    url:        str
    state_file: Path


PRODUCTS: list[Product] = [
    Product(
        name       = "BAK N21700CG 5000mAh 10.2A NO WRAP",
        url        = ORIGIN + "/ua/bak-n21700cg-5000mah-10-2a-no-wrap.html",
        state_file = STATE_DIR / "stock_no_wrap.txt",
    ),
    Product(
        name       = "BAK N21700CG 5000mAh 10.2A CLEAR WRAP",
        url        = ORIGIN + "/ua/bak-n21700cg-5000mah-10-2a-clear-wrap.html",
        state_file = STATE_DIR / "stock_clear_wrap.txt",
    ),
    Product(
        name       = "** INR21700-M50LT 4930mAh 14.4A",
        url        = ORIGIN + "/ua/m50lt.html",
        state_file = STATE_DIR / "stock_m50lt.txt",
    ),
    Product(
        name       = "EVE INR21700-58E 5700mAh 16.8A",
        url        = ORIGIN + "/ua/eve-inr21700-58e-5700mah-16-8a.html",
        state_file = STATE_DIR / "stock_eve_58e.txt",
    ),
]

# ---------------------------------------------------------------------------
# Stock enum
# ---------------------------------------------------------------------------
class Stock(str, Enum):
    IN_STOCK     = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"


# ---------------------------------------------------------------------------
# Settings (env var → .env file)
# ---------------------------------------------------------------------------
def setting(name: str) -> str | None:
    value = os.environ.get(name)
    if value is not None:
        return value
    if os.environ.get("CI") or not ENV_FILE.is_file():
        return None
    prefix = name + "="
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(message: str) -> None:
    token   = setting("TELEGRAM_BOT_TOKEN") or ""
    chat_id = setting("TELEGRAM_CHAT_ID") or ""
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise RuntimeError("Configure TELEGRAM_BOT_TOKEN.")
    if not re.fullmatch(r"-?[0-9]+", chat_id):
        raise RuntimeError("Configure numeric TELEGRAM_CHAT_ID.")

    body = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
    req  = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, method="POST",
    )
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()

    with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Telegram rejected sendMessage (HTTP {resp.status}).")


# ---------------------------------------------------------------------------
# Stock state persistence
# ---------------------------------------------------------------------------
def record_stock(state_file: Path, current: Stock, notify: Callable) -> None:
    previous = None
    if state_file.exists():
        try:
            previous = Stock(state_file.read_text(encoding="utf-8").strip())
        except ValueError:
            pass
    if current == Stock.IN_STOCK and previous != Stock.IN_STOCK:
        notify()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(current.value + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Stock detection from raw HTML
# ---------------------------------------------------------------------------
def _detect_stock_from_html(html: str, product: Product) -> Stock:
    has_oos = (
        "Немає в наявності" in html
        or "Out of stock" in html
    )
    has_atc = "product-addtocart-button" in html

    if has_oos and not has_atc:
        return Stock.OUT_OF_STOCK
    if has_atc and not has_oos:
        return Stock.IN_STOCK
    # Both present or neither — IN_STOCK wins if button exists
    # (preorder pages show button without out-of-stock text)
    if has_atc:
        return Stock.IN_STOCK
    raise RuntimeError(
        f"[{product.name}] Cannot determine stock: oos={has_oos}, atc={has_atc}"
    )


# ---------------------------------------------------------------------------
# Single HTTP session — no browser, no Xvfb, ~50 MB RAM
# ---------------------------------------------------------------------------
def _run_session() -> dict[Product, Stock]:
    from curl_cffi import requests as cf_requests

    session = cf_requests.Session(impersonate=IMPERSONATE)
    headers = {"Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8,en-US;q=0.7,en;q=0.6"}

    results: dict[Product, Stock] = {}
    for product in PRODUCTS:
        resp = session.get(product.url, headers=headers, timeout=30)
        if resp.status_code == 403:
            raise RuntimeError(
                f"Cloudflare blocked the request for [{product.name}] "
                f"(HTTP 403). Try a different IP or impersonate target."
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"[{product.name}] Unexpected HTTP {resp.status_code}."
            )
        status = _detect_stock_from_html(resp.text, product)
        results[product] = status
        log.info("[%s] %s", product.name, status.value)

    return results


# ---------------------------------------------------------------------------
# Public check with retry
# ---------------------------------------------------------------------------
def check_all() -> dict[Product, Stock]:
    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            results = _run_session()
            if attempt > 1:
                log.info("Succeeded on attempt %d.", attempt)
            return results
        except Exception as exc:
            last_exc = exc
            log.warning("Attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                log.info("Retrying in %d s...", RETRY_DELAY_S)
                time.sleep(RETRY_DELAY_S)

    raise RuntimeError(
        f"All {MAX_RETRIES} attempts failed. Last error: {last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Simulate stock (offline self-test, no network)
# ---------------------------------------------------------------------------
def simulate_stock() -> None:
    sim_product = Product(
        name="TEST PRODUCT", url="", state_file=Path("unused")
    )

    with tempfile.TemporaryDirectory(prefix="nkon-sim-") as tmpdir:
        state_file    = Path(tmpdir) / "stock.txt"
        notifications: list[Stock] = []

        for expected, html in [
            (Stock.OUT_OF_STOCK, "<p>Немає в наявності</p>"),
            (Stock.IN_STOCK,     "<button id='product-addtocart-button'>Додати</button>"),
            (Stock.IN_STOCK,     "<button id='product-addtocart-button'>Додати</button>"),
        ]:
            actual = _detect_stock_from_html(html, sim_product)
            if actual != expected:
                raise AssertionError(f"Expected {expected}, got {actual}")
            record_stock(state_file, actual, lambda: notifications.append(actual))
            log.info("%s | notifications so far: %d", actual.value, len(notifications))

        if notifications != [Stock.IN_STOCK]:
            raise AssertionError(
                f"Expected exactly one IN_STOCK notification, got: {notifications}"
            )
        log.info("Simulation passed OK")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    args = sys.argv[1:]

    if args == ["--simulate-stock"]:
        simulate_stock()
        return

    if args not in ([], ["--check-only"]):
        raise SystemExit("Expected --check-only, --simulate-stock, or no arguments.")

    results = check_all()

    if args == ["--check-only"]:
        return

    # Monitor mode: notify on IN_STOCK transition
    for product, current in results.items():
        def _notify(p=product) -> None:
            send_telegram(f"Доступен к заказу на NKON!\n{p.name}\n{p.url}")
            log.info("Telegram notification sent for: %s", p.name)

        record_stock(product.state_file, current, _notify)


if __name__ == "__main__":
    main()

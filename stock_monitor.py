# -*- coding: utf-8 -*-
"""
NKON stock monitor — Camoufox edition.

Checks product pages without authentication. Sends a Telegram notification
when a product becomes available (in stock or available for pre-order).

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


@dataclass(frozen=True)
class Product:
    name:       str
    url:        str
    heading_re: re.Pattern
    state_file: Path


PRODUCTS: list[Product] = [
    Product(
        name       = "BAK N21700CG 5000mAh 10.2A NO WRAP",
        url        = ORIGIN + "/ua/bak-n21700cg-5000mah-10-2a-no-wrap.html",
        heading_re = re.compile(r"BAK\s+N21700CG.*5000.*БЕЗ\s+УПАКОВКИ", re.IGNORECASE),
        state_file = STATE_DIR / "stock_no_wrap.txt",
    ),
    Product(
        name       = "BAK N21700CG 5000mAh 10.2A CLEAR WRAP",
        url        = ORIGIN + "/ua/bak-n21700cg-5000mah-10-2a-clear-wrap.html",
        heading_re = re.compile(r"BAK\s+N21700CG.*5000.*ПРОЗОР", re.IGNORECASE),
        state_file = STATE_DIR / "stock_clear_wrap.txt",
    ),
    Product(
        name       = "** INR21700-M50LT 4930mAh 14.4A",
        url        = ORIGIN + "/ua/m50lt.html",
        heading_re = re.compile(r"\*\*\s+INR21700-M50LT", re.IGNORECASE),
        state_file = STATE_DIR / "stock_m50lt.txt",
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
    import ssl
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
# Browser helpers
# ---------------------------------------------------------------------------
def _navigate(page, url: str, description: str) -> None:
    from urllib.parse import urlparse
    response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    if response is None:
        raise RuntimeError(f"NKON returned no response for {description}.")
    if not response.ok:
        raise RuntimeError(f"NKON returned HTTP {response.status} for {description}.")
    parsed = urlparse(page.url)
    if parsed.scheme != "https" or parsed.hostname != "www.nkon.nl":
        raise RuntimeError("NKON redirected outside its storefront.")


def _detect_stock(page, product: Product) -> Stock:
    page.get_by_role("heading", level=1, name=product.heading_re).wait_for(timeout=30_000)

    out_of_stock = page.locator("text=/^(Немає в наявності|Out of stock)$/i")
    add_to_cart  = page.locator("button#product-addtocart-button").or_(
        page.get_by_role("button", name=re.compile(
            r"^(Додати (до|в) кошик|У кошик|Add to Cart)$", re.IGNORECASE
        ))
    )

    deadline = time.monotonic() + 15
    while True:
        try:
            oos = out_of_stock.count()
        except Exception:
            oos = 0
        try:
            atc = add_to_cart.count()
        except Exception:
            atc = 0
        if oos > 0 or atc > 0:
            break
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"[{product.name}] Timed out (15 s): neither out-of-stock text "
                "nor add-to-cart button appeared."
            )
        time.sleep(0.3)

    unavailable = out_of_stock.first.is_visible()
    buyable     = add_to_cart.first.is_visible()
    if unavailable == buyable:
        raise RuntimeError(f"[{product.name}] Stock signals missing or contradictory.")
    return Stock.OUT_OF_STOCK if unavailable else Stock.IN_STOCK


# ---------------------------------------------------------------------------
# Single browser session — no login, checks all products
# ---------------------------------------------------------------------------
def _run_session(headed: bool) -> dict[Product, Stock]:
    from camoufox.sync_api import Camoufox
    from camoufox.fingerprints import Screen

    with Camoufox(
        headless=not headed,
        geoip=True,
        locale=["uk-UA", "uk", "ru", "en-US", "en"],
        screen=Screen(min_width=1280, max_width=1920, min_height=800, max_height=1080),
        os="windows",
        i_know_what_im_doing=True,
        virtual_display=not headed and sys.platform.startswith("linux"),
    ) as browser:
        page = browser.new_page()
        try:
            results: dict[Product, Stock] = {}
            for product in PRODUCTS:
                _navigate(page, product.url, product.name)
                status = _detect_stock(page, product)
                results[product] = status
                log.info("[%s] %s", product.name, status.value)
            return results
        except Exception:
            if headed:
                shot = STATE_DIR / "nkon-error.png"
                shot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(shot))
                log.error("Screenshot saved to %s", shot)
                log.error("Keeping browser open 90 s for inspection.")
                time.sleep(90)
            raise


# ---------------------------------------------------------------------------
# Public check with retry
# ---------------------------------------------------------------------------
def check_all() -> dict[Product, Stock]:
    headed    = os.environ.get("NKON_HEADED") == "1"
    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            results = _run_session(headed)
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
    from camoufox.sync_api import Camoufox

    sim_product = Product(
        name="TEST PRODUCT", url="about:blank",
        heading_re=re.compile(r"TEST PRODUCT", re.IGNORECASE),
        state_file=Path("unused"),
    )

    with tempfile.TemporaryDirectory(prefix="nkon-sim-") as tmpdir:
        state_file    = Path(tmpdir) / "stock.txt"
        notifications: list[Stock] = []

        with Camoufox(headless=True, i_know_what_im_doing=True) as browser:
            page = browser.new_page()
            for expected in [Stock.OUT_OF_STOCK, Stock.IN_STOCK, Stock.IN_STOCK]:
                markup = (
                    "<p>Немає в наявності</p>"
                    if expected == Stock.OUT_OF_STOCK
                    else "<button id='product-addtocart-button'>Додати до кошик</button>"
                )
                page.set_content(f"<h1>TEST PRODUCT</h1>{markup}")
                actual = _detect_stock(page, sim_product)
                if actual != expected:
                    raise AssertionError(f"Expected {expected}, detected {actual}")
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
        raise SystemExit(
            "Expected --check-only, --simulate-stock, or no arguments."
        )

    results = check_all()

    if args == ["--check-only"]:
        return

    # Monitor mode: notify when any product transitions to IN_STOCK
    for product, current in results.items():
        def _notify(p=product) -> None:
            send_telegram(f"Доступен к заказу на NKON!\n{p.name}\n{p.url}")
            log.info("Telegram notification sent for: %s", p.name)

        record_stock(product.state_file, current, _notify)


if __name__ == "__main__":
    main()

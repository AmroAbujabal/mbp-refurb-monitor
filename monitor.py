#!/usr/bin/env python3
"""Watch Apple CA's refurbished store for MacBook Pros under a price, push via ntfy.sh.

Run once:      python3 monitor.py
Test the push: python3 monitor.py --test-notify
Config lives in config.env (env vars of the same name win).
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.env"
# CI overrides this to a git-tracked path so dedupe survives ephemeral runners.
STATE_FILE = Path(os.environ.get("STATE_FILE") or HERE / "seen.json")
LOG_FILE = HERE / "monitor.log"

STORE_URL = "https://www.apple.com/ca/shop/refurbished/mac/macbook-pro"
# Apple ships the whole product grid as a JSON blob assigned to this global.
MARKER = "window.REFURB_GRID_BOOTSTRAP = "
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


class ParseError(RuntimeError):
    """Page loaded but didn't look like we expect - i.e. Apple changed their HTML."""


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {msg}"
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)


def load_config():
    cfg = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                val = val.split("#", 1)[0].strip().strip("\"'")
                cfg[key.strip()] = val
    for key in ("NTFY_TOPIC", "MAX_PRICE"):
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    if not cfg.get("NTFY_TOPIC"):
        raise SystemExit(f"NTFY_TOPIC not set - add it to {CONFIG_FILE}")
    return cfg["NTFY_TOPIC"], float(cfg.get("MAX_PRICE", 1900))


def ssl_context():
    """python.org's macOS Python ships no CA bundle; use the certifi one it installs alongside."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()  # system CAs (Linux, Homebrew python)


# Apple takes the store offline for inventory updates and returns 503; observed
# 2026-09-12. A maintenance blip should not look like a broken monitor.
TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}


def fetch(url=STORE_URL, timeout=30, attempts=3):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-CA,en;q=0.9",
    })
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code not in TRANSIENT_CODES or attempt == attempts:
                raise
            log(f"WARN attempt {attempt}/{attempts}: HTTP {exc.code}, retrying")
        except (urllib.error.URLError, OSError) as exc:
            if attempt == attempts:
                raise
            log(f"WARN attempt {attempt}/{attempts}: {exc}, retrying")
        time.sleep(5 * attempt)


def normalize(text):
    """Apple mixes non-breaking spaces and non-breaking hyphens into titles."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ").replace("‑", "-")).strip()


def product_url(path):
    """Apple's path -> absolute URL. Anything not a plain relative path is untrusted:
    "@evil.com/x" would concatenate into https://www.apple.com@evil.com/x, which
    browsers resolve to evil.com. That URL is one tap away in the notification."""
    if isinstance(path, str) and path.startswith("/") and not path.startswith("//"):
        return "https://www.apple.com" + path
    return STORE_URL


def as_product(node):
    """Return a product dict if this node is a product listing, else None."""
    if not ("title" in node and "partNumber" in node and isinstance(node.get("price"), dict)):
        return None
    part = node["partNumber"]
    raw = node["price"].get("currentPrice", {}).get("raw_amount")
    if not (raw and part):
        return None
    try:
        price = float(raw)
    except (TypeError, ValueError):
        log(f"WARN skipping {part}: unparseable price {raw!r}")
        return None
    return {"part": part, "title": normalize(str(node["title"])), "price": price,
            "url": product_url(node.get("productDetailsUrl"))}


def collect_products(node, products):
    """Recursively gather product listings from anywhere in the JSON tree.

    Walking for shape rather than following a fixed path means Apple can reshuffle
    their nesting without breaking us; only renaming the fields would.
    """
    if isinstance(node, dict):
        found = as_product(node)
        if found:
            products[found["part"]] = found
        for value in node.values():
            collect_products(value, products)
    elif isinstance(node, list):
        for value in node:
            collect_products(value, products)


def parse_products(html):
    """Pull every product out of the embedded grid JSON. Raises ParseError if the shape changed."""
    start = html.find(MARKER)
    if start < 0:
        raise ParseError(f"{MARKER.strip()} not found in page")
    try:
        data, _ = json.JSONDecoder().raw_decode(html, start + len(MARKER))
    except ValueError as exc:
        raise ParseError(f"grid JSON did not parse: {exc}") from exc

    products = {}
    collect_products(data, products)
    if not products:
        raise ParseError("grid JSON parsed but contained no products")
    return list(products.values())


def macbook_pros(products):
    return [p for p in products if "macbook pro" in p["title"].lower()]


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        log("WARN seen.json was corrupt, starting fresh")
        return {}
    if not isinstance(data, dict):
        log("WARN seen.json was not an object, starting fresh")
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def new_matches(matches, seen):
    """New part number, or same part at a lower price than when we last told you."""
    return [m for m in matches
            if m["part"] not in seen or m["price"] < seen[m["part"]]]


def notify(topic, title, body, click=STORE_URL):
    """POST to ntfy.sh. Returns True on success. Header values must stay ASCII."""
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": title.encode("ascii", "replace").decode("ascii"),
            "Priority": "high",
            "Tags": "computer,moneybag",
            "Click": click,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=ssl_context()) as resp:
            return resp.status < 300
    except (urllib.error.URLError, OSError) as exc:
        log(f"ERROR ntfy push failed: {exc}")
        return False


def check_once(topic, max_price):
    products = parse_products(fetch())
    mbps = macbook_pros(products)
    if not mbps:
        raise ParseError(f"{len(products)} products parsed but none are MacBook Pros")

    cheapest = min(mbps, key=lambda p: p["price"])
    matches = sorted((p for p in mbps if p["price"] < max_price), key=lambda p: p["price"])
    seen = load_state()
    fresh = new_matches(matches, seen)

    pushed_parts = set()
    if fresh:
        body = "\n".join(f"${m['price']:,.0f} - {m['title']}\n{m['url']}" for m in fresh)
        title = f"MacBook Pro under ${max_price:,.0f}: {len(fresh)} listing(s)"
        click = fresh[0]["url"] if len(fresh) == 1 else STORE_URL
        if notify(topic, title, body, click):
            pushed_parts = {m["part"] for m in fresh}
        else:
            # Push failed - leave state alone so the next run retries these.
            log(f"checked={len(mbps)} cheapest=${cheapest['price']:,.2f} "
                f"matches={len(matches)} notified=0 (push failed, will retry)")
            return 1

    # Keep the price we last *told you about*, not just the last price seen: otherwise a
    # listing that ticks up and back down again re-alerts at a price you already heard.
    STATE_FILE.write_text(json.dumps(
        {m["part"]: (m["price"] if m["part"] in pushed_parts else seen.get(m["part"], m["price"]))
         for m in matches}, indent=2), encoding="utf-8")
    log(f"checked={len(mbps)} cheapest=${cheapest['price']:,.2f} "
        f"[{cheapest['title'][:60]}] matches={len(matches)} notified={len(pushed_parts)}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--test-notify", action="store_true",
        help="send a test push and exit, to verify your ntfy topic works")
    args = ap.parse_args()
    topic, max_price = load_config()  # SystemExit here is loud on purpose

    if args.test_notify:
        ok = notify(
            topic, "MacBook Pro monitor test",
            "If you can read this, notifications work.")
        # Deliberately not logging the topic: monitor.log is not mode 600 and the
        # topic is the only thing gating access to your notifications.
        log(f"test notification {'sent' if ok else 'FAILED'}")
        return 0 if ok else 1

    try:
        return check_once(topic, max_price)
    except ParseError as exc:
        log(f"ERROR page structure changed: {exc}")
        return 1
    except (urllib.error.URLError, OSError) as exc:
        log(f"ERROR fetch failed: {exc}")
        return 1
    except Exception as exc:  # never die to a bare traceback: cron discards stderr
        log(f"ERROR unexpected: {exc!r}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

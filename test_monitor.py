#!/usr/bin/env python3
"""Self-check: python3 test_monitor.py  (no pytest needed)"""
import ast
import json
import tempfile
from pathlib import Path

import monitor

def page(products):
    return "<html><script>" + monitor.MARKER + json.dumps({"products": products}) + ";</script></html>"

def prod(part, title, amount):
    return {"partNumber": part, "title": title,
            "price": {"currentPrice": {"raw_amount": amount}},
            "productDetailsUrl": "/ca/shop/product/" + part}

FIXTURE = page([
    prod("A1", "Refurbished 14-inch MacBook Pro Apple M5 chip", "2379.00"),
    prod("A2", "Refurbished 13‑inch MacBook\xa0Pro Apple M1 chip", "1049.00"),  # nbsp + nb-hyphen
    prod("A3", "Refurbished 15‑inch MacBook\xa0Air Apple M5 chip", "999.00"),
    prod("A4", "Refurbished MacBook Neo Apple A18 Pro chip - Silver", "799.00"),
    prod("A5", "Refurbished 24-inch iMac Apple M4 Chip", "1299.00"),
])

def test_parse_and_filter():
    products = monitor.parse_products(FIXTURE)
    assert len(products) == 5, products
    pros = {p["part"] for p in monitor.macbook_pros(products)}
    # A2 only matches if nbsp is normalized; Air/Neo/iMac must never match.
    assert pros == {"A1", "A2"}, pros
    a2 = next(p for p in products if p["part"] == "A2")
    assert a2["price"] == 1049.0
    assert a2["url"].startswith("https://www.apple.com/ca/shop/product/")

def test_price_threshold():
    pros = monitor.macbook_pros(monitor.parse_products(FIXTURE))
    under = [p["part"] for p in pros if p["price"] < 1900]
    assert under == ["A2"], under

def test_notify_dedupe():
    m = [{"part": "A2", "price": 1049.0}]
    assert monitor.new_matches(m, {}) == m                      # never seen -> notify
    assert monitor.new_matches(m, {"A2": 1049.0}) == []         # same price -> stay quiet
    assert monitor.new_matches(m, {"A2": 1199.0}) == m          # dropped further -> notify
    assert monitor.new_matches(m, {"A2": 999.0}) == []          # went up a bit -> stay quiet

def test_broken_page_is_loud():
    for bad in ["<html>Apple redesigned everything</html>",
                page([]),
                "<html><script>" + monitor.MARKER + "{not json;</script></html>"]:
        try:
            monitor.parse_products(bad)
        except monitor.ParseError:
            continue
        raise AssertionError("silently accepted a broken page: " + bad[:40])

def test_hostile_product_url_is_rejected():
    """A crafted productDetailsUrl must never produce a link off apple.com."""
    hostile = ["@evil.com/x", "//evil.com/x", "https://evil.com/x",
               "\\evil.com", None, 12, "javascript:alert(1)"]
    for bad in hostile:
        url = monitor.product_url(bad)
        assert url == monitor.STORE_URL or url.startswith("https://www.apple.com/"), (bad, url)
        assert "evil.com" not in url, (bad, url)
    assert monitor.product_url("/ca/shop/product/x") == "https://www.apple.com/ca/shop/product/x"


def test_topic_never_reaches_the_log():
    """No log() call anywhere may interpolate the ntfy topic.

    A whole-file grep for "topic" can't work: notify() legitimately builds
    f"https://ntfy.sh/{topic}". So check log() call sites specifically.
    """
    tree = ast.parse(Path(monitor.__file__).read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "log"]
    assert len(calls) >= 5, f"only found {len(calls)} log() calls - did log() get renamed?"
    for node in calls:
        for arg in node.args:
            src = ast.unparse(arg)
            assert "topic" not in src, f"log() call leaks the ntfy topic: {src}"


class Harness:
    """Point check_once at temp state/log files and stub out the network."""

    def __init__(self, html, push_ok=True):
        self.dir = tempfile.TemporaryDirectory()
        self.pushes = []
        self.push_ok = push_ok
        self._html = html
        self._saved = (monitor.STATE_FILE, monitor.LOG_FILE, monitor.fetch, monitor.notify)

    def __enter__(self):
        monitor.STATE_FILE = Path(self.dir.name) / "seen.json"
        monitor.LOG_FILE = Path(self.dir.name) / "monitor.log"
        monitor.fetch = lambda *a, **k: self._html
        monitor.notify = lambda *a, **k: (self.pushes.append(a[2]), self.push_ok)[1]
        return self

    def __exit__(self, *exc):
        monitor.STATE_FILE, monitor.LOG_FILE, monitor.fetch, monitor.notify = self._saved
        self.dir.cleanup()

    def set_html(self, html):
        self._html = html

    @property
    def state(self):
        return json.loads(monitor.STATE_FILE.read_text()) if monitor.STATE_FILE.exists() else None


def test_zero_macbook_pros_is_an_error_not_no_deals():
    """Products parse but none are MacBook Pros => Apple changed something. Must raise."""
    only_airs = page([prod("B1", "Refurbished MacBook\xa0Air M5", "999.00")])
    with Harness(only_airs) as h:
        try:
            monitor.check_once("topic", 1900)
        except monitor.ParseError:
            assert h.pushes == []
            return
        raise AssertionError("zero MacBook Pros was treated as 'no deals'")


def test_failed_push_leaves_state_untouched():
    """If ntfy is down we must retry next run, not swallow the alert."""
    with Harness(FIXTURE, push_ok=False) as h:
        assert monitor.check_once("topic", 1900) == 1
        assert h.state is None, "state was written despite a failed push"
        h.push_ok = True
        assert monitor.check_once("topic", 1900) == 0
        assert len(h.pushes) == 2 and h.state == {"A2": 1049.0}


def test_price_bounce_does_not_realert():
    """Notified at 1049 -> rises to 1199 -> back to 1049. Only the first should alert."""
    with Harness(FIXTURE) as h:
        monitor.check_once("topic", 1900)
        assert len(h.pushes) == 1 and h.state == {"A2": 1049.0}

        h.set_html(page([prod("A2", "Refurbished 13-inch MacBook Pro M1", "1199.00")]))
        monitor.check_once("topic", 1900)
        assert len(h.pushes) == 1, "alerted on a price increase"
        assert h.state == {"A2": 1049.0}, "state forgot the price we announced"

        h.set_html(page([prod("A2", "Refurbished 13-inch MacBook Pro M1", "1049.00")]))
        monitor.check_once("topic", 1900)
        assert len(h.pushes) == 1, "re-alerted at a price already announced"


def test_delisted_item_realerts_when_it_returns():
    with Harness(FIXTURE) as h:
        monitor.check_once("topic", 1900)
        h.set_html(page([prod("A1", "Refurbished 14-inch MacBook Pro M5", "2379.00")]))  # A2 gone
        monitor.check_once("topic", 1900)
        assert h.state == {}, h.state
        h.set_html(FIXTURE)
        monitor.check_once("topic", 1900)
        assert len(h.pushes) == 2, "a returning listing should alert again"


def test_bad_state_file_does_not_crash():
    with Harness(FIXTURE) as h:
        monitor.STATE_FILE.write_text('{"A2": null}')
        assert monitor.check_once("topic", 1900) == 0
        monitor.STATE_FILE.write_text('["not", "an", "object"]')
        assert monitor.check_once("topic", 1900) == 0


def test_config_tolerates_comments_and_quotes():
    with tempfile.TemporaryDirectory() as d:
        saved = monitor.CONFIG_FILE
        monitor.CONFIG_FILE = Path(d) / "config.env"
        monitor.CONFIG_FILE.write_text('NTFY_TOPIC="abc"  # my topic\nMAX_PRICE=1900 # CAD\n')
        try:
            assert monitor.load_config() == ("abc", 1900.0)
        finally:
            monitor.CONFIG_FILE = saved


if __name__ == "__main__":
    for name, fn in sorted(vars().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
    print("all passed")

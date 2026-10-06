#!/usr/bin/env python3
"""
Riftbound gallery watcher: opens the official card gallery in a headless browser,
collects every card image, and posts newly appeared cards to a Discord webhook.
Each new card is posted as its own separate message.

Test (posts nothing):   python riftbound_gallery_watcher.py --list
Run for real:           python riftbound_gallery_watcher.py
GitHub Actions:         python riftbound_gallery_watcher.py --once

Optional env vars: GALLERY_URL, CARD_SELECTOR, POLL_SECONDS, DISCORD_WEBHOOK_URL
"""
import json
import os
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from playwright.sync_api import sync_playwright

URL = os.environ.get("GALLERY_URL", "https://playriftbound.com/en-us/card-gallery/")
SELECTOR = os.environ.get("CARD_SELECTOR", "main img")
POLL = int(os.environ.get("POLL_SECONDS", "1800"))
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
STATE_FILE = "gallery_state.json"
SKIP_WORDS = ("riotbar", "logo", "icon", "sprite", "avatar", ".svg", "news_live")

# query params that change the rendering, not the card identity
IGNORE_PARAMS = {"w", "h", "q", "fm", "auto", "dpr", "fit", "v", "t", "cb"}

GRAB_JS = """els => els.map(e => ({
    src: e.currentSrc || e.src || '',
    alt: e.alt || '',
    w: e.naturalWidth || 0
}))"""


def norm(url):
    """Stable identity for an image URL: drops size/cache params and fragments."""
    u = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(u.query) if k.lower() not in IGNORE_PARAMS]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), ""))


def scrape():
    """Return {normalized_key: (image_url, card_name)} for every card image found."""
    raw = {}  # src -> (alt, width), collected on every scroll step

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(URL, wait_until="networkidle", timeout=60000)

        def collect():
            for it in page.eval_on_selector_all(SELECTOR, GRAB_JS):
                if it["src"] and it["src"] not in raw:
                    raw[it["src"]] = (it["alt"], it["w"])

        stable = 0
        last_total = -1
        for _ in range(300):  # hard cap so it can never loop forever
            collect()

            # click any "load more" style button
            try:
                btn = page.get_by_role("button", name=re.compile(r"(load|show|view) more", re.I))
                if btn.count() and btn.first.is_visible():
                    btn.first.click()
            except Exception:
                pass

            page.evaluate("window.scrollBy(0, 1500)")
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(900)

            at_bottom = page.evaluate(
                "window.innerHeight + window.scrollY >= document.body.scrollHeight - 5"
            )
            if len(raw) == last_total and at_bottom:
                stable += 1
                if stable >= 6:
                    break
            else:
                stable = 0
            last_total = len(raw)

        collect()
        browser.close()

    cards, skipped = {}, 0  # norm_key -> (src, name)
    for src, (alt, w) in raw.items():
        if not src.startswith("http") or any(word in src.lower() for word in SKIP_WORDS):
            skipped += 1
            continue
        if w and w < 120:  # skip tiny images
            skipped += 1
            continue
        key = norm(src)
        if key not in cards:  # collapses the same card seen at different sizes
            cards[key] = (src, alt or src.rsplit("/", 1)[-1])

    print(f"Scraped {len(raw)} images total, kept {len(cards)} unique cards, skipped {skipped}.")
    return cards


def post_one(src, name):
    """Post a single card to Discord as its own message."""
    payload = {
        "content": "New Riftbound card revealed!",
        "embeds": [
            {
                "title": name[:250],
                "image": {"url": src.replace("w=302", "w=744")},  # larger version
                "color": 0xC89B3C,
            }
        ],
    }
    while True:
        r = requests.post(WEBHOOK, json=payload, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)))
            continue
        r.raise_for_status()
        break
    time.sleep(1.5)  # stay under Discord's webhook rate limit


def load_seen():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        raise RuntimeError(f"{STATE_FILE} is not valid JSON. Fix it or delete it to start over.")


def save_state(keys):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(keys), f)
    os.replace(tmp, STATE_FILE)  # atomic, no half-written file


def check():
    cards = scrape()
    if not cards:
        raise RuntimeError("Scrape returned nothing (page layout may have changed).")
    seen = load_seen()
    if seen is None:
        save_state(cards.keys())
        print(f"Baseline saved: {len(cards)} cards (nothing posted).")
        return

    # normalize old entries too, so an existing state file still matches
    seen_set = {norm(s) for s in seen}
    print(f"Previously seen: {len(seen_set)} cards.")
    new = {k: v for k, v in cards.items() if k not in seen_set}
    if not new:
        print("No new cards.")
        return

    print(f"{len(new)} new cards, posting...")
    for key, (src, name) in new.items():
        post_one(src, name)
        seen_set.add(key)
        save_state(seen_set)  # saved after EACH post, so a crash never causes re-posts


def main():
    if "--list" in sys.argv:
        cards = scrape()
        print(f"Found {len(cards)} card images with selector {SELECTOR!r}:\n")
        for key, (src, name) in list(cards.items())[-25:]:
            print(f"  {name}\n    {src}")
        if not cards:
            print("Nothing found. Try a different CARD_SELECTOR.")
        return

    if not WEBHOOK:
        sys.exit("Set DISCORD_WEBHOOK_URL first (or use --list to test).")

    once = "--once" in sys.argv  # used by GitHub Actions: check one time, then exit
    while True:
        try:
            check()
        except Exception as e:  # keep running through temporary failures
            print(f"Error: {e}")
            if once:
                sys.exit(1)
        if once:
            return
        time.sleep(POLL)


if __name__ == "__main__":
    main()

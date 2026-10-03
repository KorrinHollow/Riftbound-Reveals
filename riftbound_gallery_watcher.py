#!/usr/bin/env python3
"""
Riftbound gallery watcher: opens the official card gallery in a headless browser,
collects every card image, and posts newly appeared cards to a Discord webhook.
Each new card is posted as its own separate message.
Free: no API keys, no paid services.

Setup:
  pip install playwright requests
  playwright install chromium

Step 1 - test what it can see (does not post anything):
  python riftbound_gallery_watcher.py --list

Step 2 - run for real:
  export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
  python riftbound_gallery_watcher.py

The first real run saves all current cards as a baseline and posts nothing.
After that, any card that newly appears gets posted.

Optional env vars:
  GALLERY_URL      default https://playriftbound.com/en-us/card-gallery/
  CARD_SELECTOR    CSS selector for card images (default: "main img"). If --list
                   shows junk or misses cards, inspect a card image in your browser
                   and set a tighter selector, e.g. "img[alt]" or ".card-grid img".
  POLL_SECONDS     default 1800 (30 min)
"""
import json
import os
import sys
import time

import requests
from playwright.sync_api import sync_playwright

URL = os.environ.get("GALLERY_URL", "https://playriftbound.com/en-us/card-gallery/")
SELECTOR = os.environ.get("CARD_SELECTOR", "main img")
POLL = int(os.environ.get("POLL_SECONDS", "1800"))
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
STATE_FILE = "gallery_state.json"
SKIP_WORDS = ("riotbar", "logo", "icon", "sprite", "avatar", ".svg", "news_live")


def scrape():
    """Return {image_url: card_name} for every card image found on the gallery."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(URL, wait_until="networkidle", timeout=60000)

        # Scroll until no new images load (handles lazy loading / infinite scroll)
        last_count, stable = -1, 0
        while stable < 3:
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1200)
            count = page.eval_on_selector_all(SELECTOR, "els => els.length")
            stable = stable + 1 if count == last_count else 0
            last_count = count

        items = page.eval_on_selector_all(
            SELECTOR,
            """els => els.map(e => ({
                src: e.currentSrc || e.src || '',
                alt: e.alt || '',
                w: e.naturalWidth || 0
            }))""",
        )
        browser.close()

    cards = {}
    for it in items:
        src = it["src"]
        if not src.startswith("http") or any(w in src.lower() for w in SKIP_WORDS):
            continue
        if it["w"] and it["w"] < 120:  # skip tiny images
            continue
        cards[src] = it["alt"] or src.rsplit("/", 1)[-1]
    return cards


def post(new_cards):
    """Post each new card to Discord as its own separate message."""
    for src, name in new_cards.items():
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


def check():
    cards = scrape()
    if not cards:
        raise RuntimeError("Scrape returned nothing (page layout may have changed).")
    seen = load_seen()
    if seen is None:
        print(f"Baseline saved: {len(cards)} cards (nothing posted).")
        json.dump(sorted(cards), open(STATE_FILE, "w"))
        return
    new = {s: n for s, n in cards.items() if s not in set(seen)}
    if new:
        print(f"{len(new)} new cards, posting...")
        post(new)
        json.dump(sorted(set(seen) | set(cards)), open(STATE_FILE, "w"))
    else:
        print("No new cards.")


def main():
    if "--list" in sys.argv:
        cards = scrape()
        print(f"Found {len(cards)} card images with selector {SELECTOR!r}:\n")
        for src, name in list(cards.items())[:25]:
            print(f"  {name}\n    {src}")
        if len(cards) > 25:
            print(f"  ... and {len(cards) - 25} more")
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

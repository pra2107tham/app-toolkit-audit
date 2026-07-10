"""Browser-use verification loop: every cited evidence URL that plain HTTP
could not fetch cleanly (403 bot-walls, redirect loops, 4xx/5xx) is retried in
headless Chromium with a rendered-DOM text extraction. The url_text_cache is
updated in place; re-running the verify stage then re-grades affected apps
against what a real browser saw.

Run: uv run python -m src.browser_fetch
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT / "out" / "url_text_cache.json"
MAX_CHARS = 3500


def main():
    cache = json.loads(CACHE_FILE.read_text())
    retry = [u for u, e in cache.items() if not (200 <= e["status"] < 400)]
    print(f"retrying {len(retry)} URLs in headless Chromium")
    fixed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        for url in retry:
            old = cache[url]["status"]
            try:
                resp = page.goto(url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)  # let SPAs render
                status = resp.status if resp else 0
                text = page.evaluate("() => document.body ? document.body.innerText : ''")
                text = " ".join(text.split())[:MAX_CHARS]
                if 200 <= status < 400 and len(text) > 200:
                    cache[url] = {"status": status, "text": text, "via": "browser"}
                    fixed += 1
                    print(f"  RECOVERED {old}->{status} {url[:90]}")
                else:
                    cache[url] = {"status": status, "text": text or cache[url]["text"], "via": "browser"}
                    print(f"  still bad {old}->{status} {url[:90]}")
            except Exception as e:
                print(f"  browser error ({old}) {url[:90]} :: {str(e)[:80]}")
        browser.close()
    CACHE_FILE.write_text(json.dumps(cache))
    print(f"recovered {fixed}/{len(retry)} URLs via browser")


if __name__ == "__main__":
    main()

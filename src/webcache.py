"""Deterministic page-text fetcher with a disk cache. Used to pre-fetch cited
evidence pages so verifier/judge agents grade against provided text instead of
being trusted to call fetch tools themselves."""
import html as html_lib
import json
import re
import threading
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT / "out" / "url_text_cache.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

_lock = threading.Lock()
_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    return _cache


def fetch_text(url: str, max_chars: int = 3500) -> dict:
    """Return {status, text} for a URL, cached on disk. status 0 = fetch error."""
    key = url.split("#")[0]
    with _lock:
        cache = _load()
        if key in cache:
            return cache[key]
    try:
        r = requests.get(key, timeout=20, allow_redirects=True, headers=UA)
        text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", r.text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html_lib.unescape(re.sub(r"\s+", " ", text)).strip()
        entry = {"status": r.status_code, "text": text[:max_chars]}
    except Exception as e:
        entry = {"status": 0, "text": f"FETCH_ERROR: {e}"}
    with _lock:
        cache = _load()
        cache[key] = entry
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache))
    return entry


def evidence_dump(record: dict, homepage: str) -> str:
    """Pre-fetched content of every cited evidence page, formatted for a prompt."""
    sections = []

    def add(field: str, url: str | None):
        if not url or not url.startswith("http"):
            sections.append(f"### [{field}] — NO EVIDENCE URL CITED")
            return
        f = fetch_text(url)
        sections.append(f"### [{field}] {url}\nHTTP {f['status']}\n{f['text']}")

    add("description (graded against homepage)", homepage)
    add("auth_methods", (record.get("auth_methods") or {}).get("evidence_url"))
    add("credential_access", (record.get("credential_access") or {}).get("evidence_url"))
    add("api_surface", (record.get("api_surface") or {}).get("docs_url"))
    sections.append("### [buildability] — no own URL; grade against the pages above")
    return "\n\n".join(sections)

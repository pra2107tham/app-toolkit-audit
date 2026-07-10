"""S0 — deterministic enrichment. No LLM involved.

Facts an agent should never have to guess:
- Does Composio already ship a toolkit for the app? (Composio toolkit registry)
- Does an MCP server already exist? (official MCP registry, public API)
- Is the homepage alive?
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
MCP_REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"

# app slug -> aliases to try against Composio registry slugs/names
ALIASES = {
    "meta-ads": ["facebook ads", "meta ads", "facebook_ads"],
    "google-ads": ["google ads", "googleads", "google_ads"],
    "zoho-crm": ["zoho crm", "zoho_crm", "zoho"],
    "zoho-cliq": ["zoho cliq", "zoho_cliq"],
    "monday": ["monday", "monday.com", "monday_com"],
    "whatsapp-business": ["whatsapp", "whatsapp business"],
    "lark": ["lark", "larksuite", "feishu"],
    "jira": ["jira", "atlassian"],
    "magento": ["magento", "adobe commerce"],
    "amazon-selling-partner": ["amazon selling partner", "amazon sp api", "amazon_selling_partner"],
    "salesforce-commerce-cloud": ["salesforce commerce", "commerce cloud"],
    "help-scout": ["helpscout", "help scout", "help_scout"],
    "otter-ai": ["otter", "otter ai", "otter_ai"],
    "mongodb-atlas": ["mongodb", "mongodb atlas"],
    "youtube-transcript": ["youtube transcript", "youtube"],
}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def fetch_composio_registry() -> list[dict]:
    """Paginate the full Composio toolkit registry once and cache it."""
    cache = OUT / "composio_toolkits.json"
    if cache.exists():
        return json.loads(cache.read_text())
    from composio import Composio

    client = Composio(api_key=os.environ["COMPOSIO_API_KEY"])
    items, cursor = [], None
    while True:
        kwargs = {"cursor": cursor} if cursor else {}
        page = client.toolkits.list(**kwargs)
        page_items = getattr(page, "items", None) or (page.get("items") if isinstance(page, dict) else []) or []
        for tk in page_items:
            d = tk if isinstance(tk, dict) else (tk.model_dump() if hasattr(tk, "model_dump") else vars(tk))
            meta = d.get("meta") or {}
            items.append(
                {
                    "slug": d.get("slug"),
                    "name": d.get("name"),
                    "auth_schemes": d.get("auth_schemes") or d.get("composio_managed_auth_schemes"),
                    "no_auth": d.get("no_auth"),
                    "tools_count": meta.get("tools_count") if isinstance(meta, dict) else None,
                }
            )
        cursor = getattr(page, "next_cursor", None) or (page.get("next_cursor") if isinstance(page, dict) else None)
        if not cursor:
            break
    OUT.mkdir(exist_ok=True)
    cache.write_text(json.dumps(items, indent=2))
    return items


def match_composio(app: dict, registry: list[dict]) -> dict:
    targets = {norm(app["slug"]), norm(app["name"])} | {norm(a) for a in ALIASES.get(app["slug"], [])}
    targets.discard("")
    exact, partial = [], []
    for tk in registry:
        tk_norms = {norm(tk.get("slug") or ""), norm(tk.get("name") or "")}
        tk_norms.discard("")
        if tk_norms & targets:
            exact.append(tk)
        elif any(t and n and (t in n or n in t) and min(len(t), len(n)) >= 5 for t in targets for n in tk_norms):
            partial.append(tk)
    best = exact[0] if exact else None
    return {
        "exists": best is not None,
        "slug": best["slug"] if best else None,
        "tools_count": best["tools_count"] if best else None,
        "auth_schemes": best["auth_schemes"] if best else None,
        "match_kind": "exact" if exact else ("ambiguous" if partial else "none"),
        "candidates": [t["slug"] for t in (exact + partial)[:5]],
    }


def check_mcp(app: dict) -> dict:
    try:
        r = requests.get(MCP_REGISTRY, params={"search": app["name"]}, timeout=15)
        r.raise_for_status()
        servers = r.json().get("servers", [])
    except Exception as e:
        return {"exists": False, "error": str(e), "matches": []}
    netloc = urlparse(app["homepage"]).netloc.replace("www.", "")
    domain_reversed = ".".join(reversed(netloc.split(".")))  # front.com -> com.front
    # exact word tokens that identify the app: slug parts + normalized full name,
    # minus tokens too generic to identify anything
    STOP = {"ai", "io", "app", "api", "cli", "com", "the", "ads", "business", "cloud", "meta"}
    app_tokens = ({t for t in re.split(r"[^a-z0-9]+", app["slug"].lower()) if len(t) >= 3} | {norm(app["name"])}) - STOP
    matches = []
    for s in servers[:30]:
        srv = s.get("server", s)
        name = srv.get("name", "")
        vendor_prefix, _, path = name.partition("/")
        is_vendor = vendor_prefix == domain_reversed or vendor_prefix.startswith(domain_reversed + ".")
        path_tokens = {t for t in re.split(r"[^a-z0-9]+", path.lower()) if t}
        # community match only on exact word-boundary tokens ("front" != "storefront")
        is_community = bool(app_tokens & path_tokens) or norm(path) in app_tokens
        if is_vendor or is_community:
            matches.append({"name": name, "official_vendor": bool(is_vendor)})
    return {
        "exists": bool(matches),
        "official": any(m["official_vendor"] for m in matches),
        "matches": matches[:5],
        "registry_url": f"{MCP_REGISTRY}?search={requests.utils.quote(app['name'])}",
    }


def check_homepage(app: dict) -> bool:
    try:
        r = requests.head(app["homepage"], timeout=10, allow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (research-pipeline)"})
        if r.status_code >= 400:
            r = requests.get(app["homepage"], timeout=10, allow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 (research-pipeline)"})
        return r.status_code < 400
    except Exception:
        return False


def main(skip_composio: bool = False):
    apps = json.loads((ROOT / "data" / "apps.json").read_text())
    (OUT / "enrichment").mkdir(parents=True, exist_ok=True)
    registry = [] if skip_composio else fetch_composio_registry()
    combined = {}
    for app in apps:
        entry = {
            "slug": app["slug"],
            "composio_toolkit": match_composio(app, registry) if registry else {"exists": None, "match_kind": "skipped"},
            "mcp_server": check_mcp(app),
            "homepage_alive": check_homepage(app),
        }
        (OUT / "enrichment" / f"{app['slug']}.json").write_text(json.dumps(entry, indent=2))
        combined[app["slug"]] = entry
        time.sleep(0.2)  # be polite to the MCP registry
    (OUT / "enrichment.json").write_text(json.dumps(combined, indent=2))
    n_composio = sum(1 for e in combined.values() if e["composio_toolkit"].get("exists"))
    n_mcp = sum(1 for e in combined.values() if e["mcp_server"].get("exists"))
    print(f"enriched {len(combined)} apps | composio toolkit: {n_composio} | mcp server: {n_mcp}")


if __name__ == "__main__":
    import sys

    main(skip_composio="--skip-composio" in sys.argv)

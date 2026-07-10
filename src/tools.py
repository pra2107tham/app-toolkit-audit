"""Composio tool wiring. COMPOSIO_SEARCH is a no-auth toolkit: it works with
only a COMPOSIO_API_KEY, no connected accounts, which is why it is the research
tool source here."""
import os

from composio import Composio
from composio_crewai import CrewAIProvider
from crewai.tools import tool
from dotenv import load_dotenv

load_dotenv()

SCOUT_TOOLS = ["COMPOSIO_SEARCH_TAVILY", "COMPOSIO_SEARCH_WEB"]
ANALYST_TOOLS = ["COMPOSIO_SEARCH_FETCH_URL_CONTENT", "COMPOSIO_SEARCH_TAVILY"]
VERIFY_TOOLS = ["COMPOSIO_SEARCH_FETCH_URL_CONTENT", "COMPOSIO_SEARCH_TAVILY"]
FIXER_TOOLS = ["COMPOSIO_SEARCH_TAVILY", "COMPOSIO_SEARCH_WEB", "COMPOSIO_SEARCH_FETCH_URL_CONTENT"]

_client: Composio | None = None


def composio_client() -> Composio:
    global _client
    if _client is None:
        _client = Composio(provider=CrewAIProvider(), api_key=os.environ["COMPOSIO_API_KEY"])
    return _client


@tool("PLAIN_HTTP_FETCH")
def plain_http_fetch(url: str, max_characters: int = 5000) -> str:
    """Fetch a web page over plain HTTP and return its readable text plus the
    HTTP status code. Use as a fallback when COMPOSIO_SEARCH_FETCH_URL_CONTENT
    fails or errors on a URL."""
    import html as html_lib
    import re

    import requests

    try:
        r = requests.get(
            url.split("#")[0], timeout=20, allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"},
        )
        text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", r.text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html_lib.unescape(re.sub(r"\s+", " ", text)).strip()
        return f"HTTP {r.status_code} | {text[:max_characters]}"
    except Exception as e:
        return f"FETCH_ERROR: {e}"


def with_fallback(tools: list) -> list:
    return list(tools) + [plain_http_fetch]


def get_tools(names: list[str], user_id: str = "pipeline"):
    """Fetch specific COMPOSIO_SEARCH tools as CrewAI tools; fall back to the
    whole toolkit filtered by name if the SDK rejects a `tools=` selector."""
    client = composio_client()
    try:
        tools = client.tools.get(user_id=user_id, tools=names)
        if tools:
            return tools
    except Exception:
        pass
    all_tools = client.tools.get(user_id=user_id, toolkits=["COMPOSIO_SEARCH"])
    wanted = {n.upper() for n in names}
    filtered = [t for t in all_tools if getattr(t, "name", "").upper() in wanted]
    return filtered or all_tools

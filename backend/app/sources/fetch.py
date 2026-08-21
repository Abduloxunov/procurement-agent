"""Rung 1 of the extraction ladder: plain HTTP fetch.

Handles manufacturer sites, distributors and anything else that serves real
HTML. Marketplaces that gate on JavaScript fall through to the browser rung
(see browser.py), and anything that fails there falls through to manual paste.
"""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from app.sources.models import PageContent

# A default python-httpx User-Agent gets 403'd by a lot of supplier sites.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en,zh-CN;q=0.9,zh;q=0.8",
}

STRIP_TAGS = ("script", "style", "nav", "footer", "header", "noscript", "svg", "form")

# Truncate before extraction. A 1M context window is not a reason to post
# whole documents into it -- see the budget note in the design doc.
MAX_CHARS = 12_000


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, readable text)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(STRIP_TAGS):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""
    text = soup.get_text(separator="\n", strip=True)

    # Collapse the blank-line noise that get_text leaves behind.
    lines = [line for line in (l.strip() for l in text.splitlines()) if line]
    return title, "\n".join(lines)


def fetch(url: str, timeout: float = 25.0) -> PageContent:
    """Fetch and flatten one page. Never raises -- failure is a returned state."""
    try:
        with httpx.Client(
            headers=HEADERS, follow_redirects=True, timeout=timeout
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return PageContent(url=url, ok=False, rung="fetch", error=str(exc)[:200])

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        return PageContent(
            url=url, ok=False, rung="fetch",
            error=f"not html: {content_type[:60]}",
        )

    title, text = html_to_text(response.text)
    if len(text) < 200:
        return PageContent(
            url=url, title=title, ok=False, rung="fetch",
            error="too little text -- probably JavaScript-rendered",
        )

    return PageContent(
        url=url,
        title=title,
        text=text[:MAX_CHARS],
        rung="fetch",
        ok=True,
    )

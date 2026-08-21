"""Rung 2 of the extraction ladder: a real browser.

Marketplaces gate on JavaScript or bot checks, and those are the pages that
carry prices -- a live search for FST100-2006A ranked two Alibaba results top
and both were unreadable without this.

Deliberately plain. One browser, launched and closed per page, with the flags
that keep Chromium alive on a 1-2 GB host. No stealth infrastructure, no
proxy rotation, no fingerprint patching -- if a site needs that, it falls
through to manual paste rather than starting an arms race.

The interface matches fetch.fetch() exactly, so the pipeline does not know or
care which rung produced a page. That is also what makes moving this to a
Cloud Run job later a deployment change rather than a rewrite.
"""

from __future__ import annotations

from app.sources.fetch import HEADERS, MAX_CHARS, html_to_text
from app.sources.models import PageContent

# Domains observed to need a real browser -- JavaScript-rendered or
# bot-protected. Saves a fetch attempt that is known to fail.
NEEDS_BROWSER = {
    "aliexpress.com",
    "robu.in",        # 403 on plain fetch; renders fine in the browser
}

# Domains that serve an interactive CAPTCHA. The browser rung cannot pass
# these and we do not try -- solving CAPTCHAs is both off-limits and an arms
# race that breaks weekly. These go straight to manual paste or an RFQ.
#
# Alibaba: returns HTTP 200 with a slider-CAPTCHA page, title "CAPTCHA
# Verification". Confirmed 2026-08. Its listed prices are indicative anyway,
# so an RFQ to the supplier is the better path.
CAPTCHA_WALLED = {
    "alibaba.com",
    "1688.com",
}

# Chromium dies on small hosts without these. --disable-dev-shm-usage moves
# the render pipeline off /dev/shm, which Docker caps at 64 MB by default.
LAUNCH_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
]

IMPLEMENTED = True


def _matches(domain: str, domains: set[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in domains)


def needs_browser(domain: str) -> bool:
    return _matches(domain, NEEDS_BROWSER)


def is_captcha_walled(domain: str) -> bool:
    """True if no automated rung can read this site. Skip straight to manual."""
    return _matches(domain, CAPTCHA_WALLED)


def fetch(url: str, timeout: float = 45.0) -> PageContent:
    """Render one page in Chromium. Never raises -- failure is a returned state."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return PageContent(
            url=url, ok=False, rung="browser",
            error="playwright not installed -- pip install playwright "
                  "&& playwright install chromium",
        )

    timeout_ms = int(timeout * 1000)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=LAUNCH_ARGS)
            try:
                context = browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    locale="en-US",
                    viewport={"width": 1440, "height": 900},
                    extra_http_headers={
                        "Accept-Language": HEADERS["Accept-Language"]
                    },
                )
                page = context.new_page()

                # Images and fonts are pure cost here -- we only want text.
                page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in {"image", "media", "font"}
                    else route.continue_(),
                )

                response = page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout_ms
                )
                status = response.status if response else 0

                # Let client-side rendering settle. networkidle often never
                # fires on marketplace pages, so a bounded wait is safer.
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass

                html = page.content()
            finally:
                browser.close()

    except Exception as exc:  # noqa: BLE001
        return PageContent(
            url=url, ok=False, rung="browser", error=str(exc)[:200]
        )

    if status and status >= 400:
        return PageContent(
            url=url, ok=False, rung="browser", error=f"HTTP {status}"
        )

    title, text = html_to_text(html)
    if len(text) < 200:
        return PageContent(
            url=url, title=title, ok=False, rung="browser",
            error="page rendered but almost empty -- likely a bot challenge",
        )

    return PageContent(
        url=url, title=title, text=text[:MAX_CHARS], rung="browser", ok=True
    )

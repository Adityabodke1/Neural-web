"""Optional Playwright-based renderer for JavaScript-heavy pages.

Playwright is optional: if it or its browsers are not installed, `render()`
returns None and the caller falls back to plain HTTPX fetching. We never bypass
login screens, CAPTCHAs or anti-bot walls — we only render public pages.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("neuralweb.playwright")

_pw_lock = asyncio.Lock()
_pw = None  # lazily initialised playwright browser


async def _get_browser():
    global _pw
    async with _pw_lock:
        if _pw is not None:
            return _pw
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.info("playwright not installed; JS rendering disabled")
            return None
        try:
            pw = await async_playwright().start()
            _pw = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
            return _pw
        except Exception as e:
            log.warning("could not launch playwright chromium: %s", e)
            return None


async def render(url: str, timeout_ms: int = 25000, wait_until: str = "domcontentloaded") -> str | None:
    """Render a page with a real browser. Returns final HTML or None."""
    browser = await _get_browser()
    if browser is None:
        return None
    try:
        context = await browser.new_context(
            user_agent="NeuralWebResearch/1.0 (+autonomous research agent)",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        await page.goto(url, timeout=timeout_ms, wait_until=wait_until)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        html = await page.content()
        await context.close()
        return html
    except Exception as e:
        log.warning("playwright render failed for %s: %s", url, e)
        return None


async def shutdown() -> None:
    global _pw
    if _pw is not None:
        try:
            await _pw.close()
        except Exception:
            pass
        _pw = None

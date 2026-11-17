"""Optional browser automation engine (Playwright).

Sensitive web actions (purchases, posts, account changes, deletions) always
require explicit confirmation. CAPTCHAs, logins behind credentials and paywalls
are never bypassed. Browser subsystem can be disabled via emergency controls.
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Optional

from ..scraping.url_guard import validate_url
from . import permissions
from .terminal import assert_switch

log = logging.getLogger("neuralweb.browser")

_pw = None
_pw_started = False


async def _start():
    global _pw, _pw_started
    if _pw_started:
        return _pw
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.info("playwright not installed; browser agent disabled")
        _pw_started = True
        return None
    try:
        pw = await async_playwright().start()
        _pw = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
    except Exception as e:
        log.warning("browser launch failed: %s", e)
    _pw_started = True
    return _pw


SENSITIVE_HINTS = re.compile(
    r"purchase|checkout|payment|pay now|delete (account|user)|submit post|publish post|"
    r"send (email|message)|change (password|email|settings)|invite|transfer", re.I)


class BrowserAgent:
    async def _page(self, url: str):
        assert_switch("browser_enabled")
        url = validate_url(url)
        browser = await _start()
        if browser is None:
            raise RuntimeError("browser automation unavailable (playwright not installed)")
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        return context, page

    async def open_url(self, url: str) -> dict:
        context, page = await self._page(url)
        try:
            title = await page.title()
            text = (await page.inner_text("body"))[:8000]
            return {"url": page.url, "title": title, "text": text}
        finally:
            await context.close()

    async def extract_page(self, url: str) -> dict:
        context, page = await self._page(url)
        try:
            title = await page.title()
            body = await page.inner_text("body")
            links = await page.eval_on_selector_all(
                "a[href]", "els => els.slice(0, 80).map(e => e.href)")
            return {"url": page.url, "title": title,
                    "text": body[:15000], "links": links}
        finally:
            await context.close()

    async def fill_form(self, url: str, fields: list[dict], submit: bool = False) -> dict:
        """Fill form fields. Submitting requires sensitive-action confirmation."""
        context, page = await self._page(url)
        try:
            for f in fields:
                sel = f.get("selector", "")
                val = str(f.get("value", ""))
                if not sel:
                    continue
                if f.get("type") == "select":
                    await page.select_option(sel, val)
                else:
                    await page.fill(sel, val)
            result = {"url": page.url, "filled": len(fields), "submitted": False}
            if submit:
                await page.click("button[type=submit]")
                result["submitted"] = True
                result["final_url"] = page.url
            permissions.audit("browser_fill_form", url, level=2,
                              detail=f"submit={submit} fields={len(fields)}")
            return result
        finally:
            await context.close()

    async def click_element(self, url: str, selector: str) -> dict:
        confirm_needed = bool(SENSITIVE_HINTS.search(selector))
        if confirm_needed:
            aid = permissions.request_approval(
                "browser_click", f"click on '{selector}' at {url}")
            raise permissions.PermissionDenied(
                f"potentially sensitive action needs approval #{aid}: click '{selector}'")
        context, page = await self._page(url)
        try:
            await page.click(selector, timeout=10000)
            permissions.audit("browser_click", f"{url} :: {selector}", level=2)
            return {"url": page.url, "clicked": selector}
        finally:
            await context.close()

    async def take_screenshot(self, url: str) -> dict:
        context, page = await self._page(url)
        try:
            data = await page.screenshot(full_page=False)
            import tempfile
            from ..config import settings
            out = settings.export_dir / f"browser_{int(__import__('time').time())}.png"
            settings.export_dir.mkdir(exist_ok=True)
            out.write_bytes(data)
            permissions.audit("browser_screenshot", url, level=1)
            return {"url": page.url, "screenshot": str(out), "b64_png": base64.b64encode(data).decode()[:100] + "…"}
        finally:
            await context.close()

    async def read_console(self, url: str, wait_ms: int = 4000) -> dict:
        """Load a page and collect console messages + errors (for debugging)."""
        assert_switch("browser_enabled")
        url = validate_url(url)
        browser = await _start()
        if browser is None:
            raise RuntimeError("browser automation unavailable")
        context = await browser.new_context()
        page = await context.new_page()
        logs: list[dict] = []
        page.on("console", lambda m: logs.append(
            {"type": m.type, "text": m.text[:500]}))
        page.on("pageerror", lambda e: logs.append(
            {"type": "pageerror", "text": str(e)[:500]}))
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(wait_ms)
            return {"url": url, "console": logs[:100]}
        finally:
            await context.close()

    async def shutdown(self) -> None:
        if _pw is not None:
            try:
                await _pw.close()
            except Exception:
                pass


browser_agent = BrowserAgent()

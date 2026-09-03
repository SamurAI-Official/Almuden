"""Browser tool — headless web scraper for exchange announcements.

Uses playwright to scrape exchange announcement pages when no API is available.
Falls back to aiohttp if playwright is not installed.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from config import Settings

log = logging.getLogger(__name__)


class Browser:
    """Headless browser for scraping web pages."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._playwright_available = self._check_playwright()

    def _check_playwright(self) -> bool:
        """Check if playwright is available."""
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            log.debug("playwright not available, using aiohttp fallback")
            return False

    async def fetch_page(self, url: str, selector: Optional[str] = None) -> str:
        """Fetch a page and return its HTML or text content."""
        if self._playwright_available:
            return await self._fetch_with_playwright(url, selector)
        else:
            return await self._fetch_with_aiohttp(url)

    async def _fetch_with_playwright(self, url: str, selector: Optional[str] = None) -> str:
        """Fetch using playwright (headless browser)."""
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=15000)

                if selector:
                    elements = await page.query_selector_all(selector)
                    texts = []
                    for elem in elements:
                        text = await elem.text_content()
                        if text:
                            texts.append(text.strip())
                    await browser.close()
                    return "\n".join(texts)
                else:
                    content = await page.content()
                    await browser.close()
                    return content
        except Exception as exc:
            log.debug("Playwright error for %s: %s", url, exc)
            return ""

    async def _fetch_with_aiohttp(self, url: str) -> str:
        """Fetch using aiohttp (simple HTTP request)."""
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    return ""
        except Exception as exc:
            log.debug("aiohttp error for %s: %s", url, exc)
            return ""

    async def close(self) -> None:
        """No-op cleanup."""
        pass
"""CaMeL Playwright 自动登录。"""

from typing import Optional

from app.core.logger import get_logger

CAMEL_BASE = "https://chat.camel-hub.com"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"

logger = get_logger(__name__)


async def playwright_login(email: str, password: str) -> Optional[str]:
    """用 Playwright 登录 CaMeL，返回 camel_session cookie 值；失败返回 None。"""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                context = await browser.new_context(user_agent=_UA, locale="zh-CN", viewport={"width": 1920, "height": 1080})
                page = await context.new_page()
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    delete navigator.__proto__.webdriver;
                """)

                logger.info("[Login] Navigating to %s/login ...", CAMEL_BASE)
                await page.goto(f"{CAMEL_BASE}/login", wait_until="networkidle", timeout=30000)
                await page.wait_for_selector('input[name="email"]', timeout=10000)
                await page.fill('input[name="email"]', email)
                await page.fill('input[name="password"]', password)
                logger.info("[Login] Submitting for %s ...", email)
                async with page.expect_navigation(wait_until="networkidle", timeout=30000):
                    await page.click('button[type="submit"]')
                await page.wait_for_timeout(2000)

                cookies = await context.cookies()
                logger.info("[Login] Got %d cookies: %s", len(cookies), [c["name"] for c in cookies])
                cookie_value = next((c["value"] for c in cookies if c["name"] == "camel_session"), None)
                if cookie_value:
                    logger.info("[Login] OK Cookie refreshed for %s", email)
                else:
                    logger.warning("[Login] FAIL camel_session cookie not found for %s", email)
                return cookie_value
            finally:
                await browser.close()
    except Exception:
        logger.error("[Login] FAIL for %s", email, exc_info=True)
        return None

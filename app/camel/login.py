"""CaMeL Playwright 自动登录。"""

from typing import Optional

CAMEL_BASE = "https://chat.camel-hub.com"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"


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
            context = await browser.new_context(user_agent=_UA, locale="zh-CN", viewport={"width": 1920, "height": 1080})
            page = await context.new_page()
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                delete navigator.__proto__.webdriver;
            """)

            print(f"[Login] Navigating to {CAMEL_BASE}/login ...")
            await page.goto(f"{CAMEL_BASE}/login", wait_until="networkidle", timeout=30000)
            await page.wait_for_selector('input[name="email"]', timeout=10000)
            await page.fill('input[name="email"]', email)
            await page.fill('input[name="password"]', password)
            print(f"[Login] Submitting for {email} ...")
            async with page.expect_navigation(wait_until="networkidle", timeout=30000):
                await page.click('button[type="submit"]')
            await page.wait_for_timeout(2000)

            cookies = await context.cookies()
            print(f"[Login] Got {len(cookies)} cookies: {[c['name'] for c in cookies]}")
            cookie_value = next((c["value"] for c in cookies if c["name"] == "camel_session"), None)
            await browser.close()
            if cookie_value:
                print(f"[Login] OK Cookie refreshed for {email}")
            else:
                print(f"[Login] FAIL camel_session cookie not found for {email}")
            return cookie_value
    except Exception as e:
        print(f"[Login] FAIL Error: {e}")
        import traceback
        traceback.print_exc()
        return None

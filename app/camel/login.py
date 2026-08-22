"""CaMeL 自动登录模块 — 支持主站 OAuth 2.0 PKCE 认证流程与单账号专属 Proxy。"""

import asyncio
from typing import Optional

from app.core.logger import get_logger

CAMEL_BASE = "https://chat.camel-hub.com"
AUTH_BASE = "https://api.camel-hub.com"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"

logger = get_logger(__name__)


async def _playwright_login_once(email: str, password: str, proxy: str = "") -> Optional[str]:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        launch_kwargs = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        if proxy and proxy.strip():
            launch_kwargs["proxy"] = {"server": proxy.strip()}

        browser = await p.chromium.launch(**launch_kwargs)
        try:
            context = await browser.new_context(
                user_agent=_UA,
                locale="zh-CN",
                viewport={"width": 1920, "height": 1080}
            )
            page = await context.new_page()
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                delete navigator.__proto__.webdriver;
            """)

            # 1. 访问 OAuth 登录入口
            logger.info("[Login] Navigating to OAuth entry: %s/api/auth/camel/login?redirect=%%2Fchat ...", CAMEL_BASE)
            await page.goto(f"{CAMEL_BASE}/api/auth/camel/login?redirect=%2Fchat", wait_until="networkidle", timeout=35000)

            # 2. 等待重定向至 api.camel-hub.com 登录表单
            logger.info("[Login] Current URL on auth page: %s", page.url)
            await page.wait_for_selector('input[name="username"], input[name="email"], #username', timeout=15000)

            # 填入用户名/邮箱与密码
            user_input = await page.query_selector('input[name="username"], input[name="email"], #username')
            if user_input:
                await user_input.fill(email)

            pass_input = await page.query_selector('input[name="password"], #password')
            if pass_input:
                await pass_input.fill(password)

            # 勾选用户协议复选框
            checkbox = await page.query_selector('input[type="checkbox"]')
            if checkbox:
                is_checked = await checkbox.is_checked()
                if not is_checked:
                    await checkbox.click()

            await page.wait_for_timeout(500)

            # 3. 点击提交按钮（“继续” 或 submit button）
            logger.info("[Login] Submitting credentials for %s ...", email)
            submit_btn = await page.query_selector('button:has-text("继续"), button[type="submit"]')
            if submit_btn:
                await submit_btn.click()
            else:
                await page.keyboard.press("Enter")

            # 4. 等待 OAuth 回调跳转回 chat.camel-hub.com
            try:
                await page.wait_for_url("**/chat**", timeout=25000)
            except Exception:
                await page.wait_for_timeout(3000)

            # 5. 提取 Cookie
            cookies = await context.cookies()
            logger.info("[Login] Fetched %d cookies from context", len(cookies))
            cookie_value = next((c["value"] for c in cookies if c["name"] == "camel_session"), None)

            if cookie_value:
                logger.info("[Login] OK Successfully obtained camel_session cookie for %s", email)
            else:
                logger.warning("[Login] FAIL camel_session cookie not found in %d cookies for %s", len(cookies), email)

            return cookie_value
        finally:
            await browser.close()


async def playwright_login(email: str, password: str, proxy: str = "", max_retries: int = 2) -> Optional[str]:
    """用 Playwright 自动化完成 OAuth 2.0 PKCE 登录，支持单账号专属 Proxy 与重试；返回 camel_session 值。"""
    if not password:
        logger.warning("[Login] No password configured for %s, skipping automated browser login", email)
        return None

    for attempt in range(1, max_retries + 1):
        try:
            try:
                cookie = await _playwright_login_once(email, password, proxy=proxy)
            except TypeError:
                cookie = await _playwright_login_once(email, password)
            if cookie:
                return cookie
        except Exception as e:
            logger.warning("[Login] Attempt %d/%d failed for %s: %s", attempt, max_retries, email, e)
            if attempt < max_retries:
                await asyncio.sleep(2 * attempt)

    logger.error("[Login] All %d login attempts failed for %s", max_retries, email)
    return None

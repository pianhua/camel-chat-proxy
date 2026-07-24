"""Cookie auto-refresh and multi-account rotation for CaMeL Chat."""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CAMEL_BASE = "https://chat.camel-hub.com"
ACCOUNTS_FILE = Path(__file__).parent / "accounts.json"


@dataclass
class Account:
    name: str
    email: str
    password: str
    camel_session: str = ""
    enabled: bool = True
    last_refresh: Optional[float] = None
    request_count: int = 0
    _fail_count: int = field(default=0, repr=False)

    @property
    def is_valid(self) -> bool:
        return self.enabled and bool(self.camel_session)

    def mark_failure(self):
        self._fail_count += 1
        if self._fail_count >= 3:
            self.enabled = False
            logger.warning("Account %s disabled after %d failures", self.name, self._fail_count)

    def mark_success(self):
        self._fail_count = 0
        self.request_count += 1


class AccountManager:
    def __init__(self, accounts_file: Path = ACCOUNTS_FILE):
        self.accounts_file = accounts_file
        self.accounts: list[Account] = []
        self._current_index = 0
        self.load()

    def load(self):
        if not self.accounts_file.exists():
            logger.warning("accounts.json not found, creating empty")
            self.accounts = []
            return
        data = json.loads(self.accounts_file.read_text(encoding="utf-8"))
        self.accounts = [Account(**a) for a in data.get("accounts", [])]
        logger.info("Loaded %d accounts", len(self.accounts))

    def save(self):
        data = {
            "accounts": [
                {
                    "name": a.name,
                    "email": a.email,
                    "password": a.password,
                    "camel_session": a.camel_session,
                    "enabled": a.enabled,
                    "last_refresh": a.last_refresh,
                    "request_count": a.request_count,
                }
                for a in self.accounts
            ]
        }
        self.accounts_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_next_account(self) -> Optional[Account]:
        """Round-robin selection of an enabled account with a valid cookie."""
        valid = [a for a in self.accounts if a.is_valid]
        if not valid:
            return None
        account = valid[self._current_index % len(valid)]
        self._current_index += 1
        return account

    async def refresh_all_cookies(self):
        """Attempt to login and refresh cookies for all accounts."""
        for account in self.accounts:
            if not account.enabled:
                continue
            if account.camel_session and account.last_refresh:
                # Refresh if older than 6 hours
                age = time.time() - account.last_refresh
                if age < 6 * 3600:
                    logger.info("Account %s cookie still fresh (%.1fh)", account.name, age / 3600)
                    continue
            logger.info("Refreshing cookie for %s...", account.name)
            success = await self._login(account)
            if success:
                account.last_refresh = time.time()
                account.mark_success()
                logger.info("Account %s cookie refreshed", account.name)
            else:
                account.mark_failure()
                logger.error("Failed to refresh cookie for %s", account.name)
        self.save()

    async def _login(self, account: Account) -> bool:
        """Login via CaMeL web form and extract camel_session cookie."""
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                # Step 1: GET login page to extract action ID and initial cookies
                r = await client.get(f"{CAMEL_BASE}/login")
                if r.status_code != 200:
                    logger.error("Failed to fetch login page: %s", r.status_code)
                    return False

                # Extract action ID from hidden input
                match = re.search(r'\$ACTION_ID_([a-f0-9]+)', r.text)
                if not match:
                    logger.error("Could not find ACTION_ID in login page")
                    return False
                action_id = match.group(1)
                logger.debug("Found ACTION_ID: %s", action_id)

                # Step 2: POST login form
                boundary = "----WebKitFormBoundary" + "x" * 16
                body = self._build_multipart(boundary, action_id, account.email, account.password)
                headers = {
                    "content-type": f"multipart/form-data; boundary={boundary}",
                    "accept": "text/x-component",
                    "origin": CAMEL_BASE,
                    "referer": f"{CAMEL_BASE}/login",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
                }
                login_r = await client.post(
                    f"{CAMEL_BASE}/login",
                    content=body,
                    headers=headers,
                )

                # Check for session cookie in response
                set_cookie = login_r.headers.get("set-cookie", "")
                session_match = re.search(r'camel_session=([^;]+)', set_cookie)
                if session_match:
                    account.camel_session = session_match.group(1)
                    return True

                # Also check redirect location for session
                if login_r.status_code in (301, 302, 303, 307, 308):
                    # Follow one redirect to get cookie
                    redirect_url = login_r.headers.get("location", "")
                    if redirect_url:
                        redirect_r = await client.get(
                            redirect_url if redirect_url.startswith("http") else f"{CAMEL_BASE}{redirect_url}",
                            headers={"referer": f"{CAMEL_BASE}/login"},
                        )
                        set_cookie = redirect_r.headers.get("set-cookie", "")
                        session_match = re.search(r'camel_session=([^;]+)', set_cookie)
                        if session_match:
                            account.camel_session = session_match.group(1)
                            return True

                logger.error("No camel_session in login response. Status: %s, Cookies: %s",
                           login_r.status_code, set_cookie[:200])
                return False

        except Exception as e:
            logger.exception("Login error for %s: %s", account.name, e)
            return False

    def _build_multipart(self, boundary: str, action_id: str, email: str, password: str) -> bytes:
        """Build Next.js Server Action multipart form body."""
        lines = [
            f"--{boundary}",
            f'Content-Disposition: form-data; name="_1_$ACTION_ID_{action_id}"',
            "",
            "",
            f"--{boundary}",
            'Content-Disposition: form-data; name="_1_redirectTo"',
            "",
            "",
            f"--{boundary}",
            'Content-Disposition: form-data; name="_1_email"',
            "",
            email,
            f"--{boundary}",
            'Content-Disposition: form-data; name="_1_password"',
            "",
            password,
            f"--{boundary}",
            'Content-Disposition: form-data; name="0"',
            "",
            '["$K1"]',
            f"--{boundary}--",
            "",
        ]
        return "\r\n".join(lines).encode("utf-8")


# Global manager instance
_manager: Optional[AccountManager] = None


def get_manager() -> AccountManager:
    global _manager
    if _manager is None:
        _manager = AccountManager()
    return _manager

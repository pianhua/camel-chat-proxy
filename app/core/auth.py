"""管理面板认证。"""

from fastapi import Header, HTTPException
from .config import config_manager


def verify_admin(x_admin_password: str = Header(None, alias="x-admin-password")) -> bool:
    if not x_admin_password:
        raise HTTPException(status_code=401, detail="Admin password required (x-admin-password header)")
    if x_admin_password != config_manager.config.admin_password:
        raise HTTPException(status_code=403, detail="Invalid admin password")
    return True

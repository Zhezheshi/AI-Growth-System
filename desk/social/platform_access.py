"""Release policy for platform operations that require an official user action."""
import os

from fastapi import HTTPException


def xhs_manual():
    return os.getenv("LEAD_XHS_MANUAL") == "1"


def require_xhs_automation():
    if xhs_manual():
        raise HTTPException(409, "小红书自动操作已停用。请在小红书官方客户端手动操作。")

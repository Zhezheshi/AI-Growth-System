"""Names and official page URL normalization for acquisition sources."""
import re
from typing import Literal
from urllib.parse import urlsplit

Platform = Literal["xhs", "douyin"]
PLATFORM_NAMES = {"xhs": "小红书", "douyin": "抖音"}
DOUYIN_ORIGIN = "https://www.douyin.com"
DOUYIN_HOSTS = {"www.douyin.com", "douyin.com"}


def douyin_link(value, profile=False):
    url = urlsplit(value.strip())
    path = url.path.rstrip("/")
    pattern = r"/user/([A-Za-z0-9_-]{8,160})" if profile else r"/(?:video|note)/([0-9]{12,24})"
    match = re.fullmatch(pattern, path)
    if url.scheme != "https" or url.netloc.lower() not in DOUYIN_HOSTS or not match:
        raise ValueError("请使用抖音网页的完整" + ("用户主页" if profile else "作品") + "链接。")
    return match[1], DOUYIN_ORIGIN + path

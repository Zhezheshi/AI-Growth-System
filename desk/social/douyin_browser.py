"""Read search results and visible comments on the Douyin website."""
import re
import time
from urllib.parse import quote, urljoin, urlsplit

from .acquisition_platforms import DOUYIN_HOSTS, DOUYIN_ORIGIN, douyin_link
from .xhs_browser import (PlatformBlocked, browser_context, coverage, ensure_access, first_text,
                          read_comment_pane, search_results)

SEARCH = 'input[data-e2e="searchbar-input"]:visible, input[data-e2e="search-input"]:visible, input[placeholder*="搜索"]:visible'
SEARCH_BUTTON = '[data-e2e="searchbar-button"]:visible'
SEARCH_PATH = re.compile(r"^https://(?:www\.)?douyin\.com/(?:jingxuan/)?search/")
HOME = DOUYIN_ORIGIN + "/jingxuan"
WORK_LINK = 'a[href*="/video/"], a[href*="/note/"], a[href*="modal_id="]'
WORK_CARD = '#waterFallScrollContainer [id^="waterfall_item_"]:has(.search-result-card)'
PANE = '[data-e2e="comment-list"]:visible, .comment-mainContent:visible'
ROWS = '[data-e2e="comment-item"]:visible'
LOGIN = re.compile(r"扫码登录|手机号登录|登录后查看评论|登录后才能查看|登录后查看更多评论")
BLOCK = re.compile(r"验证码中间页|安全验证|访问受限|访问异常|访问频繁|请完成验证|滑块验证|验证码校验")
REPLIES = re.compile(r"^\s*(?:展开\s*(?:\d+\s*条)?\s*回复|查看(?:全部|更多|\s*\d+\s*条)回复|加载更多回复|更多回复)\s*$")

COMMENT_DOM = """nodes => {
  const row = '[data-e2e="comment-item"]';
  const own = (node, selector) => [...node.querySelectorAll(selector)].find(el => el.closest(row) === node);
  const content = node => {
    const labeled = own(node, '[data-e2e="comment-content"], .comment-content, .comment-item-content');
    if (labeled) return labeled;
    const header = own(node, '.comment-item-info-wrap');
    for (let part = header?.nextElementSibling; part; part = part.nextElementSibling) {
      if (part.matches('.comment-item-stats-container') || part.matches(row) || part.querySelector(row)) break;
      if (part.innerText.trim() || part.querySelector('img')) return part;
    }
    return null;
  };
  return nodes.map(node => {
    const text = content(node);
    const author = own(node, '.comment-item-info-wrap a[href*="/user/"], a[data-e2e="comment-user-name"][href*="/user/"]')
      || own(node, 'a[href*="/user/"]');
    const header = own(node, '.comment-item-info-wrap, [data-e2e="comment-user-name"]');
    const parent = node.parentElement?.closest(row);
    const previous = node.closest('[data-e2e="comment-reply-list"], .comment-reply-list')?.previousElementSibling;
    const parentRow = parent || (previous?.matches(row) ? previous : previous?.querySelector(row));
    const anchor = own(node, '[id^="tooltip_"]');
    return {
      id: node.dataset.commentId || anchor?.id || node.id || '',
      published_at_raw: own(node, '[data-e2e="comment-time"], .comment-item-time')?.innerText.trim() || '',
      parent_id: parentRow?.dataset.commentId || parentRow?.id || '',
      text: text?.innerText.trim() || '',
      nickname: author?.innerText.trim() || header?.innerText.trim() || '',
      profile_url: author?.href || '',
      parent: parentRow && parentRow !== node ? content(parentRow)?.innerText.trim() || '' : '',
      media: own(node, '[data-e2e="comment-image"], .comment-picture, .comment-item-image') ? 'image' :
             text?.querySelector('img') ? 'emoji' : 'other'
    };
  });
}"""


def access(page, report, check):
    ensure_access(page, report, check, platform="抖音", login=LOGIN, blocked=BLOCK)


def prepare_login(page, report, check):
    from playwright.sync_api import TimeoutError as BrowserTimeout

    login = page.get_by_text("登录", exact=True).filter(visible=True).first
    try:
        page.locator(SEARCH).first.or_(login).first.wait_for(timeout=5500)
    except BrowserTimeout:
        access(page, report, check)
    if not login.is_visible():
        return
    login.click(timeout=3500)
    report("waiting_login", "请在抖音专用窗口扫码登录，登录完成后继续。")
    deadline = time.monotonic() + 120
    clear = 0
    while time.monotonic() < deadline:
        check()
        if BLOCK.search(page.title()) or page.get_by_text(BLOCK).filter(visible=True).count():
            raise PlatformBlocked("抖音提示安全验证，任务已停止。请先在抖音网页完成正常验证。")
        if not login.is_visible() and not page.get_by_text(LOGIN).filter(visible=True).count():
            clear += 1
            if clear >= 3:
                return
        else:
            clear = 0
        page.wait_for_timeout(700)
    raise PlatformBlocked("等待抖音登录超时。请完成登录后重试。")


def total_count(page):
    panel = page.locator(PANE).first
    sources = (
        page.locator('[data-e2e="comment-count"]:visible, [data-tabkey="semiTabcomment"]:visible').first,
        panel.get_by_text(re.compile(r"^(?:全部评论|评论)\s*[（(]?\s*[\d,，]+\s*[）)]?$|^(?:共\s*)?[\d,，]+\s*条评论$")).first,
        page.locator('[data-e2e="feed-comment-icon"]:visible').first,
    )
    for label in sources:
        if not label.is_visible():
            continue
        candidates = (label.inner_text(timeout=2000), label.get_attribute("aria-label", timeout=2000))
        for value in candidates:
            text = (value or "").strip()
            match = re.fullmatch(r"(?:全部评论|评论)?\s*[（(]?\s*([\d,，]+)\s*[）)]?", text)
            match = match or re.fullmatch(r"(?:共\s*)?([\d,，]+)\s*条评论", text)
            if match and (number := re.sub(r"[,，]", "", match[1])).isdigit():
                return int(number)
    return None


def at_end(page):
    return page.locator(PANE).first.get_by_text(
        re.compile(r"^\s*(?:暂时没有更多了|没有更多评论了?|没有更多了|已加载全部评论|已经到底了|到底了|[-– ]*THE END[-– ]*)\s*$", re.I)
    ).first.is_visible()


def work_links(hrefs):
    links = {}
    for href in hrefs:
        raw = urljoin(DOUYIN_ORIGIN, href)
        try:
            ident, url = douyin_link(raw)
        except ValueError:
            source = urlsplit(raw)
            match = re.search(r"(?:^|&)modal_id=(\d{12,24})(?:&|$)", source.query)
            if not match or source.scheme != "https" or source.netloc not in DOUYIN_HOSTS:
                continue
            ident, url = douyin_link(DOUYIN_ORIGIN + "/video/" + match[1])
        links.setdefault(ident, url)
    return links


def rendered_links(page):
    links = work_links(page.locator(WORK_LINK).evaluate_all("nodes => nodes.filter(n => n.getClientRects().length).map(n => n.href)"))
    card_ids = page.locator(WORK_CARD).evaluate_all("nodes => nodes.filter(n => n.getClientRects().length).map(n => n.id)")
    for card_id in card_ids:
        if match := re.fullmatch(r"waterfall_item_(\d{12,24})", card_id):
            ident = match[1]
            links.setdefault(ident, DOUYIN_ORIGIN + "/video/" + ident)
    return links


def read_work(page, url, report, check, on_progress=None):
    from playwright.sync_api import TimeoutError as BrowserTimeout

    page.goto(url, wait_until="domcontentloaded", timeout=17000)
    access(page, report, check)
    if not page.locator(PANE).first.is_visible():
        tab = page.locator('[data-tabkey="semiTabcomment"]:visible, [data-e2e="feed-comment-icon"]:visible').first
        if tab.is_visible():
            tab.click(timeout=3500)
    try:
        page.locator(PANE).first.wait_for(timeout=9000)
    except BrowserTimeout:
        access(page, report, check)
        if not page.locator(PANE).first.is_visible():
            raise
    desc = first_text(page, '[data-e2e="video-desc"]:visible, [data-e2e="video-description"]:visible, h1:visible', 1600)
    title = (desc.split("\n", 1)[0] or page.title().split(" - 抖音", 1)[0] or "抖音作品")[:240]
    rows, audit = read_comment_pane(page, report, check, root=ROWS, pane=PANE, dom=COMMENT_DOM,
                                    replies=REPLIES, access=access, total_reader=total_count, end_reader=at_end,
                                    on_progress=(lambda rows, audit: on_progress(title, desc, rows, audit)) if on_progress else None)
    return title, desc, rows, audit


def collect(profile_id, keywords, report, check, on_note=None, should_visit=None):
    from playwright.sync_api import TimeoutError as BrowserTimeout

    visited, count = set(), 0
    try:
        with browser_context(profile_id, "douyin") as context:
            home = context.pages[0] if context.pages else context.new_page()
            home.set_default_timeout(8000)
            home.goto(HOME, wait_until="domcontentloaded", timeout=18000)
            access(home, report, check)
            prepare_login(home, report, check)
            reader = context.new_page()
            reader.set_default_timeout(8000)
            for index, word in enumerate(keywords):
                check()
                report("searching", f"在抖音搜索「{word}」")
                page = home
                try:
                    if index and urlsplit(home.url).path.rstrip("/") != "/jingxuan":
                        home.goto(HOME, wait_until="domcontentloaded", timeout=17000)
                    search = page.locator(SEARCH).first
                    if search.is_visible():
                        search.fill(word, timeout=3500)
                        submit = page.locator(SEARCH_BUTTON).first
                        try:
                            with page.expect_popup(timeout=3200) as opened:
                                if submit.is_visible():
                                    submit.click(timeout=3500)
                                else:
                                    search.press("Enter", timeout=3500)
                            page = opened.value
                            page.set_default_timeout(8000)
                        except BrowserTimeout:
                            pass  # Some search flows navigate the same tab.
                        try:
                            page.wait_for_url(SEARCH_PATH, timeout=7000)
                        except BrowserTimeout:
                            access(page, report, check)
                            raise
                    else:
                        page.goto(DOUYIN_ORIGIN + "/search/" + quote(word, safe="") + "?type=video",
                                  wait_until="domcontentloaded", timeout=17000)
                    access(page, report, check)
                    page.locator(f"{WORK_LINK}, {WORK_CARD}").first.wait_for(state="attached", timeout=11000)
                except BrowserTimeout:
                    access(page, report, check)
                    report("searching", "本搜索页没有加载出作品，继续其他搜索词。")
                    continue
                if not SEARCH_PATH.match(page.url):
                    continue
                try:
                    for ident, url in search_results(page, f"{WORK_LINK}, {WORK_CARD}", rendered_links, report, check, access):
                        check()
                        if ident in visited:
                            continue
                        visited.add(ident)
                        if should_visit and not should_visit(ident):
                            continue
                        def progress(title, desc, rows, audit):
                            if on_note:
                                on_note({"url": url, "title": title, "description": desc, "keyword": word,
                                         "comments": rows, "coverage": audit})
                        try:
                            reader.bring_to_front()
                            title, desc, rows, audit = read_work(reader, url, report, check, progress)
                        except BrowserTimeout:
                            access(reader, report, check)
                            title, desc, rows = "未能读完的抖音作品", "", []
                            audit = coverage(None, 0, 0, False, False, "timeout")
                        count += len(rows)
                        total = audit["total"] if audit["total"] is not None else "未知"
                        report("searching", f"本作品读到 {len(rows)} 条，页面总数 {total}；本轮累计 {count} 条。")
                        yield {"url": url, "title": title, "description": desc, "keyword": word,
                               "comments": rows, "coverage": audit}
                finally:
                    if page != home and not page.is_closed():
                        page.close()
    except BrowserTimeout as exc:
        raise RuntimeError("抖音网页加载超时，请确认专用浏览器能正常打开抖音。") from exc
    if not visited:
        raise RuntimeError("未找到可读取的抖音作品，请检查登录状态或网页搜索结果。")

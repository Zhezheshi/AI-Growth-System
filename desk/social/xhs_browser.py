"""Read visible Xiaohongshu search and note pages using a dedicated local browser."""
import os
import re
import time
import shutil
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import urlencode, urljoin, urlsplit

from ..storage import ROOT
from .platform_access import require_xhs_automation

ORIGIN = "https://www.xiaohongshu.com"
SEARCH_INPUT = "input#search-input:visible, input.search-input:visible, input[placeholder*='搜索']:visible"
NOTE_LINK = 'a[href*="/explore/"], a[href*="/search_result/"], a[href*="/discovery/item/"]'
NOTE_ID = re.compile(r"/(?:explore|search_result|discovery/item)/([a-fA-F0-9]{24})/?$")
COMMENT_ROOT = '.comments-container .comment-item:visible, .comments-container [id^="comment-"]:visible'
COMMENT_TOTAL = ".comments-container .total:visible"
COMMENT_END = ".comments-container .end-container:visible"
REPLIES = re.compile(r"^\s*(?:展开(?:\s*\d+\s*条)?回复|查看更多回复|加载更多回复|更多回复)\s*$")
BLOCK = re.compile(r"安全限制|当前网络存在风险|访问频繁|访问受限|访问异常|安全验证|滑块验证|完成验证后继续|300012")
LOGIN = re.compile(r"手机号登录|扫码登录|登录后查看更多|登录后查看|登录后继续|登录后才能")

COMMENT_DOM = """nodes => nodes.flatMap(node => {
  if (node.matches('[id^="comment-"]') && node.closest('.comment-item') !== null &&
      !node.classList.contains('comment-item')) return [];
  const inner = node.querySelector(':scope > .comment-inner-container') || node;
  const content = inner.querySelector('.right .content, .content, .note-text');
  const own = content && content.closest('.comment-item') === node.closest('.comment-item') ? content : null;
  const author = inner.querySelector('.author a[href*="/user/profile/"], a.name[href*="/user/profile/"]');
  const parent = node.closest('.parent-comment')?.querySelector(':scope > .comment-item .content');
  return [{id: node.id || node.getAttribute('data-comment-id') || '',
           published_at_raw: inner.querySelector('.date')?.innerText.trim() || '',
           parent_id: parent && parent !== own ? parent.closest('.comment-item')?.id || '' : '',
           text: own?.innerText.trim() || '', nickname: author?.innerText.trim() || '',
           media: inner.querySelector('.comment-picture img') ? 'image' :
                  own?.querySelector('img.note-content-emoji') ? 'emoji' : 'other',
           profile_url: author?.href || '', parent: parent && parent !== own ? parent.innerText.trim() : ''}];
})"""


class PlatformBlocked(RuntimeError):
    pass


def note_links(hrefs):
    result = {}
    for href in hrefs:
        url = urlsplit(urljoin(ORIGIN, href))
        match = NOTE_ID.fullmatch(url.path)
        if match and url.scheme == "https" and url.netloc == "www.xiaohongshu.com":
            ident = match[1].lower()
            normalized = ORIGIN + url.path.rstrip("/") + ("?" + url.query if url.query else "")
            if ident not in result or (not urlsplit(result[ident]).query and url.query):
                result[ident] = normalized
    return result


def ensure_access(page, report, check, *, platform="小红书", login=LOGIN, blocked=BLOCK):
    deadline = time.monotonic() + 100
    waiting = False
    while True:
        check()
        if blocked.search(page.title()) or page.get_by_text(blocked).filter(visible=True).count():
            raise PlatformBlocked(f"{platform}提示访问限制或安全验证，任务已停。请先在正常浏览器确认可访问后再试。")
        if not page.get_by_text(login).filter(visible=True).count():
            if waiting:
                report("searching", "登录提示已消失，继续搜索。")
            return
        if not waiting:
            report("waiting_login", f"请在刚打开的{platform}窗口扫码登录；登录后本任务会继续。")
            waiting = True
        if time.monotonic() > deadline:
            raise PlatformBlocked(f"等待{platform}登录超时。完成登录后可重试。")
        page.wait_for_timeout(900)


def first_text(page, selector, limit):
    found = page.locator(selector).first
    return found.inner_text(timeout=2500).strip()[:limit] if found.is_visible() else ""


class FinishExtraction(Exception):
    """Close browsing and analyze comments already collected in this run."""


def coverage(total, read, unreadable, ended, hidden, stop, non_text=None):
    if stop == "stopped":
        return {"total": total, "read": read, "unreadable": unreadable, "non_text": non_text or {},
                "status": "partial", "reason": "stopped"}
    if unreadable:
        return {"total": total, "read": read, "unreadable": unreadable, "non_text": non_text or {},
                "status": "partial", "reason": "unreadable"}
    if total is not None and read == total and not hidden and (ended or total == 0):
        return {"total": total, "read": read, "unreadable": 0, "status": "complete", "reason": "matched"}
    if hidden:
        reason = "folded"
    elif total is not None and read < total:
        reason = "below_total"
    elif total is not None and read > total:
        reason = "count_mismatch"
    else:
        reason = stop if stop == "timeout" else "unknown_total" if total is None else "no_end"
    status = "partial" if hidden or reason in {"below_total", "timeout"} else "unverified"
    return {"total": total, "read": read, "unreadable": 0, "status": status, "reason": reason}


def read_note(page, url, report, check, on_progress=None):
    from playwright.sync_api import TimeoutError as BrowserTimeout

    page.goto(url, wait_until="commit", timeout=16000)
    try:
        page.locator(COMMENT_ROOT).first.wait_for(timeout=6500)
    except BrowserTimeout:
        pass
    ensure_access(page, report, check)
    title = first_text(page, "#detail-title:visible, .note-content .title:visible, h1:visible", 240)
    description = first_text(page, "#detail-desc:visible, .note-content .desc:visible", 1600)
    title = title or description.split("\n", 1)[0][:240]
    if not title:
        return title, description, [], coverage(None, 0, 0, False, False, "timeout")
    rows, audit = read_comment_pane(page, report, check,
        on_progress=(lambda rows, audit: on_progress(title, description, rows, audit)) if on_progress else None)
    return title, description, rows, audit


def scroll_forward(target):
    return target.evaluate("""node => {
        let pane = node;
        while (pane && !(pane.scrollHeight > pane.clientHeight &&
            /auto|scroll/.test(getComputedStyle(pane).overflowY))) pane = pane.parentElement;
        const scroller = pane || window;
        const before = pane?.scrollTop ?? window.scrollY;
        scroller.scrollBy({top: Math.max(500, (pane?.clientHeight || window.innerHeight) * .8), behavior: 'instant'});
        return (pane?.scrollTop ?? window.scrollY) > before;
    }""", timeout=2500)


def search_results(page, elements, get_links, report, check, access):
    """Yield newly rendered results until scrolling and loading stop producing progress."""
    from playwright.sync_api import TimeoutError as BrowserTimeout
    seen, stalled = set(), 0
    while True:
        check()
        access(page, report, check)
        links = get_links(page)
        fresh = [(ident, url) for ident, url in links.items() if ident not in seen]
        for ident, url in fresh:
            seen.add(ident)
            yield ident, url
        page.bring_to_front()
        target = page.locator(elements).last
        try:
            moved = target.is_visible() and scroll_forward(target)
        except BrowserTimeout:
            moved = False
        stalled = 0 if fresh or moved else stalled + 1
        if stalled >= 5:
            return
        page.wait_for_timeout(650)


def read_comment_pane(page, report, check, *, root=COMMENT_ROOT, total_selector=COMMENT_TOTAL,
                      end_selector=COMMENT_END, pane=".comments-container", dom=COMMENT_DOM,
                      replies=REPLIES, access=ensure_access, total_reader=None, end_reader=None, on_progress=None):
    from playwright.sync_api import TimeoutError as BrowserTimeout
    rows, unreadable = {}, {}
    total, ended, hidden, stop = None, False, False, "timeout"
    if page.locator(pane).first.is_visible():
        stable = 0
        previous = None
        stalled_expanders = set()
        while True:
            try:
                check()
                access(page, report, check)
            except FinishExtraction:
                stop = "stopped"
                break
            visible = page.locator(root)
            if total_reader:
                displayed = total_reader(page)
                total = total if displayed is None else displayed
            else:
                count_text = first_text(page, total_selector, 60)
                if match := re.fullmatch(r"\s*(?:共\s*)?([0-9][0-9,，]*)\s*条评论\s*", count_text):
                    total = int(match[1].replace(",", "").replace("，", ""))
            if total is None and page.locator(pane).get_by_text("暂无评论", exact=True).first.is_visible():
                total = 0
            changed = []
            for item in visible.evaluate_all(dom):
                key = item["id"] or (item["profile_url"] or item["nickname"], item["text"], item["parent"])
                if item["text"]:
                    unreadable.pop(key, None)
                    if rows.get(key) != item:
                        rows[key] = item
                        changed.append(item)
                elif item["id"] and key not in rows:
                    unreadable[key] = item["media"]
            ended = end_reader(page) if end_reader else page.locator(end_selector).first.is_visible()
            expanders = page.locator(pane).get_by_text(replies).filter(visible=True)
            hidden = bool(expanders.count())
            if on_progress and changed:
                on_progress(changed, coverage(total, len(rows), len(unreadable), ended, hidden, "reading"))
            snapshot = (len(rows), len(unreadable), expanders.count(), ended)
            stable = stable + 1 if snapshot == previous else 0
            previous = snapshot
            if total == 0 or (ended and not hidden and stable >= 2):
                stop = "settled"
                break
            # Key each visible expander by its thread and label. After a click the
            # label or reply count can change, allowing another expansion.
            for button in expanders.all():
                key = button.evaluate("""el => {
                    const thread = el.closest('.parent-comment,[data-e2e="comment-item"]') || el.parentElement;
                    return [(thread?.id || thread?.querySelector('[id^="tooltip_"],.comment-item[id]')?.id || ''),
                            (thread?.querySelectorAll('.comment-item,[data-e2e="comment-item"]').length || 0),
                            el.innerText.trim()];
                }""")
                key = tuple(key)
                if key in stalled_expanders:
                    continue
                stalled_expanders.add(key)
                try:
                    button.click(timeout=1800)
                    page.wait_for_timeout(600)
                except BrowserTimeout:
                    continue
                break
            else:
                # Prefer the rendered scroll pane over a pointer hover.
                try:
                    moved = bool(visible.count()) and scroll_forward(visible.last)
                except BrowserTimeout:
                    moved = False
                if moved:
                    stable = 0
                elif stable >= 5:
                    stop = "settled"
                    break
                page.wait_for_timeout(650)
    non_text = {key: list(unreadable.values()).count(key) for key in ("image", "emoji", "other")}
    return list(rows.values()), coverage(total, len(rows), len(unreadable), ended, hidden, stop, non_text)


@contextmanager
def browser_context(profile_id, platform="xhs"):
    if platform == "xhs":
        require_xhs_automation()
    from playwright.sync_api import sync_playwright
    bundled = ROOT / "runtime" / "playwright"
    if bundled.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled))
    directory = ROOT / "runtime" / ("douyin-browser" if platform == "douyin" else "xhs-browser") / profile_id
    edge_installed = (Path("/Applications/Microsoft Edge.app").is_dir()
                      or shutil.which("microsoft-edge")
                      or any((Path(os.environ[k]) / "Microsoft/Edge/Application/msedge.exe").is_file()
                             for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if k in os.environ))
    browser = {"channel": "msedge"} if edge_installed else {}
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(directory, headless=False, accept_downloads=False,
                                                     viewport={"width": 1250, "height": 820}, **browser)
        try:
            yield ctx
        finally:
            ctx.close()


def collect(profile_id, keywords, report, check, on_note=None, should_visit=None):
    from playwright.sync_api import TimeoutError as BrowserTimeout
    visited, total = set(), 0
    try:
        with browser_context(profile_id) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(8000)
            page.goto(ORIGIN + "/explore", wait_until="domcontentloaded", timeout=20000)
            ensure_access(page, report, check)
            login = page.get_by_role("button", name="登录", exact=True).first
            if login.is_visible():
                login.click(timeout=4000)
                ensure_access(page, report, check)
            reader = ctx.new_page()
            reader.set_default_timeout(8000)
            for index, word in enumerate(keywords):
                check()
                if index:
                    try:
                        page.goto(ORIGIN + "/explore", wait_until="domcontentloaded", timeout=16000)
                    except BrowserTimeout:
                        report("searching", "小红书首页加载超时，跳过这个搜索词。")
                        continue
                ensure_access(page, report, check)
                report("searching", f"在小红书搜索「{word}」")
                search = page.locator(SEARCH_INPUT).first
                try:
                    if search.is_visible():
                        search.fill(word, timeout=4000)
                        search.press("Enter", timeout=4000)
                    else:
                        page.goto(ORIGIN + "/search_result?" + urlencode({"keyword": word, "source": "web_search_result_notes"}),
                                  wait_until="domcontentloaded", timeout=16000)
                    page.wait_for_url(re.compile(r"/search_result(?:_ai)?(?:[/?]|$)"), timeout=8000)
                except BrowserTimeout:
                    ensure_access(page, report, check)
                    if not re.search(r"/search_result(?:_ai)?(?:[/?]|$)", page.url):
                        report("searching", "一个搜索页未打开，继续其他搜索词。")
                        continue
                ensure_access(page, report, check)
                try:
                    page.locator(NOTE_LINK).first.wait_for(state="visible", timeout=10000)
                except BrowserTimeout:
                    ensure_access(page, report, check)
                def get_links(tab):
                    return note_links(tab.locator(NOTE_LINK).evaluate_all("a => a.filter(x => x.getClientRects().length).map(x => x.href)"))
                for ident, url in search_results(page, NOTE_LINK, get_links, report, check, ensure_access):
                    check()
                    if ident in visited:
                        continue
                    visited.add(ident)
                    if should_visit and not should_visit(ident):
                        continue
                    def progress(title, description, rows, audit):
                        if on_note:
                            on_note({"url": url, "title": title or "未能读取标题的笔记", "description": description,
                                     "keyword": word, "comments": rows, "coverage": audit})
                    try:
                        reader.bring_to_front()
                        title, description, rows, audit = read_note(reader, url, report, check, progress)
                    except BrowserTimeout:
                        report("searching", "一篇笔记加载超时，保留已读评论并继续。")
                        title, description, rows = "", "", []
                        audit = coverage(None, 0, 0, False, False, "timeout")
                    total += len(rows)
                    displayed = audit["total"] if audit["total"] is not None else "未知"
                    report("searching", f"本篇读到 {len(rows)} 条，页面总数 {displayed}；本轮累计 {total} 条。")
                    yield {"url": url, "title": title or "未能读取标题的笔记", "description": description,
                           "keyword": word, "comments": rows, "coverage": audit}
    except BrowserTimeout as exc:
        raise RuntimeError("小红书页面加载超时；请确认网络和浏览器可以正常打开小红书。") from exc
    if not visited:
        raise RuntimeError("没有打开可核对的笔记。可能是登录状态、搜索无结果或页面结构已变化。")

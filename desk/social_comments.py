"""Operator-started searches of visible social comments, staged for sales review."""
import hashlib
import json
from contextlib import closing
from threading import Event, Lock, Thread
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from . import inbox
from .social import douyin_browser, xhs_browser
from .social.platform_access import require_xhs_automation
from .storage import database, now

router = APIRouter(prefix="/api/social-comments")
lock = Lock()
stop = Event()
state = {"running":False, "message":"", "works":0}
READERS = {"xhs":xhs_browser, "douyin":douyin_browser}


class Search(BaseModel):
    account: str
    keyword: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=3, ge=1, le=50)


def save_comments(account, note):
    link = inbox.safe_link(note["url"])
    work = urlsplit(link).path.rstrip("/").rsplit("/", 1)[-1]
    with database() as conn:
        for row in note["comments"]:
            text = row["text"].strip()
            if not text or len(text) > 10000:
                continue
            identity = row.get("id") or [row.get("profile_url") or row.get("nickname"),text,row.get("parent")]
            key = hashlib.sha256(json.dumps([work,identity], ensure_ascii=False).encode()).hexdigest()
            conn.execute("""INSERT INTO social_comments(account,event_id,keyword,title,url,sender,body,captured_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account,event_id) DO UPDATE SET
                title=excluded.title,url=excluded.url,body=excluded.body,sender=excluded.sender""",
                (account, "social:"+key, note["keyword"], note["title"], link,
                 row.get("nickname") or "未知来源", text, now()))


class SearchStopped(Exception):
    pass


def run_search(account, platform, keyword, limit):
    def check():
        if stop.is_set():
            raise SearchStopped()

    def report(_phase, message):
        with lock:
            state["message"] = message

    def progress(note):
        save_comments(account, note)
    count = 0
    comment_count = 0
    message = "任务已结束。"
    try:
        profile = hashlib.sha256(account.encode()).hexdigest()[:32]
        with closing(READERS[platform].collect(profile, [keyword], report, check, on_note=progress)) as notes:
            for note in notes:
                progress(note)
                count += 1
                comment_count += len(note["comments"])
                with lock:
                    state["works"] = count
                check()
                if count >= limit:
                    break
        message = f"本轮已查看 {count} 篇作品，读到 {comment_count} 条文字评论。"
    except SearchStopped:
        message = "本轮已停止，已读文字评论可继续复核。"
    except (RuntimeError, HTTPException) as exc:
        message = getattr(exc, "detail", None) or str(exc)
    except Exception as exc:
        message = (f"浏览器读取未完成（{type(exc).__name__}）。请确认本机已安装 Edge 或 Playwright Chromium，"
                   "并检查浏览器窗口及网络。已读评论可继续复核。")
    finally:
        with lock:
            state.update(running=False,message=message)


@router.post("/search")
def start(payload: Search):
    keyword = payload.keyword.strip()
    if not keyword:
        raise ValueError("请填写行业关键词")
    with database() as conn:
        account = conn.execute("SELECT id,platform FROM accounts WHERE id=?", (payload.account,)).fetchone()
    if not account or account["platform"] not in READERS:
        raise ValueError("请选择已登记的小红书或抖音账号")
    if account["platform"] == "xhs":
        require_xhs_automation()
    with lock:
        if state["running"]:
            raise HTTPException(409,"已有搜索进行中，请先停止或等待结束")
        stop.clear()
        state.update(running=True,works=0,message="正在打开专用浏览器…")
        Thread(target=run_search,args=(account["id"],account["platform"],keyword,payload.limit),daemon=True).start()
        return dict(state)


@router.post("/stop")
def cancel():
    with lock:
        if state["running"]:
            stop.set()
            state["message"] = "正在停止，等待当前页面操作结束…"
        return dict(state)


@router.get("")
def list_comments(account: str = "", offset: int = Query(default=0,ge=0)):
    with lock:
        task = dict(state)
    with database() as conn:
        items = [dict(r) for r in conn.execute("""SELECT s.*,a.label,a.platform,i.id AS inquiry_id
            FROM social_comments s JOIN accounts a ON a.id=s.account
            LEFT JOIN inquiries i ON i.account=s.account AND i.event_id=s.event_id
            WHERE (?='' OR s.account=?) ORDER BY s.id DESC LIMIT 31 OFFSET ?""", (account,account,offset))]
    return {"task":task,"items":items[:30],"more":len(items)>30}


@router.post("/{ident}/accept")
def accept_comment(ident:int):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        record = conn.execute("SELECT * FROM social_comments WHERE id=?", (ident,)).fetchone()
        if not record:
            raise HTTPException(404,"找不到该条候选评论")
        event = {**dict(record),"kind":"comment","occurred_at":record["captured_at"]}
        return {"added":inbox.accept(conn, record["account"], event, "social")}

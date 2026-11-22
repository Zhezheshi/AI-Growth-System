import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from threading import Lock
from typing import Literal
from urllib.error import HTTPError
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import ai, buyers, connectors, inbox, knowledge
from .quoting import CustomQuote, calc_quote
from .prospecting import load_demo_run, router as prospecting_router
from .discovery import initialize_discovery, router as discovery_router
from .originals import listed_sources, router as originals_router
from .parts_directory import router as parts_directory_router
from .social_comments import router as social_comments_router
from .storage import ROOT, database, initialize, now

ASSETS = ROOT / "desk" / "static"
LOCAL_HOSTS = ["localhost", "127.0.0.1"]


sync_lock = Lock()


def sync_sources():
    with sync_lock:
        return pull_sources()


def pull_sources():
    with database() as conn:
        accounts = [dict(r) for r in conn.execute(
            "SELECT * FROM accounts WHERE platform IN ('youtube','x') AND external_id<>''")]
    if os.getenv("DESK_LEGACY_DB"):
        accounts.append({"id": "legacy", "label": "原获客工具", "platform": "legacy"})
    if not accounts:
        raise ValueError("尚未配置可主动同步的来源")
    count, errors = 0, []
    for account in accounts:
        ident, platform, label = account["id"], account["platform"], account["label"]
        stamp = now()
        with database() as conn:
            conn.execute("""INSERT INTO inbox_sync(source,label,last_attempt) VALUES (?,?,?)
                ON CONFLICT(source) DO UPDATE SET label=excluded.label,last_attempt=excluded.last_attempt""",
                (ident, label, stamp))
            cursor = conn.execute("SELECT page_token FROM inbox_sync WHERE source=?", (ident,)).fetchone()[0]
        try:
            next_page = ""
            if platform != "legacy":
                key = os.getenv(f"DESK_{platform.upper()}_KEY_{ident.upper()}")
                if not key:
                    raise ValueError("未配置读取凭据")
                fetch = connectors.youtube_comments if platform == "youtube" else connectors.x_messages
                events, next_page = fetch(account, key, cursor)
            with database() as conn:
                added = connectors.sync_legacy(conn) if platform == "legacy" else sum(
                    inbox.accept(conn, ident, event, "platform") for event in events)
                conn.execute("UPDATE inbox_sync SET last_success=?,page_token=?,error='' WHERE source=?",
                             (stamp, next_page, ident))
            count += added
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
            message = "本次读取失败，请复查配置并重试"
            expired_cursor = bool(cursor and isinstance(exc, HTTPError) and exc.code in (400, 410))
            if expired_cursor:
                message = "上游分页失效，下次将重新扫描。请对照渠道记录核实缺口"
            with database() as conn:
                conn.execute("UPDATE inbox_sync SET error=?,page_token=? WHERE source=?",
                             (message, "" if expired_cursor else cursor, ident))
            errors.append(label)
    if errors:
        raise ValueError("同步失败的来源：" + "、".join(errors))
    return count


async def sync_loop(app, seconds):
    while True:
        try:
            await asyncio.to_thread(sync_sources)
        except (ValueError, OSError, sqlite3.Error, KeyError):
            app.state.sync_error = "有来源同步失败。请检查账号、凭据或原获客配置。"
        else:
            app.state.sync_error = ""
            app.state.last_sync = now()
        await asyncio.sleep(seconds)


@asynccontextmanager
async def lifespan(app):
    initialize()
    initialize_discovery()
    load_demo_run()
    app.state.sync_error = ""
    app.state.last_sync = ""
    interval = int(os.getenv("DESK_SYNC_SECONDS", "0"))
    task = asyncio.create_task(sync_loop(app, max(interval, 30))) if interval else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="冠英外贸工作台", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[*LOCAL_HOSTS, *filter(None, os.getenv("DESK_PUBLIC_HOST", "").split(","))])
app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")
app.mount("/examples", StaticFiles(directory=ROOT / "examples"), name="examples")
app.include_router(originals_router)
app.include_router(discovery_router)
app.include_router(prospecting_router)
app.include_router(parts_directory_router)
app.include_router(social_comments_router)


@app.middleware("http")
async def boundary(request, call_next):
    path = request.url.path
    host = request.url.hostname
    public = path == "/health" or path == "/webhooks/instagram" or path.startswith("/ingest/")
    if not public:
        password = os.getenv("DESK_OPERATOR_PASSWORD")
        if password:
            auth = request.headers.get("Authorization", "")
            try:
                name, _, secret = base64.b64decode(auth[6:], validate=True).partition(b":") if auth.startswith("Basic ") else (b"", b"", b"")
            except binascii.Error:
                name, secret = b"", b""
            if name != b"operator" or not secrets.compare_digest(secret, password.encode()):
                return PlainTextResponse("请登录工作台", 401, headers={"WWW-Authenticate": 'Basic realm="Kooying"'})
        elif host not in LOCAL_HOSTS:
            return PlainTextResponse("请先配置工作台登录", 403)
        origin = request.headers.get("Origin")
        if request.method not in {"GET", "HEAD"} and origin and urlsplit(origin).netloc != request.headers.get("host"):
            return PlainTextResponse("只接受来自工作台页面的操作", 403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({"detail": str(exc)[:300]}, status_code=400)


@app.exception_handler(sqlite3.IntegrityError)
async def duplicate(request, exc):
    return JSONResponse({"detail": "数据重复，或引用的产品、账号不存在"}, status_code=409)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Search(Body):
    query: str = Field(default="", max_length=1600)
    code: str = Field(default="", max_length=180)
    namespace: str = Field(default="", max_length=100)
    vehicle: str = Field(default="", max_length=180)
    axle: str = Field(default="", max_length=180)
    position: Literal["", "front", "rear", "trailer"] = ""
    kind: Literal["", "drum", "disc", "shoe", "clutch", "wind"] = ""
    dimensions: str = Field(default="", max_length=100)
    holes: int | None = Field(default=None, ge=0, le=100)
    material: str = Field(default="", max_length=80)
    qty: int | None = Field(default=None, gt=0, le=100000000)
    market: str = Field(default="", max_length=80)
    currency: str = Field(default="", max_length=3)
    incoterm: str = Field(default="", max_length=25)


class Batch(Body):
    content: str = Field(min_length=1, max_length=10_000_000)


class CaseNote(Body):
    product: str = Field(min_length=1, max_length=150)
    evidence: str = Field(min_length=1, max_length=3000)
    source: str = Field(min_length=1, max_length=200)


class Account(Body):
    platform: Literal["xhs", "douyin", "x", "tiktok", "youtube", "instagram", "made_in_china"]
    label: str = Field(min_length=1, max_length=80)
    external_id: str = Field(default="", max_length=128)


class Event(Body):
    event_id: str = Field(min_length=1, max_length=250)
    kind: Literal["dm", "comment"]
    sender: str = Field(default="", max_length=160)
    body: str = Field(min_length=1, max_length=10000)
    url: str = Field(default="", max_length=1600)
    occurred_at: str = Field(default="", max_length=80)


class ManualEvent(Event):
    account: str = Field(min_length=1, max_length=150)


class Update(Body):
    role: Literal["unknown", "end_user", "trade"]
    status: Literal["new", "following", "done"]
    owner: str = Field(default="", max_length=80)
    replied_at: str = Field(default="", max_length=80)
    next_action: str | None = Field(default=None, max_length=300)
    revision: int | None = Field(default=None, ge=0)
    company_id: int | None = Field(default=None, gt=0)


class MessageImport(Batch):
    account: str = Field(min_length=1, max_length=150)


class AIInput(Body):
    body: str = Field(min_length=1, max_length=10000)


class BuyerUpdate(Body):
    stage: Literal["new", "invited", "meeting", "visited", "followup", "closed"]
    notes: str = Field(default="", max_length=3000)


class BuyerLead(Body):
    company: str = Field(min_length=1, max_length=170)
    country: str = Field(min_length=1, max_length=100)
    products: str = Field(min_length=1, max_length=350)
    business_contact: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=140)
    whatsapp: str = Field(default="", max_length=140)
    facebook: str = Field(default="", max_length=300)
    wechat: str = Field(default="", max_length=100)
    telegram: str = Field(default="", max_length=100)
    next_action: str = Field(default="", max_length=200)


@app.get("/")
def index():
    return FileResponse(ASSETS / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/info")
def info(request: Request):
    with database() as conn:
        products = conn.execute("SELECT COUNT(*) FROM catalog WHERE demo=0").fetchone()[0]
        counts = dict(conn.execute("""SELECT r.kind,COUNT(*) FROM records r
            JOIN catalog c ON c.key=r.product WHERE c.demo=0 GROUP BY r.kind""").fetchall())
        demo = bool(conn.execute("SELECT 1 FROM catalog WHERE demo=1 LIMIT 1").fetchone())
        sync_log = [dict(row) for row in conn.execute("""SELECT source,label,last_attempt,last_success,error,
            page_token<>'' AS more FROM inbox_sync ORDER BY label""")]
    return {"platforms": inbox.PLATFORMS, "ai": ai.enabled(), "ai_provider": ai.provider(), "products": products, "records": counts,
            "demo": demo, "buyer_workbook": buyers.workbook_path().is_file(),
            "legacy": bool(os.getenv("DESK_LEGACY_DB")), "last_sync": request.app.state.last_sync,
            "sync_error": request.app.state.sync_error, "sync_sources": sync_log,
            "sources": listed_sources()}


@app.post("/api/search")
def search(payload: Search):
    with database() as conn:
        return knowledge.search(conn, payload.model_dump())


@app.post("/api/quotes/calculate")
def calculate_quote(payload: CustomQuote):
    with database() as conn:
        return calc_quote(conn, payload)


@app.get("/api/knowledge/{key}")
def product_knowledge(key: str):
    with database() as conn:
        if not conn.execute("SELECT 1 FROM catalog WHERE key=?", (key,)).fetchone():
            raise HTTPException(404, "未收录该型号")
        return knowledge.proof(conn, key)


@app.post("/api/import/{kind}")
def import_data(kind: str, payload: Batch):
    with database() as conn:
        return {"imported": knowledge.import_csv(conn, kind, payload.content)}


@app.post("/api/cases")
def record_case(payload: CaseNote):
    if knowledge.normalize(payload.product).startswith("demo"):
        raise ValueError("企业经验请关联正式型号")
    row = {**payload.model_dump(), "record_id": secrets.token_hex(8)}
    with database() as conn:
        knowledge.save_record(conn, "cases", row)
    return {"record_id": row["record_id"]}


@app.get("/api/buyers")
def buyer_list(query: str = "", grade: str = ""):
    with database() as conn:
        return buyers.list_buyers(conn, query, grade)


@app.post("/api/buyers/import")
def buyer_import(payload: Batch):
    with database() as conn:
        return {"imported": buyers.import_buyers(conn, payload.content)}


@app.post("/api/buyers", status_code=201)
def buyer_capture(payload: BuyerLead):
    value = {key: "" for key in buyers.FIELDS}
    value.update(payload.model_dump())
    value.update(id=secrets.token_hex(8), grade="C", attendance="销售现场记录，待确认",
                 match="现场留资，待复核产品匹配")
    if not any(value[key] for key in ("email", "whatsapp", "facebook", "wechat", "telegram")):
        raise ValueError("请登记至少一种买家确认的联系方式")
    inbox.safe_link(value["facebook"])
    with database() as conn:
        conn.execute("INSERT INTO buyers(id,grade,payload) VALUES (?,?,?)",
                     (value["id"], value["grade"], json.dumps(value, ensure_ascii=False)))
    return {"id": value["id"]}


@app.get("/api/buyers/workbook")
def buyer_workbook():
    if not (path := buyers.workbook_path()).is_file():
        raise HTTPException(404, "本机未配置买家 Excel")
    return FileResponse(path, filename="冠英展会全球买家.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.patch("/api/buyers/{ident}")
def buyer_followup(ident: str, payload: BuyerUpdate):
    with database() as conn:
        if not buyers.followup(conn, ident, payload.stage, payload.notes):
            raise HTTPException(404, "买家不存在")
    return {"updated": True}


@app.post("/api/assist/{task}")
def assist(task: Literal["query", "inquiry", "buyer"], request: AIInput):
    return ai.assist(task, request.body)


@app.get("/api/accounts")
def accounts():
    with database() as conn:
        return [dict(r) for r in conn.execute("""SELECT id,platform,label,external_id,secret_digest<>'' has_token
            FROM accounts ORDER BY platform,label""")]


@app.post("/api/accounts", status_code=201)
def register_account(payload: Account):
    with database() as conn:
        return inbox.create_account(conn, payload.platform, payload.label, payload.external_id)


@app.get("/api/inbox")
def inquiries(platform: str = "", status: str = "", role: str = "", account: str = "", offset: int = 0,
              queue: Literal["", "unassigned", "unanswered", "overdue"] = ""):
    with database() as conn:
        return inbox.list_inbox(conn, platform, status, role, account, max(offset, 0), queue)


@app.post("/api/inbox")
def manual(payload: ManualEvent):
    with database() as conn:
        return {"added": inbox.accept(conn, payload.account, payload.model_dump())}


@app.post("/api/inbox/import")
def import_messages(payload: MessageImport):
    with database() as conn:
        return inbox.import_messages(conn, payload.account, payload.content)


@app.patch("/api/inbox/{ident}")
def update(ident: int, payload: Update):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM inquiries WHERE id=?", (ident,)).fetchone()
        if not row:
            raise HTTPException(404, "询盘不存在")
        if payload.revision is not None and payload.revision != row["revision"]:
            raise HTTPException(409, "其他同事已更新，请刷新询盘后再保存")
        owner = payload.owner.strip()
        replied_at, replied_by = row["replied_at"], row["replied_by"]
        if payload.replied_at:
            if not owner:
                raise ValueError("登记实际回复时请填写回复人姓名")
            stamp = inbox.fiscal_time(payload.replied_at)
            if stamp < row["occurred_at"] or stamp > now():
                raise ValueError("实际回复时间须在收件时间与当前时间之间")
            if stamp != replied_at:
                replied_at, replied_by = stamp, owner
        company_id = payload.company_id if "company_id" in payload.model_fields_set else row["company_id"]
        if company_id is not None and not conn.execute(
                "SELECT 1 FROM discovery_companies WHERE id=?", (company_id,)).fetchone():
            raise ValueError("所选企业不存在，请刷新后重试")
        next_action = row["next_action"] if payload.next_action is None else payload.next_action
        followup = (payload.role, payload.status, owner, replied_at, replied_by, next_action)
        changed = followup != tuple(row[k] for k in ("role","status","owner","replied_at","replied_by","next_action"))
        followed_at = datetime.now(UTC).isoformat(timespec="microseconds") if changed else row["followed_at"]
        conn.execute("""UPDATE inquiries SET role=?,status=?,owner=?,replied_at=?,replied_by=?,
            next_action=?,company_id=?,followed_at=?,revision=revision+1,
            handled_at=CASE WHEN ?='new' THEN '' WHEN handled_at='' THEN ? ELSE handled_at END
            WHERE id=?""", (*followup, company_id, followed_at, payload.status, now(), ident))
    return {"updated": True}


@app.post("/api/sync")
def sync(request: Request):
    try:
        count = sync_sources()
    except (OSError, sqlite3.Error, KeyError):
        request.app.state.sync_error = "同步失败，请检查平台权限或原获客配置"
        raise HTTPException(502, request.app.state.sync_error) from None
    request.app.state.last_sync = now()
    request.app.state.sync_error = ""
    return {"added": count}


@app.post("/ingest/{account}")
def ingest(account: str, payload: Event, request: Request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    with database() as conn:
        if not inbox.check_account(conn, account, token):
            raise HTTPException(401, "接收凭据无效")
        return {"added": inbox.accept(conn, account, payload.model_dump(), "relay")}


@app.get("/webhooks/instagram")
def instagram_challenge(request: Request):
    token = os.getenv("DESK_META_VERIFY_TOKEN", "")
    query = request.query_params
    if token and query.get("hub.mode") == "subscribe" and secrets.compare_digest(
        token.encode(), query.get("hub.verify_token", "").encode()
    ):
        return PlainTextResponse(query.get("hub.challenge", ""))
    raise HTTPException(403, "订阅验证失败")


@app.post("/webhooks/instagram")
async def instagram_delivery(request: Request):
    body = await request.body()
    if len(body) > 2_000_000:
        raise HTTPException(413, "消息批次超出大小限制")
    secret = os.getenv("DESK_META_APP_SECRET", "")
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not secret or not secrets.compare_digest(signature, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(401, "平台签名无效")
    count = 0
    with database() as conn:
        for account, event in connectors.instagram_events(json.loads(body)):
            row = conn.execute("SELECT id FROM accounts WHERE platform='instagram' AND external_id=?", (account,)).fetchone()
            if row and account:
                count += inbox.accept(conn, row["id"], event, "platform")
    return {"added": count}

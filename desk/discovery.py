"""Source-backed buyer discovery. Source URLs are opened only by the operator's browser."""
import csv
import io
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import ai
from .inbox import safe_link
from .storage import ROOT, database, now

router = APIRouter()
SNAPSHOT = ROOT / "data" / "indonesia_buyers.json"
OUTREACH_BATCH = ROOT / "data" / "indonesia_outreach_2026-11-21.json"
COUNTRY_NAMES = {"印尼": "indonesia", "印度尼西亚": "indonesia", "印度尼西亞": "indonesia"}
ROLES = {
    "fleet": ("车队", "物流", "fleet", "transport", "logistics", "armada"),
    "workshop": ("维修", "修理", "workshop", "repair", "bengkel", "service center"),
    "trade": ("经销", "进口商", "分销", "distribut", "importer", "reseller", "dealer"),
}
PRODUCTS = {
    "drum": ("鼓式", "鼓片", "制动衬片", "brake lining", "drum brake", "kampas rem tromol"),
    "disc": ("盘式", "盘片", "brake pad", "disc brake", "kampas rem cakram"),
}
QUERY_ROLE = {"fleet": "truck fleet operator", "workshop": "commercial truck workshop", "trade": "truck parts distributor"}
QUERY_PRODUCT = {"drum": "truck brake lining", "disc": "heavy duty brake pads"}


def key(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def market(country):
    return COUNTRY_NAMES.get(key(country), key(country))


def contains(text, terms):
    return any(t in text.casefold() for t in terms)


def company_id(conn, data):
    name, country = data["company"], data["country"]
    name_key = re.sub(r"[^\w]", "", key(name))
    if not name_key or not market(country):
        raise ValueError("公司与国家不能为空")
    site = safe_link(data.get("website", ""))
    domain = (urlsplit(site).hostname or "").lower().removeprefix("www.")
    rows = conn.execute("""SELECT * FROM discovery_companies WHERE country_key=? AND
        (name_key=? OR (domain<>'' AND domain=?)) ORDER BY id""",
        (market(country), name_key, domain)).fetchall()
    matching = [r for r in rows if r["name_key"] == name_key and domain and domain == r["domain"]]
    if not matching:
        matching = [r for r in rows if r["name_key"] == name_key and
                    (not r["domain"] or not domain)]
    if len(matching) > 1:
        raise ValueError("存在多个同名主体，请提供企业官网以区分")
    if matching:
        ident = matching[0]["id"]
        if domain and not matching[0]["domain"]:
            conn.execute("UPDATE discovery_companies SET domain=? WHERE id=?", (domain, ident))
        return ident
    return conn.execute("""INSERT INTO discovery_companies(country_key,name_key,country,company,domain)
        VALUES(?,?,?,?,?)""", (market(country),name_key,country,name,domain)).lastrowid


def save_source(conn, raw, mode):
    data = {k: str(raw.get(k) or "").strip() for k in ("company","country","role","products",
        "website","evidence","summary","source_url","contact_url","checked_at","next_action")}
    if not all(data[k] for k in ("company","country","evidence","source_url","checked_at")):
        raise ValueError("每条证据需要公司、国家、原文、来源链接和核查日期")
    date.fromisoformat(data["checked_at"])
    for field in ("source_url","contact_url"):
        safe_link(data[field])
    ident = company_id(conn, data)
    conn.execute("""INSERT INTO discovery_evidence(company_id,source_url,mode,payload) VALUES(?,?,?,?)
        ON CONFLICT(company_id,source_url,mode) DO UPDATE SET payload=excluded.payload""",
        (ident,data["source_url"],mode,json.dumps(data,ensure_ascii=False)))
    return ident


def initialize_discovery():
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Refresh replaceable source snapshots while retaining company identity, imports and reviews.
        conn.execute("DELETE FROM discovery_evidence WHERE mode='公开资料快照'")
        if SNAPSHOT.is_file():
            for row in json.loads(SNAPSHOT.read_text(encoding="utf-8")):
                save_source(conn,row,"公开资料快照")
        for row in json.loads(OUTREACH_BATCH.read_text(encoding="utf-8")):
            save_source(conn, row, "本机 Codex CLI 主动获客")


class Batch(BaseModel):
    content: str = Field(min_length=1,max_length=2_000_000)


class Review(BaseModel):
    decision: Literal["值得联系","待核实","不匹配"]
    reason: str = Field(min_length=1,max_length=500)


@router.get("/static/discovery.html")
def discovery_page():
    return FileResponse(Path(__file__).parent / "static" / "discovery.html")


@router.post("/api/discovery/import/{kind}")
def load_directory(kind: Literal["directory","crm"], data: Batch):
    reader = csv.DictReader(io.StringIO(data.content.lstrip("\ufeff")))
    required = {"company","country"}
    if kind == "directory":
        required |= {"source_url","evidence","checked_at"}
    if not required.issubset(reader.fieldnames or ()):
        raise ValueError("缺少 CSV 列：" + "、".join(sorted(required - set(reader.fieldnames or ()))))
    count = 0
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for row in reader:
            try:
                if kind == "crm":
                    data_row = {k:(row.get(k) or "").strip() for k in ("company","country","website")}
                    ident = company_id(conn,data_row)
                    conn.execute("UPDATE discovery_companies SET crm=1 WHERE id=?", (ident,))
                else:
                    save_source(conn,row,"授权名录")
            except (ValueError, TypeError) as err:
                raise ValueError(f"第 {reader.line_num} 行无效：{err}") from err
            count += 1
        if count == 0:
            raise ValueError("名录没有数据行")
    return {"processed":count}


@router.patch("/api/discovery/reviews/{ident}")
def review(ident:int, body:Review):
    if not body.reason.strip():
        raise ValueError("请填写复核依据")
    with database() as conn:
        changed = conn.execute("""UPDATE discovery_companies SET decision=?,reason=?,reviewed_at=?
            WHERE id=?""",(body.decision,body.reason.strip(),now(),ident)).rowcount
        if not changed:
            raise HTTPException(404,"找不到该企业")
    return {"saved":True}


@router.get("/api/discovery/search")
def discover(country:str="印尼", product:Literal["drum","disc"]="drum",
             role:Literal["","fleet","workshop","trade"]="", query:str=""):
    if not country.strip() or len(country)>100 or len(query)>200:
        raise ValueError("请填写有效的市场与关键词")
    country_key = market(country)
    lookup = f"{country_key} {QUERY_PRODUCT[product]} {QUERY_ROLE.get(role,'commercial vehicles')} {query.strip()}".strip()
    rows = {}
    with database() as conn:
        conn.execute("BEGIN")
        for entity in conn.execute("""SELECT c.*, e.payload,e.mode FROM discovery_companies c
             LEFT JOIN discovery_evidence e ON c.id=e.company_id WHERE c.country_key=?
             ORDER BY c.id,e.source_url,e.mode""",(country_key,)):
            card = rows.setdefault(entity["id"],{k:entity[k] for k in ("id","company","country","crm","decision","reason","reviewed_at")})
            proofs = card.setdefault("proofs",[])
            if entity["payload"]:
                proofs.append({**json.loads(entity["payload"]), "source_mode":entity["mode"]})
        for item in conn.execute("""SELECT company_id,owner,status,next_action,followed_at,total FROM (
                SELECT i.company_id,i.owner,i.status,i.next_action,i.followed_at,
                    COUNT(*) OVER (PARTITION BY company_id) AS total,
                    ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY followed_at DESC,i.id DESC) AS rank
                FROM inquiries i JOIN discovery_companies c ON c.id=i.company_id WHERE c.country_key=?
                ) WHERE rank=1""", (country_key,)):
            card = rows[item["company_id"]]
            card["inquiry_count"] = item["total"]
            if item["followed_at"]:
                card["last_followup"] = {k:item[k] for k in ("followed_at","owner","status","next_action")}
    results = []
    for card in rows.values():
        proofs = card["proofs"]
        facts = " ".join(" ".join(str(p[k]) for k in ("products","evidence","summary","role")) for p in proofs)
        roles = " ".join(p["role"] for p in proofs)
        if role and not contains(roles,ROLES[role]):
            continue
        if query and key(query) not in key(card["company"]+" "+facts):
            continue
        direct = contains(facts,PRODUCTS[product])
        related = contains(facts,("商用车","重卡","客车","卡车","制动","刹车","车队","挂车","牵引车","truck","fleet","bus fleet","brake","truk","armada"))
        if not direct and not related and not card.get("inquiry_count"):
            continue
        contact = any(p["contact_url"] for p in proofs)
        card["match"] = "有该类产品线索" if direct else ("商用车相关，所用片型待核" if related else "关联企业，品类待核")
        card["priority"] = 0 if direct and contact else (1 if contact or direct else 2)
        card["history"] = "孚盟／已知客户" if card["crm"] else "客户历史待核"
        card["roles"] = "、".join(dict.fromkeys(p["role"] for p in proofs if p["role"])) or "身份待核"
        results.append(card)
    results.sort(key=lambda c:(bool(c["crm"]), c["priority"],key(c["company"])))
    return {"items":results,"query":lookup,"searched_at":now(),"ai_enabled":ai.enabled(), "ai_provider":ai.provider(),
            "search_url":"https://www.google.com/search?"+urlencode({"q":lookup})}


@router.get("/api/discovery/companies/{ident}/inquiries")
def company_inquiries(ident:int):
    with database() as conn:
        return [dict(r) for r in conn.execute("""SELECT i.id,i.sender,i.body,i.kind,i.occurred_at,
            i.owner,i.status,i.next_action,i.replied_at,a.label FROM inquiries i
            JOIN accounts a ON a.id=i.account WHERE i.company_id=?
            ORDER BY i.occurred_at DESC,i.id DESC""", (ident,))]


class Capture(BaseModel):
    company: str = Field(min_length=1,max_length=200)
    country: str = Field(min_length=1,max_length=100)
    source_url: str = Field(min_length=1,max_length=1500)
    evidence: str = Field(min_length=1,max_length=3000)
    products: str = Field(default="",max_length=250)
    role: str = Field(default="",max_length=120)
    website: str = Field(default="",max_length=1500)
    contact_url: str = Field(default="",max_length=1500)
    next_action: str = Field(default="",max_length=500)
    checked_at: date


@router.post("/api/discovery/capture")
def capture(body:Capture):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return {"id":save_source(conn,body.model_dump(mode="json"),"销售核对的公开页")}


@router.post("/api/discovery/assist/{ident}")
def analyze_evidence(ident:int):
    with database() as conn:
        proofs = conn.execute("SELECT payload FROM discovery_evidence WHERE company_id=? ORDER BY source_url",(ident,)).fetchall()
    if not proofs:
        raise HTTPException(404,"找不到企业证据")
    excerpts = [json.loads(p["payload"])["evidence"] for p in proofs]
    result = ai.assist("buyer","\n".join(excerpts)[:5000])
    if result.get("supported") and not any(result.get("evidence") and result["evidence"] in p for p in excerpts):
        return ai.buyer_review_pending("本次 AI 意见缺少可回查的原文，请人工复核。")
    return result

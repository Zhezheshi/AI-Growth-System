import csv
import hashlib
import io
import re
import secrets
from datetime import UTC, datetime
from urllib.parse import urlsplit

from .storage import now

PLATFORMS = {
    "xhs": "小红书", "douyin": "抖音", "x": "X", "tiktok": "TikTok",
    "youtube": "YouTube", "instagram": "Instagram", "made_in_china": "中国制造网",
}
ROLES = {"end_user", "trade", "unknown"}
EVIDENCE = {
    "end_user": r"our\s+(?:fleet|workshop|garage|trucks?)|we\s+(?:run|maintain|operate)\b|自有车队|我们(?:修理厂|维修站|车队)|自家(?:车|卡车)|nuestra\s+flota|nuestro\s+taller|bengkel\s+kami|أسطولنا",
    "trade": r"we\s+(?:are\s+an?\s+)?(?:distributor|importer|trading company|wholesaler)|for resale|sell wholesale|我们(?:是)?(?:经销商|贸易公司|进口商)|找代理|批发转售|redistribución|mayorista|موزع",
    "intent": r"\b(?:quote|quotation|price|rfq|moq|in stock|availability|compatible)\b|询价|报价|适配|什么价|多少钱|有货|precio|cotización|berapa harga|السعر|عرض سعر",
}


def classify(body):
    matches = {key: re.search(pattern, body, re.I) for key, pattern in EVIDENCE.items()}
    role = "unknown"
    if matches["end_user"] and not matches["trade"]:
        role = "end_user"
    if matches["trade"] and not matches["end_user"]:
        role = "trade"
    evidence = " / ".join(dict.fromkeys(m[0] for m in matches.values() if m))
    return role, evidence, "buy" if matches["intent"] else "review"


def fiscal_time(value):
    if not value:
        return now()
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("时间需要 ISO 8601 格式及明确时区")
    return stamp.astimezone(UTC).isoformat(timespec="seconds")


def safe_link(url):
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("来源链接请使用 HTTPS")
    return url


def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def create_account(conn, platform, label, external_id="", *, grant=True):
    if not label.strip() or platform not in PLATFORMS:
        raise ValueError("平台或账号名称无效")
    key = secrets.token_hex(12)
    secret = secrets.token_urlsafe(32) if grant else ""
    conn.execute("INSERT INTO accounts VALUES (?,?,?,?,?)", (key, platform, label.strip(), external_id,
                 digest(secret) if secret else ""))
    return {"id": key, "secret": secret}


def accept(conn, account, event, mode="manual"):
    body, event_id = event["body"].strip(), str(event["event_id"]).strip()
    if not (body and event_id and len(body) <= 10000 and len(event_id) <= 380):
        raise ValueError("原文或消息 ID 超出范围")
    if event["kind"] not in {"dm", "comment"}:
        raise ValueError("消息类型填写 dm 或 comment")
    role, evidence, intent = classify(body)
    stamp = fiscal_time(event.get("occurred_at", ""))
    cursor = conn.execute("""INSERT INTO inquiries
        (account,event_id,kind,sender,body,url,occurred_at,received_at,mode,suggested_role,evidence,intent)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account,event_id) DO NOTHING""",
        (account, event_id, event["kind"], event.get("sender", "")[:160] or "未知来源", body,
         safe_link(event.get("url", "")), stamp, now(), mode, role, evidence, intent))
    return cursor.rowcount


def import_messages(conn, account, content):
    if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (account,)).fetchone():
        raise ValueError("请选择有效账号")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    required = {"event_id", "kind", "body", "occurred_at"}
    if not required.issubset(reader.fieldnames or ()):
        raise ValueError("CSV 需要 event_id、kind、body、occurred_at 列")
    total = added = 0
    try:
        for row in reader:
            if None in row:
                raise ValueError("列数超过表头，含逗号的原文请使用双引号")
            if not all((row.get(k) or "").strip() for k in required):
                raise ValueError("必填字段为空")
            event = {k: (row.get(k) or "").strip() for k in (*required, "sender", "url")}
            added += accept(conn, account, event, "import")
            total += 1
    except (ValueError, csv.Error) as exc:
        raise ValueError(f"第 {reader.line_num} 行：{exc}") from exc
    if not total:
        raise ValueError("CSV 中没有询盘")
    return {"added": added, "duplicates": total - added}


def list_inbox(conn, platform, status, role, account="", offset=0, queue=""):
    where, values = [], []
    for col, value in (("a.platform", platform), ("i.status", status), ("i.account", account)):
        if value:
            where.append(f"{col}=?")
            values.append(value)
    if role:
        where.append("(CASE WHEN i.role='unknown' THEN i.suggested_role ELSE i.role END)=?")
        values.append(role)
    if queue:
        where.append("i.status<>'done'")
        where.append("i.owner=''" if queue == "unassigned" else "i.replied_at=''")
        if queue == "overdue":
            where.append("julianday('now')-julianday(i.occurred_at)>=8.0/24")
    sql = """SELECT i.*,a.platform,a.label FROM inquiries i JOIN accounts a ON a.id=i.account"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += """ ORDER BY (i.status<>'done' AND i.replied_at='') DESC, (i.intent='buy') DESC,
        (i.kind='dm') DESC, i.occurred_at ASC,i.id ASC LIMIT 101 OFFSET ?"""
    rows = [dict(r) for r in conn.execute(sql, (*values, offset))]
    counts = dict(conn.execute("""SELECT COUNT(*) total, COUNT(*) FILTER (WHERE status='new') pending,
        COUNT(*) FILTER (WHERE status<>'done' AND replied_at='') unanswered,
        COUNT(*) FILTER (WHERE status<>'done' AND owner='') unassigned,
        COUNT(*) FILTER (WHERE status='new' AND kind='dm') dm,
        COUNT(*) FILTER (WHERE status='new' AND (role='end_user' OR (role='unknown'
        AND suggested_role='end_user'))) direct FROM inquiries""").fetchone())
    companies = [dict(r) for r in conn.execute(
        "SELECT id,company,country,domain FROM discovery_companies ORDER BY company COLLATE NOCASE,country,id")]
    return {"items": rows[:100], "more": len(rows) > 100, "counts": counts, "sla_hours": 8, "companies":companies,
            "platform_counts": dict(conn.execute("""SELECT a.platform,COUNT(*) FROM inquiries i
            JOIN accounts a ON a.id=i.account WHERE i.status='new' GROUP BY a.platform""").fetchall())}


def load_demo(conn):
    rows = [
        ("instagram", "海外主账号", "dm", "Maya · Merapi Fleet",
         "We maintain our own fleet. Can you quote 200 sets for WVA 19032? The axle was replaced last year."),
        ("youtube", "冠英产品频道", "comment", "Luis · Taller Móvil",
         "Nuestro taller necesita pastillas para el eje. ¿Tienen precio para 29087?"),
        ("x", "Kooying Export", "dm", "Riley · Northline",
         "We run a fleet of 42 trucks. Is GYS-018 in stock?"),
        ("tiktok", "东南亚业务号", "comment", "Nadia · Bengkel Atlas",
         "Bengkel kami perlu pelapis rem 19032. Berapa harga 100 set?"),
        ("xhs", "冠隆服务号", "dm", "陈师傅 · 恒信车队",
         "我们车队的 JH6 换过后桥，旧片上有 QT435SH10-3502572FW，能查型号吗？"),
        ("douyin", "冠隆产品号", "comment", "汽配集采",
         "我们是贸易公司，询价，打算批发转售。"),
    ]
    for platform, label, kind, sender, body in rows:
        account = f"demo-{platform}"
        conn.execute("INSERT OR IGNORE INTO accounts(id,platform,label) VALUES (?,?,?)", (account, platform, label))
        accept(conn, account, {"event_id": f"demo-{platform}-01", "kind": kind, "sender": sender, "body": body},
               "demo")


def check_account(conn, ident, token):
    row = conn.execute("SELECT secret_digest FROM accounts WHERE id=?", (ident,)).fetchone()
    return bool(row and row["secret_digest"] and secrets.compare_digest(row["secret_digest"], digest(token)))

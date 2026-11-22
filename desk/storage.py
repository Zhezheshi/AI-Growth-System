import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
    key TEXT PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
    dimensions TEXT NOT NULL, holes INTEGER, position TEXT NOT NULL,
    source TEXT NOT NULL, page INTEGER, demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS references_map (
    product TEXT NOT NULL REFERENCES catalog, type TEXT NOT NULL, label TEXT NOT NULL,
    key TEXT NOT NULL, namespace TEXT NOT NULL, source TEXT NOT NULL, page INTEGER,
    PRIMARY KEY(product,type,key,namespace,source)
);
CREATE INDEX IF NOT EXISTS ref_lookup ON references_map(type,key);
CREATE TABLE IF NOT EXISTS records (
    kind TEXT NOT NULL, source TEXT NOT NULL, record_id TEXT NOT NULL,
    product TEXT NOT NULL REFERENCES catalog, payload TEXT NOT NULL,
    PRIMARY KEY(kind,source,record_id)
);
CREATE INDEX IF NOT EXISTS records_product ON records(product,kind);
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY, platform TEXT NOT NULL, label TEXT NOT NULL,
    external_id TEXT NOT NULL DEFAULT '', secret_digest TEXT NOT NULL DEFAULT '',
    UNIQUE(platform,label)
);
CREATE UNIQUE INDEX IF NOT EXISTS account_external ON accounts (platform,external_id)
WHERE external_id <> '';
CREATE TABLE IF NOT EXISTS discovery_companies(
 id INTEGER PRIMARY KEY, country_key TEXT NOT NULL, name_key TEXT NOT NULL,
 company TEXT NOT NULL, country TEXT NOT NULL, domain TEXT NOT NULL DEFAULT '',
 crm INTEGER NOT NULL DEFAULT 0, decision TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
 reviewed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS discovery_market ON discovery_companies(country_key,name_key);
CREATE TABLE IF NOT EXISTS discovery_evidence(
 company_id INTEGER NOT NULL REFERENCES discovery_companies, source_url TEXT NOT NULL, mode TEXT NOT NULL,
 payload TEXT NOT NULL, PRIMARY KEY(company_id,source_url,mode)
);
CREATE TABLE IF NOT EXISTS inquiries (
    id INTEGER PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts,
    event_id TEXT NOT NULL, kind TEXT NOT NULL, sender TEXT NOT NULL,
    body TEXT NOT NULL, url TEXT NOT NULL, occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL, mode TEXT NOT NULL,
    suggested_role TEXT NOT NULL, evidence TEXT NOT NULL, intent TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'unknown', owner TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new', handled_at TEXT NOT NULL DEFAULT '',
    UNIQUE(account,event_id)
);
CREATE INDEX IF NOT EXISTS inquiries_queue ON inquiries(status,received_at);
CREATE TABLE IF NOT EXISTS social_comments (
    id INTEGER PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts,
    event_id TEXT NOT NULL, keyword TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
    sender TEXT NOT NULL, body TEXT NOT NULL, captured_at TEXT NOT NULL, UNIQUE(account,event_id)
);
CREATE TABLE IF NOT EXISTS inbox_sync (
    source TEXT PRIMARY KEY, label TEXT NOT NULL, last_attempt TEXT NOT NULL,
    last_success TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
    page_token TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS buyers (
    id TEXT PRIMARY KEY, grade TEXT NOT NULL, payload TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'new', notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
);
"""


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def database_path():
    return Path(os.environ.get("DESK_DATABASE", ROOT / "runtime" / "desk.db")).resolve()


@contextmanager
def database():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize():
    from .knowledge import save_product, save_record
    from .inbox import load_demo
    from .buyers import import_buyers

    with database() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(inquiries)")}
        for name, definition in (("replied_at", "TEXT NOT NULL DEFAULT ''"),
                                 ("replied_by", "TEXT NOT NULL DEFAULT ''"),
                                 ("next_action", "TEXT NOT NULL DEFAULT ''"),
                                 ("revision", "INTEGER NOT NULL DEFAULT 0"),
                                 ("company_id", "INTEGER REFERENCES discovery_companies(id)"),
                                 ("followed_at", "TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                conn.execute(f"ALTER TABLE inquiries ADD COLUMN {name} {definition}")
        if "followed_at" not in columns:
            conn.execute("UPDATE inquiries SET followed_at=max(handled_at,replied_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS inquiries_company ON inquiries(company_id,followed_at,id)")
        for row in json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8")):
            save_product(conn, row, demo=False, insert_only=True)
        buyer_file = Path(os.getenv("DESK_BUYER_CSV", ROOT / "runtime" / "buyers.csv"))
        if buyer_file.is_file() and not conn.execute("SELECT 1 FROM buyers LIMIT 1").fetchone():
            import_buyers(conn, buyer_file.read_text(encoding="utf-8-sig"))
        if os.environ.get("DESK_DEMO") == "1":
            for group, rows in json.loads((ROOT / "data" / "demo.json").read_text(encoding="utf-8")).items():
                for row in rows:
                    if group == "products":
                        save_product(conn, row, demo=True, insert_only=True)
                    else:
                        save_record(conn, group, row, insert_only=True)
            load_demo(conn)

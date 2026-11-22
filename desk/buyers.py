import csv
import io
import json
from pathlib import Path
import os

from .inbox import safe_link
from .storage import ROOT, now

FIELDS = ("id", "company", "country", "type", "products", "match", "grade", "attendance",
          "address", "email", "phone", "business_contact", "whatsapp", "facebook", "wechat",
          "telegram", "purchase_history", "purchase_volume", "scale_proxy",
          "product_source", "contact_source", "attendance_source", "next_action")


def workbook_path():
    return Path(os.getenv("DESK_BUYER_WORKBOOK", ROOT / "runtime" / "buyers.xlsx"))


def import_buyers(conn, content):
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if reader.fieldnames is None or not {"id", "company", "country", "grade"}.issubset(reader.fieldnames):
        raise ValueError("买家 CSV 必须包含 id、company、country、grade 列")
    count = 0
    for row in reader:
        value = {field: (row.get(field) or "").strip() for field in FIELDS}
        if not all(value[key] for key in ("id", "company", "country")) or value["grade"] not in ("A", "B", "C"):
            raise ValueError(f"第 {reader.line_num} 行缺少编号、公司、国家或 A/B/C 优先级")
        for field in ("product_source", "contact_source", "attendance_source", "facebook"):
            safe_link(value[field])
        conn.execute("""INSERT INTO buyers(id,grade,payload) VALUES (?,?,?)
            ON CONFLICT(id) DO UPDATE SET grade=excluded.grade,payload=excluded.payload""",
            (value["id"], value["grade"], json.dumps(value, ensure_ascii=False)))
        count += 1
    if not count:
        raise ValueError("买家 CSV 没有数据行")
    return count


def list_buyers(conn, query="", grade=""):
    rows = conn.execute("""SELECT * FROM buyers
        WHERE (?='' OR grade=?) AND (?='' OR instr(lower(payload),lower(?))>0)
        ORDER BY grade,id LIMIT 300""", (grade, grade, query, query))
    return [{**json.loads(row["payload"]), "stage": row["stage"], "notes": row["notes"],
             "updated_at": row["updated_at"]} for row in rows]


def followup(conn, ident, stage, notes):
    return conn.execute("UPDATE buyers SET stage=?,notes=?,updated_at=? WHERE id=?",
                        (stage, notes, now(), ident)).rowcount

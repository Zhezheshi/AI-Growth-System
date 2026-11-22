import csv
import io
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from datetime import date, timedelta, timezone, datetime
from decimal import Decimal, InvalidOperation


def normalize(value):
    return re.sub(r"[^a-z0-9\u3400-\u9fff]", "", unicodedata.normalize("NFKC", str(value)).casefold())


def dimension_key(value):
    return re.sub(r"\s", "", unicodedata.normalize("NFKC", value).casefold()).replace("x", "×").replace("*", "×")


def page_number(value):
    if value in (None, ""):
        return None
    page = int(value)
    if not 1 <= page <= 100000:
        raise ValueError("页码必须为正整数")
    return page


def required(row, *keys):
    for field in keys:
        if not str(row.get(field, "")).strip():
            raise ValueError(f"缺少字段 {field}")


def save_product(conn, row, *, demo=False, insert_only=False):
    required(row, "code", "name", "kind", "source")
    key = normalize(row["code"])
    if not key or row["kind"] not in {"drum", "disc", "wind", "shoe", "clutch"}:
        raise ValueError("型号或产品类别无效")
    if key.startswith("demo") != demo:
        raise ValueError("DEMO 前缀保留给演示数据")
    holes = row.get("holes")
    holes = int(holes) if holes not in ("", None) else None
    if holes is not None and not 0 <= holes <= 100:
        raise ValueError("孔数超出范围")
    position = row.get("position", "")
    if position not in {"", "front", "rear", "trailer"}:
        raise ValueError("安装位置填写 front / rear / trailer")
    verb = "INSERT OR IGNORE" if insert_only else "INSERT"
    update = "" if insert_only else """ON CONFLICT(key) DO UPDATE SET
        code=excluded.code, name=excluded.name, kind=excluded.kind, dimensions=excluded.dimensions,
        holes=excluded.holes, position=excluded.position, source=excluded.source, page=excluded.page"""
    cursor = conn.execute(f"""{verb} INTO catalog VALUES (?,?,?,?,?,?,?,?,?,?) {update}""", (
        key, row["code"], row["name"], row["kind"], row.get("dimensions", ""), holes,
        position, row["source"], page_number(row.get("page")), demo
    ))
    if cursor.rowcount == 0:
        return
    for reference in row.get("refs", []):
        save_reference(conn, {
            "product": row["code"], "type": reference[0], "label": reference[1],
            "namespace": reference[2], "source": row["source"], "page": row.get("page")
        })


def save_reference(conn, row):
    required(row, "product", "type", "label", "namespace", "source")
    key = normalize(row["label"])
    if not key or row["type"] not in {"code", "family", "axle", "vehicle"}:
        raise ValueError("参考类型填写 code / family / axle / vehicle，并提供可检索的值")
    conn.execute("""INSERT INTO references_map VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(product,type,key,namespace,source) DO UPDATE SET label=excluded.label,page=excluded.page""", (
        normalize(row["product"]), row["type"], row["label"], key, row["namespace"], row["source"],
        page_number(row.get("page"))
    ))


SPEC = {
    "fit": ("axle", "position", "material", "evidence"),
    "reports": ("material", "result", "evidence"),
    "prices": ("material", "currency", "market", "incoterm", "min_qty", "unit_price", "valid_from", "valid_to"),
    "cases": ("evidence",),
}


def save_record(conn, kind, row, *, insert_only=False):
    required(row, "product", "source", "record_id", *SPEC[kind])
    raw_verified = str(row.get("verified", "")).casefold()
    if raw_verified not in {"", "0", "1", "false", "true"}:
        raise ValueError("verified 填写 0 或 1")
    value = {**row, "verified": raw_verified in {"1", "true"}, "page": page_number(row.get("page"))}
    if kind == "fit" and row["position"] not in {"front", "rear", "trailer"}:
        raise ValueError("fit 需要明确安装位置")
    if kind == "reports" and row["result"] not in {"pass", "fail", "pending"}:
        raise ValueError("result 填写 pass / fail / pending")
    if kind == "prices":
        price = Decimal(str(row["unit_price"]))
        currency = row["currency"].upper()
        if (not price.is_finite() or price <= 0 or price > 100000000 or not re.fullmatch(r"[A-Z]{3}", currency)
                or not 1 <= int(row["min_qty"]) <= 100000000
                or date.fromisoformat(row["valid_from"]) > date.fromisoformat(row["valid_to"])):
            raise ValueError("报价币种、价格、起订量或有效期无效")
        value.update(unit_price=str(price), currency=currency, min_qty=int(row["min_qty"]))
    verb = "INSERT OR IGNORE" if insert_only else "INSERT"
    update = "" if insert_only else "ON CONFLICT(kind,source,record_id) DO UPDATE SET product=excluded.product,payload=excluded.payload"
    conn.execute(f"{verb} INTO records VALUES (?,?,?,?,?) {update}", (
        kind, row["source"], row["record_id"], normalize(row["product"]), json.dumps(value, ensure_ascii=False)
    ))


def import_csv(conn, kind, content):
    if kind not in {"products", "references", *SPEC}:
        raise ValueError("未知资料类型")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("CSV 表头缺失或重复")
    count = 0
    for row in reader:
        try:
            if None in row or None in row.values():
                raise ValueError("列数与表头不一致")
            cleaned = {key.strip(): value.strip() for key, value in row.items()}
            if normalize(cleaned.get("product", cleaned.get("code", ""))).startswith("demo"):
                raise ValueError("演示型号不接收企业资料")
            if kind == "products":
                save_product(conn, cleaned)
            elif kind == "references":
                save_reference(conn, cleaned)
            else:
                save_record(conn, kind, cleaned)
            count += 1
        except (ValueError, InvalidOperation, sqlite3.IntegrityError) as exc:
            raise ValueError(f"CSV 第 {reader.line_num} 行：{exc}") from exc
    if not count:
        raise ValueError("CSV 没有数据行")
    return count


def matching(text, value, namespace=""):
    if not value:
        return False
    pattern = r"[\s./_-]*".join(re.escape(char) for char in normalize(value))
    if not pattern:
        return False
    prefix = rf"(?:{re.escape(namespace.casefold())}[\s:#-]*)?" if namespace in {"WVA", "FMSI", "BFMC"} else ""
    return bool(re.search(rf"(?<![a-z0-9]){prefix}{pattern}(?![a-z0-9]|[-./]\d)",
                          unicodedata.normalize("NFKC", text).casefold()))


def proof(conn, product):
    rows = conn.execute("SELECT kind,payload FROM records WHERE product=? ORDER BY source,record_id", (product,))
    result = {kind: [] for kind in SPEC}
    for kind, payload in rows:
        result[kind].append(json.loads(payload))
    return result


def quote_basis(product, records, form, fit):
    result = {"ready": False, "missing": [], "actions": [], "report": None, "price": None}

    def request(owner, detail):
        result["missing"].append(detail)
        result["actions"].append({"owner": owner, "detail": detail})

    material = normalize(form["material"])
    if not fit:
        request("技术人员", "已复核的当前车桥、安装位置和材料记录")
    if not material:
        request("技术人员", "材料代码")
    reports = [r for r in records["reports"] if r["verified"] and normalize(r["material"]) == material]
    if any(r["result"] == "fail" for r in reports):
        request("技术人员", "同材料的试验结果存在未解除的失败记录")
    elif passed := next((r for r in reports if r["result"] == "pass"), None):
        result["report"] = passed
    else:
        request("技术人员", "与型号和材料对应的已复核合格报告")
    commercial = {"qty": "订购套数", "market": "目标市场", "currency": "币种", "incoterm": "贸易条款"}
    missing_terms = [label for key, label in commercial.items() if not form[key]]
    if missing_terms:
        request("客户与销售", "确认" + "、".join(missing_terms))
        return result
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    rates = [r for r in records["prices"] if (
        r["verified"] and normalize(r["material"]) == material and r["min_qty"] <= form["qty"]
        and r["valid_from"] <= today <= r["valid_to"]
        and all(normalize(r[key]) == normalize(form[key]) for key in ("market", "currency", "incoterm"))
    )]
    if not rates:
        request("业务或财务", "适用数量和贸易条件的有效价表")
        return result
    tier = [r for r in rates if r["min_qty"] == max(p["min_qty"] for p in rates)]
    if len({Decimal(r["unit_price"]) for r in tier}) != 1:
        request("业务或财务", "同一阶梯存在不同价格，需要财务复核")
    if result["missing"]:
        return result
    rate = tier[0]
    result.update(ready=True, price=rate, subtotal=str(Decimal(rate["unit_price"]) * form["qty"]),
                  qty=form["qty"], demo=bool(product["demo"]))
    return result


def sales_answer(state, eligible, form):
    """Use only catalog evidence and customer-supplied fields for a reply."""
    field, options = "", []
    if state == "missing":
        zh = "能拍一下旧片完整编码和车桥铭牌吗？我们会按标签上的编码体系继续查。"
        en = "Could you send the complete marking on the old part and a photo of the axle plate so we can check the reference?"
    elif state == "conflict":
        zh = "目前提供的信息与目录有冲突。能再拍一下旧片标记、孔位和带尺测量的照片吗？我们请技术同事复核。"
        en = "The details differ from our catalog. Could you send photos of the markings, holes and measurements for our technical team?"
    elif state == "ambiguous":
        zh = "这个参考对应多个型号。能提供旧片完整标记、实测尺寸和现装车桥铭牌吗？"
        en = "Several variants are listed. Could you send the full marking, measurements of the old part and a photo of the current axle plate?"
        best_pairs = 0
        for key, zh_prompt, en_prompt in [
            ("holes", "拆下的旧片实测有多少个孔", "How many holes are on the old part"),
            ("dimensions", "拆下的旧片实测尺寸是多少", "What are the measured dimensions of the old part"),
        ]:
            if form[key] not in (None, ""):
                continue
            counts = Counter(row[key] for row in eligible if row[key] not in (None, ""))
            known = sum(counts.values())
            # Each pair with two known, different values can be distinguished.
            pairs = sum(n * (known - n) for n in counts.values()) // 2
            if pairs <= best_pairs:
                continue
            best_pairs = pairs
            values = sorted(counts)
            field, options = key, []
            zh, en = zh_prompt + "？", en_prompt + "?"
            if len(values) <= 6:
                options = values
                zh += " 目录中有 " + "、".join(map(str, values)) + "。"
                en += " Catalog values include " + ", ".join(map(str, values)) + "."
    else:
        part = eligible[0]["code"]
        if state == "verified":
            zh = f"我们已核对 {part} 与您提供的车桥、安装位置及材料记录，会按本次订单条件核价。"
            en = f"We matched {part} to the axle, fitting position and material in our verified records. We will check pricing for your order."
        else:
            zh, en = f"目录候选为 {part}。", f"Our catalog lists {part} as a candidate. "
            questions = [
                ("axle", "请提供现装车桥的铭牌或型号，以便技术人员确认适配。", "Could you share the currently fitted axle model or its identification plate?"),
                ("position", "请确认旧片的安装位置。", "Could you confirm the fitting position of the old part?"),
                ("material", "旧片上有材料标记吗？也可以提供采购规格，供技术人员核对。", "Is there a material marking on the old part, or a purchasing specification we can check?"),
            ]
            for key, zh_question, en_question in questions:
                if not form[key]:
                    field = key
                    zh, en = zh + zh_question, en + en_question
                    break
            else:
                zh += "我们将请技术人员复核您提供的车桥、位置和材料。"
                en += "Our technical team will review the axle, position and material details you supplied."
    if any(row["demo"] for row in eligible):
        zh, en = "演示样例 · " + zh, "DEMO ONLY · " + en
    return {"zh": zh, "en": en, "field": field, "options": options}


def search(conn, form):
    query, code, namespace, axle, vehicle, position, dimensions, holes = (
        form[k] for k in ("query", "code", "namespace", "axle", "vehicle", "position", "dimensions", "holes")
    )
    named_codes = re.findall(
        r"(?<![a-z0-9])(WVA|FMSI|BFMC|OEM?)"
        r"(?:\s+(?:part\s+)?(?:no\.?|number|code)\b\.?)?[\s:#-]*"
        r"([0-9a-z][0-9a-z./-]*[0-9a-z])(?=$|[^a-z0-9])", query, re.I)
    named_codes = [pair for pair in named_codes if any(char.isdigit() for char in pair[1])]
    if not code and not namespace and len(named_codes) == 1:
        namespace, code = named_codes[0]
    products = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM catalog")}
    signals = {}
    for r in conn.execute("SELECT * FROM references_map ORDER BY type,label,source"):
        signals.setdefault(r["product"], []).append(dict(r))
    verified_axles = set()
    if axle:
        for r in conn.execute("SELECT product,payload FROM records WHERE kind='fit'"):
            fit = json.loads(r["payload"])
            if fit["verified"] and normalize(fit["axle"]) == normalize(axle):
                verified_axles.add(r["product"])
    candidates = []
    for product in products.values():
        if form["kind"] and product["kind"] != form["kind"]:
            continue
        refs = signals.get(product["key"], [])
        sku_match = bool(code and not namespace and normalize(code) == product["key"]) or (
            not code and matching(query, product["code"]))
        lookup_values = {"code": code, "family": code, "axle": axle, "vehicle": vehicle}
        hits = [r for r in refs if (
            (normalize(lookup_values[r["type"]]) == r["key"] if lookup_values[r["type"]]
             else matching(query, r["label"], r["namespace"]))
            and (r["type"] not in {"code", "family"} or not namespace or normalize(namespace) == normalize(r["namespace"]))
        )]
        explicit_code = sku_match or any(r["type"] == "code" for r in hits)
        family = any(r["type"] == "family" for r in hits)
        exact_dimensions = bool(dimensions and product["dimensions"] and
                                dimension_key(dimensions) == dimension_key(product["dimensions"]))
        if not (sku_match or hits or product["key"] in verified_axles or (not code and exact_dimensions)):
            continue
        conflicts = []
        if code and not (explicit_code or family):
            conflicts.append("给定编码与该记录未建立对应关系")
        if position and product["position"] and position != product["position"]:
            conflicts.append("安装位置不同")
        if dimensions and product["dimensions"] and not exact_dimensions:
            conflicts.append("尺寸与目录记录不同")
        if holes is not None and product["holes"] is not None and holes != product["holes"]:
            conflicts.append("孔数与目录记录不同")
        records = proof(conn, product["key"])
        fits = [r for r in records["fit"] if r["verified"] and axle and position
                and normalize(r["axle"]) == normalize(axle) and r["position"] == position]
        if axle and any(r["type"] == "axle" for r in refs) and not fits and not any(
                r["type"] == "axle" and r["key"] == normalize(axle) for r in hits):
            conflicts.append("当前车桥与目录所列车桥不同")
        fit = next((r for r in fits if form["material"] and
                    normalize(r["material"]) == normalize(form["material"])), None)
        score = 8 * explicit_code + 4 * family + 5 * exact_dimensions + sum(
            weight for kind, weight in (("axle", 5), ("vehicle", 2))
            if any(r["type"] == kind for r in hits))
        candidates.append({**product, "hits": hits, "refs": refs, "conflicts": conflicts,
                           "score": score, "fit": fit, "records": records, "code_match": explicit_code})
    candidates.sort(key=lambda r: (bool(r["conflicts"]), -r["score"], r["code"]))
    eligible = [p for p in candidates if not p["conflicts"]]
    confirmed = eligible[0]["key"] if len(eligible) == 1 and eligible[0]["fit"] else None
    for item in candidates:
        item["quote"] = quote_basis(item, item.pop("records"), form, item["key"] == confirmed)
    if confirmed:
        state = "verified"
    elif eligible:
        state = "ambiguous" if len(eligible) > 1 else "candidate"
    elif candidates:
        state = "conflict"
    else:
        state = "missing"
    return {"state": state, "count": len(eligible), "total": len(candidates),
            "answer": sales_answer(state, eligible, form), "items": candidates[:30]}

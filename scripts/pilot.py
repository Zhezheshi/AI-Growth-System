"""Freeze a local discovery result and evaluate independently entered pilot reviews."""
import argparse
import csv
import json
import math
import statistics
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen


COHORTS = ("企业", "团队")
FIELDS = ("排名", "企业编号", "公司", "市场", "客户历史", "来源与原文")
REVIEW = ("已送审", "判断", "复核人", "原因", "人工分钟", "辅助分钟")
COLUMNS = [*FIELDS, *(group + field for group in COHORTS for field in REVIEW)]
DECISIONS = ("值得联系", "不匹配", "待核实")


def normalized(value):
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def identity(card):
    # Distinguish separate local businesses with the same name when domains differ.
    domains = {urlsplit(p["website"]).hostname.lower().removeprefix("www.")
               for p in card["proofs"] if p.get("website") and urlsplit(p["website"]).hostname}
    return normalized(card["country"]), normalized(card["company"]), tuple(sorted(domains))


def excel_text(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def selection(items, exclude):
    excluded_names = {normalized(name) for name in exclude}
    known_ids = {c["id"] for c in items if c["crm"]}
    known_subjects = {identity(c) for c in items if c["crm"]}
    seen_ids, seen_subjects, rows = set(), set(), []
    for card in items:
        subject = identity(card)
        if card["id"] in known_ids or subject in known_subjects or subject[1] in excluded_names:
            continue
        if card["id"] in seen_ids or subject in seen_subjects:
            continue
        seen_ids.add(card["id"])
        seen_subjects.add(subject)
        sources = "\n".join(f"{p['source_url']}  {p['evidence']}" for p in card["proofs"])
        values = (len(rows) + 1, card["id"], card["company"], card["country"], card["history"], sources)
        rows.append(dict(zip(FIELDS, map(excel_text, values))))
        if len(rows) == 10:
            break
    return rows


def freeze(target, result, filters, exclude):
    if target.exists():
        raise ValueError("评测目录已存在，请为新一轮评测另取目录名")
    rows = selection(result["items"], exclude)
    snapshot = {"frozen_at": datetime.now(timezone.utc).isoformat(), "filters": filters,
                "query": result["query"], "searched_at": result["searched_at"],
                "exclude_company": exclude, "rows": rows}
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        staging = Path(tmp) / "run"
        staging.mkdir()
        (staging / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
        with (staging / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        staging.rename(target)
    return len(rows)


def minutes(value):
    if not value:
        return None
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("用时须为非负有限分钟数")
    return value


def score_group(rows, group):
    sent = reviewed = 0
    counts = dict.fromkeys(DECISIONS, 0)
    manual, assisted = [], []
    for row in rows:
        assigned, verdict, person, reason, old, new = (row[group + k].strip() for k in REVIEW)
        try:
            if assigned not in ("", "是", "否"):
                raise ValueError("已送审列填写 是、否 或留空")
            if assigned != "是":
                if any((verdict, person, reason, old, new)):
                    raise ValueError("填写复核数据前请将已送审设为 是")
                continue
            sent += 1
            if not verdict:
                if old or new:
                    raise ValueError("记录完成判断的耗时前请填写判断")
                continue
            if verdict not in counts or not person:
                raise ValueError("请填写有效判断和复核人")
            reviewed += 1
            counts[verdict] += 1
            for value, times in ((old, manual), (new, assisted)):
                measure = minutes(value)
                if measure is not None:
                    times.append(measure)
        except ValueError as exc:
            raise ValueError(f"{row['公司']} · {group}复核有误，{exc}") from exc
    manual_median = statistics.median(manual) if manual else None
    assisted_median = statistics.median(assisted) if assisted else None
    return {
        "实际送审分母": sent, "未送审": len(rows) - sent, "已复核": reviewed, "送审后未复核": sent - reviewed,
        "认可数": counts["值得联系"], "待核实": counts["待核实"], "不匹配": counts["不匹配"],
        "认可数占全部送审批次百分比": round(100 * counts["值得联系"] / sent, 2) if sent else None,
        "人工计时样本数": len(manual), "人工中位分钟": manual_median,
        "辅助计时样本数": len(assisted), "辅助中位分钟": assisted_median,
        "人工减辅助中位分钟差": round(manual_median - assisted_median, 4) if manual and assisted else None,
    }


def report(target):
    snapshot = json.loads((target / "snapshot.json").read_text(encoding="utf-8"))
    with (target / "review.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != set(COLUMNS) or len(reader.fieldnames) != len(COLUMNS):
            raise ValueError("复核文件表头已变化，请使用本轮 review.csv")
        rows = list(reader)
    if any(None in row or any(v is None for v in row.values()) for row in rows):
        raise ValueError("CSV 行的列数与表头不符")
    frozen = {r["企业编号"]: r for r in snapshot["rows"]}
    supplied = {row["企业编号"]: {k: row[k] for k in FIELDS} for row in rows}
    if len(rows) != len(frozen) or supplied != frozen:
        raise ValueError("送审名单或证据与冻结快照不符，请恢复原始行，仅编辑复核列")
    return {"冻结时间": snapshot["frozen_at"], "检索条件": snapshot["filters"], "冻结候选数": len(rows),
            **{group + "复核": score_group(rows, group) for group in COHORTS}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("freeze", help="从运行中的本地工作台冻结前十家候选")
    create.add_argument("directory", type=Path)
    create.add_argument("--port", type=int, default=8794)
    create.add_argument("--country", default="印尼")
    create.add_argument("--product", choices=("drum", "disc"), default="drum")
    create.add_argument("--role", choices=("", "fleet", "workshop", "trade"), default="")
    create.add_argument("--query", default="")
    create.add_argument("--exclude-company", action="append", default=[], metavar="公司名")
    summary = sub.add_parser("report", help="读取本轮 review.csv 并按复核来源统计")
    summary.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "report":
            print(json.dumps(report(args.directory), ensure_ascii=False, indent=2))
            return
        filters = {key: getattr(args, key) for key in ("country", "product", "role", "query")}
        url = f"http://127.0.0.1:{args.port}/api/discovery/search?" + urlencode(filters)
        with urlopen(url, timeout=15) as response:
            data = json.load(response)
        n = freeze(args.directory, data, filters, args.exclude_company)
        print(f"冻结 {n} 家候选，填写 {args.directory / 'review.csv'}")
    except (OSError, URLError, ValueError, KeyError) as exc:
        print(f"评测未完成：{exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

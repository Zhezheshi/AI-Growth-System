import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .inbox import accept


def legacy_account(conn, platform, slot, label):
    account = f"legacy-{platform}-{slot}"
    conn.execute("INSERT OR IGNORE INTO accounts(id,platform,label) VALUES (?,?,?)",
                 (account, platform, f"原获客 · {label} [{slot[:8]}]"))
    return account


def legacy_datetime(value):
    # Existing collector stores normalized ISO UTC. Incomplete platform times
    # fall back to the time the record is captured in this workbench.
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.isoformat() if stamp.tzinfo else ""
    except (AttributeError, ValueError):
        return ""


def sync_legacy(conn):
    path, org = os.environ.get("DESK_LEGACY_DB", ""), os.environ.get("DESK_LEGACY_ORG", "")
    if not path or not org:
        raise ValueError("配置 DESK_LEGACY_DB 与 DESK_LEGACY_ORG 后可导入原获客数据")
    product = os.environ.get("DESK_LEGACY_PRODUCT", "")
    slots = set(filter(None, os.environ.get("DESK_LEGACY_DM_SLOTS", "").split(",")))
    if not (product or slots):
        raise ValueError("请显式指定冠英的产品 ID 或私信账号槽位")
    count = 0
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as old:
        old.row_factory = sqlite3.Row
        if product:
            for row in old.execute("""SELECT e.*,l.platform,l.nickname
                FROM xhs_evidence e JOIN xhs_leads l ON l.id=e.lead_id AND l.organization_id=e.organization_id
                JOIN discovery_briefs b ON b.id=e.product_version
                JOIN discovery_products p ON p.id=b.product_id AND p.organization_id=e.organization_id
                WHERE e.organization_id=? AND p.id=? AND l.platform IN ('xhs','douyin')""", (org, product)):
                account = legacy_account(conn, row["platform"], "discovery", "评论获客")
                count += accept(conn, account, {"event_id": row["id"], "kind": "comment",
                    "body": row["body"], "sender": row["nickname"], "url": row["note_url"],
                    "occurred_at": legacy_datetime(row["captured_at"])}, "legacy")
        if slots:
            labels = {r["slot"]: r["label"] for r in old.execute(
                "SELECT slot,label FROM xhs_dm_senders WHERE organization_id=?", (org,))}
            for row in old.execute("""SELECT m.peer_id,m.platform_id,m.body,m.platform_time,c.nickname
                FROM xhs_dm_messages m JOIN xhs_dm_conversations c USING (organization_id,peer_id)
                WHERE m.organization_id=? AND m.direction='in' AND trim(m.body)<>''""", (org,)):
                slot = row["peer_id"].partition(":")[0] if ":" in row["peer_id"] else "default"
                if slot not in slots:
                    continue
                account = legacy_account(conn, "xhs", slot, labels.get(slot, "默认账号"))
                count += accept(conn, account, {"event_id": f"{row['peer_id']}:{row['platform_id']}", "kind": "dm",
                    "body": row["body"], "sender": row["nickname"],
                    "occurred_at": legacy_datetime(row["platform_time"])}, "legacy")
    return count


def fetch_json(url, headers=None):
    with urlopen(Request(url, headers=headers or {}), timeout=12) as response:
        return json.load(response)


def youtube_comments(account, credential, cursor=""):
    # Pull a bounded window of the latest top-level comments for the bound channel.
    query = {"part": "snippet", "allThreadsRelatedToChannelId": account["external_id"],
             "maxResults": 100, "textFormat": "plainText", "order": "time", "key": credential}
    events = []
    for page in range(5):
        payload = fetch_json("https://www.googleapis.com/youtube/v3/commentThreads?" + urlencode(query))
        for item in payload["items"]:
            comment = item["snippet"]["topLevelComment"]
            row = comment["snippet"]
            link = "https://www.youtube.com/watch?" + urlencode({"v": row["videoId"], "lc": comment["id"]})
            events.append({"event_id": comment["id"], "kind": "comment", "sender": row["authorDisplayName"],
                           "body": row["textOriginal"], "occurred_at": row["publishedAt"], "url": link})
        token = payload.get("nextPageToken", "")
        if page == 0 and cursor:
            token = cursor
        if not token:
            break
        query["pageToken"] = token
    return events, token


def x_messages(account, credential, cursor=""):
    headers = {"Authorization": "Bearer " + credential}
    profile = fetch_json("https://api.x.com/2/users/me", headers)
    if profile["data"]["id"] != account["external_id"]:
        raise ValueError("X 授权身份与绑定账号不符")
    params = {"event_types": "MessageCreate", "dm_event.fields": "id,text,created_at",
              "expansions": "sender_id", "user.fields": "username", "max_results": 100}
    events = []
    for page in range(5):
        data = fetch_json("https://api.x.com/2/dm_events?" + urlencode(params), headers)
        if data.get("errors") and not data.get("data"):
            raise ValueError("X 未能读取私信")
        names = {r["id"]: r.get("username", r["id"]) for r in data.get("includes", {}).get("users", [])}
        for event in data.get("data", []):
            if not event.get("text") or event.get("sender_id") in (None, account["external_id"]):
                continue
            events.append({"event_id": event["id"], "kind": "dm", "sender": names.get(event["sender_id"], event["sender_id"]),
                           "body": event["text"], "occurred_at": event.get("created_at", "")})
        token = data.get("meta", {}).get("next_token", "")
        if page == 0 and cursor:
            token = cursor
        if not token:
            break
        params["pagination_token"] = token
    return events, token


def instagram_events(payload):
    if payload.get("object") != "instagram":
        return
    for entry in payload.get("entry", []):
        account = str(entry.get("id", ""))
        for event in entry.get("messaging", []):
            message = event.get("message") or {}
            if message.get("is_echo") or not message.get("mid") or not message.get("text"):
                continue
            stamped = event.get("timestamp")
            yield account, {"event_id": f"dm:{message['mid']}", "kind": "dm", "sender": event["sender"]["id"],
                            "body": message["text"], "occurred_at": datetime.fromtimestamp(
                                stamped / 1000, UTC).isoformat() if isinstance(stamped, (float, int)) else ""}
        for change in entry.get("changes", []):
            comment = change.get("value") or {}
            if change.get("field") != "comments" or not comment.get("id") or not comment.get("text"):
                continue
            author = comment.get("from") or {}
            if str(author.get("id")) == account:
                continue
            yield account, {"event_id": f"comment:{comment['id']}", "kind": "comment", "body": comment["text"],
                            "sender": author.get("username") or str(author.get("id", ""))}

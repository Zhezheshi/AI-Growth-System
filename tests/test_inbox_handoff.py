import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from desk import connectors
from desk.main import app, lifespan, sync_sources
from desk.storage import SCHEMA, database, initialize


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        db = root / "test.db"
        self.enterContext(patch.dict(os.environ, {
            "DESK_DATABASE": str(db), "DESK_BUYER_CSV": str(root / "no-buyers.csv"),
            "DESK_SYNC_SECONDS": "0", "DESK_DEMO": "0", "DESK_LEGACY_DB": "",
        }))
        await self.enterAsyncContext(lifespan(app))
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"))

    async def account(self, label, platform="made_in_china", external_id=""):
        result = await self.client.post("/api/accounts", json={
            "platform": platform, "label": label, "external_id": external_id})
        self.assertEqual(result.status_code, 201)
        return result.json()["id"]

    async def test_linking_revision_and_latest_followup(self):
        a = await self.account("企业关联测试")
        for n in ("one","two"):
            await self.client.post("/api/inbox", json={"account":a,"event_id":n,"kind":"comment","body":n})
        inbox = (await self.client.get("/api/inbox")).json()
        first, second = [r["id"] for r in inbox["items"]]
        company, other = [c["id"] for c in inbox["companies"][:2]]
        fields = {"role":"unknown","status":"new","company_id":company,"revision":0}
        for ident in (first, second):
            self.assertEqual((await self.client.patch(f"/api/inbox/{ident}",json=fields)).status_code,200)

        async def card():
            cards = (await self.client.get("/api/discovery/search")).json()["items"]
            return next(c for c in cards if c["id"] == company)

        self.assertEqual((await card())["inquiry_count"],2)
        self.assertNotIn("last_followup",await card())
        fields.update(owner="Chen",status="following",next_action="核实车桥")
        self.assertEqual((await self.client.patch(f"/api/inbox/{first}",json=fields)).status_code,409)
        del fields["company_id"]  # Previous API clients can still update without changing the link.
        fields["revision"] = 1
        await self.client.patch(f"/api/inbox/{first}",json=fields)
        await self.client.patch(f"/api/inbox/{second}",json={**fields,"next_action":"跟进样品"})
        self.assertEqual((await card())["last_followup"]["next_action"],"跟进样品")
        await self.client.patch(f"/api/inbox/{second}",json={**fields,"revision":2,"company_id":other})
        self.assertEqual((await card())["last_followup"]["next_action"],"核实车桥")
        failed = await self.client.patch(f"/api/inbox/{first}",json={**fields,"revision":2,"company_id":999999})
        self.assertEqual(failed.status_code,400)
        linked = (await self.client.get(f"/api/discovery/companies/{company}/inquiries")).json()
        self.assertEqual([r["id"] for r in linked],[first])
        await self.client.patch(f"/api/inbox/{first}",json={**fields,"revision":2,"company_id":None})
        self.assertFalse((await card()).get("inquiry_count"))

    async def test_shift_handoff_and_duplicate_source_ids(self):
        a, b = await self.account("出口一组"), await self.account("出口二组")
        csv = "event_id,kind,body,occurred_at\n" + (
            "123,dm,Can you quote GYS-007?,2020-01-01T03:00:00+08:00\n"
            "124,comment,Thanks,2020-01-01T02:00:00+08:00\n"
            "125,dm,16 holes,2020-01-01T04:00:00+08:00\n"
            "123,dm,Can you quote GYS-007?,2020-01-01T03:00:00+08:00\n")
        result = await self.client.post("/api/inbox/import", json={"account": a, "content": csv})
        self.assertEqual(result.json(), {"added": 3, "duplicates": 1})
        ev = {"event_id": "123", "kind": "comment", "body": "Vehicle details",
              "occurred_at": "2020-01-02T03:00:00+08:00", "account": b}
        self.assertEqual((await self.client.post("/api/inbox", json=ev)).json(), {"added": 1})
        async def queue(name):
            return (await self.client.get("/api/inbox", params={"queue": name})).json()["items"]
        records = await queue("unassigned")
        self.assertEqual(len(records), 4)
        row = records[0]
        self.assertEqual((row["intent"], row["mode"], row["event_id"]), ("buy", "import", "123"))
        self.assertEqual(len(await queue("overdue")), 4)
        path = f"/api/inbox/{row['id']}"
        claim = {"role": "unknown", "status": "following", "owner": "Li", "next_action": "核对车桥",
                 "revision": row["revision"]}
        self.assertEqual((await self.client.patch(path, json=claim)).status_code, 200)
        self.assertEqual(len(await queue("unassigned")), 3)
        self.assertEqual(len(await queue("unanswered")), 4)
        self.assertEqual((await self.client.patch(path, json=claim)).status_code, 409)
        reply = {**claim, "revision": 1, "replied_at": "2020-01-01T08:00:00+08:00"}
        self.assertEqual((await self.client.patch(path, json={**reply, "owner": ""})).status_code, 400)
        self.assertEqual((await self.client.patch(path, json={**reply, "replied_at": "2019-01-01T00:00:00Z"})).status_code, 400)
        self.assertEqual((await self.client.patch(path, json=reply)).status_code, 200)
        self.assertEqual(len(await queue("unanswered")), 3)
        await self.client.patch(path, json={"owner": "Ma", "status": "done", "role": "unknown"})
        await self.client.post("/api/inbox/import", json={"account": a, "content": csv})
        saved = (await self.client.get("/api/inbox", params={"status": "done"})).json()["items"][0]
        self.assertEqual((saved["owner"], saved["replied_by"], saved["replied_at"], saved["next_action"]),
                         ("Ma", "Li", "2020-01-01T00:00:00+00:00", "核对车桥"))

    async def test_invalid_import_is_atomic(self):
        source = await self.account("CSV")
        content = ("event_id,kind,body,occurred_at\n1,dm,hello,2020-01-01T01:00:00Z\n"
                   "2,dm,hello,2020-01-01T01:00:00\n")
        result = await self.client.post("/api/inbox/import", json={"account": source, "content": content})
        self.assertEqual(result.status_code, 400)
        self.assertEqual((await self.client.get("/api/inbox")).json()["items"], [])

    async def test_per_source_retry_preserves_cursor_and_records(self):
        ident = await self.account("授权频道", "youtube", "channel1")
        self.enterContext(patch.dict(os.environ, {f"DESK_YOUTUBE_KEY_{ident.upper()}": "example"}))
        event = {"event_id": "1", "kind": "comment", "body": "Can you quote?"}
        with patch("desk.connectors.youtube_comments", return_value=([event], "next1")):
            self.assertEqual(sync_sources(), 1)
        with patch("desk.connectors.youtube_comments", side_effect=OSError("offline")) as fetch:
            with self.assertRaises(ValueError):
                sync_sources()
            self.assertEqual(fetch.call_args.args[-1], "next1")
        initialize()
        info = (await self.client.get("/api/info")).json()["sync_sources"][0]
        self.assertTrue(info["last_success"] and info["error"] and info["more"])
        with patch("desk.connectors.youtube_comments", return_value=([event, {**event,"event_id":"2"}], "")):
            self.assertEqual(sync_sources(), 1)
        info = (await self.client.get("/api/info")).json()["sync_sources"][0]
        self.assertFalse(info["error"] or info["more"])

    async def test_migration_preserves_old_handled_state(self):
        legacy_file = Path(os.environ["DESK_DATABASE"]).with_name("legacy.db")
        with patch.dict(os.environ, {"DESK_DATABASE": str(legacy_file)}):
            with database() as conn:
                conn.executescript(SCHEMA)
                conn.execute("INSERT INTO accounts(id,platform,label) VALUES ('a','instagram','Old')")
                conn.execute("""INSERT INTO inquiries(account,event_id,kind,sender,body,url,occurred_at,
                    received_at,mode,suggested_role,evidence,intent,status,owner,handled_at)
                    VALUES ('a','e','dm','Shop','quote','','2020-01-01T00:00:00+00:00',
                    '2020-01-01T00:01:00+00:00','platform','unknown','','buy','done','Chen',
                    '2020-01-02T00:01:00+00:00')""")
            initialize()
            initialize()
            record = (await self.client.get("/api/inbox")).json()["items"][0]
            self.assertEqual((record["owner"], record["status"], record["replied_at"], record["revision"]),
                             ("Chen", "done", "", 0))

    def test_resume_reads_head_and_pending_page(self):
        events = []
        def page(url):
            events.append(url)
            return {"items": [], "nextPageToken": "page2"} if len(events) == 1 else {"items": []}
        with patch.object(connectors, "fetch_json", side_effect=page):
            self.assertEqual(connectors.youtube_comments({"external_id": "channel"}, "key", "page6"), ([], ""))
        self.assertNotIn("pageToken", events[0])
        self.assertIn("pageToken=page6", events[1])

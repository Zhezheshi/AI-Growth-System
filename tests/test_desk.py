import hashlib
import hmac
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx

from desk.main import app, lifespan
from desk.storage import database


class DeskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        work = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict("os.environ", {
            "DESK_DEMO": "1", "DESK_DATABASE": str(Path(work) / "test.db"),
            "DESK_SYNC_SECONDS": "0", "DESK_META_APP_SECRET": "test-secret",
        }))
        await self.enterAsyncContext(lifespan(app))
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ))

    async def search(self, **data):
        response = await self.client.post("/api/search", json=data)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_reference_namespace_and_page(self):
        info = await self.client.get("/api/info")
        self.assertEqual(info.status_code, 200, info.text)
        self.assertGreaterEqual(info.json()["products"], 74)
        self.assertTrue(info.json()["demo"])
        result = await self.search(code="19032", namespace="WVA")
        self.assertEqual(result["state"], "candidate")
        self.assertEqual(result["items"][0]["code"], "GYS-007")
        self.assertEqual(result["items"][0]["page"], 6)
        self.assertFalse(result["items"][0]["quote"]["ready"])
        self.assertEqual((await self.search(code="19032", namespace="OE"))["state"], "missing")

    async def test_same_reference_two_variants(self):
        params = {"code": "QT435SH10-3502572FW", "position": "rear"}
        all_matches = await self.search(**params)
        self.assertEqual((all_matches["state"], all_matches["count"]), ("ambiguous", 2))
        narrowed = await self.search(**params, holes=16, dimensions="177 x 197 x 15.5/18.5")
        self.assertEqual((narrowed["state"], narrowed["count"]), ("candidate", 1))
        self.assertEqual(narrowed["items"][0]["code"], "JH6后A")
        self.assertIn("孔数与目录记录不同", narrowed["items"][1]["conflicts"])
        self.assertEqual((await self.search(**params, holes=3))["state"], "conflict")

    async def test_fmsi_series_is_not_an_exact_variant(self):
        result = await self.search(query="Can you quote FMSI4707?", kind="drum")
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual({row["code"] for row in result["items"]}, {"GYS-087","GYS-088"})
        self.assertFalse(any(row["code_match"] for row in result["items"]))
        self.assertEqual((await self.search(query="Can you quote WVA4707?"))["state"], "missing")

    async def test_quote_uses_exact_fit_grade_and_price_tier(self):
        params = {"code":"DEMO-D01", "axle":"DEMO-AXLE", "position":"rear", "material":"DEMO-M1",
                  "market":"Indonesia", "currency":"USD", "incoterm":"FOB"}
        quote = (await self.search(**params, qty=200))["items"][0]["quote"]
        self.assertTrue(quote["demo"])
        self.assertEqual(quote["subtotal"], "3360.00")
        low = (await self.search(**params, qty=199))["items"][0]["quote"]
        self.assertEqual(low["price"]["unit_price"], "18.20")
        bad = {**params, "axle":"another axle"}
        self.assertFalse((await self.search(**bad, qty=200))["items"][0]["quote"]["ready"])
        bad = {**params, "material":"unverified"}
        self.assertFalse((await self.search(**bad, qty=200))["items"][0]["quote"]["ready"])

    async def test_custom_quote_calculates_weight_and_multiple_accessory_materials(self):
        params = {"product":"GYS-007", "axle":"BPW", "position":"front", "material":"M-1",
                  "thickness":"17.5/10.5", "market":"Indonesia", "standard":"RFQ-SPEC-01",
                  "currency":"USD", "incoterm":"CIF", "delivery_point":"Jakarta", "qty":200,
                  "kg_per_set":"2.5", "material_cost_per_kg":"4.80", "includes_attachments":True,
                  "attachments":[{"name":"Rivet A","material":"Copper","units_per_set":10,"cost_per_piece":"0.25"},
                                 {"name":"Rivet B","material":"Aluminium","units_per_set":4,"cost_per_piece":"0.125"}],
                  "other_cost_per_set":"1.10", "trade_cost_total":"220", "proposed_per_set":"19.00",
                  "source":"Internal cost sheet 123"}
        response = await self.client.post("/api/quotes/calculate",json=params)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["terms"], "CIF Jakarta")
        self.assertEqual(Decimal(data["total_cost"]), Decimal("3440"))
        self.assertEqual(Decimal(data["difference"]), Decimal("360"))
        self.assertEqual(data["attachments"][1]["material"], "Aluminium")
        self.assertIsNone(data["fit"])
        self.assertTrue(any("技术复核" in value for value in data["reviews"]))
        self.assertEqual((await self.client.post("/api/quotes/calculate",json={**params,"includes_attachments":False})).status_code,400)
        self.assertEqual((await self.client.post("/api/quotes/calculate",json={**params,"kg_per_set":"0"})).status_code,422)
        bare = await self.client.post("/api/quotes/calculate",json={**params,
            "includes_attachments":False,"attachments":[]})
        self.assertEqual(Decimal(bare.json()["total_cost"]), Decimal("2840"))

    async def test_inbox_replay_auth_and_account_isolation(self):
        accounts = [(await self.client.post("/api/accounts",json={"platform":"tiktok","label":label})).json()
                    for label in ("Workshop account","Export account")]
        event = {"event_id":"unique-1", "kind":"dm", "sender":"Test", "body":"Our fleet needs a quote."}
        route = f"/ingest/{accounts[0]['id']}"
        self.assertEqual((await self.client.post(route,json=event)).status_code, 401)
        for account in accounts:
            result = await self.client.post(f"/ingest/{account['id']}",json=event,
                headers={"Authorization":"Bearer " + account["secret"]})
            self.assertEqual(result.json()["added"], 1)
        duplicate = await self.client.post(route,json=event,headers={"Authorization":"Bearer " + accounts[0]["secret"]})
        self.assertEqual(duplicate.json()["added"], 0)
        rows = (await self.client.get("/api/inbox",params={"account":accounts[0]["id"]})).json()["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["suggested_role"],rows[0]["role"]), ("end_user","unknown"))
        await self.client.patch(f"/api/inbox/{rows[0]['id']}",json={
            "role":"end_user","status":"following","owner":"sales"})
        row = (await self.client.get("/api/inbox",params={"account":accounts[0]["id"]})).json()["items"][0]
        self.assertEqual((row["role"],row["status"],row["owner"]),("end_user","following","sales"))

    async def test_signed_meta_receiver(self):
        created = await self.client.post("/api/accounts",json={
            "platform":"instagram","label":"Company","external_id":"ig123"})
        self.assertEqual(created.status_code, 201)
        body = json.dumps({"object":"instagram","entry":[{"id":"ig123","messaging":[
            {"sender":{"id":"customer"},"message":{"mid":"m1","text":"hello"}},
            {"sender":{"id":"ig123"},"message":{"mid":"m2","text":"reply","is_echo":True}},
        ]}]}).encode()
        path = "/webhooks/instagram"
        self.assertEqual((await self.client.post(path,content=body)).status_code,401)
        sign = "sha256=" + hmac.new(b"test-secret",body,hashlib.sha256).hexdigest()
        self.assertEqual((await self.client.post(path,content=body,headers={"X-Hub-Signature-256":sign})).json(),{"added":1})

    async def test_failed_import_is_atomic(self):
        response = await self.client.post("/api/import/products",json={
            "content":"code,name,kind,source\nABC-123,New part,drum,Test\nDEF,Invalid,unknown,Test\n"})
        self.assertEqual(response.status_code,400)
        self.assertEqual((await self.search(code="ABC-123"))["state"],"missing")

    async def test_buyer_import_preserves_followup(self):
        data = {"content":"id,company,country,grade,email\nTEST-001,Test Trucks,Mexico,A,buyer@example.org\n"}
        self.assertEqual((await self.client.post("/api/buyers/import",json=data)).json(),{"imported":1})
        await self.client.patch("/api/buyers/TEST-001",json={"stage":"meeting","notes":"Meet at the booth"})
        await self.client.post("/api/buyers/import",json=data)
        row = (await self.client.get("/api/buyers",params={"grade":"A","query":"Test Trucks"})).json()[0]
        self.assertEqual((row["stage"],row["notes"]),("meeting","Meet at the booth"))

    async def test_case_notes_are_not_fitment(self):
        saved = await self.client.post("/api/cases",json={
            "product":"GYS-007","source":"Field order #10","evidence":"Shop matched old lining"})
        self.assertEqual(saved.status_code,200)
        with database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM records WHERE kind='cases' AND product='gys007'").fetchone()[0],1)
        self.assertEqual((await self.search(code="GYS-007",axle="BPW",position="front",material="X"))["state"],"candidate")

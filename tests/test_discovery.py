import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from desk.discovery import initialize_discovery
from desk.main import app, lifespan


def csv_data(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output,fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return {"content":output.getvalue()}


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict("os.environ", {"DESK_DATABASE":str(Path(tmp)/"desk.db"),"DESK_SYNC_SECONDS":"0"}))
        await self.enterAsyncContext(lifespan(app))
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),base_url="http://localhost"))
        self.lead = {"country":"Indonesia","company":"Sample Garage","website":"https://garage.example",
                     "source_url":"https://garage.example/vehicles","evidence":"Our workshop maintains trucks.",
                     "role":"维修服务商","checked_at":"2026-11-21","products":"重卡维保"}

    async def search(self, **params):
        result = await self.client.get("/api/discovery/search",params=params)
        self.assertEqual(result.status_code,200,result.text)
        return result.json()["items"]

    async def test_snapshot_industry_evidence_and_market_filter(self):
        result = await self.search(country="印尼",product="drum")
        self.assertGreaterEqual(len(result),10)
        self.assertTrue(any(row["match"].startswith("商用车相关") for row in result))
        self.assertTrue(all(row["proofs"][0]["source_url"].startswith("https://") for row in result))
        self.assertEqual(await self.search(country="nonexistent market"),[])

    async def test_reimport_and_restart_preserve_review_and_match_crm(self):
        capture = await self.client.post("/api/discovery/capture",json=self.lead)
        self.assertEqual(capture.status_code,200,capture.text)
        ident=capture.json()["id"]
        review = await self.client.patch(f"/api/discovery/reviews/{ident}",
            json={"decision":"待核实","reason":"联系采购负责人确认直采"})
        self.assertEqual(review.status_code,200)
        directory = {**self.lead,"country":"印尼","company":"SAMPLE GARAGE"}
        for _ in range(2):
            response = await self.client.post("/api/discovery/import/directory",json=csv_data([directory]))
            self.assertEqual(response.status_code,200,response.text)
        response = await self.client.post("/api/discovery/import/crm",json=csv_data([{
            "company":"Sample Garage","country":"印度尼西亚","website":""}]))
        self.assertEqual(response.status_code,200,response.text)
        initialize_discovery()
        rows=await self.search(country="Indonesia",query="sample garage",role="workshop")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["id"],ident)
        self.assertEqual(len(rows[0]["proofs"]),2)
        self.assertEqual((rows[0]["decision"],rows[0]["crm"]),("待核实",1))

    async def test_distinct_companies_at_group_domain_remain_separate(self):
        for name in ["Sample Company East","Sample Company West"]:
            response=await self.client.post("/api/discovery/capture",json={**self.lead,"company":name})
            self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(await self.search(query="Sample Company")),2)

    async def test_bad_directory_rolls_back_whole_import(self):
        bad = {**self.lead,"company":"Invalid","source_url":"javascript:alert(0)"}
        response=await self.client.post("/api/discovery/import/directory",json=csv_data([self.lead,bad]))
        self.assertEqual(response.status_code,400,response.text)
        self.assertIn("第 3 行",response.text)
        self.assertEqual(await self.search(query="Sample Garage"),[])

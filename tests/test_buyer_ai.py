import io
import json
import unittest
from unittest.mock import patch

import httpx

from desk.main import app


class BuyerEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ))
        self.source = "Our workshop services heavy truck brakes."
        self.payload = {"body": self.source}

    async def run_model(self, result):
        raw = json.dumps({"choices": [{"message": {"content": json.dumps(result)}}]}).encode()
        with patch.dict("os.environ", {"DESK_AI_MODEL": "test", "DESK_AI_BASE_URL": "https://model.invalid"}):
            with patch("desk.ai.urlopen", return_value=io.BytesIO(raw)) as request:
                response = await self.client.post("/api/assist/buyer", json=self.payload)
        self.assertEqual(response.status_code, 200)
        sent = json.loads(request.call_args.args[0].data)
        self.assertEqual(sent["messages"][-1]["content"], self.source)
        return response.json()

    async def test_verbatim_quote_and_fallback(self):
        result = await self.run_model({"role": "workshop", "evidence": "Our workshop", "question": "贵司自行采购吗？"})
        self.assertTrue(result["supported"])
        self.assertEqual(result["role"], "workshop")
        missing = await self.run_model({"role": "fleet", "summary": "采购量很大", "evidence": "run 100 trucks"})
        self.assertFalse(missing["supported"])
        self.assertEqual((missing["role"], missing["evidence"], missing["summary"]), ("unknown", "", ""))

    async def test_manual_review_without_model(self):
        with patch.dict("os.environ", {"DESK_AI_MODEL": "", "DESK_AI_BASE_URL": ""}):
            with patch("desk.ai.urlopen") as request:
                response = await self.client.post("/api/assist/buyer", json=self.payload)
                result = response.json()
        request.assert_not_called()
        self.assertFalse(result["supported"])
        self.assertIn("人工", result["reason"])

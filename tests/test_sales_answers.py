import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from desk.knowledge import search
from desk.main import Search
from desk.quoting import CustomQuote, calc_quote
from desk.storage import database, initialize


class SalesAnswerTests(unittest.TestCase):
    def setUp(self):
        work = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict("os.environ", {
            "DESK_DATABASE": str(Path(work) / "answers.db"), "DESK_DEMO": "1",
        }))
        initialize()
        self.conn = self.enterContext(database())

    def search(self, **params):
        return search(self.conn, Search(**params).model_dump())

    def test_actual_measurement_refines_the_question(self):
        params = {"code": "QT435SH10-3502572FW", "position": "rear"}
        answer = self.search(**params)["answer"]
        self.assertEqual((answer["field"], answer["options"]), ("holes", [16, 20]))
        self.assertIn("20", answer["en"])
        result = self.search(**params, holes=16)
        self.assertEqual(result["state"], "candidate")
        self.assertEqual(result["answer"]["field"], "axle")
        self.assertIn("JH6后A", result["answer"]["zh"])
        self.assertFalse(result["items"][0]["quote"]["ready"])
        self.assertEqual(self.search(**params, holes=3)["answer"]["options"], [])

    def test_reference_system_in_customer_text(self):
        for label in ("OE 19032", "OE no. 19032", "OEM part number 19032"):
            with self.subTest(label=label):
                self.assertEqual(self.search(query=f"Can you supply {label}?")["state"], "missing")
        wva = self.search(query="Can you supply WVA 19032?", qty=150)
        self.assertEqual(wva["items"][0]["code"], "GYS-007")
        actions = wva["items"][0]["quote"]["actions"]
        self.assertEqual(len(actions), len(wva["items"][0]["quote"]["missing"]))
        customer = next(action for action in actions if action["owner"] == "客户与销售")
        self.assertIn("币种", customer["detail"])
        self.assertNotIn("套数", customer["detail"])

    def test_oe_lookup_preserves_catalog_evidence_and_code_boundaries(self):
        for request in [
            {"namespace": "OE", "code": "A91 3222 S 1293"},
            {"namespace": "目录 OE 参考码", "code": "A913222S1293"},
            {"query": "Can you quote OEM part number A91 3222 S 1293?"},
            {"query": "OE A91 3222 S 1293", "namespace": "OE"},
        ]:
            with self.subTest(request=request):
                result = self.search(**request)
                self.assertEqual((result["count"], result["state"]), (1, "candidate"))
                item = result["items"][0]
                self.assertEqual(item["code"], "4515Q")
                hit, = item["hits"]
                self.assertEqual((hit["namespace"], hit["label"], hit["page"]),
                                 ("目录 OE 参考码", "A91 3222 S 1293", 32))
        self.assertEqual(self.search(query="OE A 3222 T 1346")["items"][0]["code"], "4551Q")
        self.assertEqual(self.search(query="OE A91 3222 S 12930")["count"], 0)
        self.assertEqual(self.search(code="A91 3222 S 1293", namespace="WVA")["count"], 0)

    def test_demo_label_survives_copy_and_private_costs_stay_internal(self):
        params = dict(code="DEMO-D01", axle="DEMO-AXLE", material="DEMO-M1", position="rear",
                      market="Indonesia", currency="USD", incoterm="FOB", qty=200)
        result = self.search(**params)
        self.assertEqual(result["state"], "verified")
        self.assertTrue(result["answer"]["en"].startswith("DEMO ONLY"))
        self.assertEqual(result["items"][0]["quote"]["subtotal"], "3360.00")
        form = CustomQuote(product=params.pop("code"), **params, thickness="15",
                           standard="INTERNAL-SPEC", delivery_point="Jakarta", kg_per_set="1.5",
                           material_cost_per_kg="2.35", includes_attachments=False,
                           other_cost_per_set="0", trade_cost_total="0", proposed_per_set="33.12",
                           source="Private Supplier Invoice")
        cost = calc_quote(self.conn, form)
        self.assertEqual(Decimal(cost["total_cost"]), Decimal("705"))
        reply = cost["customer_reply"]
        self.assertTrue(reply.startswith("DEMO ONLY"))
        self.assertIn("FOB terms for Jakarta", reply)
        for private in (cost["total_cost"], cost["proposed_total"], form.source, "33.12", "2.35"):
            self.assertNotIn(private, reply)

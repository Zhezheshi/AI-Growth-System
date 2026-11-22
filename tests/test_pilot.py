import csv
import tempfile
import unittest
from pathlib import Path

from scripts.pilot import COLUMNS, freeze, report


def candidate(number, crm=False):
    return {"id": number, "crm": crm, "company": f"Buyer {number}", "country": "Indonesia",
            "history": "客户历史待核", "proofs": [{"website": f"https://buyer{number}.example",
                                             "source_url": "https://source.example", "evidence": "Catalog"}]}


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.run_dir = Path(self.enterContext(tempfile.TemporaryDirectory())) / "pilot"
        self.cards = [candidate(i) for i in range(1, 16)]
        self.cards.insert(1, candidate(1))
        self.cards[3]["crm"] = True
        result = {"items": self.cards, "query": "brakes", "searched_at": "2026-11-21T12:00:00Z"}
        freeze(self.run_dir, result, {"country": "Indonesia"}, ["BUYER 2"])

    def rows(self):
        with (self.run_dir / "review.csv").open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def save(self, rows):
        with (self.run_dir / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def test_frozen_order_exclusions_and_no_fabricated_reviews(self):
        rows = self.rows()
        self.assertEqual(len(rows), 10)
        self.assertEqual([r["公司"] for r in rows[:3]], ["Buyer 1", "Buyer 4", "Buyer 5"])
        stats = report(self.run_dir)
        self.assertEqual(stats["企业复核"]["已复核"], 0)
        self.assertIsNone(stats["团队复核"]["人工中位分钟"])
        self.assertIsNone(stats["企业复核"]["认可数占全部送审批次百分比"])

    def test_actual_denominator_paired_times_and_independent_groups(self):
        rows = self.rows()
        for i, decision in enumerate(("值得联系", "待核实", "")):
            rows[i]["企业已送审"] = "是"
            if decision:
                rows[i].update(企业判断=decision, 企业复核人="销售", 企业人工分钟=str(2 + i * 4),
                               企业辅助分钟=str(i + 1))
        rows[0].update(团队已送审="是", 团队判断="不匹配", 团队复核人="测试")
        self.save(list(reversed(rows)))
        stats = report(self.run_dir)
        enterprise = stats["企业复核"]
        self.assertEqual(enterprise["认可数占全部送审批次百分比"], 33.33)
        self.assertEqual(enterprise["实际送审分母"], 3)
        self.assertEqual(enterprise["送审后未复核"], 1)
        self.assertEqual(enterprise["待核实"], 1)
        self.assertEqual(enterprise["人工中位分钟"], 4)
        self.assertEqual(enterprise["辅助中位分钟"], 1.5)
        self.assertEqual(stats["团队复核"]["不匹配"], 1)
        self.assertIsNone(stats["团队复核"]["人工减辅助中位分钟差"])

    def test_tampered_sample_and_invalid_times(self):
        rows = self.rows()
        self.save(rows[1:])
        with self.assertRaisesRegex(ValueError, "冻结快照"):
            report(self.run_dir)
        rows[0].update(企业已送审="是", 企业判断="值得联系", 企业复核人="销售", 企业人工分钟="NaN")
        self.save(rows)
        with self.assertRaisesRegex(ValueError, "有限分钟"):
            report(self.run_dir)

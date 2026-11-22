import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from desk import ai, prospecting
from desk.discovery import company_id
from desk.storage import database, initialize


class ProspectingTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"DESK_DATABASE": str(Path(tmp)/"test.db"), "DESK_DEMO":"0"}))
        initialize()
        self.request = prospecting.Research(country="印尼",role="workshop")
        self.lead = dict(company="PT. Surya", website="https://surya.example", role="重卡维修企业",
                         products="卡车维护", summary="官网列有车间服务，可询问替换件采购。",
                         evidence="Truck workshop", source_url="https://surya.example/services",
                         contact_url="https://surya.example/contact", next_action="车间由谁采购刹车片？",gap="")

    def queue(self):
        with patch.object(prospecting.ai,"provider",return_value="codex"),patch.object(prospecting,"Thread"):
            return prospecting.start_run(self.request)["id"]

    def complete(self, rows):
        ident=self.queue()
        calls = [[r["source_url"], r["contact_url"]] for r in rows] + [[], [], [], []]
        with patch.object(ai,"run_codex",return_value=({"candidates":rows},calls)) as cli:
            prospecting.run_search(ident,self.request.model_dump())
            self.assertTrue(cli.call_args.kwargs["live"])
        return prospecting.get_run(ident)

    def test_batch_classification_and_replay_preserve_local_sales_data(self):
        with database() as conn:
            known = company_id(conn,dict(country="Indonesia",company="Known Tbk",website="https://known.example"))
            conn.execute("UPDATE discovery_companies SET crm=1,reason='old review' WHERE id=?",(known,))
        known_row = {**self.lead,"company":"PT Known", "website":"https://known.example"}
        pending = {**self.lead,"company":"West", "website":"https://west.example",
                   "contact_url":"javascript:void(0)"}
        rows = [self.lead,known_row,pending]
        result = self.complete(rows)
        self.assertEqual((result["status"],result["web_calls"]),("done",7))
        self.assertEqual([r["category"] for r in result["results"]],["新增","重复","待核实"])
        self.assertEqual(result["results"][1]["company_id"],known)
        self.assertIn("联系入口",result["results"][2]["gap"])
        replay = self.complete(rows)
        self.assertEqual([r["category"] for r in replay["results"]],["重复","重复","待核实"])
        self.assertEqual(result,prospecting.get_run(result["id"]))
        with database() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM discovery_companies").fetchone()[0],2)
            self.assertEqual(tuple(conn.execute("SELECT crm,reason FROM discovery_companies WHERE id=?", (known,)).fetchone()), (1,"old review"))

    def test_shared_domain_ambiguous_and_repeat_candidate(self):
        result=self.complete([self.lead,self.lead,{**self.lead,"company":"West Workshop"}])
        self.assertEqual([r["category"] for r in result["results"]],["新增","待核实"])
        self.assertIn("经营主体",result["results"][1]["gap"])

    def test_busy_timeout_and_disabled(self):
        with patch.object(ai,"provider",return_value=""):
            with self.assertRaises(HTTPException) as disabled:
                prospecting.start_run(self.request)
            self.assertEqual(disabled.exception.status_code,503)
        ident=self.queue()
        with self.assertRaises(HTTPException) as busy:
            self.queue()
        self.assertEqual(busy.exception.status_code,409)
        with patch.object(ai,"run_codex",side_effect=subprocess.TimeoutExpired("codex",720)):
            prospecting.run_search(ident,self.request.model_dump())
        self.assertEqual(prospecting.get_run(ident)["status"],"failed")
        self.assertEqual(self.complete([])["results"],[])

    def test_unopened_contact_is_held_for_review(self):
        ident = self.queue()
        with patch.object(ai,"run_codex",return_value=({"candidates":[self.lead]},[[self.lead["source_url"]]])):
            prospecting.run_search(ident,self.request.model_dump())
        result = prospecting.get_run(ident)["results"][0]
        self.assertEqual(result["category"], "待核实")
        self.assertIn("联系页缺少本轮打开记录",result["gap"])
        self.assertIsNone(result["company_id"])

    def test_previous_public_sites_exclude_crm_and_other_markets(self):
        self.complete([self.lead])
        with database() as conn:
            company_id(conn,dict(country="Indonesia",company="Internal client",website="https://private.example"))
        self.assertEqual(prospecting.previously_explored(self.request.model_dump()), ["surya.example"])
        self.assertEqual(prospecting.previously_explored({**self.request.model_dump(), "country":"Brazil"}), [])

    def test_pilot_is_demo_only_and_restart_keeps_history(self):
        archive = Path(os.environ["DESK_DATABASE"]).with_suffix(".json")
        archive.write_text(json.dumps({"request": self.request.model_dump(), "started": "2026-11-22T00:00:00+00:00",
                          "finished": "2026-11-22T00:02:00+00:00", "web_calls": 2, "results": [self.lead]}))
        with patch.object(prospecting, "PUBLIC_PILOT", archive):
            prospecting.load_demo_run()
            self.assertFalse(prospecting.recent_runs()["runs"])
            with patch.dict(os.environ, {"DESK_DEMO": "1"}):
                prospecting.load_demo_run()
                prospecting.load_demo_run()
        runs = prospecting.recent_runs()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0]["request"]["snapshot"])
        self.assertEqual(prospecting.get_run(runs[0]["id"])["results"][0]["category"], "新增")

    def test_cli_counts_completed_calls_and_marks_only_opened_pages(self):
        seen = "https://garage.example/contact"
        events = [
            {"type":"item.started", "item":{"type":"web_search"}},
            {"type":"item.completed","item":{"type":"web_search","results":[{"url":seen,"ref_id":"turn0search1"}]}},
            {"type":"item.completed","item":{"type":"web_search","results":[{"url":seen,"ref_id":"turn1view0"}]}},
        ]
        def respond(args, **kwargs):
            self.assertNotIn("code_mode_host",args)
            Path(args[args.index('--output-last-message')+1]).write_text('{"candidates":[]}')
            return subprocess.CompletedProcess(args,0,stdout="\n".join(map(json.dumps,events)))
        with patch.object(ai.shutil,"which",return_value="/test/codex"),patch.object(ai.subprocess,"run",side_effect=respond):
            result, calls = ai.run_codex(prospecting.SCHEMA,"public terms",live=True)
        self.assertEqual(result, {"candidates":[]})
        self.assertEqual(calls, [[], [seen]])

    def test_cli_requires_observed_web_activity(self):
        def respond(args, **kwargs):
            self.assertIn('web_search="live"',args)
            self.assertIn('--json',args)
            Path(args[args.index('--output-last-message')+1]).write_text('{"candidates":[]}')
            return subprocess.CompletedProcess(args,0,stdout=json.dumps({"type":"turn.completed"}))
        with patch.object(ai.shutil,"which",return_value="/test/codex"),patch.object(ai.subprocess,"run",side_effect=respond):
            with self.assertRaisesRegex(ValueError,"未记录公开网络查询"):
                ai.run_codex(prospecting.SCHEMA,"public terms",live=True)

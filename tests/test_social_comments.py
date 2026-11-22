import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from fastapi import HTTPException

from desk import inbox, social_comments as social
from desk.storage import database, initialize


class SocialCommentsTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"DESK_DATABASE":str(Path(tmp)/"desk.db"),"DESK_DEMO":"0"}))
        initialize()
        with database() as conn:
            self.account = inbox.create_account(conn,"xhs","销售账号")["id"]
        self.note = {"url":"https://www.xiaohongshu.com/explore/0123456789abcdef01234567",
                     "title":"卡车维修","keyword":"卡车刹车片","comments":[
                         {"id":"c123","text":"有 16 孔的吗？","nickname":"汽修店"}]}

    def test_progress_final_and_replay_share_one_candidate(self):
        for _ in range(3):
            social.save_comments(self.account, self.note)
        alternate = self.note["url"].replace("/explore/", "/discovery/item/")
        social.save_comments(self.account, {**self.note, "url": alternate})
        items = social.list_comments(self.account,0)["items"]
        self.assertEqual(len(items),1)
        self.assertIsNone(items[0]["inquiry_id"])
        self.assertEqual(social.accept_comment(items[0]["id"]),{"added":1})
        self.assertEqual(social.accept_comment(items[0]["id"]),{"added":0})
        with database() as conn:
            item = conn.execute("SELECT * FROM inquiries").fetchone()
            self.assertEqual((item["body"],item["mode"],item["account"]),("有 16 孔的吗？","social",self.account))

    def test_operator_can_stop_and_keep_progress(self):
        import time
        started = Event()
        def collect(profile, words, report, check, on_note):
            on_note(self.note)
            started.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                check()
                time.sleep(0.01)
            yield self.note
        with patch.object(social.xhs_browser,"collect",collect), patch.dict(os.environ,{"LEAD_XHS_MANUAL":"0"}):
            body=social.Search(account=self.account,keyword="刹车片",limit=1)
            social.start(body)
            self.assertTrue(started.wait(2))
            try:
                with self.assertRaises(HTTPException):
                    social.start(body)
            finally:
                social.cancel()
                for _ in range(200):
                    if not social.list_comments(self.account,0)["task"]["running"]:
                        break
                    time.sleep(0.01)
            page=social.list_comments(self.account,0)
            self.assertFalse(page["task"]["running"])
            self.assertEqual(len(page["items"]),1)

    def test_manual_mode_blocks_xhs_before_launch(self):
        with patch.dict(os.environ, {"LEAD_XHS_MANUAL":"1"}):
            with self.assertRaises(HTTPException):
                social.start(social.Search(account=self.account,keyword="鼓式制动片"))

import asyncio
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import httpx

from desk.main import app, lifespan
from desk.originals import upload_folder


class OriginalsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bound = tmp / "external"
        self.bound.mkdir()
        self.enterContext(patch.dict(os.environ, {"DESK_DATABASE":str(tmp/"desk.db"),"DESK_DOCS_DIR":str(self.bound)}))
        await self.enterAsyncContext(lifespan(app))
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"))

    async def upload(self, name, content):
        return await self.client.post("/api/knowledge/files", params={"name":name}, content=content)

    async def test_upload_is_atomic_and_names_round_trip(self):
        name = "鼓片样件 #5%.pdf"
        data = b"%PDF-1.7\nexample\n%%EOF"
        responses = await asyncio.gather(self.upload(name,data),self.upload(name,data))
        self.assertEqual(sorted(r.status_code for r in responses),[201,409])
        listing = (await self.client.get("/api/info")).json()["sources"]
        matches = [doc for doc in listing if doc["name"] == name]
        self.assertEqual(len(matches),1)
        self.assertEqual((await self.client.get("/source/"+matches[0]["id"])).content,data)
        self.assertEqual(list(self.bound.iterdir()),[])

    async def test_catalog_original_priority_and_missing_binding(self):
        name = "kooying catalog.pdf"
        (self.bound/name).write_bytes(b"%PDF-external")
        self.assertEqual((await self.upload(name,b"%PDF-new")).status_code,409)
        self.assertEqual((await self.client.get("/source/catalog-en")).content,b"%PDF-external")
        self.assertEqual((await self.upload("冠隆产品册.pdf", b"%PDF-upload")).status_code,201)
        self.assertEqual((await self.client.get("/source/catalog-cn")).content,b"%PDF-upload")

    async def test_invalid_input_leaves_no_file(self):
        for name,content,status in [("../report.pdf",b"%PDF-1",400),("file.html",b"test",415),
                                    ("empty.pdf",b"",422),("table.xlsx",b"incorrect",422)]:
            self.assertEqual((await self.upload(name,content)).status_code,status)
        with patch("desk.originals.MAX_BYTES",3):
            self.assertEqual((await self.upload("report.pdf",b"%PDF-1")).status_code,413)
        self.assertEqual(list(upload_folder().iterdir()),[])

    async def test_office_download(self):
        buf = BytesIO()
        with ZipFile(buf,"w") as archive:
            archive.writestr("[Content_Types].xml","<Types/>")
            archive.writestr("word/document.xml","<document/>")
        response = await self.upload("spec.docx",buf.getvalue())
        self.assertEqual(response.status_code,201)
        download = await self.client.get("/source/upload/spec.docx")
        self.assertIn("attachment",download.headers["content-disposition"])
        self.assertEqual(download.content,buf.getvalue())

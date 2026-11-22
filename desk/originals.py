"""Local knowledge originals. External bindings are read-only; uploads belong to one database."""
import os
import tempfile
import unicodedata
from pathlib import Path
from re import fullmatch
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .storage import ROOT, database_path

router = APIRouter()
SOURCES = {
    "kooying catalog.pdf": "catalog-en",
    "冠隆产品册.pdf": "catalog-cn",
    "2026年浙江冠英企业介绍.pdf": "company",
}
TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_BYTES = 100 * 1024 * 1024


def upload_folder():
    db = database_path()
    return db.with_name(db.name + ".uploads")


def bound_file(name):
    folder = os.getenv("DESK_DOCS_DIR") or ROOT / "runtime" / "docs"
    return Path(folder, name)


def listed_sources():
    folder = upload_folder()
    sources = [{"name": name, "id": key,
                "available": bound_file(name).is_file() or (folder / name).is_file()}
               for name, key in SOURCES.items()]
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if path.name not in SOURCES and path.suffix.lower() in TYPES and not path.is_symlink() and path.is_file():
                sources.append({"name": path.name, "id": "upload/" + quote(path.name, safe=""), "available": True})
    return sources


def checked_name(value):
    name = unicodedata.normalize("NFC", value)
    if (not name or name.startswith(".") or name != name.strip() or len(name.encode()) > 240
            or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name)
            or fullmatch(r"CON|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³]", name.split(".", 1)[0].rstrip().upper())):
        raise HTTPException(400, "文件名无效，请重命名后再上传")
    if Path(name).suffix.lower() not in TYPES:
        raise HTTPException(415, "请选择 PDF、DOCX、XLSX、JPG 或 PNG 文件")
    return name


def content_matches(path, suffix):
    with path.open("rb") as data:
        head = data.read(8)
        if suffix == ".pdf":
            return head.startswith(b"%PDF-")
        if suffix in (".jpg", ".jpeg"):
            return head.startswith(b"\xff\xd8\xff")
        if suffix == ".png":
            return head == b"\x89PNG\r\n\x1a\n"
        try:
            part = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
            with ZipFile(data) as archive:
                return "[Content_Types].xml" in archive.NameToInfo and part in archive.NameToInfo
        except BadZipFile:
            return False


@router.post("/api/knowledge/files", status_code=201)
async def upload_original(request: Request, name: str):
    name = checked_name(name)
    # Keep the existing catalog URL working when an operator supplies a missing original.
    name = next((known for known in SOURCES if known.casefold() == name.casefold()), name)
    folder = upload_folder()
    target = folder / name
    conflict = "已有同名原件。可直接打开已有文件，或重命名后再上传。"
    if target.exists() or (name in SOURCES and bound_file(name).is_file()):
        raise HTTPException(409, conflict)
    tmp = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as output:
            tmp = Path(output.name)
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "单个文件不能超过 100 MB")
                output.write(chunk)
        if not size or not content_matches(tmp, target.suffix.lower()):
            raise HTTPException(422, "文件为空或内容与扩展名不符")
        # Publish only the complete file, without replacing a concurrent upload.
        os.link(tmp, target)
    except FileExistsError:
        raise HTTPException(409, conflict) from None
    except OSError:
        raise HTTPException(507, "本机保存失败，请检查存储空间和目录写入权限") from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return {"name": name}


def serve(path):
    if not path.is_file():
        raise HTTPException(404, "本机尚未配置此文件")
    suffix = path.suffix.lower()
    return FileResponse(path, media_type=TYPES[suffix], filename=path.name,
                        content_disposition_type="attachment" if suffix in (".docx", ".xlsx") else "inline",
                        headers={"X-Content-Type-Options": "nosniff"})


@router.get("/source/upload/{name}")
def uploaded_source(name: str):
    path = upload_folder() / checked_name(name)
    if path.is_symlink():
        raise HTTPException(404, "找不到此原件")
    return serve(path)


@router.get("/source/{key}")
def bound_source(key: str):
    name = next((n for n, ident in SOURCES.items() if ident == key), None)
    if not name:
        raise HTTPException(404, "找不到此原件")
    path = bound_file(name)
    return serve(path) if path.is_file() else uploaded_source(name)

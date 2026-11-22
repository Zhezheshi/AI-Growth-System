from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query


router = APIRouter()
BASE = "https://www.hino.co.id/service/tokosukucadang"


def extract_stores(html, url, page, date):
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        columns = [cell.get_text(strip=True).casefold() for cell in table.select("thead th")]
        if columns in (["toko", "provinsi", "city", "alamat", "telepon"],
                       ["toko", "provinsi", "kota", "alamat", "telepon"]):
            break
    else:
        raise ValueError("上游名录的表头已变化，请打开来源页核对")
    items = []
    for row in table.select("tbody tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
        if len(cells) != 5 or not cells[0]:
            continue
        company, province, city, address, _phone = cells
        items.append({
            "company": company, "country": "印度尼西亚", "role": "零部件经销网点",
            "products": "Hino 商用车配件，制动片品类待核",
            "evidence": " · ".join(filter(None, (company, province, city, address))),
            "summary": "Hino 印尼官方配件网点名录收录该门店。具体在售制动片、维修业务及直接采购权限待销售核实。",
            "source_url": url, "contact_url": url, "website": "", "checked_at": date,
            "next_action": "请从该名录行核对当前门店入口，并询问是否经营商用车制动片及能否自行选厂。",
        })
    next_path = urlsplit(BASE).path + "/" + str(page * 10)
    has_next = any(urlsplit(a["href"]).path == next_path for a in soup.select("ul.pagination a[href]"))
    return {"items": items, "page": page, "source_url": url, "checked_at": date, "has_next": has_next}


@router.get("/api/sources/hino")
def hino_stores(city: str = Query(default="", max_length=80), page: int = Query(default=1, ge=1, le=500)):
    offset = (page - 1) * 10
    url = BASE + (f"/{offset}" if offset else "") + "?" + urlencode({"search": city.strip()})
    try:
        response = httpx.get(url, timeout=12)
        response.raise_for_status()
        return extract_stores(response.text, url, page, datetime.now(timezone.utc).date().isoformat())
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "官方名录暂时无法读取，请重试或打开 Hino 名录核对") from exc

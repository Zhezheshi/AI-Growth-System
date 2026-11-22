from decimal import Decimal, localcontext

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .knowledge import normalize, proof

Cost = Annotated[Decimal, Field(ge=0, max_digits=13, decimal_places=4, allow_inf_nan=False)]
PositiveCost = Annotated[Decimal, Field(gt=0, max_digits=13, decimal_places=4, allow_inf_nan=False)]


class CostInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Attachment(CostInput):
    name: str = Field(min_length=1, max_length=80)
    material: str = Field(min_length=1, max_length=80)
    units_per_set: int = Field(gt=0, le=10000)
    cost_per_piece: Cost


class CustomQuote(CostInput):
    product: str = Field(min_length=1, max_length=180)
    axle: str = Field(default="", max_length=180)
    position: Literal["", "front", "rear", "trailer"] = ""
    material: str = Field(min_length=1, max_length=80)
    thickness: str = Field(min_length=1, max_length=100)
    market: str = Field(min_length=1, max_length=80)
    standard: str = Field(min_length=1, max_length=120)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    incoterm: Literal["EXW", "FCA", "FOB", "CFR", "CIF", "DAP", "DDP"]
    delivery_point: str = Field(min_length=1, max_length=160)
    qty: int = Field(gt=0, le=100000000)
    kg_per_set: PositiveCost
    material_cost_per_kg: PositiveCost
    includes_attachments: bool
    attachments: list[Attachment] = Field(default_factory=list, max_length=30)
    other_cost_per_set: Cost
    trade_cost_total: Cost
    proposed_per_set: PositiveCost | None = None
    source: str = Field(min_length=1, max_length=300)


def calc_quote(conn, form):
    if form.includes_attachments != bool(form.attachments):
        raise ValueError("含附件时请逐项填名称、材质、数量和单价。不含附件时请移除附件行。")
    key = normalize(form.product)
    product = conn.execute("SELECT code,name,demo FROM catalog WHERE key=?", (key,)).fetchone()
    if product is None:
        raise ValueError("请先从目录选择一个已收录型号")
    records = proof(conn, key)
    fit = next((r for r in records["fit"] if r["verified"] and form.position
                and r["position"] == form.position and form.axle and
                normalize(r["axle"]) == normalize(form.axle) and
                normalize(r["material"]) == normalize(form.material)), None)
    reports = [r for r in records["reports"] if r["verified"] and normalize(r["material"]) == normalize(form.material)]
    failed = any(r["result"] == "fail" for r in reports)
    report = next((r for r in reports if r["result"] == "pass"), None)
    missing = ["请技术复核本单订制厚度及市场标准",
               "请业务负责人复核成本来源、贸易责任和拟议价格"]
    if not fit:
        missing.append("补齐当前车桥、位置和配方的技术复核")
    if failed:
        missing.append("同配方有未解除的试验失败记录")
    elif not report:
        missing.append("补齐同型号、同配方的合格试验记录")

    with localcontext() as decimal_context:
        decimal_context.prec = 40
        material = form.kg_per_set * form.material_cost_per_kg
        parts = [{"name": a.name, "material": a.material, "units_per_set": a.units_per_set,
                  "cost_per_set": str(a.units_per_set * a.cost_per_piece)} for a in form.attachments]
        accessory = sum((Decimal(a["cost_per_set"]) for a in parts), Decimal(0))
        subtotal = (material + accessory + form.other_cost_per_set) * form.qty + form.trade_cost_total
        proposal = form.proposed_per_set * form.qty if form.proposed_per_set is not None else None
        difference = str(proposal - subtotal) if proposal is not None else None
    customer_reply = (
        f"We have received your enquiry for {form.qty} sets, catalog reference {product['code']}, "
        f"on {form.incoterm} terms for {form.delivery_point}. "
        "We will confirm the specifications and final selling price after review."
    )
    if product["demo"]:
        customer_reply = "DEMO ONLY · " + customer_reply
    return {
        "customer_reply": customer_reply,
        "product": dict(product), "currency": form.currency, "qty": form.qty,
        "terms": f"{form.incoterm} {form.delivery_point}",
        "material_per_set": str(material), "accessories_per_set": str(accessory),
        "other_per_set": str(form.other_cost_per_set), "trade_cost_total": str(form.trade_cost_total),
        "total_cost": str(subtotal), "proposed_total": str(proposal) if proposal is not None else None,
        "difference": difference,
        "attachments": parts, "source": form.source, "fit": fit, "report": report if not failed else None,
        "reviews": missing,
    }

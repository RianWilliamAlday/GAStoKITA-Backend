from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import io
from db.database import get_session, Sale, OilSale, Fuel, Pump, OilProduct

router = APIRouter(prefix="/api/receipts", tags=["receipts"])

def build_pdf_bytes(
    total_amount: float,
    amount_paid: float,
    change_given: float,
    sale_type: str = "fuel",
    items: list = None,
    fuel_name: str = "Fuel",
    pump_name: str = "",
    oil_name: str = "Oil",
    single_qty: float = 0.0,
    single_price: float = 0.0,
):
    buf = io.BytesIO()
    item_count = len(items) if items else 1
    w = 80 * mm
    h = max(180 * mm, (120 + item_count * 8) * mm)
    c = canvas.Canvas(buf, pagesize=(w, h))
    y = h - 10 * mm

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(w / 2, y, "GASTOKITA")
    y -= 7 * mm

    c.setFont("Helvetica", 8)
    c.drawCentredString(w / 2, y, "U-Fuel Receipt")
    y -= 5 * mm
    c.drawCentredString(w / 2, y, "------------------------------")
    y -= 5 * mm

    c.setFont("Helvetica", 8)

    if items:
        for item in items:
            c.drawString(5 * mm, y, str(item))
            y -= 5 * mm
    elif sale_type == "fuel":
        clean_fuel = (
            fuel_name.split()[0]
            if fuel_name
            else (pump_name.split()[0] if pump_name else "Fuel")
        )
        line1 = f"{clean_fuel} {single_qty:.3f}L x P{single_price:.2f}"
        c.drawString(5 * mm, y, line1)
        y -= 5 * mm
    else:
        line1 = f"{oil_name} {int(single_qty)} x P{single_price:.2f}"
        c.drawString(5 * mm, y, line1)
        y -= 5 * mm

    c.drawString(5 * mm, y, f"Due:  P{total_amount:.2f}")
    y -= 5 * mm
    c.drawString(5 * mm, y, f"Paid: P{amount_paid:.2f}")
    y -= 8 * mm

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(w / 2, y, f"P {change_given:.2f}")
    y -= 6 * mm

    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(w / 2, y, "CHANGE")
    y -= 8 * mm

    c.setFont("Helvetica", 8)
    c.drawCentredString(w / 2, y, "Thank you!")
    y -= 5 * mm
    c.drawCentredString(w / 2, y, "------------------------------")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


@router.get("/{sale_type}/{sale_id}/pdf")
def get_receipt_pdf(
    sale_type: str, sale_id: str, session: Session = Depends(get_session)
):
    if sale_type == "multi":
        tx_ref = str(sale_id)

        if sale_id.isdigit():
            fuel_s = session.exec(
                select(Sale).where(Sale.id == int(sale_id))
            ).first()
            oil_s = session.exec(
                select(OilSale).where(OilSale.id == int(sale_id))
            ).first()
            if fuel_s and getattr(fuel_s, "transaction_ref", None):
                tx_ref = fuel_s.transaction_ref
            elif oil_s and getattr(oil_s, "transaction_ref", None):
                tx_ref = oil_s.transaction_ref

        fuel_sales = session.exec(
            select(
                Sale, Fuel.name.label("fuel_name"), Pump.name.label("pump_name")
            )
            .outerjoin(Fuel, Sale.fuel_id == Fuel.id)
            .outerjoin(Pump, Sale.pump_id == Pump.id)
            .where(Sale.transaction_ref == tx_ref)
        ).all()

        oil_sales = session.exec(
            select(OilSale, OilProduct)
            .outerjoin(OilProduct, OilSale.oil_product_id == OilProduct.id)
            .where(OilSale.transaction_ref == tx_ref)
        ).all()

        if not fuel_sales and not oil_sales:
            raise HTTPException(404, "Multi sale transaction not found")

        items = []
        total_due = 0.0
        amount_paid = 0.0
        change_given = 0.0

        for s_obj, f_name, p_name in fuel_sales:
            clean_fuel = (
                f_name.split()[0]
                if f_name
                else (p_name.split()[0] if p_name else "Fuel")
            )
            liters = float(s_obj.liters_sold)
            price = float(
                getattr(s_obj, "price_per_liter", 0)
                or (s_obj.total_amount / liters if liters > 0 else 0)
            )
            items.append(f"{clean_fuel} {liters:.3f}L x P{price:.2f}")

            total_due += s_obj.total_amount
            amount_paid = max(amount_paid, s_obj.amount_paid)
            change_given = max(change_given, s_obj.change_given)

        for o_obj, o_prod in oil_sales:
            p_label = (
                f"{o_prod.brand} {o_prod.name}".strip() if o_prod else "Oil"
            )
            qty = int(o_obj.quantity)
            price = float(
                getattr(o_obj, "price_per_unit", 0)
                or (o_obj.total_amount / qty if qty > 0 else 0)
            )
            items.append(f"{p_label} {qty} x P{price:.2f}")

            total_due += o_obj.total_amount
            amount_paid = max(amount_paid, o_obj.amount_paid)
            change_given = max(change_given, o_obj.change_given)

        pdf = build_pdf_bytes(
            total_amount=round(total_due, 2),
            amount_paid=amount_paid,
            change_given=change_given,
            sale_type="multi",
            items=items,
        )

    elif sale_type == "fuel":
        sale_int_id = int(sale_id) if sale_id.isdigit() else 0
        result = session.exec(
            select(
                Sale, Fuel.name.label("fuel_name"), Pump.name.label("pump_name")
            )
            .outerjoin(Fuel, Sale.fuel_id == Fuel.id)
            .outerjoin(Pump, Sale.pump_id == Pump.id)
            .where(Sale.id == sale_int_id)
        ).first()

        if not result:
            raise HTTPException(404, "Fuel sale not found")

        sale_obj, fuel_name, pump_name = result

        pdf = build_pdf_bytes(
            total_amount=sale_obj.total_amount,
            amount_paid=sale_obj.amount_paid,
            change_given=sale_obj.change_given,
            sale_type="fuel",
            fuel_name=fuel_name,
            pump_name=pump_name,
            single_qty=float(sale_obj.liters_sold),
            single_price=float(sale_obj.price_per_liter),
        )

    else:
        sale_int_id = int(sale_id) if sale_id.isdigit() else 0
        result = session.exec(
            select(OilSale, OilProduct)
            .outerjoin(OilProduct, OilSale.oil_product_id == OilProduct.id)
            .where(OilSale.id == sale_int_id)
        ).first()

        if not result:
            raise HTTPException(404, "Oil sale not found")

        sale_obj, oil_product = result
        product_name = (
            f"{oil_product.brand} {oil_product.name}".strip()
            if oil_product
            else "Oil"
        )

        pdf = build_pdf_bytes(
            total_amount=sale_obj.total_amount,
            amount_paid=sale_obj.amount_paid,
            change_given=sale_obj.change_given,
            sale_type="oil",
            oil_name=product_name,
            single_qty=float(sale_obj.quantity),
            single_price=float(sale_obj.price_per_unit),
        )

    return StreamingResponse(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=receipt_{sale_type}_{sale_id}.pdf"
        },
    )
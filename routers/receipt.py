from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from escpos.printer import Win32Raw
import io
from ..db.database import get_session, Sale, OilSale

router = APIRouter(prefix="/api/receipts", tags=["receipts"])

def build_pdf_bytes(sale, sale_type):
    buf = io.BytesIO()
    w, h = 80*mm, 200*mm
    c = canvas.Canvas(buf, pagesize=(w,h))
    y = h - 10*mm
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(w/2, y, "GASTOKITA")
    y -= 7*mm

    def get(obj, *keys, default=""):
        for k in keys:
            if isinstance(obj, dict):
                if k in obj and obj[k] not in (None, ""):
                    return obj[k]
            elif hasattr(obj, k):
                v = getattr(obj, k)
                if v not in (None, ""):
                    return v
        return default
    
    c.setFont("Helvetica", 8)
    c.drawCentredString(w/2, y, "U-Fuel Receipt")
    y -= 5*mm
    c.drawCentredString(w/2, y, "------------------------------")
    y -= 7*mm
    c.setFont("Helvetica", 8)

    if sale_type == "fuel":
        fuel_name = get(sale, 'fuel_name', 'fuel_type', 'name', default='Fuel')
        pump = get(sale, 'pump_id', 'pump_number', 'pump_name', default='')
        pump_txt = f" {pump}" if pump else ""
        liters = float(get(sale, 'liters_sold', 'liters', 'quantity', default=0))
        price = float(get(sale, 'price_per_liter', 'price_per_unit', 'price', default=0))
        line1 = f"{fuel_name}{pump_txt} {liters:.3f}L x P{price:.2f}"
    else:
        oil_name = get(sale, 'product_name', 'brand', default='Oil')
        qty = get(sale, 'quantity', 'liters_sold', default=1)
        price = float(get(sale, 'price_per_unit', 'price', default=0))
        line1 = f"{oil_name} {qty} x P{price:.2f}"

    c.drawString(5*mm, y, line1)
    y -= 5*mm
    c.drawString(5*mm, y, f"Due:  P{sale.total_amount:.2f}")
    y -= 5*mm
    c.drawString(5*mm, y, f"Paid: P{sale.amount_paid:.2f}")
    y -= 8*mm
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(w/2, y, f"P {sale.change_given:.2f}")
    y -= 6*mm
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(w/2, y, "CHANGE")
    y -= 8*mm
    c.setFont("Helvetica", 8)
    c.drawCentredString(w/2, y, "Thank you!")
    y -= 5*mm
    c.drawCentredString(w/2, y, "------------------------------")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf

def check_printer(name="XP-58H"):
    try:
        import win32com.client
        wmi = win32com.client.GetObject("winmgmts:")
        for p in wmi.ExecQuery(f"Select * from Win32_Printer where Name='{name}'"):
            print(f"WMI: WorkOffline={p.WorkOffline} Status={p.PrinterStatus} State={p.PrinterState}")
            return not p.WorkOffline
        return False
    except Exception as e:
        print(f"WMI check fail, will try to print anyway: {e}")
        return True

@router.get("/{sale_type}/{sale_id}/pdf")
def get_receipt_pdf(sale_type: str, sale_id: int, session: Session = Depends(get_session)):
    if sale_type == "fuel":
        sale = session.get(Sale, sale_id)
    else:
        sale = session.get(OilSale, sale_id)
    if not sale:
        raise HTTPException(404, "Sale not found")
    pdf = build_pdf_bytes(sale, sale_type)
    return StreamingResponse(pdf, media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=receipt_{sale_type}_{sale_id}.pdf"})

@router.post("/print")
def print_receipt_direct(payload: dict):
    """
    payload = {
      "label": "Diesel",
      "details": "10L",
      "total": 650.00,
      "paid": 1000.00,
      "change": 350.00
    }
    """
    if not check_printer("XP-58H"):
        raise HTTPException(status_code=500, detail="Printer offline / USB unplugged")

    try:
        p = Win32Raw("XP-58H")
        
        p.set(align='center', bold=True, width=2, height=2)
        p.text("GASTOKITA\n")
        p.set(align='center', bold=False, width=1, height=1)
        p.text("U-Fuel Receipt\n")
        p.text("------------------------------\n")
        p.set(align='left')
        p.text(f"{payload.get('label','')} {payload.get('details','')}\n")
        p.text(f"Due:  P{payload.get('total',0):.2f}\n")
        p.text(f"Paid: P{payload.get('paid',0):.2f}\n")
        p.set(align='center', bold=True, width=2, height=2)
        p.text(f"\nP {payload.get('change',0):.2f}\n")
        p.set(align='center', bold=True, width=1, height=1)
        p.text("CHANGE\n\n")
        p.set(align='center')
        p.text("Thank you!\n")
        p.text("------------------------------\n")
        p.cut()
        p.close()
        
        return {"status": "printed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500
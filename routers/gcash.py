from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select, func
from datetime import datetime
from typing import Optional, Literal, List
from pydantic import BaseModel
from db.database import get_session, Sale, Fuel, Pump, FuelBatch, FuelSaleBatch, OilProduct, OilSale, Attendant, User
from services.gcashService import create_gcash_checkout, retrieve_checkout
from services.auth import get_current_user

router = APIRouter(prefix="/api/payments", tags=["GCash Payments"])
ProductType = Literal["fuel", "oil", "multi"]

class MultiFuelItem(BaseModel):
    pump_id: Optional[int] = None
    fuel_id: Optional[int] = None
    liters_sold: float

class MultiOilItem(BaseModel):
    oil_product_id: int
    quantity: int

class GCashDialogRequest(BaseModel):
    product_type: ProductType = "fuel"
    pump_id: Optional[int] = None
    fuel_id: Optional[int] = None
    liters_sold: Optional[float] = None
    oil_product_id: Optional[int] = None
    quantity: Optional[int] = None
    attendant_name: str
    fuel_items: Optional[List[MultiFuelItem]] = []
    oil_items: Optional[List[MultiOilItem]] = []

def get_fifo_batches(session: Session, fuel_id: int):
    return session.exec(select(FuelBatch).where(FuelBatch.fuel_id == fuel_id, FuelBatch.liters_remaining > 0).order_by(FuelBatch.restocked_at.asc(), FuelBatch.id.asc())).all()

@router.post("/gcash/dialog-checkout")
def create_dialog_checkout(
    data: GCashDialogRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    clean = data.attendant_name.strip()
    att = session.exec(select(Attendant).where(func.lower(Attendant.name) == clean.lower(), Attendant.is_active == True)).first()
    if not att:
        cnt = session.exec(select(func.count()).select_from(Attendant)).one()
        if cnt != 0:
            active_names = session.exec(select(Attendant.name).where(Attendant.is_active == True)).all()
            raise HTTPException(400, f"Invalid attendant '{data.attendant_name}'. Must be one of {active_names}")

    total_amount = 0.0
    description = ""
    sale = None
    oil_sale = None

    if data.product_type == "multi":
        if not data.fuel_items and not data.oil_items:
            raise HTTPException(400, "Multi checkout requires fuel_items or oil_items")

        tx_ref = f"M-{datetime.now().strftime('%y%m%d%H%M%S')}"
        pending_sales = []
        pending_oil_sales = []

        for fi in (data.fuel_items or []):
            if not fi.liters_sold or fi.liters_sold <= 0:
                raise HTTPException(400, "Liters must be > 0")
            fuel = None
            pump = None
            if fi.pump_id:
                pump = session.get(Pump, fi.pump_id)
                if not pump: raise HTTPException(404, f"Pump {fi.pump_id} not found")
                fuel = session.get(Fuel, pump.fuel_type_id)
            elif fi.fuel_id:
                fuel = session.get(Fuel, fi.fuel_id)
            if not fuel: raise HTTPException(404, "Fuel not found")

            batches = get_fifo_batches(session, fuel.id)
            total_avail = sum(b.liters_remaining for b in batches)
            if total_avail < fi.liters_sold:
                raise HTTPException(400, f"Not enough stock for {fuel.name}. Only {total_avail:.2f}L, requested {fi.liters_sold}L")

            item_amount = 0.0
            liters_needed = fi.liters_sold
            for b in batches:
                if liters_needed <= 0: break
                take = min(b.liters_remaining, liters_needed)
                item_amount += take * b.selling_price
                liters_needed -= take
            item_amount = round(item_amount, 2)
            total_amount += item_amount

            s = Sale(
                fuel_id=fuel.id,
                pump_id=pump.id if pump else None,
                attendant_name=data.attendant_name,
                recorded_by=current_user.id,
                liters_sold=fi.liters_sold,
                price_per_liter=round(item_amount / fi.liters_sold, 2),
                total_amount=item_amount,
                amount_paid=0,
                change_given=0,
                receipt_no="PENDING",
                transaction_ref=tx_ref,
                payment_method="gcash",
                payment_status="pending"
            )
            session.add(s)
            pending_sales.append(s)

        for oi in (data.oil_items or []):
            if not oi.oil_product_id or not oi.quantity or oi.quantity <= 0:
                raise HTTPException(400, "Need valid oil_product_id and quantity > 0")
            oil = session.get(OilProduct, oi.oil_product_id)
            if not oil: raise HTTPException(404, f"Oil {oi.oil_product_id} not found")
            if oil.stock < oi.quantity:
                raise HTTPException(400, f"Not enough stock for {oil.brand} {oil.name}. Only {oil.stock} pcs")

            item_amount = round(oil.price * oi.quantity, 2)
            total_amount += item_amount

            os_item = OilSale(
                oil_product_id=oil.id,
                quantity=oi.quantity,
                price_per_unit=oil.price,
                total_amount=item_amount,
                amount_paid=0,
                change_given=0,
                receipt_no="PENDING",
                transaction_ref=tx_ref,
                payment_method="gcash",
                payment_status="pending",
                sold_by=current_user.id,
                attendant_name=data.attendant_name
            )
            session.add(os_item)
            pending_oil_sales.append(os_item)

        total_amount = round(total_amount, 2)
        session.flush()

        item_cnt = len(pending_sales) + len(pending_oil_sales)
        description = f"Multi Sale ({item_cnt} items)"

        try:
            checkout = create_gcash_checkout(
                amount_php=total_amount,
                description=description,
                reference_id=tx_ref,
                metadata={
                    "product_type": "multi",
                    "transaction_ref": tx_ref,
                    "attendant": data.attendant_name
                }
            )
        except Exception as e:
            session.rollback()
            raise HTTPException(500, f"PayMongo error: {str(e)}")

        for s in pending_sales:
            s.paymongo_checkout_id = checkout['checkout_id']
            s.receipt_no = f"F-{s.sold_at.strftime('%y%m%d')}-{s.id:06d}-GCASH-PENDING"
            session.add(s)

        for os_item in pending_oil_sales:
            os_item.paymongo_checkout_id = checkout['checkout_id']
            os_item.receipt_no = f"O-{os_item.sold_at.strftime('%y%m%d')}-{os_item.id:06d}-GCASH-PENDING"
            session.add(os_item)

        session.commit()

        return {
            "transaction_ref": tx_ref,
            "sale_id": tx_ref,
            "product_type": "multi",
            "checkout_id": checkout['checkout_id'],
            "checkout_url": checkout['checkout_url'],
            "total_amount": total_amount,
            "status": "pending"
        }

    elif data.product_type == "fuel":
        if not data.liters_sold or data.liters_sold <= 0:
            raise HTTPException(400, "Liters must be > 0")
        fuel = None
        pump = None
        if data.pump_id:
            pump = session.get(Pump, data.pump_id)
            if not pump: raise HTTPException(404, "Pump not found")
            fuel = session.get(Fuel, pump.fuel_type_id)
        elif data.fuel_id:
            fuel = session.get(Fuel, data.fuel_id)
        else:
            raise HTTPException(400, "Need pump_id or fuel_id")
        if not fuel: raise HTTPException(404, "Fuel not found")

        batches = get_fifo_batches(session, fuel.id)
        total_avail = sum(b.liters_remaining for b in batches)
        if total_avail < data.liters_sold:
            raise HTTPException(400, f"Not enough stock. Only {total_avail:.2f}L, requested {data.liters_sold}L")

        liters_needed = data.liters_sold
        for b in batches:
            if liters_needed <= 0: break
            take = min(b.liters_remaining, liters_needed)
            total_amount += take * b.selling_price
            liters_needed -= take
        total_amount = round(total_amount, 2)
        description = f"{fuel.name} {data.liters_sold}L"

        sale = Sale(
            fuel_id=fuel.id,
            pump_id=pump.id if pump else None,
            attendant_name=data.attendant_name,
            recorded_by=current_user.id,
            liters_sold=data.liters_sold,
            price_per_liter=round(total_amount / data.liters_sold, 2),
            total_amount=total_amount,
            amount_paid=0,
            change_given=0,
            receipt_no="PENDING",
            payment_method="gcash",
            payment_status="pending"
        )
        session.add(sale)
        session.flush()

    else:
        if not data.oil_product_id or not data.quantity:
            raise HTTPException(400, "Need oil_product_id and quantity")
        oil = session.get(OilProduct, data.oil_product_id)
        if not oil: raise HTTPException(404, "Oil not found")
        if oil.stock < data.quantity:
            raise HTTPException(400, f"Not enough oil stock. Only {oil.stock} pcs")
        total_amount = round(oil.price * data.quantity, 2)
        description = f"{oil.brand} {oil.name} x{data.quantity}"

        oil_sale = OilSale(
            oil_product_id=oil.id,
            quantity=data.quantity,
            price_per_unit=oil.price,
            total_amount=total_amount,
            amount_paid=0,
            change_given=0,
            receipt_no="PENDING",
            payment_method="gcash",
            payment_status="pending",
            sold_by=current_user.id,
            attendant_name=data.attendant_name
        )
        session.add(oil_sale)
        session.flush()

    ref_id = f"{data.product_type}-{(sale.id if sale else oil_sale.id)}-{datetime.now().strftime('%H%M%S')}"
    try:
        checkout = create_gcash_checkout(
            amount_php=total_amount,
            description=description,
            reference_id=ref_id,
            metadata={
                "product_type": data.product_type,
                "sale_id": str(sale.id if sale else ""),
                "oil_sale_id": str(oil_sale.id if oil_sale else ""),
                "attendant": data.attendant_name
            }
        )
    except Exception as e:
        session.rollback()
        raise HTTPException(500, f"PayMongo error: {str(e)}")

    if sale:
        sale.paymongo_checkout_id = checkout['checkout_id']
        sale.receipt_no = f"F-{sale.sold_at.strftime('%y%m%d')}-{sale.id:06d}-GCASH-PENDING"
        session.add(sale)
    if oil_sale:
        oil_sale.paymongo_checkout_id = checkout['checkout_id']
        oil_sale.receipt_no = f"O-{oil_sale.sold_at.strftime('%y%m%d')}-{oil_sale.id:06d}-GCASH-PENDING"
        session.add(oil_sale)

    session.commit()
    if sale: session.refresh(sale)
    if oil_sale: session.refresh(oil_sale)

    return {
        "sale_id": sale.id if sale else None,
        "oil_sale_id": oil_sale.id if oil_sale else None,
        "product_type": data.product_type,
        "checkout_id": checkout['checkout_id'],
        "checkout_url": checkout['checkout_url'],
        "total_amount": total_amount,
        "status": "pending"
    }

@router.get("/gcash/status/{checkout_id}")
def check_status(checkout_id: str, session: Session = Depends(get_session)):
    try:
        info = retrieve_checkout(checkout_id)
    except Exception as e:
        raise HTTPException(500, str(e))

    sales = session.exec(select(Sale).where(Sale.paymongo_checkout_id == checkout_id)).all()
    oil_sales = session.exec(select(OilSale).where(OilSale.paymongo_checkout_id == checkout_id)).all()

    if not sales and not oil_sales:
        sales = session.exec(select(Sale).where(Sale.transaction_ref == checkout_id)).all()
        oil_sales = session.exec(select(OilSale).where(OilSale.transaction_ref == checkout_id)).all()

    product_type = "fuel"
    if (sales and oil_sales) or (len(sales) > 1) or (len(oil_sales) > 1):
        product_type = "multi"
    elif oil_sales:
        product_type = "oil"

    tx_ref = None
    if sales:
        tx_ref = sales[0].transaction_ref or str(sales[0].id)
    elif oil_sales:
        tx_ref = oil_sales[0].transaction_ref or str(oil_sales[0].id)

    status = sales[0].payment_status if sales else (oil_sales[0].payment_status if oil_sales else 'not_found')

    return {
        "checkout_id": checkout_id,
        "is_paid": info["is_paid"],
        "sale_id": tx_ref or (sales[0].id if sales else (oil_sales[0].id if oil_sales else None)),
        "transaction_ref": tx_ref,
        "product_type": product_type,
        "payment_status": status,
        "paymongo": info["payments"]
    }

@router.post("/gcash/manual-confirm/{identifier}")
def manual_confirm(identifier: str, product_type: str = "fuel", gcash_ref: str = None, session: Session = Depends(get_session)):
    if product_type == "multi" or not identifier.isdigit():
        fuel_sales = session.exec(select(Sale).where((Sale.transaction_ref == identifier) | (Sale.paymongo_checkout_id == identifier))).all()
        oil_sales = session.exec(select(OilSale).where((OilSale.transaction_ref == identifier) | (OilSale.paymongo_checkout_id == identifier))).all()

        if not fuel_sales and not oil_sales and identifier.isdigit():
            fuel_sales = session.exec(select(Sale).where(Sale.id == int(identifier))).all()
            oil_sales = session.exec(select(OilSale).where(OilSale.id == int(identifier))).all()

        for sale in fuel_sales:
            if sale.payment_status != 'paid':
                fuel = session.get(Fuel, sale.fuel_id)
                if fuel:
                    batches = get_fifo_batches(session, fuel.id)
                    liters_needed = sale.liters_sold
                    for batch in batches:
                        if liters_needed <= 0: break
                        take = min(batch.liters_remaining, liters_needed)
                        batch.liters_remaining -= take
                        fsb = FuelSaleBatch(sale_id=sale.id, batch_id=batch.id, liters_consumed=take, price_per_liter=batch.selling_price, total_amount=round(take*batch.selling_price,2))
                        session.add(fsb)
                        session.add(batch)
                        liters_needed -= take

                    remaining = get_fifo_batches(session, fuel.id)
                    fuel.actual_liters = sum(b.liters_remaining for b in remaining)
                    session.add(fuel)

                sale.payment_status = 'paid'
                sale.amount_paid = sale.total_amount
                if gcash_ref: sale.gcash_ref_no = gcash_ref
                sale.receipt_no = sale.receipt_no.replace("PENDING", "PAID")
                session.add(sale)

        for sale in oil_sales:
            if sale.payment_status != 'paid':
                oil = session.get(OilProduct, sale.oil_product_id)
                if oil:
                    oil.stock -= sale.quantity
                    session.add(oil)
                sale.payment_status = 'paid'
                sale.amount_paid = sale.total_amount
                if gcash_ref: sale.gcash_ref_no = gcash_ref
                sale.receipt_no = sale.receipt_no.replace("PENDING", "PAID")
                session.add(sale)

        session.commit()

        tx_ref = identifier
        if fuel_sales and fuel_sales[0].transaction_ref: tx_ref = fuel_sales[0].transaction_ref
        elif oil_sales and oil_sales[0].transaction_ref: tx_ref = oil_sales[0].transaction_ref

        fuel_sales_data = []
        for s in fuel_sales:
            f_obj = session.get(Fuel, s.fuel_id)
            fuel_sales_data.append({
                "fuel_name": f_obj.name if f_obj else "Fuel",
                "liters_sold": s.liters_sold,
                "unit_price": s.price_per_liter,
                "total_amount": s.total_amount
            })

        oil_sales_data = []
        for s in oil_sales:
            o_obj = session.get(OilProduct, s.oil_product_id)
            oil_sales_data.append({
                "product_name": f"{o_obj.brand} {o_obj.name}".strip() if o_obj else "Oil",
                "quantity": s.quantity,
                "unit_price": s.price_per_unit,
                "total_amount": s.total_amount
            })

        return {
            "message": "Multi sale confirmed PAID",
            "sale_id": tx_ref,
            "transaction_ref": tx_ref,
            "receipt_no": tx_ref,
            "fuel_sales": fuel_sales_data,
            "oil_sales": oil_sales_data
        }

    else:
        sale_int_id = int(identifier)
        if product_type == "fuel":
            sale = session.get(Sale, sale_int_id)
            if not sale: raise HTTPException(404, "Sale not found")
            if sale.payment_status == 'paid':
                return {"message": "Already paid", "sale_id": sale_int_id}

            fuel = session.get(Fuel, sale.fuel_id)
            batches = get_fifo_batches(session, fuel.id)
            liters_needed = sale.liters_sold
            for batch in batches:
                if liters_needed <= 0: break
                take = min(batch.liters_remaining, liters_needed)
                batch.liters_remaining -= take
                fsb = FuelSaleBatch(sale_id=sale.id, batch_id=batch.id, liters_consumed=take, price_per_liter=batch.selling_price, total_amount=round(take*batch.selling_price,2))
                session.add(fsb)
                session.add(batch)
                liters_needed -= take

            remaining = get_fifo_batches(session, fuel.id)
            fuel.actual_liters = sum(b.liters_remaining for b in remaining)
            session.add(fuel)
            sale.payment_status = 'paid'
            sale.amount_paid = sale.total_amount
            if gcash_ref: sale.gcash_ref_no = gcash_ref
            sale.receipt_no = sale.receipt_no.replace("PENDING", "PAID")
            session.add(sale)
            session.commit()
            return {"message": "Fuel sale confirmed PAID", "sale_id": sale_int_id, "receipt_no": sale.receipt_no}
        else:
            sale = session.get(OilSale, sale_int_id)
            if not sale: raise HTTPException(404, "Oil sale not found")
            oil = session.get(OilProduct, sale.oil_product_id)
            if oil:
                oil.stock -= sale.quantity
                session.add(oil)
            sale.payment_status = 'paid'
            sale.amount_paid = sale.total_amount
            sale.receipt_no = sale.receipt_no.replace("PENDING", "PAID")
            session.add(sale)
            session.commit()
            return {"message": "Oil sale confirmed PAID", "sale_id": sale_int_id, "receipt_no": sale.receipt_no}

@router.post("/webhooks/paymongo")
async def webhook(request: Request, session: Session = Depends(get_session)):
    payload = await request.json()
    data = payload.get("data", {})
    attr = data.get("attributes", {})
    event_type = attr.get("type")
    if event_type == "checkout_session.payment.paid":
        checkout_data = attr.get("data", {})
        checkout_id = checkout_data.get("id")
        sales = session.exec(select(Sale).where(Sale.paymongo_checkout_id == checkout_id)).all()
        oil_sales = session.exec(select(OilSale).where(OilSale.paymongo_checkout_id == checkout_id)).all()
        if len(sales) + len(oil_sales) > 1:
            return manual_confirm(checkout_id, "multi", session=session)
        elif sales:
            return manual_confirm(str(sales[0].id), "fuel", session=session)
        elif oil_sales:
            return manual_confirm(str(oil_sales[0].id), "oil", session=session)
    return {"status": "ignored", "type": event_type}
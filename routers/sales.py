from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select, and_, func, text
from db.database import get_session, Sale, Fuel, User, Pump, OilProduct, OilSale, FuelBatch, FuelSaleBatch, Attendant
from models.schemas import (
    SaleCreate, SaleResponse, SaleHistoryItem, SalesHistoryResponse,
    OilSaleCreate, OilSaleResponse, OilSaleHistoryItem, OilSalesHistoryResponse,
    UnifiedSaleItem, UnifiedHistoryResponse, ProductType,
    FifoSaleDetail, MultiSaleCreate, MultiSaleResponse, 
    MultiFuelItem, MultiOilItem
)
from services.auth import get_current_user
from datetime import datetime, date, time
from typing import List, Optional

router = APIRouter(prefix="/api/sales", tags=["Centralized Sales"])

def validate_attendant(session, name: str):
    from db.database import Attendant
    att = session.exec(select(Attendant).where(func.lower(Attendant.name) == name.lower(), Attendant.is_active == True)).first()
    if not att:
        cnt = session.exec(select(func.count()).select_from(Attendant)).one()
        if cnt == 0: return True
        raise HTTPException(400, f"Invalid attendant '{name}'. Add it via /api/attendants")

def get_fifo_batches(session: Session, fuel_id: int):
    return session.exec(select(FuelBatch).where(FuelBatch.fuel_id == fuel_id, FuelBatch.liters_remaining > 0).order_by(FuelBatch.restocked_at.asc(), FuelBatch.id.asc())).all()

def ensure_initial_batch(session: Session, fuel: Fuel):
    existing = session.exec(select(FuelBatch).where(FuelBatch.fuel_id == fuel.id)).first()
    if not existing and fuel.actual_liters > 0:
        batch = FuelBatch(
            fuel_id=fuel.id,
            liters_initial=fuel.actual_liters,
            liters_remaining=fuel.actual_liters,
            cost_per_liter=fuel.price * 0.8,
            selling_price=fuel.price,
            supplier="Initial Stock",
            restocked_by="system",
            restocked_at=fuel.last_restocked or datetime.now()
        )
        session.add(batch)
        session.commit()
        return batch
    return existing

def get_current_fifo_price(session: Session, fuel_id: int, fuel_fallback: Fuel = None):
    batches = get_fifo_batches(session, fuel_id)
    if batches:
        return batches[0].selling_price
    return fuel_fallback.price if fuel_fallback else 0.0

@router.post("/fuel", response_model=SaleResponse)
def create_fuel_sale(
    data: SaleCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    clean_name = data.attendant_name.strip()
    att = session.exec(
        select(Attendant).where(
            func.lower(Attendant.name) == clean_name.lower(),
            Attendant.is_active == True
        )
    ).first()
    if not att:
        cnt = session.exec(select(func.count()).select_from(Attendant)).one()
        if cnt!= 0:
            active_names = session.exec(select(Attendant.name).where(Attendant.is_active == True)).all()
            raise HTTPException(400, f"Invalid attendant '{data.attendant_name}'. Must be one of {active_names}")

    pump = session.get(Pump, data.pump_id)
    if not pump:
        raise HTTPException(404, "Pump not found")
    fuel = session.get(Fuel, pump.fuel_type_id)
    if not fuel:
        raise HTTPException(404, "Fuel type not found")

    ensure_initial_batch(session, fuel)
    batches = get_fifo_batches(session, fuel.id)
    total_available = sum(b.liters_remaining for b in batches)
    if total_available < data.liters_sold:
        raise HTTPException(400, f"Not enough stock (FIFO). Only {total_available:.2f}L available, requested {data.liters_sold}L")

    liters_needed = data.liters_sold
    consumptions = []
    total_amount = 0.0
    for batch in batches:
        if liters_needed <= 0: break
        take = min(batch.liters_remaining, liters_needed)
        total_amount += take * batch.selling_price
        consumptions.append((batch, take, batch.selling_price))
        liters_needed -= take
    if liters_needed > 0.001:
        raise HTTPException(400, "FIFO consumption failed")

    weighted_avg_price = total_amount / data.liters_sold if data.liters_sold > 0 else 0
    total_amount = round(total_amount, 2)
    is_gcash = data.payment_method.lower() == "gcash"

    if is_gcash:
        sale = Sale(
            fuel_id=fuel.id,
            pump_id=data.pump_id,
            attendant_name=clean_name,
            recorded_by=current_user.id,
            liters_sold=data.liters_sold,
            price_per_liter=round(weighted_avg_price, 2),
            total_amount=total_amount,
            amount_paid=0,
            change_given=0,
            receipt_no="PENDING",
            payment_method="gcash",
            payment_status="pending",
            paymongo_checkout_id=None
        )
    
        try:
            from services.gcashService import create_gcash_checkout
            checkout = create_gcash_checkout(
                amount_php=total_amount,
                description=f"{fuel.name} {data.liters_sold}L - {clean_name}",
                reference_id=f"fuel-{datetime.now().timestamp()}",
                metadata={"attendant": clean_name, "product_type": "fuel"}
            )
        
            sale.paymongo_checkout_id = checkout['checkout_id']
            session.add(sale)
            session.commit()
            session.refresh(sale)
            sale.receipt_no = f"F-{sale.sold_at.strftime('%y%m%d')}-{sale.id:06d}-GCASH-PENDING"
            session.add(sale)
            session.commit()

            return SaleResponse(
                id=sale.id,
                product_type="fuel",
                fuel_name=fuel.name,
                pump_name=pump.name,
                attendant_name=sale.attendant_name,
                recorded_by=current_user.name,
                liters_sold=sale.liters_sold,
                price_per_liter=sale.price_per_liter,
                total_amount=sale.total_amount,
                amount_paid=0,
                change_given=0,
                receipt_no=sale.receipt_no,
                payment_method="gcash",
                sold_at=sale.sold_at,
                fifo_breakdown=[],
                is_split_price=False,
                payment_status="pending",
                checkout_url=checkout['checkout_url'],
                checkout_id=checkout['checkout_id']
            )
        except Exception as e:
            session.rollback()
            raise HTTPException(500, f"PayMongo GCash failed: {str(e)}")

    if data.amount_paid < total_amount - 0.01:
        raise HTTPException(400, f"Amount paid P{data.amount_paid:.2f} is less than due P{total_amount:.2f}")
    change_given = round(data.amount_paid - total_amount, 2)

    for batch, take, price in consumptions:
        batch.liters_remaining -= take
        session.add(batch)

    sale = Sale(
        fuel_id=fuel.id,
        pump_id=data.pump_id,
        attendant_name=data.attendant_name,
        recorded_by=current_user.id,
        liters_sold=data.liters_sold,
        price_per_liter=round(weighted_avg_price, 2),
        total_amount=total_amount,
        amount_paid=round(data.amount_paid, 2),
        change_given=change_given,
        receipt_no="TMP",
        payment_method="cash",
        payment_status="paid"
    )
    session.add(sale)
    session.flush()
    sale.receipt_no = f"F-{sale.sold_at.strftime('%y%m%d')}-{sale.id:06d}"
    session.add(sale)

    fifo_details = []
    for batch, take, price in consumptions:
        fsb = FuelSaleBatch(
            sale_id=sale.id,
            batch_id=batch.id,
            liters_consumed=take,
            price_per_liter=price,
            total_amount=round(take * price, 2)
        )
        session.add(fsb)
        fifo_details.append(FifoSaleDetail(
            batch_id=batch.id,
            liters_consumed=take,
            price_per_liter=price,
            total_amount=round(take * price, 2),
            restocked_at=batch.restocked_at
        ))

    remaining_batches = get_fifo_batches(session, fuel.id)
    fuel.actual_liters = sum(b.liters_remaining for b in remaining_batches)
    fuel.price = remaining_batches[0].selling_price if remaining_batches else weighted_avg_price
    session.add(fuel)
    session.commit()
    session.refresh(sale)

    return SaleResponse(
        id=sale.id,
        product_type="fuel",
        fuel_name=fuel.name,
        pump_name=pump.name,
        attendant_name=sale.attendant_name,
        recorded_by=current_user.name,
        liters_sold=sale.liters_sold,
        price_per_liter=sale.price_per_liter,
        total_amount=sale.total_amount,
        amount_paid=sale.amount_paid,
        change_given=sale.change_given,
        receipt_no=sale.receipt_no,
        payment_method=sale.payment_method,
        sold_at=sale.sold_at,
        fifo_breakdown=fifo_details,
        is_split_price=len(fifo_details) > 1
    )

@router.post("/oil", response_model=OilSaleResponse)
def create_oil_sale(
    data: OilSaleCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    oil = session.get(OilProduct, data.oil_id)
    if not oil:
        raise HTTPException(404, "Oil product not found")
    if data.quantity <= 0:
        raise HTTPException(400, "Quantity must be positive")
    if oil.stock < data.quantity:
        raise HTTPException(400, f"Insufficient stock. Only {oil.stock} left")

    attendant = data.attendant_name or current_user.name
    total = round(data.quantity * oil.price, 2)
    is_gcash = data.payment_method.lower() == "gcash"

    if is_gcash:
        sale = OilSale(
            oil_product_id=oil.id,
            quantity=data.quantity,
            price_per_unit=oil.price,
            total_amount=total,
            amount_paid=0,
            change_given=0,
            receipt_no="PENDING",
            payment_method="gcash",
            payment_status="pending",
            paymongo_checkout_id=None,
            attendant_name=attendant,
            sold_by=current_user.id
        )
        session.add(sale)
        session.flush()
        sale.receipt_no = f"O-{sale.sold_at.strftime('%y%m%d')}-{sale.id:06d}-GCASH-PENDING"
        session.add(sale)

        try:
            from services.gcashService import create_gcash_checkout
            checkout = create_gcash_checkout(
                amount_php=total,
                description=f"{oil.brand} {oil.name} x{data.quantity}",
                reference_id=f"oil-{sale.id}",
                metadata={"oil_sale_id": str(sale.id), "attendant": attendant, "product_type": "oil"}
            )
            sale.paymongo_checkout_id = checkout['checkout_id']
            session.add(sale)
            session.commit()
            session.refresh(sale)

            return OilSaleResponse(
                id=sale.id,
                product_type="oil",
                product_name=f"{oil.brand} {oil.name}",
                brand=oil.brand,
                quantity=sale.quantity,
                price_per_unit=sale.price_per_unit,
                total_amount=sale.total_amount,
                amount_paid=0,
                change_given=0,
                receipt_no=sale.receipt_no,
                payment_method="gcash",
                attendant_name=sale.attendant_name,
                sold_at=sale.sold_at,
                recorded_by=current_user.name,
                remaining_stock=oil.stock,
                payment_status="pending",
                checkout_url=checkout['checkout_url'],
                checkout_id=checkout['checkout_id']
            )
        except Exception as e:
            session.rollback()
            raise HTTPException(500, f"PayMongo GCash failed: {str(e)}")

    if data.amount_paid < total - 0.01:
        raise HTTPException(400, f"Amount paid P{data.amount_paid:.2f} is less than due P{total:.2f}")

    change_given = round(data.amount_paid - total, 2)
    oil.stock -= data.quantity
    session.add(oil)

    sale = OilSale(
        oil_product_id=oil.id,
        quantity=data.quantity,
        price_per_unit=oil.price,
        total_amount=total,
        amount_paid=round(data.amount_paid, 2),
        change_given=change_given,
        receipt_no="TMP",
        payment_method="cash",
        payment_status="paid",
        attendant_name=attendant,
        sold_by=current_user.id
    )
    session.add(sale)
    session.flush()
    sale.receipt_no = f"O-{sale.sold_at.strftime('%y%m%d')}-{sale.id:06d}"
    session.add(sale)
    session.commit()
    session.refresh(sale)

    return OilSaleResponse(
        id=sale.id,
        product_type="oil",
        product_name=f"{oil.brand} {oil.name}",
        brand=oil.brand,
        quantity=sale.quantity,
        price_per_unit=sale.price_per_unit,
        total_amount=sale.total_amount,
        amount_paid=sale.amount_paid,
        change_given=sale.change_given,
        receipt_no=sale.receipt_no,
        payment_method=sale.payment_method,
        attendant_name=sale.attendant_name,
        sold_at=sale.sold_at,
        recorded_by=current_user.name,
        remaining_stock=oil.stock
    )

@router.post("/multi", response_model=MultiSaleResponse)
def create_multi_sale(
    data: MultiSaleCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    if not data.fuel_items and not data.oil_items:
        raise HTTPException(400, "Transaction must contain at least one fuel or oil item.")

    clean_name = data.attendant_name.strip()
    validate_attendant(session, clean_name)

    trx_timestamp = datetime.now()
    trx_ref = f"M-{trx_timestamp.strftime('%y%m%d%H%M%S')}"

    fuel_preparations = []
    total_fuel_amount = 0.0

    for item in data.fuel_items:
        pump = session.get(Pump, item.pump_id)
        if not pump:
            raise HTTPException(404, f"Pump ID {item.pump_id} not found")
        
        fuel = session.get(Fuel, pump.fuel_type_id)
        if not fuel:
            raise HTTPException(404, f"Fuel for Pump ID {item.pump_id} not found")

        ensure_initial_batch(session, fuel)
        batches = get_fifo_batches(session, fuel.id)
        total_available = sum(b.liters_remaining for b in batches)

        if total_available < item.liters_sold:
            raise HTTPException(
                400, f"Insufficient stock for {fuel.name}. Available: {total_available:.2f}L"
            )

        liters_needed = item.liters_sold
        consumptions = []
        item_total = 0.0

        for batch in batches:
            if liters_needed <= 0:
                break
            take = min(batch.liters_remaining, liters_needed)
            item_total += take * batch.selling_price
            consumptions.append((batch, take, batch.selling_price))
            liters_needed -= take

        avg_price = item_total / item.liters_sold if item.liters_sold > 0 else 0.0
        item_total = round(item_total, 2)
        total_fuel_amount += item_total

        fuel_preparations.append({
            "pump": pump,
            "fuel": fuel,
            "liters_sold": item.liters_sold,
            "weighted_avg_price": avg_price,
            "item_total": item_total,
            "consumptions": consumptions
        })

    oil_preparations = []
    total_oil_amount = 0.0

    for item in data.oil_items:
        oil = session.get(OilProduct, item.oil_id)
        if not oil:
            raise HTTPException(404, f"Oil product ID {item.oil_id} not found")
        if oil.stock < item.quantity:
            raise HTTPException(
                400, f"Insufficient stock for '{oil.brand} {oil.name}'. Available: {oil.stock}"
            )

        item_total = round(item.quantity * oil.price, 2)
        total_oil_amount += item_total

        oil_preparations.append({
            "oil": oil,
            "quantity": item.quantity,
            "price_per_unit": oil.price,
            "item_total": item_total
        })

    grand_total = round(total_fuel_amount + total_oil_amount, 2)
    is_gcash = data.payment_method.lower() == "gcash"

    if not is_gcash and data.amount_paid < grand_total - 0.01:
        raise HTTPException(
            400, f"Amount paid ₱{data.amount_paid:.2f} is less than due ₱{grand_total:.2f}"
        )

    change_given = round(data.amount_paid - grand_total, 2) if not is_gcash else 0.0
    paid_amount = round(data.amount_paid, 2) if not is_gcash else 0.0
    fuel_responses = []
    oil_responses = []

    for prep in fuel_preparations:
        fuel = prep["fuel"]
        pump = prep["pump"]

        if not is_gcash:
            for batch, take, price in prep["consumptions"]:
                batch.liters_remaining -= take
                session.add(batch)

        sale = Sale(
            fuel_id=fuel.id,
            pump_id=pump.id,
            attendant_name=clean_name,
            recorded_by=current_user.id,
            liters_sold=prep["liters_sold"],
            price_per_liter=round(prep["weighted_avg_price"], 2),
            total_amount=prep["item_total"],
            amount_paid=paid_amount,
            change_given=change_given,
            receipt_no=trx_ref,
            transaction_ref=trx_ref,
            payment_method=data.payment_method.lower(),
            payment_status="pending" if is_gcash else "paid"
        )
        session.add(sale)
        session.flush()

        fifo_details = []
        if not is_gcash:
            for batch, take, price in prep["consumptions"]:
                fsb = FuelSaleBatch(
                    sale_id=sale.id,
                    batch_id=batch.id,
                    liters_consumed=take,
                    price_per_liter=price,
                    total_amount=round(take * price, 2)
                )
                session.add(fsb)
                fifo_details.append(FifoSaleDetail(
                    batch_id=batch.id,
                    liters_consumed=take,
                    price_per_liter=price,
                    total_amount=round(take * price, 2),
                    restocked_at=batch.restocked_at
                ))

            remaining_batches = get_fifo_batches(session, fuel.id)
            fuel.actual_liters = sum(b.liters_remaining for b in remaining_batches)
            session.add(fuel)

        fuel_responses.append(SaleResponse(
            id=sale.id,
            product_type="fuel",
            fuel_name=fuel.name,
            pump_name=pump.name,
            attendant_name=clean_name,
            recorded_by=current_user.name,
            liters_sold=sale.liters_sold,
            price_per_liter=sale.price_per_liter,
            total_amount=sale.total_amount,
            amount_paid=paid_amount,
            change_given=change_given,
            receipt_no=trx_ref,
            payment_method=data.payment_method.lower(),
            sold_at=sale.sold_at,
            fifo_breakdown=fifo_details,
            is_split_price=len(fifo_details) > 1
        ))

    for prep in oil_preparations:
        oil = prep["oil"]
        if not is_gcash:
            oil.stock -= prep["quantity"]
            session.add(oil)

        sale = OilSale(
            oil_product_id=oil.id,
            quantity=prep["quantity"],
            price_per_unit=prep["price_per_unit"],
            total_amount=prep["item_total"],
            amount_paid=paid_amount,
            change_given=change_given,
            receipt_no=trx_ref,
            transaction_ref=trx_ref,
            payment_method=data.payment_method.lower(),
            payment_status="pending" if is_gcash else "paid",
            attendant_name=clean_name,
            sold_by=current_user.id
        )
        session.add(sale)
        session.flush()

        oil_responses.append(OilSaleResponse(
            id=sale.id,
            product_type="oil",
            product_name=f"{oil.brand} {oil.name}",
            brand=oil.brand,
            quantity=sale.quantity,
            price_per_unit=sale.price_per_unit,
            total_amount=sale.total_amount,
            amount_paid=paid_amount,
            change_given=change_given,
            receipt_no=trx_ref,
            payment_method=data.payment_method.lower(),
            attendant_name=clean_name,
            sold_at=sale.sold_at,
            recorded_by=current_user.name,
            remaining_stock=oil.stock
        ))

    checkout_url, checkout_id = None, None
    if is_gcash:
        try:
            from services.gcashService import create_gcash_checkout
            checkout = create_gcash_checkout(
                amount_php=grand_total,
                description=f"Multi Sale ({trx_ref})",
                reference_id=trx_ref,
                metadata={"attendant": clean_name, "product_type": "multi"}
            )
            checkout_url = checkout["checkout_url"]
            checkout_id = checkout["checkout_id"]
        except Exception as e:
            session.rollback()
            raise HTTPException(500, f"GCash payment initialization failed: {str(e)}")

    session.commit()

    return MultiSaleResponse(
        transaction_ref=trx_ref,
        attendant_name=clean_name,
        payment_method=data.payment_method.lower(),
        payment_status="pending" if is_gcash else "paid",
        total_amount=grand_total,
        amount_paid=paid_amount,
        change_given=change_given,
        sold_at=trx_timestamp,
        fuel_sales=fuel_responses,
        oil_sales=oil_responses,
        checkout_url=checkout_url,
        checkout_id=checkout_id
    )

@router.get("/history", response_model=UnifiedHistoryResponse)
def get_unified_history(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    product_type: Optional[ProductType] = None,
    attendant_name: Optional[str] = None,
    payment_method: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session)
):
    raw_items = []

    if product_type is None or product_type in [ProductType.FUEL, ProductType.MULTI]:
        fuel_stmt = (
            select(Sale, Fuel.name, Pump.name, User.name)
            .outerjoin(Fuel, Sale.fuel_id == Fuel.id)
            .outerjoin(Pump, Sale.pump_id == Pump.id)
            .outerjoin(User, Sale.recorded_by == User.id)
        )
        if start_date:
            fuel_stmt = fuel_stmt.where(Sale.sold_at >= datetime.combine(start_date, time.min))
        if end_date:
            fuel_stmt = fuel_stmt.where(Sale.sold_at <= datetime.combine(end_date, time.max))
        if attendant_name:
            fuel_stmt = fuel_stmt.where(func.lower(Sale.attendant_name) == attendant_name.lower())
        if payment_method:
            fuel_stmt = fuel_stmt.where(func.lower(Sale.payment_method) == payment_method.lower())

        for s, fuel_name, pump_name, user_name in session.exec(fuel_stmt).all():
            tx_ref = getattr(s, "transaction_ref", None) or s.receipt_no
            raw_items.append({
                "type": "fuel",
                "sale": s,
                "product_name": fuel_name or "Fuel",
                "pump_name": pump_name,
                "recorded_by": user_name or "Unknown",
                "transaction_ref": tx_ref
            })

    if product_type is None or product_type in [ProductType.OIL, ProductType.MULTI]:
        oil_stmt = (
            select(OilSale, OilProduct.brand, OilProduct.name, User.name)
            .outerjoin(OilProduct, OilSale.oil_product_id == OilProduct.id)
            .outerjoin(User, OilSale.sold_by == User.id)
        )
        if start_date:
            oil_stmt = oil_stmt.where(OilSale.sold_at >= datetime.combine(start_date, time.min))
        if end_date:
            oil_stmt = oil_stmt.where(OilSale.sold_at <= datetime.combine(end_date, time.max))
        if attendant_name:
            oil_stmt = oil_stmt.where(func.lower(OilSale.attendant_name) == attendant_name.lower())
        if payment_method:
            oil_stmt = oil_stmt.where(func.lower(OilSale.payment_method) == payment_method.lower())

        for s, oil_brand, oil_name, user_name in session.exec(oil_stmt).all():
            p_name = f"{oil_brand or ''} {oil_name or ''}".strip() or "Oil"
            tx_ref = getattr(s, "transaction_ref", None) or s.receipt_no
            raw_items.append({
                "type": "oil",
                "sale": s,
                "product_name": p_name,
                "recorded_by": user_name or "Unknown",
                "transaction_ref": tx_ref
            })

    grouped_transactions = {}
    for item in raw_items:
        tx_ref = item["transaction_ref"]
        if tx_ref and str(tx_ref).startswith("M-"):
            if tx_ref not in grouped_transactions:
                grouped_transactions[tx_ref] = []
            grouped_transactions[tx_ref].append(item)
        else:
            grouped_transactions[f"SINGLE-{item['type']}-{item['sale'].id}"] = [item]

    unified: List[UnifiedSaleItem] = []

    for tx_ref, items in grouped_transactions.items():
        if len(items) == 1 and not tx_ref.startswith("M-"):
            it = items[0]
            s = it["sale"]
            paid_val = s.amount_paid if s.amount_paid is not None else s.total_amount
            change_val = s.change_given if s.change_given is not None else 0.0
            att_val = s.attendant_name or "Unknown"

            if it["type"] == "fuel":
                unified.append(UnifiedSaleItem(
                    id=s.id,
                    product_type=ProductType.FUEL,
                    product_name=it["product_name"],
                    quantity=float(s.liters_sold or 0.0),
                    unit="L",
                    price_per_unit=float(s.price_per_liter or 0.0),
                    total_amount=float(s.total_amount or 0.0),
                    amount_paid=float(paid_val),
                    change_given=float(change_val),
                    receipt_no=s.receipt_no,
                    payment_method=s.payment_method or "cash",
                    attendant_name=att_val,
                    sold_at=s.sold_at or datetime.now(),
                    recorded_by=it["recorded_by"],
                    fuel_id=s.fuel_id,
                    pump_id=s.pump_id,
                    pump_name=it["pump_name"]
                ))
            else:
                unified.append(UnifiedSaleItem(
                    id=s.id,
                    product_type=ProductType.OIL,
                    product_name=it["product_name"],
                    quantity=float(s.quantity or 0.0),
                    unit="pcs",
                    price_per_unit=float(s.price_per_unit or 0.0),
                    total_amount=float(s.total_amount or 0.0),
                    amount_paid=float(paid_val),
                    change_given=float(change_val),
                    receipt_no=s.receipt_no,
                    payment_method=s.payment_method or "cash",
                    attendant_name=att_val,
                    sold_at=s.sold_at or datetime.now(),
                    recorded_by=it["recorded_by"],
                    oil_id=s.oil_product_id
                ))
        else:
            first_sale = items[0]["sale"]
            item_descriptions = []
            grand_total = 0.0

            for it in items:
                s = it["sale"]
                grand_total += float(s.total_amount or 0.0)
                if it["type"] == "fuel":
                    item_descriptions.append(f"{it['product_name']} ({s.liters_sold}L)")
                else:
                    item_descriptions.append(f"{it['product_name']} ({s.quantity} pcs)")

            combined_name = " + ".join(item_descriptions)

            is_gcash = (first_sale.payment_method or "").lower() == "gcash"

            if is_gcash:
                grand_paid = grand_total
                grand_change = 0.0
            else:
                stored_paid = float(first_sale.amount_paid or 0.0)
                grand_paid = stored_paid if stored_paid >= grand_total else grand_total
                grand_change = float(first_sale.change_given or 0.0)

            unified.append(UnifiedSaleItem(
                id=first_sale.id,
                product_type=ProductType.MULTI,
                product_name=combined_name,
                quantity=float(len(items)),
                unit="items",
                price_per_unit=round(grand_total / len(items), 2) if items else 0.0,
                total_amount=round(grand_total, 2),
                amount_paid=round(grand_paid, 2),
                change_given=round(grand_change, 2),
                receipt_no=tx_ref,
                payment_method=first_sale.payment_method or "cash",
                attendant_name=first_sale.attendant_name or "Unknown",
                sold_at=first_sale.sold_at or datetime.now(),
                recorded_by=items[0]["recorded_by"]
            ))

    unified.sort(key=lambda x: x.sold_at or datetime.min, reverse=True)

    total_count = len(unified)
    total_amount = sum(x.total_amount for x in unified)
    total_liters = sum(x.quantity for x in unified if x.product_type == ProductType.FUEL)
    total_oil_pcs = sum(x.quantity for x in unified if x.product_type == ProductType.OIL)

    total_fuel_count = sum(1 if x.product_type == ProductType.FUEL else x.product_name.count("L)") for x in unified)
    total_oil_count = sum(1 if x.product_type == ProductType.OIL else x.product_name.count("pcs)") for x in unified)

    offset = (page - 1) * page_size
    paged = unified[offset : offset + page_size]

    return UnifiedHistoryResponse(
        sales=paged,
        total_count=total_count,
        total_amount=round(total_amount, 2),
        total_liters=round(total_liters, 2),
        total_oil_pcs=int(total_oil_pcs),
        total_fuel_count=total_fuel_count,
        total_oil_count=total_oil_count,
        page=page,
        page_size=page_size
    )

@router.delete("/{sale_id}")
def void_sale(
    sale_id: int,
    product_type: ProductType = Query(..., description="fuel or oil"),
    session: Session = Depends(get_session)
):
    if product_type == ProductType.FUEL:
        sale = session.get(Sale, sale_id)
        if not sale:
            raise HTTPException(404, "Fuel sale not found")
        sale_batches = session.exec(select(FuelSaleBatch).where(FuelSaleBatch.sale_id == sale_id)).all()
        for sb in sale_batches:
            batch = session.get(FuelBatch, sb.batch_id)
            if batch:
                batch.liters_remaining += sb.liters_consumed
                session.add(batch)
            session.delete(sb)
        fuel = session.get(Fuel, sale.fuel_id)
        if fuel:
            remaining_batches = session.exec(select(FuelBatch).where(FuelBatch.fuel_id == fuel.id, FuelBatch.liters_remaining > 0)).all()
            fuel.actual_liters = sum(b.liters_remaining for b in remaining_batches)
            if remaining_batches:
                remaining_batches_sorted = sorted(remaining_batches, key=lambda b: b.restocked_at)
                fuel.price = remaining_batches_sorted[0].selling_price
            session.add(fuel)
        session.delete(sale)
        session.commit()
        return {"message": f"Fuel sale {sale_id} voided (FIFO restored). {sale.liters_sold}L returned to batches."}
    else:
        sale = session.get(OilSale, sale_id)
        if not sale:
            raise HTTPException(404, "Oil sale not found")
        oil = session.get(OilProduct, sale.oil_product_id)
        if oil:
            oil.stock += sale.quantity
            session.add(oil)
        session.delete(sale)
        session.commit()
        return {"message": f"Oil sale {sale_id} voided. {sale.quantity} pcs returned."}

@router.get("/history/export")
def export_unified_csv(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    product_type: Optional[ProductType] = None,
    session: Session = Depends(get_session)
):
    from fastapi.responses import StreamingResponse
    import csv, io
    data = get_unified_history(start_date, end_date, product_type, None, None, 1, 10000, session)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date","Time","Type","Product","Qty","Unit","Price","Total","Paid","Change","Receipt","Payment","Attendant","Recorded By","Pump"])
    for s in data.sales:
        writer.writerow([
            s.sold_at.strftime("%Y-%m-%d"),
            s.sold_at.strftime("%H:%M:%S"),
            s.product_type.value,
            s.product_name,
            f"{s.quantity:.2f}",
            s.unit,
            f"{s.price_per_unit:.2f}",
            f"{s.total_amount:.2f}",
            f"{s.amount_paid:.2f}",
            f"{s.change_given:.2f}",
            s.receipt_no or "",
            s.payment_method,
            s.attendant_name,
            s.recorded_by,
            s.pump_name or ""
        ])
    output.seek(0)
    filename = f"unified_sales_{start_date or 'all'}_to_{end_date or 'all'}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

@router.get("/daily-liters-summary")
def get_daily_liters_summary(
    fuel_id: Optional[int] = Query(None, description="Filter by specific fuel ID"),
    session: Session = Depends(get_session)
):
    """
    Calculates total liters sold starting from today at 00:00:00 up to current time.
    Resets automatically at midnight (00:00:00 tomorrow).
    Includes breakdown per fuel type.
    """
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)

    query = select(Sale).where(
        and_(
            Sale.sold_at >= today_start,
            Sale.sold_at <= today_end,
            Sale.payment_status == "paid"
        )
    )

    if fuel_id:
        query = query.where(Sale.fuel_id == fuel_id)

    sales_today = session.exec(query).all()

    total_liters = sum(s.liters_sold for s in sales_today)
    total_revenue = sum(s.total_amount for s in sales_today)
    total_transactions = len(sales_today)

    fuel_breakdown_sql = text("""
        SELECT f.id, f.name, COALESCE(SUM(s.liters_sold), 0) as liters, COALESCE(SUM(s.total_amount), 0) as revenue, COUNT(s.id) as txns
        FROM fuel f
        LEFT JOIN sale s ON s.fuel_id = f.id 
            AND s.sold_at >= :today_start 
            AND s.sold_at <= :today_end 
            AND s.payment_status = 'paid'
        GROUP BY f.id, f.name
        ORDER BY f.id ASC
    """)
    
    breakdown_rows = session.execute(
        fuel_breakdown_sql, 
        {"today_start": today_start, "today_end": today_end}
    ).fetchall()

    breakdown = [
        {
            "fuel_id": r[0],
            "fuel_name": r[1],
            "liters_sold": round(float(r[2]), 2),
            "revenue": round(float(r[3]), 2),
            "transaction_count": int(r[4])
        }
        for r in breakdown_rows
    ]

    return {
        "date": today_start.strftime("%Y-%m-%d"),
        "period_start": today_start.isoformat(),
        "period_end": today_end.isoformat(),
        "total_liters_sold": round(total_liters, 2),
        "total_revenue": round(total_revenue, 2),
        "total_transactions": total_transactions,
        "fuel_breakdown": breakdown
    }
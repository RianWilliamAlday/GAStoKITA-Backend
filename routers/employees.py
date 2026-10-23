from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select, func
from db.database import get_session, Attendant, User, Sale, OilSale, Fuel, Pump, OilProduct
from datetime import datetime, date, time, timedelta
from typing import Optional, List
from pydantic import BaseModel

router = APIRouter(prefix="/api/employees", tags=["Employees"])

class AttendantCreate(BaseModel):
    name: str
    employee_id: Optional[str] = None
    contact: Optional[str] = None

class AttendantUpdate(BaseModel):
    name: Optional[str] = None
    employee_id: Optional[str] = None
    contact: Optional[str] = None
    is_active: Optional[bool] = None

@router.post("/attendants")
def add_attendant(data: AttendantCreate, session: Session = Depends(get_session)):
    clean = data.name.strip()
    if session.exec(select(Attendant).where(func.lower(Attendant.name) == clean.lower())).first():
        raise HTTPException(400, f"'{clean}' already exists")
    att = Attendant(name=clean, employee_id=data.employee_id, contact=data.contact, is_active=True,
                    created_at=datetime.now(), updated_at=datetime.now())
    session.add(att); session.commit(); session.refresh(att)
    return att

@router.get("/cashiers")
def list_attendants(include_inactive: bool = False, search: str = None, session: Session = Depends(get_session)):
    q = select(Attendant)
    if not include_inactive: q = q.where(Attendant.is_active == True)
    if search: q = q.where(Attendant.name.ilike(f"%{search}%"))
    return session.exec(q.order_by(Attendant.name)).all()

@router.get("/attendants/active")
def list_active(session: Session = Depends(get_session)):
    return session.exec(select(Attendant).where(Attendant.is_active == True).order_by(Attendant.name)).all()

@router.get("/attendants/names")
def list_names(session: Session = Depends(get_session)):
    names = session.exec(select(Attendant.name).where(Attendant.is_active == True).order_by(Attendant.name)).all()
    return names if names else ["Attendant 1","Attendant 2","Attendant 3"]

@router.put("/attendants/{attendant_id}")
def update_attendant(attendant_id: int, data: AttendantUpdate, session: Session = Depends(get_session)):
    att = session.get(Attendant, attendant_id)
    if not att: raise HTTPException(404, "Not found")
    if data.name and data.name.strip().lower()!= att.name.lower():
        if session.exec(select(Attendant).where(func.lower(Attendant.name) == data.name.lower(), Attendant.id!= attendant_id)).first():
            raise HTTPException(400, "Name taken")
        att.name = data.name.strip()
    if data.contact is not None: att.contact = data.contact
    if data.employee_id is not None: att.employee_id = data.employee_id
    if data.is_active is not None:
        att.is_active = data.is_active
        att.deactivated_at = datetime.now() if not data.is_active else None
    att.updated_at = datetime.now()
    session.add(att); session.commit(); session.refresh(att)
    return att

@router.patch("/attendants/{attendant_id}/rename")
def rename_attendant(attendant_id: int, new_name: str = Query(...), session: Session = Depends(get_session)):
    att = session.get(Attendant, attendant_id)
    if not att: raise HTTPException(404, "Not found")
    clean = new_name.strip()
    if session.exec(select(Attendant).where(func.lower(Attendant.name) == clean.lower(), Attendant.id!= attendant_id)).first():
        raise HTTPException(400, "Name exists")
    att.name = clean; att.updated_at = datetime.now()
    session.add(att); session.commit(); session.refresh(att)
    return att

@router.patch("/attendants/{attendant_id}/deactivate")
def deactivate(attendant_id: int, session: Session = Depends(get_session)):
    att = session.get(Attendant, attendant_id)
    att.is_active = False; att.deactivated_at = datetime.now(); att.updated_at = datetime.now()
    session.add(att); session.commit()
    return {"message": f"{att.name} deactivated"}

@router.delete("/attendants/{attendant_id}")
def remove(attendant_id: int, force: bool = False, session: Session = Depends(get_session)):
    att = session.get(Attendant, attendant_id)
    cnt = session.exec(select(func.count()).select_from(Sale).where(Sale.attendant_name == att.name)).one()
    if cnt > 0 and not force:
        raise HTTPException(400, f"Has {cnt} sales - deactivate or use?force=true")
    session.delete(att); session.commit()
    return {"message": f"{att.name} removed"}

@router.post("/attendants/seed-defaults")
def seed(session: Session = Depends(get_session)):
    if session.exec(select(Attendant)).first(): return {"message": "already seeded"}
    for n in ["Attendant 1","Attendant 2","Attendant 3"]:
        session.add(Attendant(name=n, display_name=n, is_active=True, created_at=datetime.now(), updated_at=datetime.now()))
    session.commit()
    return {"message": "seeded"}

@router.get("/")
def list_cashiers(session: Session = Depends(get_session)):
    """List all user accounts that can be cashiers"""
    users = session.exec(select(User).order_by(User.name)).all()
    result = []
    for u in users:
        fuel_cnt = session.exec(select(func.count()).select_from(Sale).where(Sale.recorded_by == u.id)).one() or 0
        oil_cnt = session.exec(select(func.count()).select_from(OilSale).where(OilSale.sold_by == u.id)).one() or 0
        result.append({
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "role": u.role,
            "is_active": u.is_active,
            "total_sales_recorded": int(fuel_cnt + oil_cnt)
        })
    return result

@router.get("/{user_id}/history")
def get_cashier_history(
    user_id: int,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    product_type: Optional[str] = Query(None, description="fuel, oil, multi, all"),
    attendant_name: Optional[str] = Query(None, description="Filter by attendant on duty"),
    payment_method: Optional[str] = Query(None, description="Filter by payment method (cash, gcash)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session)
):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(404, f"Cashier account {user_id} not found")

    raw_items = []

    if product_type in (None, "all", "fuel", "multi"):
        fuel_stmt = (
            select(Sale, Fuel.name, Pump.name)
            .outerjoin(Fuel, Sale.fuel_id == Fuel.id)
            .outerjoin(Pump, Sale.pump_id == Pump.id)
            .where(Sale.recorded_by == user_id)
        )
        if start_date:
            fuel_stmt = fuel_stmt.where(Sale.sold_at >= datetime.combine(start_date, time.min))
        if end_date:
            fuel_stmt = fuel_stmt.where(Sale.sold_at <= datetime.combine(end_date, time.max))
        if attendant_name:
            fuel_stmt = fuel_stmt.where(func.lower(Sale.attendant_name) == attendant_name.lower())
        if payment_method:
            fuel_stmt = fuel_stmt.where(func.lower(Sale.payment_method) == payment_method.lower())

        for s, fuel_name, pump_name in session.exec(fuel_stmt).all():
            tx_ref = getattr(s, "transaction_ref", None) or s.receipt_no
            raw_items.append({
                "type": "fuel",
                "sale": s,
                "product_name": fuel_name or "Fuel",
                "pump_name": pump_name or "-",
                "transaction_ref": tx_ref
            })

    if product_type in (None, "all", "oil", "multi"):
        oil_stmt = (
            select(OilSale, OilProduct.brand, OilProduct.name)
            .outerjoin(OilProduct, OilSale.oil_product_id == OilProduct.id)
            .where(OilSale.sold_by == user_id)
            .where(OilSale.payment_status == "paid")
        )
        if start_date:
            oil_stmt = oil_stmt.where(OilSale.sold_at >= datetime.combine(start_date, time.min))
        if end_date:
            oil_stmt = oil_stmt.where(OilSale.sold_at <= datetime.combine(end_date, time.max))
        if attendant_name:
            oil_stmt = oil_stmt.where(func.lower(OilSale.attendant_name) == attendant_name.lower())
        if payment_method:
            oil_stmt = oil_stmt.where(func.lower(OilSale.payment_method) == payment_method.lower())

        for s, oil_brand, oil_name in session.exec(oil_stmt).all():
            p_name = f"{oil_brand or ''} {oil_name or ''}".strip() or "Oil"
            tx_ref = getattr(s, "transaction_ref", None) or s.receipt_no
            raw_items.append({
                "type": "oil",
                "sale": s,
                "product_name": p_name,
                "pump_name": "-",
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

    unified = []

    for tx_ref, items in grouped_transactions.items():
        if len(items) == 1 and not tx_ref.startswith("M-"):
            it = items[0]
            s = it["sale"]
            paid_val = s.amount_paid if s.amount_paid is not None else s.total_amount
            change_val = s.change_given if s.change_given is not None else 0.0
            att_val = s.attendant_name or "Unknown"

            if it["type"] == "fuel":
                unified.append({
                    "id": s.id,
                    "product_type": "fuel",
                    "product_name": it["product_name"],
                    "attendant_name": att_val,
                    "quantity": float(s.liters_sold or 0.0),
                    "unit": "L",
                    "price": float(s.price_per_liter or 0.0),
                    "total_amount": float(s.total_amount or 0.0),
                    "amount_paid": float(paid_val),
                    "change_given": float(change_val),
                    "payment_method": s.payment_method or "cash",
                    "receipt_no": s.receipt_no,
                    "pump_name": it["pump_name"],
                    "sold_at": s.sold_at.isoformat() if isinstance(s.sold_at, datetime) else str(s.sold_at or datetime.now()),
                    "recorded_by": user.name
                })
            else:
                unified.append({
                    "id": s.id,
                    "product_type": "oil",
                    "product_name": it["product_name"],
                    "attendant_name": att_val,
                    "quantity": float(s.quantity or 0.0),
                    "unit": "pcs",
                    "price": float(s.price_per_unit or 0.0),
                    "total_amount": float(s.total_amount or 0.0),
                    "amount_paid": float(paid_val),
                    "change_given": float(change_val),
                    "payment_method": s.payment_method or "cash",
                    "receipt_no": s.receipt_no,
                    "pump_name": "-",
                    "sold_at": s.sold_at.isoformat() if isinstance(s.sold_at, datetime) else str(s.sold_at or datetime.now()),
                    "recorded_by": user.name
                })
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
                
            unified.append({
                "id": first_sale.id,
                "product_type": "multi",
                "product_name": combined_name,
                "attendant_name": first_sale.attendant_name or "Unknown",
                "quantity": float(len(items)),
                "unit": "items",
                "price": round(grand_total / len(items), 2) if items else 0.0,
                "total_amount": round(grand_total, 2),
                "amount_paid": round(grand_paid, 2),
                "change_given": round(grand_change, 2),
                "payment_method": first_sale.payment_method or "cash",
                "receipt_no": tx_ref,
                "pump_name": "-",
                "sold_at": first_sale.sold_at.isoformat() if isinstance(first_sale.sold_at, datetime) else str(first_sale.sold_at or datetime.now()),
                "recorded_by": user.name
            })

    if product_type and product_type.lower() in ("fuel", "oil", "multi"):
        unified = [x for x in unified if x["product_type"] == product_type.lower()]

    unified.sort(key=lambda x: x["sold_at"], reverse=True)

    total_fuel_count = sum(1 if x["product_type"] == "fuel" else x["product_name"].count("L)") for x in unified)
    total_oil_count  = sum(1 if x["product_type"] == "oil" else x["product_name"].count("pcs)") for x in unified)
    
    multi_count = sum(1 for x in unified if x["product_type"] == "multi")
    total_sum = sum(x["total_amount"] for x in unified)

    by_attendant = {}
    for x in unified:
        att = x["attendant_name"]
        by_attendant[att] = round(by_attendant.get(att, 0.0) + x["total_amount"], 2)

    offset = (page - 1) * page_size
    paged = unified[offset : offset + page_size]

    return {
        "cashier": {"id": user.id, "name": user.name, "email": user.email, "role": user.role},
        "summary": {
            "total_transactions": len(unified),
            "total_amount": round(total_sum, 2),
            "total_fuel_count": total_fuel_count,
            "total_oil_count": total_oil_count,
            "fuel_tx": total_fuel_count,
            "oil_tx": total_oil_count,
            "multi_tx": multi_count,
            "by_attendant": by_attendant,
            "period": f"{start_date or 'all'} to {end_date or 'all'}"
        },
        "sales": paged,
        "page": page,
        "page_size": page_size,
        "total_count": len(unified),
        "total_amount": round(total_sum, 2),
        "total_fuel_count": total_fuel_count,
        "total_oil_count": total_oil_count
    }

@router.get("/{user_id}/summary")
def get_cashier_summary(user_id: int, days: int = Query(30), session: Session = Depends(get_session)):
    user = session.get(User, user_id)
    if not user: raise HTTPException(404, "Cashier not found")
    since = datetime.now() - timedelta(days=days)

    fuels = session.exec(select(Sale).where(Sale.recorded_by == user_id, Sale.sold_at >= since)).all()
    oils = session.exec(select(OilSale).where(OilSale.sold_by == user_id, OilSale.sold_at >= since)).all()

    return {
        "cashier": user.name,
        "period_days": days,
        "fuel": {"tx": len(fuels), "liters": round(sum(s.liters_sold for s in fuels),2), "revenue": round(sum(s.total_amount for s in fuels),2)},
        "oil": {"tx": len(oils), "qty": sum(s.quantity for s in oils), "revenue": round(sum(s.total_amount for s in oils),2)},
        "total_revenue": round(sum(s.total_amount for s in fuels+oils),2),
        "unique_attendants_handled": len(set(s.attendant_name for s in fuels+oils))
    }

@router.get("/history/all")
def get_all_cashiers_history(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    session: Session = Depends(get_session)
):
    """For defense: compare cashiers if you have 2 shifts"""
    users = session.exec(select(User)).all()
    result = []
    for u in users:
        f_filters = [Sale.recorded_by == u.id]
        o_filters = [OilSale.sold_by == u.id]
        if start_date:
            f_filters.append(Sale.sold_at >= datetime.combine(start_date, time.min))
            o_filters.append(OilSale.sold_at >= datetime.combine(start_date, time.min))
        if end_date:
            f_filters.append(Sale.sold_at <= datetime.combine(end_date, time.max))
            o_filters.append(OilSale.sold_at <= datetime.combine(end_date, time.max))

        f_cnt = session.exec(select(func.count()).select_from(Sale).where(*f_filters)).one() or 0
        o_cnt = session.exec(select(func.count()).select_from(OilSale).where(*o_filters)).one() or 0
        f_rev = session.exec(select(func.sum(Sale.total_amount)).where(*f_filters)).one() or 0
        o_rev = session.exec(select(func.sum(OilSale.total_amount)).where(*o_filters)).one() or 0

        result.append({
            "cashier_id": u.id,
            "cashier_name": u.name,
            "transactions": int(f_cnt+o_cnt),
            "revenue": round(float(f_rev+o_rev),2)
        })
    result.sort(key=lambda x: x["revenue"], reverse=True)
    return result
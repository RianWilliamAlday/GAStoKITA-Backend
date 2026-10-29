from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select, func, col
from db.database import get_session, Attendant, User, Sale, OilSale, Fuel, Pump, OilProduct, Attendance
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

class ClockInRequest(BaseModel):
    attendant_id: int
    clock_in_time: Optional[datetime] = None  # Custom time input (e.g. "2026-10-20T08:00:00")
    notes: Optional[str] = None

class ClockOutRequest(BaseModel):
    attendant_id: int
    clock_out_time: Optional[datetime] = None  # Custom time input
    notes: Optional[str] = None

class UserClockInRequest(BaseModel):
    user_id: int
    notes: Optional[str] = None

class UserClockOutRequest(BaseModel):
    user_id: int
    notes: Optional[str] = None

class UserClockOutRequest(BaseModel):
    user_id: int
    attendance_id: Optional[int] = None        # close one specific record (admin clicking a row)
    clock_out_time: Optional[datetime] = None  # custom time (admin dialog)
    notes: Optional[str] = None

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

@router.post("/attendance/clock-in")
def clock_in(data: ClockInRequest, session: Session = Depends(get_session)):
    """Clock in an attendant with an optional custom input time."""
    attendant = session.get(Attendant, data.attendant_id)
    if not attendant:
        raise HTTPException(404, "Attendant not found")
    if not attendant.is_active:
        raise HTTPException(400, "Cannot clock in an inactive attendant")

    # Use the inputtable clock-in time, or fallback to right now
    effective_clock_in = data.clock_in_time or datetime.now()

    try:
        from db.database import Attendance
        open_session = session.exec(
            select(Attendance).where(
                Attendance.attendant_id == data.attendant_id,
                Attendance.clock_out == None
            )
        ).first()

        if open_session:
            raise HTTPException(
                400, 
                f"{attendant.name} is already clocked in since {open_session.clock_in.strftime('%Y-%m-%d %H:%M:%S')}"
            )

        att_record = Attendance(
            attendant_id=attendant.id,
            clock_in=effective_clock_in,
            notes=data.notes,
            date=effective_clock_in.date()
        )
        session.add(att_record)
        session.commit()
        session.refresh(att_record)

        return {
            "message": f"{attendant.name} clocked in successfully",
            "attendance_id": att_record.id,
            "attendant_name": attendant.name,
            "clock_in": att_record.clock_in.isoformat()
        }
    except ImportError:
        return {
            "message": f"{attendant.name} clocked in successfully",
            "attendant_id": attendant.id,
            "attendant_name": attendant.name,
            "clock_in": effective_clock_in.isoformat()
        }

@router.post("/attendance/clock-out")
def clock_out(data: ClockOutRequest, session: Session = Depends(get_session)):
    """Clock out an attendant with an optional custom input time."""
    attendant = session.get(Attendant, data.attendant_id)
    if not attendant:
        raise HTTPException(404, "Attendant not found")

    effective_clock_out = data.clock_out_time or datetime.now()

    try:
        from db.database import Attendance
        open_session = session.exec(
            select(Attendance).where(
                Attendance.attendant_id == data.attendant_id,
                Attendance.clock_out == None
            )
        ).first()

        if not open_session:
            raise HTTPException(400, f"{attendant.name} is not currently clocked in")

        if effective_clock_out < open_session.clock_in:
            raise HTTPException(400, "Clock-out time cannot be earlier than clock-in time")

        open_session.clock_out = effective_clock_out
        duration_seconds = (effective_clock_out - open_session.clock_in).total_seconds()
        open_session.hours_worked = round(duration_seconds / 3600.0, 2)

        if data.notes:
            open_session.notes = (open_session.notes or "") + f" | Out Note: {data.notes}"

        session.add(open_session)
        session.commit()
        session.refresh(open_session)

        return {
            "message": f"{attendant.name} clocked out successfully",
            "attendance_id": open_session.id,
            "clock_in": open_session.clock_in.isoformat(),
            "clock_out": open_session.clock_out.isoformat(),
            "hours_worked": open_session.hours_worked
        }
    except ImportError:
        return {
            "message": f"{attendant.name} clocked out successfully",
            "attendant_id": attendant.id,
            "attendant_name": attendant.name,
            "clock_out": effective_clock_out.isoformat()
        }

@router.get("/attendance/logs")
def get_attendance_logs(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    attendant_id: Optional[int] = Query(None),
    session: Session = Depends(get_session)
):
    """Retrieve historical attendance logs ONLY for Pump Attendants."""
    from db.database import Attendance
    # Strictly filter for pump attendants only
    stmt = (
        select(Attendance, Attendant.name)
        .outerjoin(Attendant, Attendance.attendant_id == Attendant.id)
        .where(Attendance.attendant_id != None)
    )
    
    if start_date:
        stmt = stmt.where(Attendance.clock_in >= datetime.combine(start_date, time.min))
    if end_date:
        stmt = stmt.where(Attendance.clock_in <= datetime.combine(end_date, time.max))
    if attendant_id:
        stmt = stmt.where(Attendance.attendant_id == attendant_id)

    rows = session.exec(stmt.order_by(Attendance.clock_in.desc())).all()
    logs = []
    for att, att_name in rows:
        logs.append({
            "id": att.id,
            "attendant_id": att.attendant_id,
            "attendant_name": att_name or "Unknown Attendant",
            "clock_in": att.clock_in.isoformat() if att.clock_in else None,
            "clock_out": att.clock_out.isoformat() if att.clock_out else None,
            "hours_worked": att.hours_worked or 0.0,
            "notes": att.notes,
            "is_active_shift": att.clock_out is None
        })
    return logs

@router.get("/attendance/summary")
def get_daily_attendance_summary(
    target_date: Optional[date] = Query(None, description="Single day. Omit for all time"),
    start_date: Optional[date] = Query(None, description="Start of range (inclusive)"),
    end_date: Optional[date] = Query(None, description="End of range (inclusive)"),
    session: Session = Depends(get_session)
):
    """Attendance summary strictly for Pump Attendants. No date params = all recorded shifts."""
    try:
        from db.database import Attendance
        stmt = select(Attendance, Attendant.name).outerjoin(
            Attendant, Attendance.attendant_id == Attendant.id
        ).where(
            Attendance.attendant_id != None  # Strictly filter for pump attendants
        )

        if target_date:
            stmt = stmt.where(
                Attendance.clock_in >= datetime.combine(target_date, time.min),
                Attendance.clock_in <= datetime.combine(target_date, time.max)
            )
        if start_date:
            stmt = stmt.where(Attendance.clock_in >= datetime.combine(start_date, time.min))
        if end_date:
            stmt = stmt.where(Attendance.clock_in <= datetime.combine(end_date, time.max))

        records = session.exec(stmt.order_by(Attendance.clock_in.desc())).all()
        currently_clocked_in = []
        completed_shifts = []
        total_hours = 0.0

        for att, name in records:
            item = {
                "id": att.id,
                "attendant_id": att.attendant_id,
                "attendant_name": name or "Unknown",
                "date": att.clock_in.date().isoformat() if att.clock_in else None,
                "clock_in": att.clock_in.strftime("%H:%M:%S") if att.clock_in else None,
                "clock_out": att.clock_out.strftime("%H:%M:%S") if att.clock_out else None,
                "hours_worked": att.hours_worked or 0.0
            }
            if att.clock_out is None:
                currently_clocked_in.append(item)
            else:
                completed_shifts.append(item)
                total_hours += att.hours_worked or 0.0

        return {
            "date": target_date.isoformat() if target_date else None,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "total_present": len(records),
            "currently_active_count": len(currently_clocked_in),
            "total_hours_logged": round(total_hours, 2),
            "active_attendants": currently_clocked_in,
            "completed_shifts": completed_shifts
        }
    except ImportError:
        return {
            "date": target_date.isoformat() if target_date else None,
            "start_date": None,
            "end_date": None,
            "total_present": 0,
            "currently_active_count": 0,
            "total_hours_logged": 0.0,
            "active_attendants": [],
            "completed_shifts": []
        }

@router.post("/user-attendance/login-clock-in")
def user_login_clock_in(
    req: UserClockInRequest,
    session: Session = Depends(get_session)
):
    user = session.get(User, req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today_date = date.today()
    existing = session.exec(
        select(Attendance).where(
            Attendance.user_id == req.user_id,
            Attendance.attendant_id.is_(None),   # <-- only the user's own login record
            Attendance.clock_out.is_(None),
            Attendance.date == today_date
        )
    ).first()

    if existing:
        return {"message": f"{user.name} is already clocked in.", "attendance_id": existing.id}

    new_attendance = Attendance(
        user_id=user.id,
        attendant_id=None,
        clock_in=datetime.now(),
        date=today_date,
        notes=req.notes
    )
    session.add(new_attendance)
    session.commit()
    session.refresh(new_attendance)

    return {"message": f"Successfully clocked in {user.name}", "attendance_id": new_attendance.id}

@router.post("/user-attendance/clock-out")
def user_clock_out(req: UserClockOutRequest, session: Session = Depends(get_session)):
    user = session.get(User, req.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    stmt = select(Attendance).where(
        Attendance.user_id == req.user_id,
        Attendance.attendant_id.is_(None),
        Attendance.clock_out.is_(None),
    )
    if req.attendance_id:
        stmt = stmt.where(Attendance.id == req.attendance_id)
    open_session = session.exec(stmt.order_by(Attendance.clock_in.desc())).first()

    if not open_session:
        return {"message": f"{user.name} is not currently clocked in.", "clocked_out": False}

    effective_clock_out = req.clock_out_time or datetime.now()
    if effective_clock_out < open_session.clock_in:
        raise HTTPException(status_code=400, detail="Clock-out time cannot be earlier than clock-in time")

    # Automatic clock-outs skip forgotten shifts so they don't get a huge bogus duration;
    # an admin closes those from the attendance page instead.
    if not req.attendance_id and not req.clock_out_time \
            and effective_clock_out - open_session.clock_in > timedelta(hours=24):
        return {"message": f"{user.name} has an open shift older than 24 hours. Ask an admin to close it.",
                "clocked_out": False}

    open_session.clock_out = effective_clock_out
    open_session.hours_worked = round((effective_clock_out - open_session.clock_in).total_seconds() / 3600.0, 2)
    if req.notes:
        open_session.notes = f"{open_session.notes} | Out Note: {req.notes}" if open_session.notes else f"Out Note: {req.notes}"

    session.add(open_session)
    session.commit()
    session.refresh(open_session)

    return {
        "message": f"{user.name} clocked out successfully",
        "clocked_out": True,
        "attendance_id": open_session.id,
        "clock_in": open_session.clock_in.isoformat(),
        "clock_out": open_session.clock_out.isoformat(),
        "hours_worked": open_session.hours_worked,
    }

@router.get("/admin/attendance/overview")
def get_admin_attendance_overview(
    target_date: Optional[date] = Query(None, description="Single day. Omit for all time"),
    date_from: Optional[date] = Query(None, description="Start of range (inclusive)"),
    date_to: Optional[date] = Query(None, description="End of range (inclusive)"),
    session: Session = Depends(get_session)
):
    # Build optional date filters (none given = all recorded shifts)
    date_filters = []
    if target_date:
        date_filters.append(Attendance.date == target_date)
    if date_from:
        date_filters.append(Attendance.date >= date_from)
    if date_to:
        date_filters.append(Attendance.date <= date_to)

    # 1. Attendant attendance
    att_stmt = (
        select(Attendance, Attendant.name)
        .outerjoin(Attendant, Attendance.attendant_id == Attendant.id)
        .where(Attendance.attendant_id.is_not(None), *date_filters)
        .order_by(Attendance.clock_in.desc())
    )
    att_records = session.exec(att_stmt).all()

    attendant_logs = []
    att_active_count = 0
    att_total_hours = 0.0

    for att, name in att_records:
        is_active = att.clock_out is None
        if is_active:
            att_active_count += 1
        else:
            att_total_hours += att.hours_worked or 0.0

        attendant_logs.append({
            "id": att.id,
            "attendant_id": att.attendant_id,
            "user_id": att.user_id,
            "type": "Attendant",
            "name": name or "Unknown Attendant",
            "date": att.date.isoformat() if att.date else None,
            "clock_in": att.clock_in.isoformat() if att.clock_in else None,
            "clock_out": att.clock_out.isoformat() if att.clock_out else None,
            "hours_worked": att.hours_worked or 0.0,
            "notes": att.notes,
            "is_active": is_active
        })

    # 2. User / cashier login attendance
    usr_stmt = (
        select(Attendance, User.name, User.role)
        .outerjoin(User, Attendance.user_id == User.id)
        .where(
            Attendance.user_id.is_not(None),
            Attendance.attendant_id.is_(None),
            *date_filters
        )
        .order_by(Attendance.clock_in.desc())
    )
    usr_records = session.exec(usr_stmt).all()

    user_logs = []
    usr_active_count = 0
    usr_total_hours = 0.0

    for att, name, role in usr_records:
        is_active = att.clock_out is None
        if is_active:
            usr_active_count += 1
        else:
            usr_total_hours += att.hours_worked or 0.0

        role_str = role.value if hasattr(role, "value") else str(role or "Cashier")

        user_logs.append({
            "id": att.id,
            "attendant_id": att.attendant_id,
            "user_id": att.user_id,
            "type": f"User ({role_str})",
            "name": name or "Unknown User",
            "date": att.date.isoformat() if att.date else None,
            "clock_in": att.clock_in.isoformat() if att.clock_in else None,
            "clock_out": att.clock_out.isoformat() if att.clock_out else None,
            "hours_worked": att.hours_worked or 0.0,
            "notes": att.notes,
            "is_active": is_active
        })

    return {
        "date": target_date.isoformat() if target_date else None,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "summary": {
            "total_attendants_active": att_active_count,
            "total_users_active": usr_active_count,
            "combined_active_now": att_active_count + usr_active_count,
            "total_attendant_hours": round(att_total_hours, 2),
            "total_user_hours": round(usr_total_hours, 2),
        },
        "attendant_logs": attendant_logs,
        "user_logs": user_logs,
        "combined_logs": sorted(
            attendant_logs + user_logs,
            key=lambda x: x["clock_in"] or "",
            reverse=True
        )
    }

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
            .where(Sale.payment_status == "paid")
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
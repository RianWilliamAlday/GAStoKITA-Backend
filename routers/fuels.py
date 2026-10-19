from urllib import request
from services.auth import get_current_user, User
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from db.database import get_session, Fuel, RestockLog, FuelBatch, FuelSaleBatch
from models.schemas import FuelTypeResponse, PriceUpdate, RestockRequest, ThresholdUpdate, FuelBatchResponse
from services.dipstick import get_closest_dipstick_reading
from db.data import DIPSTICK_DATA
from datetime import datetime
from typing import List

router = APIRouter(prefix="/api/fuels", tags=["fuels"])
MAX_CM = max(DIPSTICK_DATA.keys())

def check_low_stock(fuel: Fuel) -> bool:
    return fuel.actual_liters <= fuel.threshold

def get_fifo_batches(session: Session, fuel_id: int):
    return session.exec(select(FuelBatch).where(FuelBatch.fuel_id == fuel_id, FuelBatch.liters_remaining > 0).order_by(FuelBatch.restocked_at.asc(), FuelBatch.id.asc())).all()

def get_current_fifo_price(session: Session, fuel_id: int) -> float:
    batches = get_fifo_batches(session, fuel_id)
    if batches:
        return batches[0].selling_price
    fuel = session.get(Fuel, fuel_id)
    return fuel.price if fuel else 0.0

def ensure_initial_batch(session: Session, fuel: Fuel):
    """Create initial batch from existing stock if no batches exist (migration)"""
    existing = session.exec(select(FuelBatch).where(FuelBatch.fuel_id == fuel.id)).first()
    current_user: User = Depends(get_current_user)
    if not existing and fuel.actual_liters > 0:
        batch = FuelBatch(
            fuel_id=fuel.id,
            liters_initial=fuel.actual_liters,
            liters_remaining=fuel.actual_liters,
            cost_per_liter=fuel.price * 0.8,
            selling_price=fuel.price,
            supplier="Initial Stock",
            restocked_by=current_user.id,
            restocked_at=fuel.last_restocked or datetime.now()
        )
        session.add(batch)
        session.commit()
        return batch
    return existing

@router.get("", response_model=List[FuelTypeResponse])
def get_fuels(session: Session = Depends(get_session)):
    fuels = session.exec(select(Fuel)).all()
    resp = []
    for fuel in fuels:
        ensure_initial_batch(session, fuel)
        batches = get_fifo_batches(session, fuel.id)

        batches_data = []
        for b in batches:
            batches_data.append(
                FuelBatchResponse(
                    id=b.id,
                    fuel_id=b.fuel_id,
                    liters_initial=getattr(b, 'liters_initial', getattr(b, 'initial_liters', 0)),
                    liters_remaining=getattr(b, 'liters_remaining', getattr(b, 'remaining_liters', 0)),
                    cost_per_liter=getattr(b, 'cost_per_liter', 0),
                    selling_price=b.selling_price,
                    supplier=getattr(b, 'supplier', None),
                    restocked_by=getattr(b, 'restocked_by', None),
                    restocked_at=getattr(b, 'restocked_at', getattr(b, 'created_at', datetime.now())),
                )
            )

        current_price = batches[0].selling_price if batches else fuel.price
        cm, dl = get_closest_dipstick_reading(fuel.actual_liters)
        pct = min(100.0, round((dl / fuel.tank_capacity) * 100, 2))

        resp.append(FuelTypeResponse(
            id=fuel.id, name=fuel.name, price=current_price,
            actual_liters=fuel.actual_liters, tank_capacity=fuel.tank_capacity,
            display_cm=cm, display_liters=dl, display_percentage=pct,
            threshold=fuel.threshold, needs_restock=check_low_stock(fuel),
            fifo_price=current_price,
            oldest_batch_price=batches[0].selling_price if batches else None,
            newest_batch_price=batches[-1].selling_price if batches else None,
            active_batches=len(batches),
            batches=batches_data
        ))
    return resp

@router.get("/{fuel_id}/batches", response_model=List[FuelBatchResponse])
def get_fuel_batches(fuel_id: int, include_empty: bool = Query(False), session: Session = Depends(get_session)):
    fuel = session.get(Fuel, fuel_id)
    if not fuel: raise HTTPException(404, "Fuel not found")
    q = select(FuelBatch).where(FuelBatch.fuel_id == fuel_id).order_by(FuelBatch.restocked_at.asc())
    if not include_empty:
        q = q.where(FuelBatch.liters_remaining > 0)
    batches = session.exec(q).all()
    return batches

@router.get("/{fuel_id}/fifo-price")
def get_fifo_price(fuel_id: int, session: Session = Depends(get_session)):
    fuel = session.get(Fuel, fuel_id)
    if not fuel: raise HTTPException(404, "Fuel not found")
    ensure_initial_batch(session, fuel)
    batches = get_fifo_batches(session, fuel_id)
    if not batches:
        return {"fuel_id": fuel_id, "fuel_name": fuel.name, "current_fifo_price": fuel.price, "message": "No active batches, using base price", "batches": []}
    return {
        "fuel_id": fuel_id,
        "fuel_name": fuel.name,
        "current_fifo_price": batches[0].selling_price,
        "oldest_batch": {"id": batches[0].id, "price": batches[0].selling_price, "remaining": batches[0].liters_remaining, "restocked_at": batches[0].restocked_at},
        "newest_batch": {"id": batches[-1].id, "price": batches[-1].selling_price, "remaining": batches[-1].liters_remaining} if len(batches) > 1 else None,
        "total_batches": len(batches),
        "total_liters": sum(b.liters_remaining for b in batches),
        "queue": [{"batch_id": b.id, "liters_remaining": b.liters_remaining, "selling_price": b.selling_price, "cost_per_liter": b.cost_per_liter, "restocked_at": b.restocked_at} for b in batches]
    }

@router.post("/{fuel_id}/restock")
def restock_fuel(fuel_id: int, data: RestockRequest, session: Session = Depends(get_session)):
    """
    FIFO Restock: Creates a new batch at new selling_price.
    Current selling price stays at old batch price until old stock depleted.
    Example: 4kL at 60 remaining, restock 4kL at 75 -> price stays 60 until first 4kL sold.
    """
    fuel = session.get(Fuel, fuel_id)
    if not fuel: raise HTTPException(404, "Fuel type not found")
    if data.liters_added <= 0: raise HTTPException(400, "Liters added must be positive")
    if fuel.actual_liters + data.liters_added > fuel.tank_capacity:
        raise HTTPException(400, f"Exceeds tank capacity. Only {fuel.tank_capacity - fuel.actual_liters:.2f}L space left")
    if data.selling_price <= 0:
        raise HTTPException(400, "Selling price must be positive")
    ensure_initial_batch(session, fuel)
    current_user: User = Depends(get_current_user)

    cost_per_liter = data.cost / data.liters_added if data.liters_added > 0 else 0

    batch = FuelBatch(
        fuel_id=fuel.id,
        liters_initial=data.liters_added,
        liters_remaining=data.liters_added,
        cost_per_liter=cost_per_liter,
        selling_price=data.selling_price,
        supplier=data.supplier,
        restocked_by=current_user.id,
        restocked_at=datetime.now()
    )
    session.add(batch)

    fuel.actual_liters += data.liters_added
    fuel.last_restocked = datetime.now()
    session.add(fuel)

    log = RestockLog(fuel_id=fuel.id, liters_added=data.liters_added, cost=data.cost, supplier=data.supplier, restocked_by="admin")
    session.add(log)
    session.commit()
    session.refresh(fuel)
    session.refresh(batch)

    batches = get_fifo_batches(session, fuel.id)
    current_fifo_price = batches[0].selling_price if batches else data.selling_price
    fuel.price = current_fifo_price
    session.add(fuel)
    session.commit()

    cm, dl = get_closest_dipstick_reading(fuel.actual_liters)

    return {
        "message": f"Restocked {data.liters_added}L of {fuel.name} at ₱{data.selling_price}/L (FIFO)",
        "fuel_id": fuel.id,
        "new_batch": {"id": batch.id, "liters": batch.liters_initial, "selling_price": batch.selling_price, "cost_per_liter": batch.cost_per_liter},
        "current_fifo_price": current_fifo_price,
        "old_price_still_active": current_fifo_price != data.selling_price,
        "explanation": f"Old stock at ₱{current_fifo_price}/L will sell first. New batch at ₱{data.selling_price}/L queued." if current_fifo_price != data.selling_price else f"New price ₱{data.selling_price}/L is now active (no old stock).",
        "new_actual_liters": fuel.actual_liters,
        "new_display_cm": cm,
        "new_display_liters": dl,
        "needs_restock": check_low_stock(fuel),
        "active_batches": len(batches)
    }

@router.patch("/{fuel_id}/threshold")
def update_threshold(fuel_id: int, data: ThresholdUpdate, session: Session = Depends(get_session)):
    fuel = session.get(Fuel, fuel_id)
    if not fuel: raise HTTPException(404, "Fuel not found")
    if data.threshold < 0: raise HTTPException(400, "Threshold cannot be negative")
    fuel.threshold = data.threshold; session.add(fuel); session.commit()
    return {"message": "Threshold updated", "new_threshold": fuel.threshold}

@router.patch("/{fuel_id}/price")
def update_price(fuel_id: int, data: PriceUpdate, session: Session = Depends(get_session)):
    """
    Manual price override: updates the current FIFO batch (oldest active) price.
    If you want to change future batch price, use restock with new selling_price instead.
    """
    fuel = session.get(Fuel, fuel_id)
    if not fuel: raise HTTPException(404, "Fuel not found")
    if data.price <= 0: raise HTTPException(400, "Price must be positive")

    ensure_initial_batch(session, fuel)
    batches = get_fifo_batches(session, fuel_id)
    if batches:
        batches[0].selling_price = data.price
        session.add(batches[0])
        message = f"Updated current FIFO batch (ID {batches[0].id}) price to ₱{data.price}"
    else:
        message = "No active batches, updated base price"

    fuel.price = data.price
    session.add(fuel)
    session.commit()
    return {"id": fuel.id, "new_price": data.price, "message": message}

@router.post("/{fuel_id}/sync-dipstick")
def sync_from_dipstick(fuel_id: int, cm: int = Query(..., ge=0), session: Session = Depends(get_session)):
    fuel = session.get(Fuel, fuel_id)
    if cm not in DIPSTICK_DATA: raise HTTPException(400, "Invalid reading")
    new_l = DIPSTICK_DATA[cm]; old_l = fuel.actual_liters
    diff = new_l - old_l
    if diff > 0:
        ensure_initial_batch(session, fuel)
        batch = FuelBatch(fuel_id=fuel.id, liters_initial=diff, liters_remaining=diff, cost_per_liter=fuel.price*0.8, selling_price=fuel.price, supplier="Dipstick Sync", restocked_by="system")
        session.add(batch)
    elif diff < 0:
        liters_to_deduct = abs(diff)
        batches = get_fifo_batches(session, fuel_id)
        for b in batches:
            if liters_to_deduct <= 0: break
            take = min(b.liters_remaining, liters_to_deduct)
            b.liters_remaining -= take
            liters_to_deduct -= take
            session.add(b)

    fuel.actual_liters = new_l
    fuel.last_restocked = datetime.now()
    session.add(fuel)
    session.commit()
    return {"message": f"Synced {fuel.name}", "old_liters": old_l, "new_liters": new_l, "difference": diff}

@router.get("/dipstick")
def cm_to_liters(cm: int = Query(..., ge=0), fuel_name: str = Query(...), session: Session = Depends(get_session)):
    if cm not in DIPSTICK_DATA:
        if cm > MAX_CM: raise HTTPException(400, f"Max is {MAX_CM}cm")
        raise HTTPException(400, f"Invalid {cm}cm")
    liters = DIPSTICK_DATA[cm]
    fuel = session.exec(select(Fuel).where(Fuel.name == fuel_name)).first()
    if not fuel: raise HTTPException(404, f"Fuel '{fuel_name}' not found")
    pct = min(100.0, round((liters / fuel.tank_capacity) * 100, 2))
    return {"input_cm": cm, "fuel_type": fuel.name, "liters": liters, "tank_capacity": fuel.tank_capacity, "percentage": pct}

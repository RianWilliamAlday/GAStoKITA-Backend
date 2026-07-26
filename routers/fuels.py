from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from db.database import get_session, Fuel, RestockLog
from models.schemas import FuelTypeResponse, PriceUpdate, RestockRequest, ThresholdUpdate
from services.dipstick import get_closest_dipstick_reading
from db.data import DIPSTICK_DATA
from datetime import datetime

router = APIRouter(prefix="/api/fuels", tags=["fuels"])
MAX_CM = max(DIPSTICK_DATA.keys())

def check_low_stock(fuel: Fuel) -> bool:
    return fuel.actual_liters <= fuel.threshold

@router.get("", response_model=list[FuelTypeResponse])
def get_fuels(session: Session = Depends(get_session)):
    fuels = session.exec(select(Fuel)).all()
    response_data = []
    for fuel in fuels:
        cm, display_liters = get_closest_dipstick_reading(fuel.actual_liters)
        percentage = min(100.0, round((display_liters / fuel.tank_capacity) * 100, 2))
        response_data.append(FuelTypeResponse(id=fuel.id, name=fuel.name, price=fuel.price, actual_liters=fuel.actual_liters, tank_capacity=fuel.tank_capacity, display_cm=cm, display_liters=display_liters, display_percentage=percentage, threshold=fuel.threshold, needs_restock=check_low_stock(fuel)))
    return response_data

@router.post("/{fuel_id}/restock")
def restock_fuel(fuel_id: int, data: RestockRequest, session: Session = Depends(get_session)):
    fuel = session.get(Fuel, fuel_id)
    if not fuel: raise HTTPException(404, "Fuel type not found")
    if data.liters_added <= 0: raise HTTPException(400, "Liters added must be positive")
    if fuel.actual_liters + data.liters_added > fuel.tank_capacity:
        remaining = fuel.tank_capacity - fuel.actual_liters
        raise HTTPException(400, f"Exceeds tank capacity. Only {remaining:.2f}L space left")
    fuel.actual_liters += data.liters_added
    fuel.last_restocked = datetime.now()
    session.add(fuel)
    log = RestockLog(fuel_id=fuel.id, liters_added=data.liters_added, cost=data.cost, supplier=data.supplier, restocked_by="admin")
    session.add(log)
    session.commit()
    session.refresh(fuel)
    cm, display_liters = get_closest_dipstick_reading(fuel.actual_liters)
    return {"message": f"Restocked {data.liters_added}L of {fuel.name}", "fuel_id": fuel.id, "new_actual_liters": fuel.actual_liters, "new_display_cm": cm, "new_display_liters": display_liters, "needs_restock": check_low_stock(fuel)}

@router.patch("/{fuel_id}/threshold")
def update_threshold(fuel_id: int, data: ThresholdUpdate, session: Session = Depends(get_session)):
    fuel = session.get(Fuel, fuel_id)
    if not fuel: raise HTTPException(404, "Fuel type not found")
    if data.threshold < 0: raise HTTPException(400, "Threshold cannot be negative")
    fuel.threshold = data.threshold
    session.add(fuel); session.commit()
    return {"message": "Threshold updated", "fuel": fuel.name, "new_threshold": fuel.threshold, "needs_restock": check_low_stock(fuel)}

@router.get("/dipstick")
def cm_to_liters(cm: int = Query(..., ge=0), fuel_name: str = Query(...), session: Session = Depends(get_session)):
    if cm not in DIPSTICK_DATA:
        if cm > MAX_CM: raise HTTPException(400, f"Max dipstick is {MAX_CM}cm")
        raise HTTPException(400, f"Invalid reading {cm}cm")
    liters = DIPSTICK_DATA[cm]
    fuel = session.exec(select(Fuel).where(Fuel.name == fuel_name)).first()
    if not fuel: raise HTTPException(404, f"Fuel '{fuel_name}' not found")
    if liters > fuel.tank_capacity: raise HTTPException(400, f"{liters}L exceeds capacity")
    percentage = min(100.0, round((liters / fuel.tank_capacity) * 100, 2))
    return {"input_cm": cm, "fuel_type": fuel.name, "liters": liters, "tank_capacity": fuel.tank_capacity, "percentage": percentage}

@router.patch("/{fuel_id}/price")
def update_price(fuel_id: int, data: PriceUpdate, session: Session = Depends(get_session)):
    fuel = session.get(Fuel, fuel_id)
    if not fuel: raise HTTPException(404, "Fuel type not found")
    if data.price <= 0: raise HTTPException(400, "Price must be positive")
    fuel.price = data.price; session.add(fuel); session.commit(); session.refresh(fuel)
    return {"id": fuel.id, "new_price": fuel.price}

@router.post("/{fuel_id}/sync-dipstick")
def sync_from_dipstick(fuel_id: int, cm: int = Query(..., ge=0), session: Session = Depends(get_session)):
    fuel = session.get(Fuel, fuel_id)
    if cm not in DIPSTICK_DATA: raise HTTPException(400, "Invalid dipstick")
    new_liters = DIPSTICK_DATA[cm]; old_liters = fuel.actual_liters
    fuel.actual_liters = new_liters; fuel.last_restocked = datetime.now(); session.add(fuel); session.commit()
    return {"message": f"Synced {fuel.name}", "old_liters": old_liters, "new_liters": new_liters, "difference": new_liters - old_liters}
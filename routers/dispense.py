from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from db.database import get_session, Fuel, Pump
from models.schemas import DispenseRequest
from services.dipstick import get_closest_dipstick_reading

router = APIRouter(prefix="/api/dispense", tags=["dispense"])

@router.post("")
def dispense_fuel(data: DispenseRequest, session: Session = Depends(get_session)):
    pump = session.get(Pump, data.pump_id)
    if not pump:
        raise HTTPException(status_code=404, detail="Pump not found")

    if pump.status!= "Available":
        raise HTTPException(status_code=400, detail=f"Pump {pump.name} is {pump.status}")

    fuel = session.get(Fuel, pump.fuel_type_id)
    if not fuel:
        raise HTTPException(status_code=404, detail="Fuel type not found")

    if data.amount_liters <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    if fuel.actual_liters < data.amount_liters:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough fuel. Only {fuel.actual_liters:.2f}L left"
        )

    fuel.actual_liters -= data.amount_liters
    session.add(fuel)
    session.commit()
    session.refresh(fuel)

    cm, display_liters = get_closest_dipstick_reading(fuel.actual_liters)
    percentage = min(100.0, round((display_liters / fuel.tank_capacity) * 100, 2))

    return {
        "message": f"Dispensed {data.amount_liters}L from {pump.name}",
        "actual_internal_liters": fuel.actual_liters,
        "snapped_dipstick_cm": cm,
        "snapped_dipstick_liters": display_liters,
        "new_display_percentage": percentage
    }
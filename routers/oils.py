from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from db.database import get_session, OilProduct, OilRestockLog

router = APIRouter(prefix="/oils", tags=["Oils"])

class OilCreate(BaseModel):
    brand: str
    name: str
    variant: Optional[str] = None
    stock: int = 0
    price: float
    cost: float = 0
    low_stock_threshold: int = 5

class OilRestockRequest(BaseModel):
    quantity_added: int
    total_cost: float
    supplier: Optional[str] = None

@router.post("/", response_model=OilProduct)
def create_oil(data: OilCreate, session: Session = Depends(get_session)):
    oil = OilProduct(**data.model_dump(), updated_at=datetime.now())
    session.add(oil)
    session.commit()
    session.refresh(oil)
    return oil

@router.get("/", response_model=List[OilProduct])
def list_oils(session: Session = Depends(get_session)):
    return session.exec(select(OilProduct).order_by(OilProduct.brand, OilProduct.name)).all()

@router.get("/{oil_id}", response_model=OilProduct)
def get_oil(oil_id: int, session: Session = Depends(get_session)):
    oil = session.get(OilProduct, oil_id)
    if not oil:
        raise HTTPException(404, "Oil product not found")
    return oil

@router.put("/{oil_id}", response_model=OilProduct)
def update_oil(oil_id: int, data: OilCreate, session: Session = Depends(get_session)):
    oil = session.get(OilProduct, oil_id)
    if not oil:
        raise HTTPException(404, "Not found")
    for k, v in data.model_dump().items():
        setattr(oil, k, v)
    oil.updated_at = datetime.now()
    session.add(oil)
    session.commit()
    session.refresh(oil)
    return oil

@router.post("/{oil_id}/restock")
def restock_oil(oil_id: int, data: OilRestockRequest, session: Session = Depends(get_session)):
    oil = session.get(OilProduct, oil_id)
    if not oil:
        raise HTTPException(404, "Oil product not found")
    if data.quantity_added <= 0:
        raise HTTPException(400, "Quantity must be positive")

    oil.stock += data.quantity_added
    if data.quantity_added > 0:
        oil.cost = data.total_cost / data.quantity_added
    oil.supplier = data.supplier or getattr(oil, "supplier", None)
    oil.updated_at = datetime.now()

    log = OilRestockLog(
        oil_product_id=oil.id,
        quantity_added=data.quantity_added,
        total_cost=data.total_cost,
        supplier=data.supplier
    )
    session.add(oil)
    session.add(log)
    session.commit()
    session.refresh(oil)

    return {
        "message": f"Restocked {data.quantity_added} pcs of {oil.brand} {oil.name}",
        "new_stock": oil.stock,
        "needs_restock": oil.stock <= oil.low_stock_threshold
    }
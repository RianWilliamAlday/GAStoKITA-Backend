from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlmodel import Session, select
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from supabase import create_client, Client
from db.database import get_session, OilProduct, OilRestockLog
from services.auth import get_current_user, User
from dotenv import load_dotenv
from pathlib import Path
import os, uuid

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

router = APIRouter(prefix="/oils", tags=["Oils"])

class OilCreate(BaseModel):
    brand: str
    name: str
    variant: Optional[str] = None
    stock: int = 0
    price: float
    cost: float = 0  # base cost per unit
    low_stock_threshold: int = 5
    image_url: Optional[str] = None

class OilRestockRequest(BaseModel):
    quantity_added: int
    unit_cost: float  # BASE COST PER UNIT (the system computes the total)
    supplier: Optional[str] = None

@router.post("/", response_model=OilProduct)
def create_oil(
    data: OilCreate, 
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user)
):
    oil = OilProduct(**data.model_dump(), updated_at=datetime.now())
    session.add(oil)
    session.flush()

    if data.stock > 0 and data.cost > 0:
        total_initial_cost = round(data.stock * data.cost, 2)
        initial_log = OilRestockLog(
            oil_product_id=oil.id,
            quantity_added=data.stock,
            total_cost=total_initial_cost,
            supplier="Initial Inventory Creation",
            restocked_by=current_user.id,
            restocked_at=datetime.now()
        )
        session.add(initial_log)

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
def restock_oil(
    oil_id: int, 
    data: OilRestockRequest, 
    session: Session = Depends(get_session), 
    current_user: User = Depends(get_current_user)
):
    """
    `unit_cost` is the base cost PER UNIT. The system computes the batch total:
    total_batch_cost = quantity_added * unit_cost.
    The log stores the total (used by expenses); the product's `cost` becomes
    the weighted-average cost per unit.
    """
    oil = session.get(OilProduct, oil_id)
    if not oil:
        raise HTTPException(404, "Oil product not found")
    if data.quantity_added <= 0:
        raise HTTPException(400, "Quantity must be positive")
    if data.unit_cost < 0:
        raise HTTPException(400, "Unit cost cannot be negative")

    total_batch_cost = round(data.quantity_added * data.unit_cost, 2)

    current_total_value = oil.stock * oil.cost
    new_total_value = current_total_value + total_batch_cost
    new_total_stock = oil.stock + data.quantity_added

    if new_total_stock > 0:
        oil.cost = round(new_total_value / new_total_stock, 2)  # Weighted-average cost per unit

    oil.stock = new_total_stock
    oil.updated_at = datetime.now()

    # Expense log records TOTAL expenditure
    log = OilRestockLog(
        oil_product_id=oil.id,
        quantity_added=data.quantity_added,
        total_cost=total_batch_cost,
        supplier=data.supplier,
        restocked_by=current_user.id,
        restocked_at=datetime.now()
    )
    
    session.add(oil)
    session.add(log)
    session.commit()
    session.refresh(oil)

    return {
        "message": f"Restocked {data.quantity_added} pcs of {oil.brand} {oil.name}",
        "new_stock": oil.stock,
        "unit_cost": data.unit_cost,
        "total_cost": total_batch_cost,
        "base_cost_per_unit": oil.cost,
        "selling_price": oil.price,  # Remains unchanged
        "needs_restock": oil.stock <= oil.low_stock_threshold
    }

@router.post("/{oil_id}/upload-image")
async def upload_oil_image(
    oil_id: int, 
    file: UploadFile = File(...), 
    session: Session = Depends(get_session)
):
    oil = session.get(OilProduct, oil_id)
    if not oil:
        raise HTTPException(404, "Oil product not found")
        
    if not supabase_client:
        raise HTTPException(500, "Supabase storage client not configured")

    allowed_types = ["image/jpeg", "image/png", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(400, "Only JPEG, PNG, and WebP images are allowed")

    ext = file.filename.split(".")[-1]
    file_path = f"products/oil_{oil_id}_{uuid.uuid4().hex[:8]}.{ext}"

    file_bytes = await file.read()

    res = supabase_client.storage.from_("GAStoKITA").upload(
        path=file_path,
        file=file_bytes,
        file_options={"content-type": file.content_type}
    )

    public_url = f"{SUPABASE_URL}/storage/v1/object/public/GAStoKITA/{file_path}"

    oil.image_url = public_url
    oil.updated_at = datetime.now()
    session.add(oil)
    session.commit()
    session.refresh(oil)

    return {
        "message": "Image uploaded successfully",
        "oil_id": oil.id,
        "image_url": public_url
    }
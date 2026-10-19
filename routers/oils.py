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
    cost: float = 0
    low_stock_threshold: int = 5
    image_url: Optional[str] = None

class OilRestockRequest(BaseModel):
    quantity_added: int
    total_cost: float
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
def restock_oil(oil_id: int, data: OilRestockRequest, session: Session = Depends(get_session)):
    oil = session.get(OilProduct, oil_id)
    if not oil:
        raise HTTPException(404, "Oil product not found")
    if data.quantity_added <= 0:
        raise HTTPException(400, "Quantity must be positive")

    current_total_value = oil.stock * oil.cost
    new_total_value = current_total_value + data.total_cost
    new_total_stock = oil.stock + data.quantity_added

    if new_total_stock > 0:
        oil.cost = round(new_total_value / new_total_stock, 2)

    oil.stock = new_total_stock
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
        "weighted_avg_cost": oil.cost,
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
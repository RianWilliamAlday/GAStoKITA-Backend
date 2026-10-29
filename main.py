import os
from fastapi import FastAPIOffline
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from backend.routers import employees, fuels, inventory, auth, receipt, sales, analytics, expenses, oils, gcash
from db.database import create_db_and_tables
from pathlib import Path

@asynccontextmanager
async def lifespan(app: FastAPIOffline):
    create_db_and_tables()
    yield

app = FastAPIOffline(
    title="Backend",
    description="API for gas station app",
    version="1.8.5",
    lifespan=lifespan
)

HTML = os.path.join(os.path.dirname(__file__), "static")
UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

@app.get("/payment/success", response_class=FileResponse)
def payment_success():
    return FileResponse(os.path.join(HTML, "success.html"))

@app.get("/payment/failed", response_class=FileResponse)
def payment_failed():
    return FileResponse(os.path.join(HTML, "failed.html"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fuels.router)
app.include_router(inventory.router)
app.include_router(receipt.router)
app.include_router(auth.router)
app.include_router(sales.router)
app.include_router(analytics.router)
app.include_router(expenses.router)
app.include_router(oils.router)
app.include_router(employees.router)
app.include_router(gcash.router)
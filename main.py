from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from backend.routers import fuels, inventory, dispense, auth, sales, analytics, expenses, oils
from db.database import create_db_and_tables
from pathlib import Path

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield

app = FastAPI(
    title="Backend",
    description="API for gas station app",
    version="1.6.1",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fuels.router)
app.include_router(inventory.router)
app.include_router(dispense.router)
app.include_router(auth.router)
app.include_router(sales.router)
app.include_router(analytics.router)
app.include_router(expenses.router)
app.include_router(oils.router)
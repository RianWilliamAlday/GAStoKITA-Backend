from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select, func
from datetime import datetime, date, time
from typing import Optional, List
from db.database import get_session, Expense, RestockLog, OilRestockLog, Fuel, OilProduct, User
from models.schemas import UnifiedExpenseItem

router = APIRouter(prefix="/expenses", tags=["Expenses"])

@router.post("/")
def create_expense(category: str, amount: float, description: str = "", session: Session = Depends(get_session)):
    exp = Expense(category=category, amount=amount, description=description, expense_date=datetime.now())
    session.add(exp)
    session.commit()
    session.refresh(exp)
    return exp

@router.get("/", response_model=List[UnifiedExpenseItem])
def list_expenses(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    session: Session = Depends(get_session)
):
    unified_list: List[UnifiedExpenseItem] = []

    exp_stmt = select(Expense, User.name).outerjoin(User, Expense.recorded_by == User.id)
    if start_date:
        exp_stmt = exp_stmt.where(Expense.expense_date >= datetime.combine(start_date, time.min))
    if end_date:
        exp_stmt = exp_stmt.where(Expense.expense_date <= datetime.combine(end_date, time.max))
    
    for exp, user_name in session.exec(exp_stmt).all():
        unified_list.append(UnifiedExpenseItem(
            id=exp.id,
            source_type="operational",
            category=exp.category,
            description=exp.description or "General Expense",
            amount=exp.amount,
            expense_date=exp.expense_date,
            recorded_by=user_name or "",
            supplier=None
        ))

    fuel_stmt = select(RestockLog, Fuel.name).outerjoin(Fuel, RestockLog.fuel_id == Fuel.id)
    if start_date:
        fuel_stmt = fuel_stmt.where(RestockLog.restocked_at >= datetime.combine(start_date, time.min))
    if end_date:
        fuel_stmt = fuel_stmt.where(RestockLog.restocked_at <= datetime.combine(end_date, time.max))

    for log, fuel_name in session.exec(fuel_stmt).all():
        unified_list.append(UnifiedExpenseItem(
            id=log.id,
            source_type="fuel_restock",
            category="Fuel Inventory Restock",
            description=f"Restocked {log.liters_added}L of {fuel_name or 'Fuel'}",
            amount=log.cost,
            expense_date=log.restocked_at,
            recorded_by=log.restocked_by or "Admin",
            supplier=log.supplier or "N/A"
        ))

    oil_stmt = (
        select(OilRestockLog, OilProduct.brand, OilProduct.name, User.name)
        .outerjoin(OilProduct, OilRestockLog.oil_product_id == OilProduct.id)
        .outerjoin(User, OilRestockLog.restocked_by == User.id)
    )
    if start_date:
        oil_stmt = oil_stmt.where(OilRestockLog.restocked_at >= datetime.combine(start_date, time.min))
    if end_date:
        oil_stmt = oil_stmt.where(OilRestockLog.restocked_at >= datetime.combine(end_date, time.max))

    for log, brand, product_name, user_name in session.exec(oil_stmt).all():
        oil_title = f"{brand} {product_name}".strip() if brand or product_name else "Oil Product"

        is_initial = log.supplier == ""
        action_label = "Initial Stock" if is_initial else "Restocked"
        category_label = "Oil Initial Stock Purchase" if is_initial else "Oil Inventory Restock"

        unified_list.append(UnifiedExpenseItem(
            id=log.id,
            source_type="oil_restock",
            category=category_label,
            description=f"{action_label} {log.quantity_added} pcs of {oil_title}",
            amount=log.total_cost,
            expense_date=log.restocked_at,
            recorded_by=user_name or "Admin",
            supplier=log.supplier or "N/A"
        ))
    unified_list.sort(key=lambda x: x.expense_date, reverse=True)
    return unified_list

@router.get("/summary")
def get_unified_expenses_summary(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    session: Session = Depends(get_session)
):

    exp_filters = []
    fuel_filters = []
    oil_filters = []

    if start_date:
        exp_filters.append(Expense.expense_date >= datetime.combine(start_date, time.min))
        fuel_filters.append(RestockLog.restocked_at >= datetime.combine(start_date, time.min))
        oil_filters.append(OilRestockLog.restocked_at >= datetime.combine(start_date, time.min))

    if end_date:
        exp_filters.append(Expense.expense_date <= datetime.combine(end_date, time.max))
        fuel_filters.append(RestockLog.restocked_at <= datetime.combine(end_date, time.max))
        oil_filters.append(OilRestockLog.restocked_at <= datetime.combine(end_date, time.max))

    operational_total = session.exec(
        select(func.coalesce(func.sum(Expense.amount), 0.0)).where(*exp_filters)
    ).one()

    fuel_restock_total = session.exec(
        select(func.coalesce(func.sum(RestockLog.cost), 0.0)).where(*fuel_filters)
    ).one()

    oil_restock_total = session.exec(
        select(func.coalesce(func.sum(OilRestockLog.total_cost), 0.0)).where(*oil_filters)
    ).one()

    total_combined = operational_total + fuel_restock_total + oil_restock_total

    return {
        "period": f"{start_date or 'all'} to {end_date or 'all'}",
        "summary": {
            "operational_expenses": round(operational_total, 2),
            "fuel_restock_expenses": round(fuel_restock_total, 2),
            "oil_restock_expenses": round(oil_restock_total, 2),
            "total_expenses": round(total_combined, 2)
        }
    }

@router.delete("/{expense_id}")
def delete_expense(expense_id: int, session: Session = Depends(get_session)):
    exp = session.get(Expense, expense_id)
    if exp:
        session.delete(exp)
        session.commit()
    return {"ok": True}
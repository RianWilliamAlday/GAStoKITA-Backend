from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from datetime import datetime
from typing import List
from db.database import get_session, Expense

router = APIRouter(prefix="/expenses", tags=["Expenses"])

@router.post("/")
def create_expense(category: str, amount: float, description: str = "", session: Session = Depends(get_session)):
    exp = Expense(category=category, amount=amount, description=description, expense_date=datetime.now())
    session.add(exp)
    session.commit()
    session.refresh(exp)
    return exp

@router.get("/", response_model=List[Expense])
def list_expenses(session: Session = Depends(get_session)):
    return session.exec(select(Expense).order_by(Expense.expense_date.desc())).all()

@router.delete("/{expense_id}")
def delete_expense(expense_id: int, session: Session = Depends(get_session)):
    exp = session.get(Expense, expense_id)
    if exp:
        session.delete(exp)
        session.commit()
    return {"ok": True}
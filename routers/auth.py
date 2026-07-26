from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel, EmailStr
from db.database import get_session, User, UserRole
from services.auth import hash_password, verify_password, create_access_token, get_current_user, require_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])

class AdminLoginRequest(BaseModel):
    password: str

class EmployeeLoginRequest(BaseModel):
    email: EmailStr
    password: str

class CreateAccountRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: UserRole

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    name: str

@router.post("/admin/login", response_model=TokenResponse)
def admin_login(data: AdminLoginRequest, session: Session = Depends(get_session)):
    """Admin login: password only. Assumes only 1 admin exists."""
    admin = session.exec(select(User).where(User.role == UserRole.ADMIN)).first()
    if not admin:
        raise HTTPException(404, "Admin account not set up")

    if not verify_password(data.password, admin.password_hash):
        raise HTTPException(401, "Incorrect password")

    token = create_access_token({"sub": admin.id, "role": admin.role})
    return TokenResponse(access_token=token, role=admin.role, name=admin.name)

@router.post("/employee/login", response_model=TokenResponse)
def employee_login(data: EmployeeLoginRequest, session: Session = Depends(get_session)):
    """Employee login: email + password"""
    user = session.exec(select(User).where(User.email == data.email)).first()
    if not user or user.role!= UserRole.EMPLOYEE:
        raise HTTPException(401, "Invalid email or password")

    if not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")

    if not user.is_active:
        raise HTTPException(403, "Account disabled")

    token = create_access_token({"sub": user.id, "role": user.role})
    return TokenResponse(access_token=token, role=user.role, name=user.name)

@router.post("/create-account", response_model=TokenResponse)
def create_account(
    data: CreateAccountRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin)
):
    """Create new account. Admin only."""
    if data.role == UserRole.ADMIN:
        existing_admin = session.exec(select(User).where(User.role == UserRole.ADMIN)).first()
        if existing_admin:
            raise HTTPException(400, "Admin already exists")

    if data.role == UserRole.EMPLOYEE:
        if session.exec(select(User).where(User.email == data.email)).first():
            raise HTTPException(400, "Email already registered")

    user = User(
        name=data.name,
        email=data.email if data.role == UserRole.EMPLOYEE else None,
        password_hash=hash_password(data.password),
        role=data.role
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    token = create_access_token({"sub": user.id, "role": user.role})
    return TokenResponse(access_token=token, role=user.role, name=user.name)

@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "role": current_user.role
    }

@router.get("/debug/users")
def debug_users(session: Session = Depends(get_session)):
    from sqlmodel import select
    users = session.exec(select(User)).all()
    return [
        {"id": u.id, "name": u.name, "email": u.email, "role": str(u.role), "active": u.is_active}
        for u in users
    ]
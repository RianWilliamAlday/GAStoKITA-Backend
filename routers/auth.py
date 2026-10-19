from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel, EmailStr
from db.database import get_session, User, UserRole
from services.auth import hash_password, verify_password, create_access_token, get_current_user, require_admin
import os
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from dotenv import load_dotenv
load_dotenv()

router = APIRouter(prefix="/api/auth", tags=["auth"])

GOOGLE_CLIENT_ID = os.getenv("CLIENT_ID")

class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str

class EmployeeLoginRequest(BaseModel):
    email: EmailStr
    password: str

class GoogleLoginRequest(BaseModel):
    id_token: str

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
    user: dict | None = None

@router.post("/admin/login", response_model=TokenResponse)
def admin_login(data: AdminLoginRequest, session: Session = Depends(get_session)):
    admin = session.exec(
        select(User).where(User.email == data.email, User.role == UserRole.ADMIN)
    ).first()
    if not admin:
        raise HTTPException(401, "Invalid email or password")
    if not verify_password(data.password, admin.password_hash):
        raise HTTPException(401, "Invalid email or password")
    token = create_access_token({"sub": admin.id, "role": admin.role})
    return TokenResponse(
        access_token=token, 
        role=admin.role, 
        name=admin.name,
        user={"id": admin.id, "name": admin.name, "role": admin.role, "email": admin.email}
    )

@router.post("/employee/login", response_model=TokenResponse)
def employee_login(data: EmployeeLoginRequest, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.email == data.email)).first()
    if not user or user.role != UserRole.EMPLOYEE:
        raise HTTPException(401, "Invalid email or password")
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account disabled")
    token = create_access_token({"sub": user.id, "role": user.role})
    return TokenResponse(
        access_token=token, 
        role=user.role, 
        name=user.name,
        user={"id": user.id, "name": user.name, "email": user.email, "role": user.role}
    )

@router.post("/employee/google-login", response_model=TokenResponse)
def employee_google_login(data: GoogleLoginRequest, session: Session = Depends(get_session)):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GOOGLE_CLIENT_ID not configured on server")
    try:
        idinfo = google_id_token.verify_oauth2_token(
            data.id_token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
        
        email = idinfo.get("email")
        email_verified = idinfo.get("email_verified", False)
        name = idinfo.get("name") or idinfo.get("given_name") or email.split("@")[0]

        if not email or not email_verified:
            raise HTTPException(401, "Google email not verified")
        user = session.exec(select(User).where(User.email == email)).first()

        if not user:
            user = User(
                name=name,
                email=email,
                password_hash=hash_password(os.urandom(16).hex()),
                role=UserRole.EMPLOYEE,
                is_active=True
            )
            session.add(user)
            session.commit()
            session.refresh(user)

        if user.role != UserRole.EMPLOYEE:
            raise HTTPException(403, "This Google account is not an employee")
        if not user.is_active:
            raise HTTPException(403, "Account disabled")

        token = create_access_token({"sub": user.id, "role": user.role})
        return TokenResponse(
            access_token=token,
            role=user.role,
            name=user.name,
            user={"id": user.id, "name": user.name, "email": user.email, "role": user.role}
        )

    except ValueError as e:
        raise HTTPException(401, f"Invalid Google token: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[GOOGLE LOGIN ERROR] {e}")
        import traceback; traceback.print_exc()
        raise HTTPException(500, f"Google login failed: {str(e)}")


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(current_user: User = Depends(get_current_user)):
    new_token = create_access_token({"sub": current_user.id, "role": current_user.role})
    return TokenResponse(
        access_token=new_token,
        role=current_user.role,
        name=current_user.name,
        user={"id": current_user.id, "name": current_user.name, "email": current_user.email, "role": current_user.role}
    )

@router.post("/create-account", response_model=TokenResponse)
def create_account(
    data: CreateAccountRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin)
):
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
    users = session.exec(select(User)).all()
    return [
        {"id": u.id, "name": u.name, "email": u.email, "role": str(u.role), "active": u.is_active}
        for u in users
    ]

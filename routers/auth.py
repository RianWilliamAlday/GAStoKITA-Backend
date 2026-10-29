from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select
from pydantic import BaseModel, EmailStr
from db.database import get_session, User, UserRole
from services.auth import hash_password, verify_password, create_access_token, get_current_user, require_admin
import os, secrets, time, html
from urllib.parse import urlencode
import httpx
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from dotenv import load_dotenv
load_dotenv()

router = APIRouter(prefix="/api/auth", tags=["auth"])

GOOGLE_CLIENT_ID = os.getenv("CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "https://gastokita-backend.onrender.com/api/auth/google/callback")
# false = only emails already in the database can sign in with Google
# true  = any verified Google account gets an EMPLOYEE account automatically (old behavior)
ALLOW_GOOGLE_SIGNUP = os.getenv("ALLOW_GOOGLE_SIGNUP", "false").lower() == "true"

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

# ---------------------------------------------------------------------------
# Google helpers
# ---------------------------------------------------------------------------
# In-memory store for browser-based Google logins: state -> {"t", "result", "error"}
# (single-process only; use Redis/DB if you ever run multiple workers)
_pending: dict[str, dict] = {}
_PENDING_TTL = 300

def _purge_pending():
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["t"] > _PENDING_TTL]:
        _pending.pop(k, None)

_RESULT_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GAStoKITA - __TITLE__</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; flex-direction: column;
    font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    background: linear-gradient(160deg, #8B0000 0%, #A61E22 55%, #5c0000 100%);
    color: #fff;
  }
  header { padding: 18px 24px; font-size: 22px; font-weight: 700; letter-spacing: .5px; }
  header span { font-weight: 400; opacity: .85; font-size: 16px; margin-left: 8px; }
  main { flex: 1; display: flex; align-items: center; justify-content: center; padding: 24px; }
  .card {
    width: 100%; max-width: 420px; text-align: center; padding: 40px 28px;
    border-radius: 25px; background: rgba(255,255,255,.15);
    border: 1.2px solid rgba(255,255,255,.3);
    backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
    box-shadow: 0 10px 30px rgba(0,0,0,.25);
  }
  .icon {
    width: 76px; height: 76px; margin: 0 auto 20px; border-radius: 50%;
    background: #fff; color: __COLOR__; font-size: 44px; line-height: 76px; font-weight: 700;
  }
  h1 { margin: 0 0 10px; font-size: 26px; }
  p { margin: 0; font-size: 15px; line-height: 1.5; opacity: .95; }
  .detail { margin-top: 14px; padding: 10px 14px; border-radius: 12px; background: rgba(0,0,0,.25); font-size: 13px; }
  footer { padding: 14px; text-align: center; font-size: 12px; opacity: .8; }
</style>
</head>
<body>
  <main>
    <div class="card">
      <div class="icon">__ICON__</div>
      <h1>__TITLE__</h1>
      <p>__HINT__</p>
      __DETAIL__
    </div>
  </main>
  <script>__SCRIPT__</script>
</body>
</html>"""

def _result_page(ok: bool, detail: str = "") -> str:
    """Branded page shown in the browser after Google redirects back."""
    page = _RESULT_PAGE
    page = page.replace("__TITLE__", "Signed in" if ok else "Sign-in failed")
    page = page.replace("__ICON__", "&#10003;" if ok else "&#10007;")
    page = page.replace("__COLOR__", "#2e7d32" if ok else "#A61E22")
    page = page.replace(
        "__HINT__",
        "You can close this tab and return to the GAStoKITA app." if ok
        else "Close this tab and try again from the app."
    )
    page = page.replace(
        "__DETAIL__",
        f'<div class="detail">{html.escape(str(detail))}</div>' if (detail and not ok) else ""
    )
    # Browsers only allow this if the tab was opened by a script, so it may do nothing.
    page = page.replace("__SCRIPT__", "setTimeout(function(){ window.close(); }, 2500);" if ok else "")
    return page

def _token_for_google_user(email: str, name: str, session: Session, role: str = "employee") -> TokenResponse:
    if role == "admin":
        # Admins are never auto-created: the email must already belong to an ADMIN account
        admin = session.exec(
            select(User).where(User.email == email, User.role == UserRole.ADMIN)
        ).first()
        if not admin:
            raise HTTPException(403, "This Google account is not an admin")
        if not admin.is_active:
            raise HTTPException(403, "Account disabled")
        token = create_access_token({"sub": admin.id, "role": admin.role})
        return TokenResponse(
            access_token=token,
            role=admin.role,
            name=admin.name,
            user={"id": admin.id, "name": admin.name, "email": admin.email, "role": admin.role}
        )

    user = session.exec(select(User).where(User.email == email)).first()
    if not user:
        if not ALLOW_GOOGLE_SIGNUP:
            raise HTTPException(403, "No account for this Google email. Ask an admin to create one.")
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

# ---------------------------------------------------------------------------
# Password logins
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Google login
# ---------------------------------------------------------------------------
@router.post("/employee/google-login", response_model=TokenResponse)
def employee_google_login(data: GoogleLoginRequest, session: Session = Depends(get_session)):
    """Direct id_token verification (kept for clients that already have an id_token)."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GOOGLE_CLIENT_ID not configured on server")
    try:
        idinfo = google_id_token.verify_oauth2_token(
            data.id_token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
        email = idinfo.get("email")
        if not email or not idinfo.get("email_verified", False):
            raise HTTPException(401, "Google email not verified")
        name = idinfo.get("name") or idinfo.get("given_name") or email.split("@")[0]
        return _token_for_google_user(email, name, session)

    except ValueError as e:
        raise HTTPException(401, f"Invalid Google token: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[GOOGLE LOGIN ERROR] {e}")
        import traceback; traceback.print_exc()
        raise HTTPException(500, f"Google login failed: {str(e)}")

@router.get("/google/start")
def google_start(role: str = "employee"):
    """Step 1: the app calls this (role=employee or role=admin), then opens the returned URL in a browser."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(500, "Google OAuth not configured on server")
    if role not in ("employee", "admin"):
        raise HTTPException(400, "Invalid role")
    _purge_pending()
    state = secrets.token_urlsafe(24)
    _pending[state] = {"t": time.time(), "result": None, "error": None, "role": role}
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })
    return {"url": url, "state": state}

@router.get("/google/callback", response_class=HTMLResponse)
def google_callback(code: str = "", state: str = "", error: str = "",
                    session: Session = Depends(get_session)):
    """Step 2: Google redirects the browser here after sign-in."""
    entry = _pending.get(state)
    if not entry:
        return HTMLResponse(_result_page(False, "This login link expired."), status_code=400)
    try:
        if error or not code:
            raise HTTPException(400, error or "Google sign-in cancelled")
        r = httpx.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }, timeout=10)
        if r.status_code != 200:
            raise HTTPException(401, "Google token exchange failed")
        idinfo = google_id_token.verify_oauth2_token(
            r.json()["id_token"], google_requests.Request(), GOOGLE_CLIENT_ID
        )
        email = idinfo.get("email")
        if not email or not idinfo.get("email_verified"):
            raise HTTPException(401, "Google email not verified")
        name = idinfo.get("name") or email.split("@")[0]
        entry["result"] = _token_for_google_user(
            email, name, session, entry.get("role", "employee")
        ).model_dump()
        return HTMLResponse(_result_page(True))
    except HTTPException as e:
        entry["error"] = e.detail
        return HTMLResponse(_result_page(False, e.detail), status_code=e.status_code)
    except Exception as e:
        print(f"[GOOGLE CALLBACK ERROR] {e}")
        entry["error"] = "Google sign-in failed"
        return HTMLResponse(_result_page(False, "Something went wrong. Check the server logs."), status_code=500)

@router.get("/google/poll/{state}")
def google_poll(state: str):
    """Step 3: the app polls this until the login finishes."""
    entry = _pending.get(state)
    if not entry:
        raise HTTPException(404, "Unknown or expired login")
    if entry["error"]:
        _pending.pop(state, None)
        return {"status": "error", "detail": entry["error"]}
    if entry["result"]:
        _pending.pop(state, None)
        return {"status": "ok", **entry["result"]}
    return {"status": "pending"}

# ---------------------------------------------------------------------------
# Other
# ---------------------------------------------------------------------------
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
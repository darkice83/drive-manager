import bcrypt
from fastapi import Depends, HTTPException, status, Cookie
from app.database import SessionLocal
from app.models import User

def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_current_user(session_user_id: int = Cookie(None)):
    if not session_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == session_user_id).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return user
    finally:
        db.close()

def require_role(required_role: str):
    roles_hierarchy = {"user": 1, "wiper": 2, "admin": 3}
    def role_dependency(user: User = Depends(get_current_user)):
        user_level = roles_hierarchy.get(user.role, 0)
        required_level = roles_hierarchy.get(required_role, 99)
        if user_level < required_level:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Insufficient permissions. Required role: {required_role} or higher.")
        return user
    return role_dependency

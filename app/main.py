from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import datetime

from app.database import engine, Base, SessionLocal, get_db
from app.models import User, Drive, DriveHistory, DriveHealthLog
from app.storage import get_connected_drives_info, get_smart_data, start_wipe_task, start_format_task, verify_system_binaries
from app.udev_listener import start_udev_listener
from app.auth import get_password_hash, verify_password, require_role, get_current_user

Base.metadata.create_all(bind=engine)

app = FastAPI(title="SAS Drive Rig Manager")
templates = Jinja2Templates(directory="app/templates")

@app.on_event("startup")
def startup_event():
    verify_system_binaries()
    db = SessionLocal()
    try:
        running_tasks = db.query(DriveHistory).filter(DriveHistory.status == "running").all()
        for task in running_tasks:
            task.status = "interrupted"
            task.details = "Server restarted unexpectedly. Task interrupted."
            task.end_time = datetime.datetime.utcnow()
        db.commit()
    finally:
        db.close()
    start_udev_listener()

@app.get("/", response_class=HTMLResponse)
def read_dashboard(request: Request, db: Session = Depends(get_db)):
    if db.query(User).count() == 0:
        return RedirectResponse(url="/setup", status_code=303)
    user_id = request.cookies.get("session_user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "index.html", {"request": request, "user": user})

@app.get("/setup", response_class=HTMLResponse)
def setup_get(request: Request, db: Session = Depends(get_db)):
    if db.query(User).count() > 0:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "setup.html", {"request": request})

@app.post("/setup")
def setup_post(payload: dict, db: Session = Depends(get_db)):
    if db.query(User).count() > 0:
        raise HTTPException(status_code=400, detail="System already initialized.")
    username = payload.get("username")
    password = payload.get("password")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required.")
    admin_user = User(username=username, hashed_password=get_password_hash(password), role="admin")
    db.add(admin_user)
    db.commit()
    return {"success": True}

@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse(request, "login.html", {"request": request})

@app.post("/login")
def login_post(payload: dict, response: Response, db: Session = Depends(get_db)):
    username = payload.get("username")
    password = payload.get("password")
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    response.set_cookie(key="session_user_id", value=str(user.id), httponly=True)
    return {"success": True, "role": user.role}

@app.get("/logout")
def logout(response: Response):
    res = RedirectResponse(url="/login", status_code=303)
    res.delete_cookie(key="session_user_id")
    return res

@app.get("/api/users")
def api_list_users(user: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    return [{"id": u.id, "username": u.username, "role": u.role, "created_at": u.created_at} for u in db.query(User).all()]

@app.post("/api/users")
def api_create_user(payload: dict, admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    username = payload.get("username")
    temp_password = payload.get("password")
    role = payload.get("role", "user")
    if role not in ["admin", "wiper", "user"]:
        raise HTTPException(status_code=400, detail="Invalid role.")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username exists.")
    db.add(User(username=username, hashed_password=get_password_hash(temp_password), role=role))
    db.commit()
    return {"success": True}

@app.delete("/api/users/{user_id}")
def api_delete_user(user_id: int, admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account.")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    db.delete(target)
    db.commit()
    return {"success": True}

@app.get("/api/drives")
def api_list_drives(user: User = Depends(require_role("user")), db: Session = Depends(get_db)):
    hardware_drives = get_connected_drives_info()
    enriched_drives = []
    for hw in hardware_drives:
        serial = hw["serial"]
        db_drive = db.query(Drive).filter(Drive.serial_number == serial).first()
        history_list = []
        health_logs = []
        if db_drive:
            histories = db.query(DriveHistory).filter(DriveHistory.drive_id == db_drive.id).order_by(DriveHistory.start_time.desc()).all()
            for h in histories:
                history_list.append({
                    "id": h.id, "action": h.action, "status": h.status, "progress": h.progress,
                    "details": h.details,
                    "partition_table_before": h.partition_table_before,
                    "filesystem_before": h.filesystem_before,
                    "partition_table_after": h.partition_table_after,
                    "filesystem_after": h.filesystem_after,
                    "duration_seconds": h.duration_seconds,
                    "speed_mb_s": h.speed_mb_s,
                    "start_time": h.start_time.strftime("%Y-%m-%d %H:%M:%S") if h.start_time else "",
                    "end_time": h.end_time.strftime("%Y-%m-%d %H:%M:%S") if h.end_time else ""
                })
            logs = db.query(DriveHealthLog).filter(DriveHealthLog.drive_id == db_drive.id).order_by(DriveHealthLog.logged_at.desc()).limit(10).all()
            for l in logs:
                health_logs.append({
                    "logged_at": l.logged_at.strftime("%Y-%m-%d %H:%M"),
                    "temperature": l.temperature,
                    "power_on_hours": l.power_on_hours,
                    "reallocated_sectors": l.reallocated_sectors,
                    "health_status": l.health_status
                })
        enriched_drives.append({
            "name": hw["name"], "path": hw["path"], "model": hw["model"],
            "serial": serial, "size": hw["size"], "transport": hw["transport"],
            "is_mounted": hw["is_mounted"], "history": history_list, "health_logs": health_logs
        })
    return JSONResponse(enriched_drives)

@app.get("/api/drives/{serial}/smart")
def api_get_smart(serial: str, user: User = Depends(require_role("user")), db: Session = Depends(get_db)):
    hardware_drives = get_connected_drives_info()
    target = next((hw for hw in hardware_drives if hw["serial"] == serial), None)
    if not target:
        raise HTTPException(status_code=404, detail="Drive not found.")
    return {"smart": get_smart_data(target["path"], serial, target["model"])}

@app.post("/api/drives/wipe")
def api_wipe_drive(payload: dict, user: User = Depends(require_role("wiper")), db: Session = Depends(get_db)):
    serial = payload.get("serial")
    path = payload.get("path")
    if not serial or not path:
        raise HTTPException(status_code=400, detail="Missing serial or path.")
    hardware_drives = get_connected_drives_info()
    target_drive = next((d for d in hardware_drives if d["path"] == path), None)
    if not target_drive or target_drive["is_mounted"]:
        raise HTTPException(status_code=400, detail="Invalid or mounted drive slot.")
    
    db_drive = db.query(Drive).filter(Drive.serial_number == serial).first()
    if not db_drive:
        db_drive = Drive(serial_number=serial, model="Unknown", size="Unknown")
        db.add(db_drive)
        db.commit()
        db.refresh(db_drive)
    
    history = DriveHistory(drive_id=db_drive.id, action="wipe", status="running", progress=0, start_time=datetime.datetime.utcnow())
    db.add(history)
    db.commit()
    db.refresh(history)
    start_wipe_task(history.id, path)
    return {"success": True, "task_id": history.id}

@app.post("/api/drives/format")
def api_format_drive(payload: dict, user: User = Depends(require_role("wiper")), db: Session = Depends(get_db)):
    serial = payload.get("serial")
    path = payload.get("path")
    if not serial or not path:
        raise HTTPException(status_code=400, detail="Missing serial or path.")
    hardware_drives = get_connected_drives_info()
    target_drive = next((d for d in hardware_drives if d["path"] == path), None)
    if not target_drive or target_drive["is_mounted"]:
        raise HTTPException(status_code=400, detail="Invalid or mounted drive slot.")
    
    db_drive = db.query(Drive).filter(Drive.serial_number == serial).first()
    if not db_drive:
        db_drive = Drive(serial_number=serial, model="Unknown", size="Unknown")
        db.add(db_drive)
        db.commit()
        db.refresh(db_drive)
    
    history = DriveHistory(drive_id=db_drive.id, action="format (512B & Strip PI)", status="running", progress=10, start_time=datetime.datetime.utcnow())
    db.add(history)
    db.commit()
    db.refresh(history)
    start_format_task(history.id, path)
    return {"success": True, "task_id": history.id}

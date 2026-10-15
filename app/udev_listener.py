import threading
import pyudev
import datetime
from app.database import SessionLocal
from app.models import Drive
from app.storage import get_connected_drives_info

def sync_drives_to_db():
    db = SessionLocal()
    try:
        drives = get_connected_drives_info()
        for d in drives:
            serial = d["serial"]
            if serial.startswith("UNKNOWN"):
                continue
            drive = db.query(Drive).filter(Drive.serial_number == serial).first()
            if drive:
                drive.last_seen = datetime.datetime.utcnow()
                drive.model = d["model"]
                drive.size = d["size"]
            else:
                new_drive = Drive(serial_number=serial, model=d["model"], size=d["size"])
                db.add(new_drive)
        db.commit()
    except Exception as e:
        print(f"Error syncing drives: {e}")
    finally:
        db.close()

def udev_monitoring_loop():
    try:
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_context(context)
        monitor.filter_by(subsystem='block', devtype='disk')
        sync_drives_to_db()
        for device in iter(monitor.poll, None):
            if device.action in ['add', 'change']:
                sync_drives_to_db()
    except Exception as e:
        print(f"Udev monitor error: {e}")

def start_udev_listener():
    t = threading.Thread(target=udev_monitoring_loop, daemon=True)
    t.start()

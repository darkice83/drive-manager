import os
import shutil
import subprocess
import threading
import datetime
import json
import time
import re
from app.database import SessionLocal
from app.models import Drive, DriveHealthLog, DriveHistory

def get_binary_path(binary_name: str):
    path = shutil.which(binary_name)
    if path:
        return path
    
    fallback_paths = {
        "smartctl": "/usr/sbin/smartctl",
        "blockdev": "/sbin/blockdev",
        "lsblk": "/usr/bin/lsblk",
        "sg_format": "/usr/bin/sg_format"
    }
    
    if binary_name in fallback_paths and os.path.exists(fallback_paths[binary_name]):
        return fallback_paths[binary_name]
            
    return binary_name

def verify_system_binaries():
    required = ["lsblk", "smartctl", "blockdev", "sg_format"]
    missing = [b for b in required if not get_binary_path(b)]
    if missing:
        print(f"\n[CRITICAL WARNING] Missing system binaries: {', '.join(missing)}")
        print("Please run: sudo apt install smartmontools util-linux sg3-utils\n")
    else:
        print("[INFO] All required storage binaries are available.")

def check_drive_format_needs(device_path: str):
    try:
        smartctl_bin = get_binary_path("smartctl")
        result = subprocess.run([smartctl_bin, "-i", device_path], capture_output=True, text=True, timeout=3)
        output = result.stdout
        
        needs_formatting = False
        reasons = []
        
        # Check logical block size
        lb_match = re.search(r'Logical block size:\s+(\d+)\s+bytes', output)
        if lb_match:
            lb_size = int(lb_match.group(1))
            if lb_size != 512:
                needs_formatting = True
                reasons.append(f"{lb_size}B Sectors")
        
        # Check protection information / DIP
        if "protection information" in output.lower() or "formatted with type" in output.lower():
            pi_match = re.search(r'(\d+)\s+bytes of protection information', output)
            if pi_match and int(pi_match.group(1)) > 0:
                needs_formatting = True
                reasons.append("DIP Enabled")
            elif "Formatted with type" in output and not "Formatted with type 0" in output:
                needs_formatting = True
                reasons.append("DIP Protected")
                
        return needs_formatting, ", ".join(reasons) if reasons else "Standard 512B"
    except Exception:
        return False, "Standard"

def get_connected_drives_info():
    try:
        lsblk_bin = get_binary_path("lsblk")
        cmd = [lsblk_bin, "-J", "-o", "NAME,SIZE,MODEL,SERIAL,TRAN,TYPE,PATH,ROTA,STATE,MOUNTPOINTS"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
        data = json.loads(result.stdout)
        drives = []

        for device in data.get("blockdevices", []):
            if device.get("type") == "disk":
                name = device.get("name", "")
                if name.startswith("loop") or name.startswith("ram"):
                    continue

                is_mounted = False
                mountpoints = device.get("mountpoints")
                if mountpoints and any(mountpoints):
                    is_mounted = True

                for child in device.get("children", []):
                    child_mps = child.get("mountpoints")
                    if child_mps and any(child_mps):
                        is_mounted = True

                serial = device.get("serial")
                if not serial:
                    serial = f"UNKNOWN_{name}"

                path = device.get("path", f"/dev/{name}")
                needs_fmt, fmt_reason = check_drive_format_needs(path)

                drives.append({
                    "name": name,
                    "path": path,
                    "size": device.get("size", "Unknown"),
                    "model": device.get("model", "Unknown Model").strip(),
                    "serial": serial.strip(),
                    "transport": device.get("tran", "unknown"),
                    "rotational": device.get("rota", True),
                    "state": device.get("state", "running"),
                    "is_mounted": is_mounted,
                    "needs_formatting": needs_fmt,
                    "format_reason": fmt_reason
                })
        return drives
    except Exception as e:
        print(f"Error fetching block devices: {e}")
        return []

def get_drive_partition_info(device_path: str):
    try:
        lsblk_bin = get_binary_path("lsblk")
        pttype_res = subprocess.run([lsblk_bin, "-no", "PTTYPE", device_path], capture_output=True, text=True, timeout=3)
        pttype = pttype_res.stdout.strip()
        pttype_display = pttype.upper() if pttype else "RAW (No Partition Table)"

        fs_res = subprocess.run([lsblk_bin, "-no", "FSTYPE", device_path], capture_output=True, text=True, timeout=3)
        fstypes = [f.strip() for f in fs_res.stdout.splitlines() if f.strip()]
        fstype_display = ", ".join(sorted(list(set(fstypes)))) if fstypes else "None"

        return {
            "pttype": pttype_display,
            "fstype": fstype_display
        }
    except Exception:
        return {"pttype": "Unknown", "fstype": "Unknown"}

def parse_and_log_smart_metrics(serial: str, model: str, output: str):
    db = SessionLocal()
    try:
        drive = db.query(Drive).filter(Drive.serial_number == serial).first()
        if not drive:
            drive = Drive(serial_number=serial, model=model, size="Unknown")
            db.add(drive)
            db.commit()
            db.refresh(drive)

        temp = None
        poh = None
        reallocated = 0
        health = "PASSED"

        if "PASSED" in output or "SMART Health Status: OK" in output:
            health = "PASSED"
        elif "FAILS" in output or "FAILED" in output:
            health = "FAILED"

        temp_match = re.search(r'Current Temperature:\s+(\d+)\s+Celsius', output)
        if not temp_match:
            temp_match = re.search(r'Current Drive Temperature:\s+(\d+)\s+C', output)
        if not temp_match:
            temp_match = re.search(r'194 Temperature_Celsius[^\n]*?-\s+(\d+)', output)
        if temp_match:
            temp = int(temp_match.group(1))

        poh_match = re.search(r'0x01\s+0x010\s+\d+\s+(\d+)', output)
        if not poh_match:
            poh_match = re.search(r'Accumulated power on time, hours:minutes\s+(\d+):', output)
        if not poh_match:
            poh_match = re.search(r'9 Power_On_Hours[^\n]*?-\s+(\d+)', output)
        if poh_match:
            poh = int(poh_match.group(1))

        realloc_match = re.search(r'5 Reallocated_Sector_Ct[^\n]*?-\s+(\d+)', output)
        if not realloc_match:
            realloc_match = re.search(r'Number of Reallocated Logical Sectors[^\n]*?-\s+(\d+)', output)
        if realloc_match:
            reallocated = int(realloc_match.group(1))

        last_log = db.query(DriveHealthLog).filter(DriveHealthLog.drive_id == drive.id).order_by(DriveHealthLog.logged_at.desc()).first()
        if not last_log or last_log.power_on_hours != poh or last_log.temperature != temp or (datetime.datetime.utcnow() - last_log.logged_at).total_seconds() > 3600:
            new_log = DriveHealthLog(
                drive_id=drive.id,
                temperature=temp,
                power_on_hours=poh,
                reallocated_sectors=reallocated,
                health_status=health
            )
            db.add(new_log)
            db.commit()
    except Exception as e:
        print(f"Error logging SMART metrics: {e}")
    finally:
        db.close()

def get_smart_data(device_path: str, serial: str, model: str):
    db = SessionLocal()
    try:
        drive = db.query(Drive).filter(Drive.serial_number == serial).first()
        if drive:
            active_wipe = db.query(DriveHistory).filter(
                DriveHistory.drive_id == drive.id,
                DriveHistory.status == "running"
            ).first()
            if active_wipe:
                return f"[BUSY] Drive is currently executing a task ({active_wipe.action} - Progress: {active_wipe.progress}%).\nSMART diagnostics are temporarily locked during operations."
                
        smartctl_bin = get_binary_path("smartctl")
        cmd = [smartctl_bin, "-x", device_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        output = result.stdout
        parse_and_log_smart_metrics(serial, model, output)
        return output
    except Exception as e:
        return f"Drive is busy or unable to fetch SMART data during active operations: {str(e)}"
    finally:
        db.close()

ACTIVE_TASKS = {}

def run_wipe_worker(task_id: int, device_path: str, total_size_bytes: int):
    db = SessionLocal()
    start_time_perf = time.time()
    try:
        before_info = get_drive_partition_info(device_path)
        task = db.query(DriveHistory).filter(DriveHistory.id == task_id).first()
        if task:
            task.partition_table_before = before_info["pttype"]
            task.filesystem_before = before_info["fstype"]
            db.commit()

        chunk_size = 1024 * 1024 * 32
        written = 0
        with open(device_path, "wb", buffering=0) as f:
            zero_chunk = b"\x00" * chunk_size
            while written < total_size_bytes:
                if task_id in ACTIVE_TASKS and ACTIVE_TASKS[task_id].get("cancel"):
                    break
                to_write = min(chunk_size, total_size_bytes - written)
                f.write(zero_chunk[:to_write])
                written += to_write

                progress = int((written / total_size_bytes) * 100)
                task = db.query(DriveHistory).filter(DriveHistory.id == task_id).first()
                if task:
                    task.progress = progress
                    db.commit()

        end_time = datetime.datetime.utcnow()
        elapsed_secs = max(0.1, time.time() - start_time_perf)
        speed_mb = (written / (1024 * 1024)) / elapsed_secs
        after_info = get_drive_partition_info(device_path)

        task = db.query(DriveHistory).filter(DriveHistory.id == task_id).first()
        if task:
            task.status = "completed" if task.progress >= 100 else "interrupted"
            task.end_time = end_time
            task.duration_seconds = round(elapsed_secs, 2)
            task.speed_mb_s = round(speed_mb, 2)
            task.partition_table_after = after_info["pttype"]
            task.filesystem_after = after_info["fstype"]
            db.commit()
    except Exception as e:
        end_time = datetime.datetime.utcnow()
        elapsed_secs = time.time() - start_time_perf
        task = db.query(DriveHistory).filter(DriveHistory.id == task_id).first()
        if task:
            task.status = "failed"
            task.details = str(e)
            task.end_time = end_time
            task.duration_seconds = round(elapsed_secs, 2)
            db.commit()
    finally:
        db.close()
        if task_id in ACTIVE_TASKS:
            del ACTIVE_TASKS[task_id]

def run_format_worker(task_id: int, device_path: str):
    db = SessionLocal()
    start_time_perf = time.time()
    try:
        before_info = get_drive_partition_info(device_path)
        task = db.query(DriveHistory).filter(DriveHistory.id == task_id).first()
        if task:
            task.partition_table_before = before_info["pttype"]
            task.filesystem_before = before_info["fstype"]
            task.progress = 50
            db.commit()

        sg_format_bin = get_binary_path("sg_format")
        cmd = [sg_format_bin, "--format", "--size=512", "--fmtpinfo=0", "-v", device_path]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        
        elapsed_secs = max(0.1, time.time() - start_time_perf)
        after_info = get_drive_partition_info(device_path)

        task = db.query(DriveHistory).filter(DriveHistory.id == task_id).first()
        if task:
            if res.returncode == 0:
                task.status = "completed"
                task.progress = 100
                task.details = "Successfully formatted to 512 bytes and removed DIP protection."
            else:
                task.status = "failed"
                task.progress = 100
                task.details = res.stderr.strip() or res.stdout.strip() or "sg_format failed."
            
            task.end_time = datetime.datetime.utcnow()
            task.duration_seconds = round(elapsed_secs, 2)
            task.partition_table_after = after_info["pttype"]
            task.filesystem_after = after_info["fstype"]
            db.commit()
    except Exception as e:
        end_time = datetime.datetime.utcnow()
        elapsed_secs = time.time() - start_time_perf
        task = db.query(DriveHistory).filter(DriveHistory.id == task_id).first()
        if task:
            task.status = "failed"
            task.details = str(e)
            task.end_time = end_time
            task.duration_seconds = round(elapsed_secs, 2)
            db.commit()
    finally:
        db.close()
        if task_id in ACTIVE_TASKS:
            del ACTIVE_TASKS[task_id]

def start_wipe_task(task_id: int, device_path: str):
    try:
        blockdev_bin = get_binary_path("blockdev")
        res = subprocess.run([blockdev_bin, "--getsz", device_path], capture_output=True, text=True, check=True)
        sectors = int(res.stdout.strip())
        total_bytes = sectors * 512
    except Exception:
        total_bytes = 1024 * 1024 * 1024 * 500

    thread = threading.Thread(target=run_wipe_worker, args=(task_id, device_path, total_bytes))
    ACTIVE_TASKS[task_id] = {"thread": thread, "cancel": False}
    thread.start()

def start_format_task(task_id: int, device_path: str):
    thread = threading.Thread(target=run_format_worker, args=(task_id, device_path))
    ACTIVE_TASKS[task_id] = {"thread": thread, "cancel": False}
    thread.start()

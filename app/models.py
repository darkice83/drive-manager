import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False, default="user")
    must_change_password = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Drive(Base):
    __tablename__ = "drives"
    id = Column(Integer, primary_key=True, index=True)
    serial_number = Column(String, unique=True, index=True, nullable=False)
    model = Column(String, nullable=True)
    size = Column(String, nullable=True)
    first_seen = Column(DateTime, default=datetime.datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.datetime.utcnow)
    histories = relationship("DriveHistory", back_populates="drive", cascade="all, delete-orphan")
    health_logs = relationship("DriveHealthLog", back_populates="drive", cascade="all, delete-orphan")

class DriveHistory(Base):
    __tablename__ = "drive_histories"
    id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("drives.id"), nullable=False)
    action = Column(String, nullable=False)
    status = Column(String, nullable=False)
    progress = Column(Integer, default=0)
    details = Column(Text, nullable=True)
    partition_table_before = Column(String, nullable=True)
    filesystem_before = Column(String, nullable=True)
    partition_table_after = Column(String, nullable=True)
    filesystem_after = Column(String, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    speed_mb_s = Column(Float, nullable=True)
    start_time = Column(DateTime, default=datetime.datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    drive = relationship("Drive", back_populates="histories")

class DriveHealthLog(Base):
    __tablename__ = "drive_health_logs"
    id = Column(Integer, primary_key=True, index=True)
    drive_id = Column(Integer, ForeignKey("drives.id"), nullable=False)
    temperature = Column(Integer, nullable=True)
    power_on_hours = Column(Integer, nullable=True)
    reallocated_sectors = Column(Integer, nullable=True)
    health_status = Column(String, nullable=True)
    logged_at = Column(DateTime, default=datetime.datetime.utcnow)
    drive = relationship("Drive", back_populates="health_logs")

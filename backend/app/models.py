from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, BigInteger, DateTime, Text, JSON, ForeignKey
from datetime import datetime, timezone

class Base(DeclarativeBase): pass

def utcnow():
    return datetime.now(timezone.utc)

class Lot(Base):
    __tablename__ = "lots"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    total_spaces: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    lot_id: Mapped[str] = mapped_column(String, ForeignKey("lots.id", ondelete="CASCADE"))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Detection(Base):
    __tablename__ = "detections"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str | None] = mapped_column(String, ForeignKey("devices.id", ondelete="SET NULL"))
    lot_id: Mapped[str] = mapped_column(String, ForeignKey("lots.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    occupied_count: Mapped[int] = mapped_column(Integer)
    total_spaces: Mapped[int] = mapped_column(Integer)
    inference_ms: Mapped[int | None] = mapped_column(Integer)
    battery_pct: Mapped[float | None]
    temp_c: Mapped[float | None]

class OccupancySnapshot(Base):
    __tablename__ = "occupancy_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lot_id: Mapped[str] = mapped_column(String, ForeignKey("lots.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    occupied: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)

class Forecast(Base):
    __tablename__ = "forecasts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lot_id: Mapped[str] = mapped_column(String, ForeignKey("lots.id", ondelete="CASCADE"))
    asof: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    horizon_hours: Mapped[int] = mapped_column(Integer)
    series: Mapped[dict] = mapped_column(JSON)  # [{ts, occupied, available}]
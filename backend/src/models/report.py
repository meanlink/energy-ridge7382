from sqlalchemy import String, Double, ForeignKey, Boolean, JSON, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
class Report(Base):
    __tablename__ = 'reports'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    destination: Mapped[str] = mapped_column(String(120))
    interval_seconds: Mapped[int]
    next_run: Mapped[float] = mapped_column(Double)
    # Next due period sequence. Bumped atomically with the Delivery claim so a
    # crash can never make the same scheduled slot run twice.
    period: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
class Delivery(Base):
    """A registered archive for exactly one (report, period) due slot.

    The row is the durable claim: it is written before any archive file.  Its
    snapshot columns freeze the analysis (params + result) that was approved
    when the period became due, so later retries/repairs never mix in newer
    analysis results.
    """
    __tablename__ = 'deliveries'
    __table_args__ = (UniqueConstraint('report_id', 'period', name='uq_delivery_report_period'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey('reports.id'))
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    due_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    report_name: Mapped[str] = mapped_column(String(160), default='')
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_name: Mapped[str] = mapped_column(String(160), default='')
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    snapshot_checksum: Mapped[str] = mapped_column(String(64), default='')
    approved_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    created_at: Mapped[float] = mapped_column(Double)
    completed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    # Manifest fingerprint (sha256 of "csv_checksum:json_checksum") and the two
    # per-file fingerprints.  Empty until both files have been verified.
    checksum: Mapped[str] = mapped_column(String(64), default='')
    csv_checksum: Mapped[str] = mapped_column(String(64), default='')
    json_checksum: Mapped[str] = mapped_column(String(64), default='')
    csv_size: Mapped[int] = mapped_column(Integer, default=0)
    json_size: Mapped[int] = mapped_column(Integer, default=0)
    filename: Mapped[str] = mapped_column(String(160), default='')
    destination: Mapped[str] = mapped_column(String(120))
    repair_count: Mapped[int] = mapped_column(Integer, default=0)
    repaired_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    last_error: Mapped[str] = mapped_column(String(1000), default='')

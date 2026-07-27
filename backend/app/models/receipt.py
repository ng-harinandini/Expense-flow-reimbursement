"""SQLAlchemy 2.x models for the receipts domain.

Schema is owned by Alembic — these declarations are the source the initial migration is
authored from. The application never creates or alters these tables at runtime.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ExtractionStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# Named PG enum type; created/dropped by Alembic (create_type controlled in the migration).
extraction_status_enum = SAEnum(
    ExtractionStatus,
    name="extraction_status",
    values_callable=lambda e: [m.value for m in e],
)


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # S3 location of the original document (nullable when stored via fallback).
    s3_bucket: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_region: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    employee_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)

    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        extraction_status_enum,
        nullable=False,
        default=ExtractionStatus.PENDING,
        index=True,
    )
    extraction_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # textract|fallback

    raw_textract: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    normalized_extraction: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    vendor_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transaction_date: Mapped[Optional[date]] = mapped_column(nullable=True)
    total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    fields: Mapped[List["ReceiptField"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", passive_deletes=True
    )
    line_items: Mapped[List["ReceiptLineItem"]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ReceiptLineItem.line_number",
    )


class ReceiptField(Base):
    """AnalyzeExpense SummaryFields (vendor, date, total, tax, etc.)."""

    __tablename__ = "receipt_fields"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("receipts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    field_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    field_label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    field_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)

    receipt: Mapped["Receipt"] = relationship(back_populates="fields")


class ReceiptLineItem(Base):
    __tablename__ = "receipt_line_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("receipts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    line_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric, nullable=True)
    unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    raw: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    receipt: Mapped["Receipt"] = relationship(back_populates="line_items")

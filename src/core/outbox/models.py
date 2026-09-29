from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum as SQLEnum, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base
from src.core.database.mixins import TimestampMixin, UUID7IDMixin
from src.core.outbox.enums import OutboxMessageStatus


class OutboxMessage(Base, UUID7IDMixin, TimestampMixin):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        # The sweeper's only hot query: pending rows in FIFO order.
        Index(
            "ix_outbox_messages_pending_created_at",
            "created_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    # The insert's own clock, not now(): now() is the start of the enqueuing
    # transaction, and a row from a long transaction would look older than
    # SWEEPER_GRACE the moment it commits, while its own publish still runs.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.clock_timestamp()
    )
    task_name: Mapped[str] = mapped_column(String(255))
    args: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    kwargs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[OutboxMessageStatus] = mapped_column(
        SQLEnum(OutboxMessageStatus), default=OutboxMessageStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

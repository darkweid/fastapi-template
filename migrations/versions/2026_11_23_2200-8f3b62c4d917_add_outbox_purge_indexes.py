"""add outbox purge indexes

Revision ID: 8f3b62c4d917
Revises: 5c1d7e90ab42
Create Date: 2026-11-23 22:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "8f3b62c4d917"
down_revision: str | Sequence[str] | None = "5c1d7e90ab42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_outbox_messages_published_at"),
        "outbox_messages",
        ["published_at"],
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.create_index(
        op.f("ix_outbox_messages_updated_at"),
        "outbox_messages",
        ["updated_at"],
        postgresql_where=sa.text("status = 'FAILED'"),
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_outbox_messages_updated_at"), table_name="outbox_messages")
    op.drop_index(op.f("ix_outbox_messages_published_at"), table_name="outbox_messages")

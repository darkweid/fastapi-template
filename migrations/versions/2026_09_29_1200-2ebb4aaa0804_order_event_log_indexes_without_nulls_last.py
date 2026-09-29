"""order event log indexes without nulls last

Revision ID: 2ebb4aaa0804
Revises: f704e41d6bd8
Create Date: 2026-09-29 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2ebb4aaa0804"
down_revision: str | Sequence[str] | None = "f704e41d6bd8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ListQuery no longer adds NULLS LAST on a NOT NULL column, and an index spelled
# `created_at DESC NULLS LAST` does not match `ORDER BY created_at DESC`.
INDEXES = {
    "ix_event_logs_actor": ["actor_type", "actor_id"],
    "ix_event_logs_object": ["object_type", "object_id"],
    "ix_event_logs_event_type": ["event_type"],
    "ix_event_logs_created_at": [],
}


def upgrade() -> None:
    for name, leading in INDEXES.items():
        op.drop_index(name, table_name="event_logs")
        op.create_index(name, "event_logs", [*leading, "created_at", "id"])


def downgrade() -> None:
    for name, leading in INDEXES.items():
        op.drop_index(name, table_name="event_logs")
        op.create_index(
            name,
            "event_logs",
            [
                *leading,
                sa.literal_column("created_at DESC NULLS LAST"),
                sa.literal_column("id DESC"),
            ],
        )

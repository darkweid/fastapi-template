"""add notes owner list index

Revision ID: 5c1d7e90ab42
Revises: 2ebb4aaa0804
Create Date: 2026-11-23 19:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c1d7e90ab42"
down_revision: str | Sequence[str] | None = "2ebb4aaa0804"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_notes_owner_created_at", "notes", ["owner_id", "created_at", "id"]
    )
    # Redundant: the new index starts with the same column.
    op.drop_index("ix_notes_owner_id", table_name="notes")


def downgrade() -> None:
    op.create_index("ix_notes_owner_id", "notes", ["owner_id"])
    op.drop_index("ix_notes_owner_created_at", table_name="notes")

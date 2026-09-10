"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports + "\n" if imports else ""}\

# revision identifiers, used by Alembic.
revision: str = "${up_revision}"
down_revision: str | None = ${'"%s"' % down_revision if down_revision else "None"}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}

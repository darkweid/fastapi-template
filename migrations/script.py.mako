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
<%!
def as_literal(value):
    """repr() with double quotes, so black leaves a fresh revision alone.

    A merge revision's down_revision is a sequence of parents, not a string;
    rendering it with %s would name one revision that does not exist and the
    merge would never find its branches.
    """
    if value is None:
        return "None"
    if isinstance(value, str):
        return '"%s"' % value
    return "(%s)" % "".join('"%s", ' % item for item in value).rstrip(", ")
%>\
revision: str = "${up_revision}"
down_revision: str | Sequence[str] | None = ${as_literal(down_revision)}
branch_labels: str | Sequence[str] | None = ${as_literal(branch_labels)}
depends_on: str | Sequence[str] | None = ${as_literal(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}

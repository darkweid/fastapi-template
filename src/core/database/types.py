from collections.abc import Sequence

from sqlalchemy.orm.strategy_options import _AbstractLoad as AbstractLoad  # noqa SLF001

# Shared aliases for eager loading collections to avoid repeating private SQLAlchemy types.
EagerLoad = AbstractLoad
EagerLoadSequence = Sequence[EagerLoad]

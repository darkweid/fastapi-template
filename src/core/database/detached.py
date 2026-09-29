from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import instance_state
from sqlalchemy.orm.collections import collection_adapter


class DetachedRead:
    """The instances a `detached_read` block hands out of the session."""

    def __init__(self) -> None:
        self.instances: list[object] = []

    def detach(self, *instances: object | None) -> None:
        """Mark instances to leave the session when the block ends, together
        with everything eager-loaded under them. `None` is accepted so a
        missing row needs no branch at the call site."""
        self.instances.extend(
            instance for instance in instances if instance is not None
        )


def _loaded_graph(roots: Iterable[object]) -> Iterator[object]:
    """Every instance reachable from the roots through relationships already
    loaded; an unloaded one is never touched, so nothing lazy-loads here."""
    seen: set[int] = set()
    stack = list(roots)
    while stack:
        instance = stack.pop()
        if id(instance) in seen:
            continue
        seen.add(id(instance))
        yield instance
        state = instance_state(instance)
        for relationship in state.mapper.relationships:
            value = state.dict.get(relationship.key)
            if value is None:
                continue
            if relationship.uselist:
                # Through the adapter, since iterating a dict-shaped collection
                # (`attribute_keyed_dict`) yields its keys, not its members.
                stack.extend(collection_adapter(value) or ())
            else:
                stack.append(value)


@asynccontextmanager
async def detached_read(session: AsyncSession) -> AsyncIterator[DetachedRead]:
    """A read that leaves neither a transaction nor tracked instances behind.

    The request session is idle once the auth dependency has run
    (`load_principal`). A dependency that reads after it autobegins a
    transaction nothing else ends before the response, so the handler's whole
    wait - an upload, a third-party call - would hold a pooled connection idle
    in transaction. The block rolls back the transaction when it opened it, on
    success and on a raised 404 alike; a transaction that was already open is
    left to whoever opened it.

    That rollback expires every instance the session holds, so what the block
    detaches leaves the session first, each instance of an eager-loaded graph
    on its own: `expunge` does not cascade along a relationship that lacks the
    `expunge` cascade. The instances stay readable for the rest of the request,
    whatever a later unit of work commits or rolls back; a write re-reads the
    row inside its own unit of work instead of mutating them. An instance the
    session does not hold (transient, or already detached) passes through.
    """
    owns_transaction = not session.in_transaction()
    read = DetachedRead()
    try:
        yield read
    finally:
        try:
            for instance in _loaded_graph(read.instances):
                if instance_state(instance).persistent:
                    session.expunge(instance)
        finally:
            if owns_transaction:
                await session.rollback()

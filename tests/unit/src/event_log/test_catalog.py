import importlib
from pathlib import Path
import pkgutil
import re

import src
from src.event_log.events import DomainEvent
from src.event_log.models import EventLog

SRC_ROOT = Path(src.__file__).parent


def _catalog() -> list[type[DomainEvent]]:
    for module in pkgutil.walk_packages(src.__path__, prefix="src."):
        if module.name.endswith(".events"):
            importlib.import_module(module.name)

    found: list[type[DomainEvent]] = []
    stack = list(DomainEvent.__subclasses__())
    while stack:
        subclass = stack.pop()
        stack.extend(subclass.__subclasses__())
        # Only production events: test modules declare their own subclasses,
        # and an intermediate base declares no code of its own.
        if subclass.__module__.startswith("src.") and "code" in vars(subclass):
            found.append(subclass)
    return found


def test_the_catalog_is_not_empty() -> None:
    """Every other test here passes vacuously if the walk finds nothing."""
    assert _catalog()


def test_no_two_events_share_a_code() -> None:
    """Two classes on one code make the log unreadable after the fact."""
    codes = [event.code for event in _catalog()]

    assert len(codes) == len(set(codes))


def test_a_code_is_prefixed_with_the_module_that_declares_it() -> None:
    """The prefix is what a reader filters and groups the log by."""
    for event in _catalog():
        module = event.__module__.split(".")[1]
        assert event.code.startswith(f"{module}."), event.code


def test_every_declared_code_is_written_somewhere() -> None:
    """A code nothing records is a row the log will never hold."""
    sources = "\n".join(
        path.read_text() for path in SRC_ROOT.rglob("*.py") if path.name != "events.py"
    )

    for event in _catalog():
        # A word boundary, not a substring: `NoteUpdated` occurs inside
        # `NoteUpdatedSomething` and would vouch for a class nothing records.
        assert re.search(rf"\b{event.__name__}\b", sources), event.code


def test_a_code_fits_the_column_that_stores_it() -> None:
    """An over-long code is dropped by the writer's swallow, so the action
    succeeds and its audit row silently never exists."""
    limit = EventLog.__table__.c.event_type.type.length

    for event in _catalog():
        assert len(event.code) <= limit, event.code
